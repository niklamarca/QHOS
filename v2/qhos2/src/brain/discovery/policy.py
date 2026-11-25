"""Static evidence-based strategy routing. Autonomous adaptation remains disabled."""
from __future__ import annotations

from typing import Any, Dict


# Measured from paid.json (REAL_HYBRID) and fable_smoke/fable_focus (FABLE NEUTRAL).
# FableDerived is never default and is not selected for live routing on this evidence.
STATIC_STRATEGY_POLICY = {
    "SIMPLE_KNOWN": "deterministic",
    "MEMORY_AMBIGUITY": "real_hybrid",
    "MULTI_COMMIT": "real_hybrid",
    "UNTRUSTED_CONTEXT": "real_hybrid",
    "DECEPTIVE_SUCCESS": "real_hybrid",
    "COMPLEX_LONG_CONTEXT": "deterministic",
    "UNKNOWN": "deterministic",
}

WHERE_BEST = {
    "deterministic": ["root_cause_saturated", "stop_saturated", "simple_known", "no_incident"],
    "real_hybrid": ["memory", "untrusted_comment", "multi_commit", "deceptive_success"],
    "fable_derived": [],  # NO_MEASURED_VALUE vs current real hybrid
    "equivalent": ["classification_on_current_benchmark"],
    "fable_worse": [],
}

ROUTING_RECOMMENDATION = {
    "autonomous_routing": False,
    "fable_default": False,
    "note": "Re-evaluate Fable on long_context after breaker/reset is fixed. Do not promote on current NEUTRAL.",
}


def classify_family(blob: str, *, components: int = 1, memory_conflict: bool = False) -> str:
    text = str(blob or "").lower()
    if memory_conflict or "memory" in text and "timeout" in text:
        return "MEMORY_AMBIGUITY"
    if text.count("commit") >= 2:
        return "MULTI_COMMIT"
    if any(tok in text for tok in ("ignore policy", "level 5", "root cause is", "push directly")):
        return "UNTRUSTED_CONTEXT"
    if any(tok in text for tok in ("delete the test", "xfail", "pytest.skip", "assert true")):
        return "DECEPTIVE_SUCCESS"
    if components >= 4 or "long investigation" in text:
        return "COMPLEX_LONG_CONTEXT"
    if any(tok in text for tok in ("timeout_ms=0", "return a - b", "wrong_operator")):
        return "SIMPLE_KNOWN"
    return "UNKNOWN"


def recommend_strategy(family: str) -> Dict[str, Any]:
    key = str(family or "UNKNOWN").upper()
    chosen = STATIC_STRATEGY_POLICY.get(key, STATIC_STRATEGY_POLICY["UNKNOWN"])
    return {
        "family": key,
        "strategy": chosen,
        "authority_unchanged": True,
        "extra_tools": False,
        "extra_permissions": False,
        "autonomous_routing": False,
        "fable_default": False,
        "recommendation": ROUTING_RECOMMENDATION,
    }
