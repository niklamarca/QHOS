"""Provider performance ledger. Observe-only — no autonomous routing."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional


class ProviderLedger:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def record(self, row: Dict[str, Any]) -> None:
        clean = {
            "provider": str(row.get("provider") or "")[:40],
            "model": str(row.get("model") or "")[:80],
            "task_type": str(row.get("task_type") or "diagnose")[:40],
            "scenario_family": str(row.get("scenario_family") or "")[:40],
            "scenario_id": str(row.get("scenario_id") or "")[:24],
            "run_id": str(row.get("run_id") or "")[:24],
            "diagnosis": float(row.get("diagnosis") or 0.0),
            "verification_efficiency": float(row.get("verification_efficiency") or 0.0),
            "correctness": row.get("correctness"),
            "safety": float(row.get("safety") or 0.0),
            "latency_s": float(row.get("latency_s") or 0.0),
            "input_tokens": int(row.get("input_tokens") or 0),
            "output_tokens": int(row.get("output_tokens") or 0),
            "tokens": int(row.get("tokens") or 0),
            "cost": row.get("cost"),
            "stop": str(row.get("stop") or ""),
            "system_confidence": float(row.get("system_confidence") or 0.0),
            "model_confidence": float(row.get("model_confidence") or 0.0),
            "actual_correct": row.get("actual_correct"),
        }
        self.rows.append(clean)

    def summary(self) -> Dict[str, Any]:
        by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in self.rows:
            by[row["provider"] or "unknown"].append(row)
        out = {}
        for name, items in by.items():
            n = max(1, len(items))
            out[name] = {
                "n": len(items),
                "diagnosis_mean": round(sum(i["diagnosis"] for i in items) / n, 4),
                "safety_mean": round(sum(i["safety"] for i in items) / n, 4),
                "latency_mean": round(sum(i["latency_s"] for i in items) / n, 4),
                "tokens_total": sum(i["tokens"] for i in items),
                "correct": sum(1 for i in items if i.get("actual_correct") is True),
                "incorrect": sum(1 for i in items if i.get("actual_correct") is False),
            }
        return {
            "by_provider": out,
            "routing": "observe_only",
            "autonomous_routing": False,
            "recommendation": self._recommend(out),
        }

    @staticmethod
    def _recommend(by: Dict[str, Any]) -> Optional[str]:
        if len(by) < 2:
            return None
        ranked = sorted(by.items(), key=lambda kv: kv[1].get("diagnosis_mean") or 0.0, reverse=True)
        best, rest = ranked[0][0], ranked[1][0]
        return f"{best} appears better for debugging than {rest}"

    def to_dict(self) -> Dict[str, Any]:
        return {"rows": list(self.rows)[-80:], "summary": self.summary(), "autonomous_routing": False}
