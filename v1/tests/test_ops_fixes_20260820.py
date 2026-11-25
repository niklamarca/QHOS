"""Regressões dos bugs de 20/08: DuckDB retenção, JSON LLM, ledger CARRY."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from evolution.llm_agents import LLMAgent
from strategies.crypto_carry_shadow import CryptoCarryShadow


def test_duckdb_prune_under_contention(tmp_path: Path):
    db = tmp_path / "t.db"
    conn = duckdb.connect(str(db))
    conn.execute(
        "CREATE TABLE backtest_results ("
        "backtest_id VARCHAR PRIMARY KEY, "
        "created_at TIMESTAMP DEFAULT current_timestamp)"
    )
    for i in range(50):
        conn.execute(
            "INSERT INTO backtest_results VALUES (?, ?)",
            [f"old-{i}", datetime.utcnow() - timedelta(days=120)],
        )
    lock = threading.Lock()
    errors: list[Exception] = []

    def prune():
        try:
            cutoff = datetime.utcnow() - timedelta(days=90)
            with lock:
                antes = conn.execute("SELECT count(*) FROM backtest_results").fetchone()[0]
                conn.execute(
                    "DELETE FROM backtest_results WHERE created_at < ?", [cutoff]
                )
                depois = conn.execute("SELECT count(*) FROM backtest_results").fetchone()[0]
            assert depois <= antes
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def insert():
        for i in range(200):
            try:
                with lock:
                    conn.execute(
                        "INSERT OR REPLACE INTO backtest_results VALUES (?, ?)",
                        [f"new-{i}", datetime.utcnow()],
                    )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

    threads = [threading.Thread(target=prune), threading.Thread(target=insert),
               threading.Thread(target=prune)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors


def test_llm_json_lenient_recovers_truncated():
    truncated = '''{"strategies": [{"name": "A", "rule_type": "mean_revert",
      "signal_type": "long_short", "rationale": "teste",
      "entry_rules": [{"feature": "rsi_14", "operator": "<", "threshold": 30, "weight": 1.0}],
      "stop_loss_pct": 0.02, "take_profit_pct": 0.05, "max_holding_days": 20,
      "position_size": 0.05, "target_assets": ["SPY"]}, {"name": "B", "rule_type": "momentum",
      "rationale": "Hipótese: '''
    parsed = LLMAgent._loads_json_lenient(truncated)
    assert isinstance(parsed, dict)
    assert parsed.get("strategies")
    assert parsed["strategies"][0]["name"] == "A"


def test_crypto_carry_creates_ledger_header(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "strategies.crypto_carry_shadow.config.data_lake_dir", tmp_path
    )
    shadow = CryptoCarryShadow()
    assert shadow.ledger_path.exists()
    text = shadow.ledger_path.read_text(encoding="utf-8")
    assert text.startswith("date,nav,ret,active_assets,btc_close")
    assert len(text.strip().splitlines()) == 1
