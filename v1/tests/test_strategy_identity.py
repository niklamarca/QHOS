import copy

import numpy as np

from evolution.strategy_dna import SignalType, StrategyFactory


def _strategy():
    return StrategyFactory.random(["SPY"], np.random.default_rng(123))


def test_factory_matches_current_single_asset_backtest_contract():
    assert len(_strategy().target_assets) == 1


def test_fingerprint_changes_with_signal_semantics():
    base = _strategy()
    changed = copy.deepcopy(base)
    changed.signal_type = (
        SignalType.LONG_ONLY
        if base.signal_type != SignalType.LONG_ONLY
        else SignalType.LONG_SHORT
    )
    assert base.fingerprint() != changed.fingerprint()


def test_fingerprint_changes_with_exit_rule():
    base = _strategy()
    changed = copy.deepcopy(base)
    changed.exit_rule.trailing_stop_pct += 0.001
    assert base.fingerprint() != changed.fingerprint()


def test_rejected_candidate_has_zero_fitness():
    strategy = _strategy()
    strategy.status = "rejected"
    strategy.oos_sharpe = 5.0
    strategy.n_trades = 500
    strategy.stability_score = 1.0
    strategy.regime_robustness = 1.0
    assert strategy.compute_fitness() == 0.0

