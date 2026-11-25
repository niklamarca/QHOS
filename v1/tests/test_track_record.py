import pandas as pd

from core.track_record import TrackRecord


def test_track_record_keeps_only_exchange_sessions_and_backs_up(tmp_path):
    path = tmp_path / "nav.csv"
    pd.DataFrame([
        {"date": "2026-08-15", "nav": 100000},  # sabado
        {"date": "2026-08-17", "nav": 100100},
        {"date": "2026-08-18", "nav": 100200},
    ]).to_csv(path, index=False)
    record = TrackRecord(str(path))
    assert record.n_sessions == 2
    assert record.n_trades == 0
    assert len(list(tmp_path.glob("*.bak"))) == 1
    assert record.summary()["execution_evidence"] == "SIMULATED_CLOSE_FILL"

