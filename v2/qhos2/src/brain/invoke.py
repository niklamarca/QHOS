"""Deterministic policy: when an LLM call is worth making.

LLM enabled ≠ sandbox, Level 3, or trading.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Sequence


INVOKE_REASONS = frozenset({
    "UNKNOWN_PATTERN",
    "MULTI_COMPONENT",
    "AMBIGUOUS_CAUSE",
    "CONTRADICTORY_EVIDENCE",
    "HIGH_VALUE_INVESTIGATION",
})

DENY_REASONS = frozenset({
    "known_duplicate",
    "heartbeat",
    "healthy_service",
    "simple_known_warning",
    "already_resolved",
    "api_disabled",
    "circuit_open",
    "budget",
    "known_baseline",
    "dismissed",
})


def should_invoke_llm(
    signals: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    api_enabled: bool = False,
    mode: str = "deterministic_only",
    duplicate: bool = False,
    circuit_open: bool = False,
    already_resolved: bool = False,
    known_baseline: bool = False,
    dismissed: bool = False,
) -> Dict[str, Any]:
    if not api_enabled or str(mode).lower() == "deterministic_only":
        return {"allowed": False, "reason": "api_disabled", "invoke": False}
    if known_baseline:
        return {"allowed": False, "reason": "known_baseline", "invoke": False}
    if dismissed:
        return {"allowed": False, "reason": "dismissed", "invoke": False}
    if circuit_open:
        return {"allowed": False, "reason": "circuit_open", "invoke": False}
    if duplicate:
        return {"allowed": False, "reason": "known_duplicate", "invoke": False}
    if already_resolved:
        return {"allowed": False, "reason": "already_resolved", "invoke": False}

    rows = list(signals) if isinstance(signals, list) else [signals]
    kinds = {str(r.get("kind") or "") for r in rows if isinstance(r, Mapping)}
    blob = " ".join(
        f"{r.get('summary') or ''} {r.get('body') or ''}"
        for r in rows if isinstance(r, Mapping)
    ).lower()
    if kinds <= {"log", "event", "information", "health"} and not any(
        tok in blob for tok in ("error", "fail", "assert", "timeout", "critical")
    ):
        return {"allowed": False, "reason": "heartbeat", "invoke": False}
    if "all green" in blob or blob.strip() in {"ok", "healthy"}:
        return {"allowed": False, "reason": "healthy_service", "invoke": False}

    fails = sum(1 for r in rows if isinstance(r, Mapping) and str(r.get("kind") or "") in {"test_failure", "test_fail"})
    if fails >= 2:
        return {"allowed": True, "reason": "MULTI_COMPONENT", "invoke": True}
    if "contradict" in blob or "mismatch" in blob:
        return {"allowed": True, "reason": "CONTRADICTORY_EVIDENCE", "invoke": True}
    if any(tok in blob for tok in ("unknown", "unfamiliar", "ambiguous", "intermittent")):
        return {"allowed": True, "reason": "AMBIGUOUS_CAUSE", "invoke": True}
    if fails == 1 or "traceback" in blob:
        return {"allowed": True, "reason": "UNKNOWN_PATTERN", "invoke": True}
    if str((rows[0] if rows else {}).get("severity") or "") in {"high", "critical"}:
        return {"allowed": True, "reason": "HIGH_VALUE_INVESTIGATION", "invoke": True}
    return {"allowed": False, "reason": "simple_known_warning", "invoke": False}
