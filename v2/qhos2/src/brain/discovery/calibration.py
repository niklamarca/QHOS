"""Score/threshold calibration. Recommendations only — never auto-applied."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


def threshold_recommendation(
    reviewed: Sequence[Mapping[str, Any]],
    *,
    current: float = 0.42,
) -> Dict[str, Any]:
    """Analyze confirmed vs dismissed score distributions. Do not mutate policy."""
    useful_scores = [
        float(r.get("brain_score") or 0.0)
        for r in reviewed
        if str(r.get("human_disposition") or "") in {"CONFIRMED", "USEFUL"}
    ]
    noise_scores = [
        float(r.get("brain_score") or 0.0)
        for r in reviewed
        if str(r.get("human_disposition") or "") in {"DISMISSED", "NOT_USEFUL"}
    ]
    if not reviewed:
        return {
            "kind": "THRESHOLD_RECOMMENDATION",
            "current": current,
            "recommended": None,
            "auto_applied": False,
            "reason": "no_reviewed_dispositions",
            "requires_human_review": True,
        }
    useful_mean = sum(useful_scores) / len(useful_scores) if useful_scores else None
    noise_mean = sum(noise_scores) / len(noise_scores) if noise_scores else None
    recommended: Optional[float] = None
    reason = "insufficient_separation"
    if useful_mean is not None and noise_mean is not None and useful_mean > noise_mean:
        recommended = round((useful_mean + noise_mean) / 2.0, 3)
        reason = "midpoint_useful_vs_noise"
    elif useful_mean is not None and noise_mean is None:
        recommended = round(max(0.2, useful_mean - 0.05), 3)
        reason = "useful_only_lower_slightly"
    return {
        "kind": "THRESHOLD_RECOMMENDATION",
        "current": current,
        "recommended": recommended,
        "auto_applied": False,
        "requires_human_review": True,
        "reason": reason,
        "useful_n": len(useful_scores),
        "noise_n": len(noise_scores),
        "useful_mean": useful_mean,
        "noise_mean": noise_mean,
        "distributions": {
            "useful": useful_scores[:20],
            "noise": noise_scores[:20],
        },
    }
