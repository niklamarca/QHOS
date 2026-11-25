from datetime import timedelta

import pandas as pd

from core.market_clock import last_settled_session, now_et
from data.provenance import assess


def _frame(partial_close: float):
    settled = last_settled_session()
    current = now_et().date()
    if current <= settled:
        current = settled + timedelta(days=1)
    index = pd.to_datetime([settled, current])
    return pd.DataFrame({
        "open": [100.0, 101.0], "high": [102.0, 103.0],
        "low": [99.0, 100.0], "close": [101.0, partial_close],
        "volume": [1000.0, 500.0],
    }, index=index)


def test_partial_bar_does_not_change_final_research_hash():
    first, safe_first = assess("SPY", _frame(102.0), source="test")
    second, safe_second = assess("SPY", _frame(120.0), source="test")
    assert len(safe_first) == len(safe_second) == 1
    assert first.data_hash != second.data_hash
    assert first.final_data_hash == second.final_data_hash
