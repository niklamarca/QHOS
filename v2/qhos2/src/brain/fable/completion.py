"""Completion honesty, claim-evidence binding, failure-first, evidence-before-write."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


COMPLETION_STATES = (
    "PROPOSED",
    "EXECUTED",
    "OBSERVED",
    "VALIDATED",
    "PARTIALLY_VALIDATED",
    "FAILED",
    "NOT_VERIFIED",
    "PRODUCTION_VALIDATED",
)

STRONG_CLAIMS = ("FIXED", "VERIFIED", "PASSED", "RESOLVED", "VALIDATED", "DONE")


def observed_evidence(result: Any) -> bool:
    findings = list(getattr(result, "findings", None) or [])
    verifications = list(getattr(result, "verifications", None) or [])
    observed = any(
        isinstance(v, Mapping) and (v.get("result") or {}).get("ok")
        for v in verifications
    )
    return bool(findings or observed)


def tests_were_run(result: Any, observations: Optional[Sequence[Mapping[str, Any]]] = None) -> bool:
    for row in observations or []:
        if isinstance(row, Mapping) and str(row.get("kind") or "") == "test_failure":
            return True
        blob = f"{row.get('summary') or ''} {row.get('body') or ''}".lower() if isinstance(row, Mapping) else ""
        if "passed" in blob and "pytest" in blob:
            return True
    for v in getattr(result, "verifications", None) or []:
        action = str((v or {}).get("action_type") or "")
        if action in {"reproduce_failure", "run_tests"} and (v.get("result") or {}).get("ok"):
            return True
    return False


def completion_state(result: Any, observations: Optional[Sequence[Mapping[str, Any]]] = None) -> str:
    stop = str(getattr(result, "stop", "") or "")
    if getattr(result, "safety_flags", None):
        if any("unauthorized" in str(x) or "denied" in str(x) for x in result.safety_flags):
            return "FAILED"
    if stop == "UNSAFE_TO_PROCEED":
        return "NOT_VERIFIED"
    if not observed_evidence(result) and not tests_were_run(result, observations):
        return "NOT_VERIFIED"
    if stop in {"ROOT_CAUSE_CONFIRMED", "LIKELY_CAUSE"} and observed_evidence(result):
        if tests_were_run(result, observations):
            return "OBSERVED"
        return "PARTIALLY_VALIDATED"
    if stop == "NO_ACTION_REQUIRED":
        return "OBSERVED"
    return "NOT_VERIFIED"


def bind_claims(result: Any, observations: Optional[Sequence[Mapping[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Downgrade verbal success that is stronger than observed results."""
    rows = []
    blob = ""
    try:
        blob = str(result.to_dict() if hasattr(result, "to_dict") else result).lower()
    except Exception:
        blob = str(result).lower()
    for word in STRONG_CLAIMS:
        if word.lower() in blob and not observed_evidence(result):
            rows.append({
                "claim": word,
                "bound": False,
                "state": "NOT_VERIFIED",
                "reason": "claim_without_observed_evidence",
            })
    for hyp in getattr(result, "hypotheses", None) or []:
        if not isinstance(hyp, Mapping):
            continue
        stmt = str(hyp.get("statement") or "").lower()
        if any(w.lower() in stmt for w in STRONG_CLAIMS) and not hyp.get("evidence_for"):
            hyp["unsupported_claim"] = True
            rows.append({
                "claim": str(hyp.get("statement") or "")[:160],
                "bound": False,
                "state": "NOT_VERIFIED",
                "reason": "hypothesis_claim_unbound",
            })
    return rows[:12]


def failure_first_traces(result: Any) -> List[Dict[str, Any]]:
    traces = [dict(t) if isinstance(t, Mapping) else {"what": str(t)} for t in (getattr(result, "traces", None) or [])]
    failures = [
        t for t in traces
        if str(t.get("result") or "").lower() in {"failed", "denied", "error"}
        or "fail" in str(t.get("what") or "").lower()
    ]
    if not failures:
        return traces
    rest = [t for t in traces if t not in failures]
    return failures + rest


def evidence_before_state_change(
    result: Any,
    *,
    memory_only: bool = False,
) -> bool:
    """False when a change_request exists without action-specific evidence."""
    cr = getattr(result, "change_request", None)
    if not cr:
        return True
    if memory_only and not observed_evidence(result):
        if hasattr(result, "change_request"):
            result.change_request = None
            result.patches = {}
            traces = list(getattr(result, "traces", None) or [])
            traces.append({
                "what": "state_change_blocked",
                "why": "similarity_is_not_evidence",
                "evidence": ["memory_only"],
                "action": "strip_change_request",
                "result": "denied",
            })
            result.traces = traces
        return False
    if not observed_evidence(result):
        if hasattr(result, "change_request"):
            result.change_request = None
            traces = list(getattr(result, "traces", None) or [])
            traces.append({
                "what": "state_change_blocked",
                "why": "no_action_specific_evidence",
                "evidence": [],
                "action": "strip_change_request",
                "result": "denied",
            })
            result.traces = traces
        return False
    return True


def apply_honesty(result: Any, observations: Optional[Sequence[Mapping[str, Any]]] = None) -> Dict[str, Any]:
    claims = bind_claims(result, observations)
    state = completion_state(result, observations)
    if hasattr(result, "traces"):
        result.traces = failure_first_traces(result)
    if hasattr(result, "sides"):
        result.sides.setdefault("fable", {})
        result.sides["fable"]["completion_state"] = state
        result.sides["fable"]["unbound_claims"] = claims
    return {
        "completion_state": state,
        "unbound_claims": claims,
        "completion_claim_accuracy": 0.0 if claims else 1.0,
    }


def production_validated_forbidden() -> str:
    """Runtime never reaches PRODUCTION_VALIDATED in this phase."""
    return "NOT_VERIFIED"
