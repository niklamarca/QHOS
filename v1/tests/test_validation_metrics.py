"""Walk-forward acceptance rules, backtest metrics on hand-built equity curves, the embargoed hold-out split and the
config loader. Pure functions: no market data, no network."""
import importlib

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from backtest.engine import BacktestEngine, BacktestResult
from backtest.walk_forward import WalkForwardResult
from core.config import SystemConfig, config
from core.embargo import research_cut, split_embargo


@pytest.fixture(autouse=True)
def pinned_thresholds(monkeypatch):
    for name, value in {
        "validation_min_oos_sharpe": 0.5,
        "validation_wf_min_positive_folds": 0.6,
        "validation_wf_min_worst_fold": 0.0,
        "validation_wf_min_stability": 0.2,
        "validation_wf_max_degradation": 0.4,
        "validation_wf_degradation_ref_min": 0.5,
        "risk_free_rate": 0.0,
        "backtest_min_trades": 10,
        "embargo_fraction": 0.20,
    }.items():
        monkeypatch.setattr(config, name, value)


# ─── Walk-forward acceptance ─────────────────────────────────────────────────

def wf(folds, anchor=0.0):
    result = WalkForwardResult(strategy_id="s", n_folds=len(folds), fold_sharpes=list(folds), is_anchor_sharpe=anchor)
    result.compute()
    return result


@pytest.mark.parametrize("folds, anchor, valid, why", [
    ([1.0, 1.1, 0.9, 1.2, 1.0], 0.0, True, "consistent positive folds"),
    ([0.4, 0.4, 0.4, 0.4, 0.4], 0.0, False, "mean below the 0.5 minimum"),
    ([1.5, 1.5, 1.5, 1.5, -0.1], 0.0, False, "one fold below zero"),
    ([0.1, 3.0, 0.1, 3.0, 0.1], 0.0, False, "unstable across folds"),
    ([1.0, 1.0, 1.0, 1.0, 1.0], 4.0, False, "collapses to 25% of the in-sample anchor"),
    ([1.0, 1.0, 1.0, 1.0, 1.0], 2.0, True, "keeps 50% of the anchor, above the 40% floor"),
    ([1.0, 1.0, 1.0, 1.0, 1.0], 0.3, True, "anchor too weak to judge degradation"),
])
def test_walk_forward_acceptance(folds, anchor, valid, why):
    assert wf(folds, anchor).overall_valid is valid, why


def test_walk_forward_summary_statistics():
    result = wf([1.0, 2.0, 3.0, -1.0])
    assert result.mean_sharpe == pytest.approx(1.25)
    assert result.min_sharpe == -1.0
    assert result.pct_positive_folds == pytest.approx(0.75)
    assert 0.0 <= result.stability_score <= 1.0


def test_degradation_ratio_is_oos_mean_over_anchor():
    assert wf([1.0] * 5, anchor=2.0).degradation_ratio == pytest.approx(0.5)


def test_walk_forward_without_folds_stays_invalid():
    result = WalkForwardResult(strategy_id="s", n_folds=5)
    result.compute()
    assert result.overall_valid is False
    assert result.mean_sharpe == 0.0


# ─── Backtest metrics ────────────────────────────────────────────────────────

def equity(values):
    return pd.Series(values, index=pd.bdate_range("2026-01-02", periods=len(values)), dtype=float)


def metrics(values, trades=()):
    eq = equity(values)
    return BacktestEngine(slippage_bps=0, commission_bps=0)._compute_metrics("s", eq, list(trades), eq, "all")


def test_max_drawdown_and_total_return_on_a_known_curve():
    # up to 120, down to 90 (-25% from the peak), recovers to 110
    values = list(np.linspace(100, 120, 10)) + list(np.linspace(120, 90, 10)) + list(np.linspace(90, 110, 10))
    result = metrics(values)
    assert result.max_drawdown == pytest.approx(-0.25, abs=1e-6)
    assert result.total_return == pytest.approx(0.10)
    assert result.calmar == pytest.approx(result.cagr / 0.25)


def test_steadily_rising_curve_has_positive_sharpe_and_no_drawdown():
    rng = np.random.default_rng(0)
    values = 100 * np.cumprod(1 + 0.001 + rng.normal(0, 0.0001, 60))
    result = metrics(values)
    assert result.sharpe > 3
    assert result.max_drawdown == pytest.approx(0.0, abs=1e-9)


def test_falling_curve_has_negative_sharpe():
    assert metrics(np.linspace(100, 80, 40)).sharpe < 0


def test_fewer_than_twenty_returns_gives_an_empty_result():
    result = metrics(np.linspace(100, 110, 20))     # 20 prices -> 19 returns
    assert (result.sharpe, result.n_trades, result.equity_curve) == (0.0, 0, None)


def test_win_rate_and_profit_factor_from_trades():
    trades = [{"pnl": 3.0, "duration": 2}, {"pnl": -1.0, "duration": 4}, {"pnl": 1.0, "duration": 6},
              {"pnl": -2.0, "duration": 8}]
    result = metrics(np.linspace(100, 110, 30), trades)
    assert result.n_trades == 4
    assert result.win_rate == pytest.approx(0.5)
    assert result.profit_factor == pytest.approx(4 / 3)
    assert result.avg_trade_duration == pytest.approx(5.0)


def test_profit_factor_is_capped_at_two_when_there_are_no_losses():
    """No losing trade would make the ratio infinite; the engine reports a fixed 2.0 instead."""
    result = metrics(np.linspace(100, 110, 30), [{"pnl": 1.0, "duration": 1}] * 3)
    assert result.profit_factor == 2.0


@pytest.mark.parametrize("n_trades, sharpe, valid", [
    (10, 1.0, True),
    (9, 1.0, False),            # below backtest_min_trades
    (50, float("nan"), False),
    (50, float("inf"), False),
])
def test_backtest_result_validity(n_trades, sharpe, valid):
    assert BacktestResult(strategy_id="s", n_trades=n_trades, sharpe=sharpe).is_valid is valid


def test_engine_accepts_explicit_zero_costs():
    engine = BacktestEngine(slippage_bps=0, commission_bps=0)
    assert engine.total_cost_bps == 0


# ─── Embargoed hold-out ──────────────────────────────────────────────────────

@pytest.mark.parametrize("n, embargo, cut", [(100, 0.2, 80), (100, 0.0, 100), (10, 0.25, 7), (0, 0.2, 0)])
def test_research_cut(n, embargo, cut):
    assert research_cut(n, embargo) == cut


def test_research_cut_uses_config_when_no_embargo_is_given():
    assert research_cut(100) == 80


def test_split_embargo_keeps_order_and_never_overlaps():
    idx = pd.bdate_range("2026-01-02", periods=50)
    features = pd.DataFrame({"x": range(50)}, index=idx)
    prices = pd.Series(range(50), index=idx, dtype=float)
    (research_f, research_p), (audit_f, audit_p) = split_embargo(features, prices, embargo=0.2)

    assert len(research_f) == 40 and len(audit_f) == 10
    assert research_f.index.max() < audit_f.index.min()          # audit window is strictly later
    assert research_p.index.equals(research_f.index)
    assert pd.concat([research_f, audit_f]).equals(features)


# ─── Config ──────────────────────────────────────────────────────────────────

def test_config_defaults_without_env_file():
    cfg = SystemConfig(_env_file=None)
    assert cfg.system_mode == "research"
    assert cfg.max_portfolio_drawdown == pytest.approx(0.10)


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv("MAX_DAILY_LOSS", "0.05")
    monkeypatch.setenv("SYSTEM_MODE", "paper")
    cfg = SystemConfig(_env_file=None)
    assert cfg.max_daily_loss == pytest.approx(0.05)
    assert cfg.system_mode == "paper"


def test_invalid_system_mode_is_rejected(monkeypatch):
    monkeypatch.setenv("SYSTEM_MODE", "yolo")
    with pytest.raises(ValidationError):
        SystemConfig(_env_file=None)


def test_boot_lists_env_overridden_field_names(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)                 # no .env in the working directory
    monkeypatch.setenv("MAX_POSITION_SIZE", "0.3")
    config_module = importlib.import_module("core.config")    # core/__init__ re-exports the `config` object
    assert "max_position_size" in config_module._campos_sobrescritos_pelo_env()
