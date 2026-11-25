import asyncio

from execution.abstract import Order, OrderSide
from execution.paper_broker import PaperBroker


def test_paper_broker_can_open_and_close_short(monkeypatch):
    monkeypatch.setattr("execution.paper_broker.np.random.normal", lambda *args, **kwargs: 0.0)
    monkeypatch.setattr("execution.paper_broker.np.random.uniform", lambda *args, **kwargs: 0.0)
    broker = PaperBroker(initial_capital=100_000.0, commission_bps=1.0, persist=False)
    broker.update_price("SPY", 100.0)

    asyncio.run(broker.submit_order(Order(symbol="SPY", side=OrderSide.SELL, quantity=10)))
    assert broker.positions["SPY"] == -10

    asyncio.run(broker.submit_order(Order(symbol="SPY", side=OrderSide.BUY, quantity=10)))
    assert "SPY" not in broker.positions
    assert broker.cash < broker.initial_capital

