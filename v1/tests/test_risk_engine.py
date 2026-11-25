"""RiskEngine: drawdown, daily loss, concentration, volatility and correlation limits, and how breaches escalate.

Limits are pinned in a fixture so a local .env cannot change what these tests assert.
"""
import asyncio

import numpy as np
import pytest

from core.config import config
from risk.risk_engine import RiskEngine

MAX_DD = 0.10
MAX_DAILY_LOSS = 0.02
MAX_POSITION = 0.50
MAX_CORRELATION = 0.65


class RecordingKillSwitch:
    """Stands in for KillSwitch: records which level the risk engine asked for."""

    def __init__(self, level: int = 0):
        self.level = level
        self.calls = []

    async def trigger_level2(self, source):
        self.calls.append((2, source))
        self.level = max(self.level, 2)

    async def trigger_level3(self, source):
        self.calls.append((3, source))
        self.level = 3


@pytest.fixture(autouse=True)
def pinned_limits(monkeypatch):
    monkeypatch.setattr(config, "max_portfolio_drawdown", MAX_DD)
    monkeypatch.setattr(config, "max_daily_loss", MAX_DAILY_LOSS)
    monkeypatch.setattr(config, "max_position_size", MAX_POSITION)
    monkeypatch.setattr(config, "max_strategy_correlation", MAX_CORRELATION)


@pytest.fixture
def engine():
    # storage is only kept as an attribute; none of the checks under test touch it
    return RiskEngine(storage=None)


def drawdown_to(engine, nav):
    """Peak at 100, then NAV falls to `nav`. Daily baseline is moved so only the drawdown check can fire."""
    engine.update_nav(100.0)
    engine.update_nav(nav)
    engine.reset_daily()


# ─── Drawdown ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nav, expected_severity", [
    (100.0, None),   # at the peak
    (95.0, None),    # 5% down, under the 10% limit
    (90.01, None),   # just under the limit
    (90.0, 2),       # exactly at the limit: reduce
    (87.0, 2),       # between 1x and 1.5x the limit: reduce
    (84.0, 3),       # beyond 1.5x the limit: flatten
    (50.0, 3),
])
def test_drawdown_severity_by_depth(engine, nav, expected_severity):
    drawdown_to(engine, nav)
    breach = engine._check_portfolio_drawdown()
    if expected_severity is None:
        assert breach is None
    else:
        assert breach.breach_type == "portfolio_drawdown"
        assert breach.severity == expected_severity
        assert breach.limit_value == MAX_DD


@pytest.mark.xfail(strict=True, reason=(
    "Boundary bug: 0.10 * 1.5 == 0.15000000000000002 in floating point, so a drawdown of exactly 15% stays at "
    "severity 2 instead of escalating to flatten. The daily-loss check (0.02 * 1.5 == 0.03) does escalate at its "
    "own 1.5x boundary, so the two checks disagree. Fix: compare with a small tolerance."))
def test_drawdown_of_exactly_one_and_a_half_times_the_limit_escalates_to_flatten(engine):
    drawdown_to(engine, 85.0)
    assert engine._check_portfolio_drawdown().severity == 3


def test_peak_follows_new_highs_and_drawdown_is_measured_from_it(engine):
    for nav in (100.0, 120.0, 110.0):
        engine.update_nav(nav)
    engine._check_portfolio_drawdown()
    assert engine.state.peak_nav == 120.0
    assert engine.state.portfolio_drawdown == pytest.approx(-10 / 120)


# ─── Daily loss ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nav, expected_severity", [
    (101.0, None),
    (98.5, None),    # -1.5%
    (98.0, 2),       # -2%: at the limit
    (97.0, 3),       # -3%: exactly 1.5x the limit
    (90.0, 3),
])
def test_daily_loss_severity(engine, nav, expected_severity):
    engine.update_nav(100.0)        # first NAV of the day is the baseline
    engine.update_nav(nav)
    breach = engine._check_daily_pnl()
    if expected_severity is None:
        assert breach is None
    else:
        assert (breach.breach_type, breach.severity) == ("daily_loss", expected_severity)


def test_reset_daily_moves_the_baseline_to_the_current_nav(engine):
    engine.update_nav(100.0)
    engine.update_nav(97.0)
    engine.reset_daily()
    engine.update_nav(96.0)         # -1% against the new 97 baseline
    assert engine._check_daily_pnl() is None
    assert engine.state.daily_pnl_pct == pytest.approx(-1 / 97)


def test_daily_loss_is_skipped_before_the_first_nav(engine):
    assert engine._check_daily_pnl() is None


# ─── Concentration, volatility, correlation (alerts only) ────────────────────

@pytest.mark.parametrize("positions, breached", [
    ({"ES": 0.30, "NQ": 0.20}, False),
    ({"ES": 0.50}, False),           # the limit itself is allowed
    ({"ES": 0.51}, True),
    ({"ES": -0.60, "NQ": 0.10}, True),   # a short counts by absolute size
])
def test_concentration_alert(engine, positions, breached):
    engine.update_positions(positions)
    breach = engine._check_concentration()
    assert (breach is not None) == breached
    if breached:
        assert breach.severity == 1


def test_update_positions_derives_exposure_and_cash(engine):
    engine.update_positions({"ES": 0.4, "NQ": -0.2})
    assert engine.state.total_exposure == pytest.approx(0.6)
    assert engine.state.cash_pct == pytest.approx(0.4)


def test_volatility_needs_twenty_observations(engine):
    for nav in np.linspace(100, 110, 19):
        engine.update_nav(float(nav))
    assert engine._check_portfolio_volatility() is None


def test_calm_nav_raises_no_volatility_alert(engine):
    for i in range(25):
        engine.update_nav(100.0 + 0.01 * i)
    assert engine._check_portfolio_volatility() is None


def test_swinging_nav_raises_a_volatility_alert(engine):
    for i in range(25):
        engine.update_nav(100.0 if i % 2 == 0 else 105.0)   # ±5% a day is far above 30% a year
    breach = engine._check_portfolio_volatility()
    assert breach.breach_type == "high_volatility"
    assert breach.severity == 1


def test_highly_correlated_strategies_raise_an_alert(engine):
    rng = np.random.default_rng(7)
    base = rng.normal(0, 1, 30)
    for pnl in base:
        engine.update_strategy_pnl("a", float(pnl))
        engine.update_strategy_pnl("b", float(pnl * 2 + 0.01))
    breach = engine._check_strategy_correlation()
    assert breach.breach_type == "strategy_correlation"
    assert breach.current_value == pytest.approx(1.0, abs=1e-6)


def test_independent_strategies_raise_no_correlation_alert(engine):
    rng = np.random.default_rng(11)
    for a, b in zip(rng.normal(0, 1, 60), rng.normal(0, 1, 60)):
        engine.update_strategy_pnl("a", float(a))
        engine.update_strategy_pnl("b", float(b))
    assert engine._check_strategy_correlation() is None


def test_correlation_ignores_strategies_with_short_history(engine):
    for pnl in range(30):
        engine.update_strategy_pnl("long_history", float(pnl))
    for pnl in range(5):
        engine.update_strategy_pnl("new", float(pnl))
    assert engine._check_strategy_correlation() is None


# ─── Escalation through check_all ────────────────────────────────────────────

@pytest.mark.parametrize("nav, expected_calls", [
    (95.0, []),                          # no breach
    (88.0, [(2, "risk_engine")]),        # severity 2 -> L2
    (80.0, [(3, "risk_engine")]),        # severity 3 -> L3
])
def test_check_all_escalates_to_the_kill_switch_level_matching_the_worst_breach(engine, nav, expected_calls):
    ks = RecordingKillSwitch()
    engine.inject_kill_switch(ks)
    drawdown_to(engine, nav)
    asyncio.run(engine.check_all())
    assert ks.calls == expected_calls


def test_alert_only_breaches_never_trigger_the_kill_switch(engine):
    ks = RecordingKillSwitch()
    engine.inject_kill_switch(ks)
    engine.update_nav(100.0)
    engine.update_positions({"ES": 0.9})     # severity 1 only
    result = asyncio.run(engine.check_all())
    assert result["severity"] == 1
    assert ks.calls == []


def test_no_automatic_kill_switch_before_any_real_nav_update(engine):
    """Research mode never calls update_nav; a stale default state must not flatten anything."""
    ks = RecordingKillSwitch()
    engine.inject_kill_switch(ks)
    engine.state.nav = 50.0                  # looks like a 50% drawdown, but no NAV was ever reported
    result = asyncio.run(engine.check_all())
    assert result["severity"] == 3
    assert ks.calls == []


def test_check_all_reports_and_clears_breaches(engine):
    drawdown_to(engine, 85.0)
    assert asyncio.run(engine.check_all())["breaches"] == 1
    engine.update_nav(100.0)
    assert asyncio.run(engine.check_all())["breaches"] == 0
    assert engine._active_breaches == []


def test_check_all_syncs_the_real_kill_switch_level_into_state(engine):
    engine.inject_kill_switch(RecordingKillSwitch(level=2))
    asyncio.run(engine.check_all())
    assert engine.state.kill_level == 2


# ─── Pre-trade gate ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("ks_level, size, nav, approved, reason", [
    (0, 0.20, 100.0, True, "ok"),
    (0, 0.50, 100.0, True, "ok"),                         # exactly the max weight
    (0, -0.40, 100.0, True, "ok"),                        # shorts are sized by absolute weight
    (0, 0.51, 100.0, False, "position_too_large"),
    (1, 0.10, 100.0, False, "kill_switch_L1_active"),
    (3, 0.10, 100.0, False, "kill_switch_L3_active"),
    (0, 0.10, 90.5, False, "near_dd_limit"),              # 9.5% down: past 90% of the 10% limit
    (0, 0.10, 92.0, True, "ok"),
])
def test_pre_trade_check(engine, ks_level, size, nav, approved, reason):
    engine.inject_kill_switch(RecordingKillSwitch(level=ks_level))
    drawdown_to(engine, nav)
    engine._check_portfolio_drawdown()
    ok, why = engine.pre_trade_check("ES", size, direction=1)
    assert ok is approved
    assert why.startswith(reason)


def test_pre_trade_check_reads_the_live_kill_switch_not_a_stale_state_copy(engine):
    ks = RecordingKillSwitch(level=0)
    engine.inject_kill_switch(ks)
    assert engine.pre_trade_check("ES", 0.1, 1)[0] is True
    ks.level = 1                                # changed after the last check_all
    assert engine.pre_trade_check("ES", 0.1, 1) == (False, "kill_switch_L1_active")
