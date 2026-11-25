"""PaperBroker fills, costs, cash checks, shorts, flatten/reduce and the state file that survives a restart.

Randomness is pinned: latency is zeroed and slippage is either zero or a fixed draw, so every number is exact.
"""
import asyncio
import json

import pytest

from execution.abstract import Order, OrderSide, OrderStatus
from execution.paper_broker import PaperBroker


@pytest.fixture(autouse=True)
def no_latency(monkeypatch):
    monkeypatch.setattr("execution.paper_broker.np.random.uniform", lambda *a, **k: 0.0)


def fixed_slippage(monkeypatch, value):
    monkeypatch.setattr("execution.paper_broker.np.random.normal", lambda *a, **k: value)


def broker(**kwargs):
    kwargs.setdefault("initial_capital", 100_000.0)
    kwargs.setdefault("slippage_bps", 0.0)
    kwargs.setdefault("commission_bps", 0.0)
    kwargs.setdefault("persist", False)
    return PaperBroker(**kwargs)


def buy(b, symbol, qty):
    return asyncio.run(b.submit_order(Order(symbol=symbol, side=OrderSide.BUY, quantity=qty)))


def sell(b, symbol, qty):
    return asyncio.run(b.submit_order(Order(symbol=symbol, side=OrderSide.SELL, quantity=qty)))


def nav(b):
    return asyncio.run(b.get_account_value())


def test_explicit_zero_costs_are_honoured():
    """Regression: `slippage_bps or default` turned an explicit 0 into the config default (5 + 3 bps)."""
    b = broker(slippage_bps=0, commission_bps=0)
    assert b.slippage_bps == 0
    assert b.commission_bps == 0


def test_costs_default_to_the_backtest_config_when_not_given(monkeypatch):
    from core.config import config
    monkeypatch.setattr(config, "backtest_slippage_bps", 5.0)
    monkeypatch.setattr(config, "backtest_commission_bps", 3.0)
    b = PaperBroker(persist=False)
    assert b.slippage_bps == pytest.approx(0.0005)
    assert b.commission_bps == pytest.approx(0.0003)


def test_zero_cost_buy_moves_cash_into_the_position():
    b = broker()
    b.update_price("ES", 100.0)
    fill = buy(b, "ES", 10)
    assert (fill.price, fill.commission) == (100.0, 0.0)
    assert b.cash == pytest.approx(99_000.0)
    assert b.positions == {"ES": 10}
    assert nav(b) == pytest.approx(100_000.0)


def test_commission_is_charged_on_notional():
    b = broker(commission_bps=10.0)          # 10 bps = 0.1%
    b.update_price("ES", 100.0)
    fill = buy(b, "ES", 10)
    assert fill.commission == pytest.approx(1.0)
    assert b.cash == pytest.approx(100_000.0 - 1_000.0 - 1.0)


@pytest.mark.parametrize("draw", [0.0005, -0.0005])
def test_slippage_always_goes_against_the_order(monkeypatch, draw):
    fixed_slippage(monkeypatch, draw)        # sign of the random draw must not matter
    b = broker(slippage_bps=5.0)
    b.update_price("ES", 100.0)
    assert buy(b, "ES", 1).price == pytest.approx(100.05)
    b.update_price("ES", 100.0)
    assert sell(b, "ES", 1).price == pytest.approx(99.95)


def test_round_trip_without_costs_returns_to_initial_capital():
    b = broker()
    b.update_price("ES", 100.0)
    buy(b, "ES", 25)
    sell(b, "ES", 25)
    assert b.positions == {}
    assert b.cash == pytest.approx(100_000.0)


def test_round_trip_with_costs_loses_exactly_the_costs():
    b = broker(commission_bps=10.0)
    b.update_price("ES", 100.0)
    buy(b, "ES", 10)
    sell(b, "ES", 10)
    assert b.cash == pytest.approx(100_000.0 - 2 * 1.0)


def test_mark_to_market_changes_nav_but_not_cash():
    b = broker()
    b.update_price("ES", 100.0)
    buy(b, "ES", 10)
    b.update_price("ES", 110.0)
    assert nav(b) == pytest.approx(100_100.0)
    assert b.cash == pytest.approx(99_000.0)
    assert b.get_pnl_summary()["total_return"] == pytest.approx(0.001)


def test_buy_larger_than_cash_is_scaled_down_to_fit():
    b = broker(initial_capital=1_000.0, commission_bps=10.0)
    b.update_price("ES", 100.0)
    fill = buy(b, "ES", 50)                  # wants $5,000 with $1,000 of cash
    assert fill.quantity < 10
    assert b.cash >= 0
    assert b.cash == pytest.approx(10.0, abs=0.5)   # keeps the 1% buffer the broker leaves


def test_buy_with_almost_no_cash_is_rejected():
    b = broker(initial_capital=0.01)
    b.update_price("ES", 100.0)
    order = Order(symbol="ES", side=OrderSide.BUY, quantity=1)
    with pytest.raises(ValueError, match="Cash insuficiente"):
        asyncio.run(b.submit_order(order))
    assert order.status == OrderStatus.REJECTED
    assert b.positions == {}


def test_sell_without_a_position_opens_a_short():
    b = broker()
    b.update_price("ES", 100.0)
    sell(b, "ES", 10)
    assert b.positions == {"ES": -10}
    assert b.cash == pytest.approx(101_000.0)
    b.update_price("ES", 90.0)               # short gains when price falls
    assert nav(b) == pytest.approx(100_100.0)


def test_filled_order_records_status_price_and_quantity():
    b = broker()
    b.update_price("ES", 100.0)
    order = Order(symbol="ES", side=OrderSide.BUY, quantity=3)
    asyncio.run(b.submit_order(order))
    assert order.status == OrderStatus.FILLED
    assert (order.filled_price, order.filled_quantity) == (100.0, 3)
    assert order.filled_at is not None


def test_flatten_all_closes_longs_and_shorts():
    b = broker()
    for symbol in ("ES", "NQ"):
        b.update_price(symbol, 100.0)
    buy(b, "ES", 10)
    sell(b, "NQ", 5)
    asyncio.run(b.flatten_all())
    assert b.positions == {}
    assert b.cash == pytest.approx(100_000.0)


@pytest.mark.parametrize("target_pct, remaining", [(0.5, 5.0), (0.25, 2.5), (0.0, 0.0)])
def test_reduce_exposure_keeps_the_target_fraction(target_pct, remaining):
    b = broker()
    b.update_price("ES", 100.0)
    buy(b, "ES", 10)
    asyncio.run(b.reduce_exposure(target_pct=target_pct))
    assert b.positions.get("ES", 0.0) == pytest.approx(remaining)


def test_state_survives_a_restart(tmp_path):
    state = tmp_path / "paper_broker_state.json"
    first = broker(persist=True, state_path=state)
    first.update_price("ES", 100.0)
    buy(first, "ES", 10)

    second = broker(persist=True, state_path=state, initial_capital=999.0)
    assert second.cash == pytest.approx(99_000.0)
    assert second.positions == {"ES": 10}
    assert second.initial_capital == 100_000.0     # anchored to the original capital, not the new argument
    assert second.get_pnl_summary()["total_return"] == pytest.approx(0.0)


def test_corrupted_state_file_starts_clean_instead_of_crashing(tmp_path):
    state = tmp_path / "paper_broker_state.json"
    state.write_text("{not json")
    b = broker(persist=True, state_path=state)
    assert b.cash == 100_000.0
    assert b.positions == {}


def test_state_file_is_written_atomically_and_holds_no_temp_file(tmp_path):
    state = tmp_path / "paper_broker_state.json"
    b = broker(persist=True, state_path=state)
    b.update_price("ES", 100.0)
    buy(b, "ES", 1)
    saved = json.loads(state.read_text())
    assert saved["positions"] == {"ES": 1}
    assert saved["fills_cum"] == 1
    assert not (tmp_path / "paper_broker_state.json.tmp").exists()


def test_persist_false_never_touches_disk(tmp_path):
    state = tmp_path / "paper_broker_state.json"
    b = broker(persist=False, state_path=state)
    b.update_price("ES", 100.0)
    buy(b, "ES", 1)
    assert not state.exists()
