"""Memória científica append-only do laboratório quantitativo.

O catálogo DuckDB é operacional e pode sofrer retenção. Este registro não é
usado para seleção: preserva cada backtest e snapshot de hipótese para que o
número de tentativas nunca seja esquecido ao calcular significância.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from core.config import config, ROOT_DIR
from core.logger import get_logger

log = get_logger("ExperimentRegistry")


class ExperimentRegistry:
    def __init__(self):
        self.root = Path(config.data_lake_dir) / "experiments"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._code_hash = self._fingerprint_code()
        self._count_cache: tuple[float, int] = (0.0, 0)
        self._hypothesis_cache: Dict[str, set[str]] = {}

    @staticmethod
    def _hash_bytes(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _fingerprint_code(self) -> str:
        from governance.epistemics import current_code_fingerprint
        return current_code_fingerprint()

    def _data_fingerprint(self) -> str:
        from governance.epistemics import current_data_fingerprint
        return current_data_fingerprint()

    def _append(self, kind: str, payload: Dict[str, Any]) -> None:
        record = {"ts": time.time(), "iso": datetime.now().isoformat(timespec="seconds"),
                  "kind": kind, "code_hash": self._code_hash,
                  "data_fingerprint": self._data_fingerprint(), "payload": payload}
        path = self.root / f"{kind}_{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        with self._lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str, ensure_ascii=False, sort_keys=True) + "\n")

    def record_backtest(self, strategy_id: str, result: Dict[str, Any]) -> None:
        # Métricas suficientes para auditoria, sem duplicar equity curves enormes.
        payload = {"strategy_id": str(strategy_id), "sharpe": float(result.get("sharpe", 0) or 0),
                   "sortino": float(result.get("sortino", 0) or 0),
                   "max_drawdown": float(result.get("max_drawdown", 0) or 0),
                   "win_rate": float(result.get("win_rate", 0) or 0),
                   "n_trades": int(result.get("n_trades", 0) or 0),
                   "cagr": float(result.get("cagr", 0) or 0),
                   "regime": str(result.get("regime", "unknown"))}
        self._append("backtests", payload)

    def record_strategy(self, strategy_id: str, metadata: Dict[str, Any]) -> None:
        self._append("strategies", {"strategy_id": str(strategy_id), "metadata": metadata})

    def _hypotheses_for(self, lineage: str) -> set[str]:
        if lineage in self._hypothesis_cache:
            return self._hypothesis_cache[lineage]
        found: set[str] = set()
        for path in self.root.glob("hypotheses_*.jsonl"):
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    payload = row.get("payload", {})
                    if payload.get("data_lineage") == lineage and payload.get("fingerprint"):
                        found.add(str(payload["fingerprint"]))
            except (OSError, json.JSONDecodeError):
                continue
        self._hypothesis_cache[lineage] = found
        return found

    def record_hypothesis_test(self, fingerprint: str, lineage: str,
                               legacy: bool = False) -> None:
        fingerprint = str(fingerprint)
        seen = self._hypotheses_for(lineage)
        if not fingerprint or fingerprint in seen:
            return
        self._append("hypotheses", {"fingerprint": fingerprint,
                                    "data_lineage": lineage, "legacy": bool(legacy)})
        seen.add(fingerprint)

    def count_unique_hypotheses(self, lineage: str) -> int:
        return len(self._hypotheses_for(lineage))

    def count_backtests(self) -> int:
        cached_at, cached_count = self._count_cache
        if time.time() - cached_at < 60.0:
            return cached_count
        total = 0
        for path in self.root.glob("backtests_*.jsonl"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    total += sum(1 for _ in fh)
            except OSError:
                pass
        self._count_cache = (time.time(), total)
        return total


experiments = ExperimentRegistry()
