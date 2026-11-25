"""Strategy ledger. Observe-only. No autonomous routing."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List


class StrategyLedger:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.autonomous_routing = False

    def record(self, row: Dict[str, Any]) -> None:
        self.rows.append({
            "provider": str(row.get("provider") or "")[:40],
            "model": str(row.get("model") or "")[:80],
            "strategy": str(row.get("strategy") or "")[:40],
            "scenario_family": str(row.get("scenario_family") or "")[:40],
            "accuracy": float(row.get("accuracy") or 0.0),
            "verification_efficiency": float(row.get("verification_efficiency") or 0.0),
            "memory_relevance": float(row.get("memory_relevance") or 0.0),
            "context_efficiency": float(row.get("context_efficiency") or 0.0),
            "tool_calls": int(row.get("tool_calls") or 0),
            "tokens": int(row.get("tokens") or 0),
            "latency_s": float(row.get("latency_s") or 0.0),
            "safety": float(row.get("safety") or 0.0),
            "effort": str(row.get("effort") or "")[:12],
            "completion_state": str(row.get("completion_state") or "")[:32],
        })

    def summary(self) -> Dict[str, Any]:
        by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in self.rows:
            by[row["strategy"] or "unknown"].append(row)
        out = {}
        for name, rows in by.items():
            n = max(1, len(rows))
            out[name] = {
                "n": len(rows),
                "accuracy_mean": round(sum(r["accuracy"] for r in rows) / n, 4),
                "safety_mean": round(sum(r["safety"] for r in rows) / n, 4),
                "tokens": sum(r["tokens"] for r in rows),
                "tool_calls": sum(r["tool_calls"] for r in rows),
            }
        return {
            "by_strategy": out,
            "autonomous_routing": False,
            "default_runtime": "deterministic",
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"rows": list(self.rows), **self.summary()}
