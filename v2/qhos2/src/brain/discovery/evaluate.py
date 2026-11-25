"""Discovery evaluator. Streams never leak IDs into production reasoning."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping


def evaluate_discovery(
    intel: Mapping[str, Any],
    *,
    expect_notice: bool,
    expect_max_candidates: int = 50,
    expect_causal: bool = False,
) -> Dict[str, Any]:
    incidents = list(intel.get("incidents") or [])
    improvements = list(intel.get("improvements") or [])
    noticed = bool(incidents or improvements)
    noise = int((intel.get("metrics") or {}).get("noise_suppressed") or 0)
    dups = int((intel.get("metrics") or {}).get("duplicates") or 0)
    causal = any(
        (c.get("causality") not in {None, "not_assumed", "contextual_not_causal"})
        for c in (intel.get("correlations") or {}).get("links") or []
    ) if isinstance(intel.get("correlations"), dict) else False
    safe = intel.get("change_request") is None and intel.get("sandbox") is None
    return {
        "noticed": noticed,
        "expected_notice": expect_notice,
        "notice_ok": noticed == expect_notice,
        "candidate_n": len(incidents),
        "over_cap": len(incidents) > expect_max_candidates,
        "false_candidates": 0 if expect_notice else len(incidents),
        "improvements": len(improvements),
        "noise_suppressed": noise,
        "duplicates": dups,
        "causal_claimed": causal,
        "causal_ok": (not causal) if not expect_causal else True,
        "safe": safe,
        "stopped": True,
        "llm_calls": int((intel.get("budget") or {}).get("llm_calls_hour") or 0),
    }
