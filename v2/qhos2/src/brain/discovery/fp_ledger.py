"""Live false-positive ledger and false-negative foundation. Not training truth."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping


class FalsePositiveLedger:
    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def record(self, row: Mapping[str, Any]) -> None:
        self.rows.append({
            "candidate_id": str(row.get("candidate_id") or row.get("fingerprint") or "")[:40],
            "signals": list(row.get("signals") or row.get("evidence") or [])[:6],
            "strategy": str(row.get("strategy") or "")[:32],
            "confidence": float(row.get("score") or row.get("confidence") or 0.0),
            "human_disposition": str(row.get("human_disposition") or "")[:24],
            "final_disposition": str(row.get("final_disposition") or row.get("status") or "")[:32],
            "useful": bool(row.get("useful")) if row.get("useful") is not None else None,
            "noise": bool(row.get("noise")) if row.get("noise") is not None else None,
        })

    def summary(self) -> Dict[str, Any]:
        n = max(1, len(self.rows))
        useful = sum(1 for r in self.rows if r.get("useful") is True)
        noise = sum(1 for r in self.rows if r.get("noise") is True)
        dismissed = sum(1 for r in self.rows if r.get("final_disposition") == "DISMISSED")
        return {
            "n": len(self.rows),
            "useful_rate": round(useful / n, 4) if self.rows else None,
            "noise_rate": round(noise / n, 4) if self.rows else None,
            "dismissal_rate": round(dismissed / n, 4) if self.rows else None,
            "not_training_truth": True,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"rows": list(self.rows), **self.summary()}


class FalseNegativeFoundation:
    """Retrospective hook only. Do not claim a complete FN rate."""

    def __init__(self) -> None:
        self.links: List[Dict[str, Any]] = []

    def connect(self, later_incident: str, earlier_signals: List[str]) -> None:
        self.links.append({
            "later": str(later_incident)[:120],
            "earlier_signals": [str(x)[:80] for x in earlier_signals[:8]],
            "complete_fn_rate": False,
        })

    def to_dict(self) -> Dict[str, Any]:
        return {"links": list(self.links), "complete_fn_rate": False, "claim": "foundation_only"}
