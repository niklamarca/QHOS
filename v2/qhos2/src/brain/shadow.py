"""Read-only live shadow. Signals are not incidents. No writes. No sandbox."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .invoke import should_invoke_llm
from .models import utc_ts
from .redact import redact_text


FEEDBACK_ACTIONS = frozenset({
    "CONFIRM", "DISMISS", "DUPLICATE", "USEFUL", "NOT_USEFUL", "PROMOTE_FOR_REVIEW",
})
DISPOSITIONS = frozenset({
    "CONFIRMED", "DISMISSED", "DUPLICATE", "STALE", "INSUFFICIENT_EVIDENCE",
    "CANDIDATE",
})
KNOWLEDGE_STATES = frozenset({
    "OBSERVED", "HYPOTHESIZED", "SUPPORTED", "CONFIRMED", "REJECTED", "STALE",
})


def fingerprint_signal(kind: str, component: str, summary: str) -> str:
    raw = f"{kind}|{component}|{summary}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _error_lines(observation: Mapping[str, Any]) -> List[str]:
    tail = observation.get("error_tail") or []
    if isinstance(tail, list):
        return [redact_text(str(x))[:240] for x in tail if str(x).strip()]
    return []


def correlate(
    observation: Mapping[str, Any],
    relations: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Link signals without claiming causality."""
    links: List[Dict[str, Any]] = []
    git = observation.get("git") if isinstance(observation.get("git"), dict) else {}
    commits = list(git.get("recent_commits") or [])[:3]
    errors = _error_lines(observation)[:3]
    if commits and errors:
        links.append({
            "kind": "temporal_cooccurrence",
            "commits": commits,
            "errors": errors,
            "causality": "not_assumed",
        })
    hpc = observation.get("hpc") if isinstance(observation.get("hpc"), dict) else {}
    if hpc and errors:
        links.append({
            "kind": "hpc_cooccurrence",
            "cycle": hpc.get("cycle"),
            "kill_level": hpc.get("kill_level"),
            "causality": "not_assumed",
        })
    for rel in list(relations or [])[:8]:
        if not isinstance(rel, Mapping):
            continue
        links.append({
            "kind": "graph_context",
            "source": str(rel.get("source") or rel.get("from") or ""),
            "verb": str(rel.get("verb") or rel.get("rel") or ""),
            "target": str(rel.get("target") or rel.get("to") or ""),
            "causality": "contextual_not_causal",
        })
    return links


def _candidate(
    *,
    kind: str,
    component: str,
    summary: str,
    severity: str = "low",
    count: int = 1,
) -> Dict[str, Any]:
    now = utc_ts()
    return {
        "kind": "potential_incident",
        "status": "CANDIDATE",
        "confirmed": False,
        "promoted": False,
        "fingerprint": fingerprint_signal(kind, component, summary),
        "summary": redact_text(summary)[:180],
        "component": component,
        "count": count,
        "severity": severity,
        "system_confidence": 0.0,
        "model_confidence": 0.0,
        "disposition": "CANDIDATE",
        "knowledge_state": "OBSERVED",
        "observed_at": now,
        "verified_at": None,
        "valid_for_component_version": None,
        "source_revision": None,
        "feedback": [],
        "change_request": None,
        "sandbox": None,
    }


def observe_live(
    observation: Mapping[str, Any],
    *,
    relations: Optional[Sequence[Mapping[str, Any]]] = None,
    seen: Optional[Dict[str, int]] = None,
    api_enabled: bool = False,
    mode: str = "deterministic_only",
) -> Dict[str, Any]:
    """Turn a perception snapshot into potential incidents. Never acts."""
    seen = seen if seen is not None else {}
    candidates: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for line in _error_lines(observation):
        fp = fingerprint_signal("error", "logs", line)
        counts[fp] = counts.get(fp, 0) + 1
        if fp in seen:
            seen[fp] += 1
            continue
        seen[fp] = 1
        candidates.append(_candidate(kind="error", component="logs", summary=line, severity="medium", count=counts[fp]))

    registry = observation.get("registry") if isinstance(observation.get("registry"), dict) else {}
    for name, info in list(registry.items())[:8]:
        if not isinstance(info, Mapping):
            continue
        if info.get("healthy") is False or str(info.get("status") or "") in {"error", "down", "unhealthy"}:
            cand = _candidate(kind="health", component=str(name), summary=str(info.get("status") or "unhealthy"), severity="high")
            if cand["fingerprint"] not in seen:
                seen[cand["fingerprint"]] = 1
                candidates.append(cand)

    git = observation.get("git") if isinstance(observation.get("git"), dict) else {}
    hpc = observation.get("hpc") if isinstance(observation.get("hpc"), dict) else {}
    health = observation.get("truth") if isinstance(observation.get("truth"), dict) else {}
    committee = observation.get("committee") if isinstance(observation.get("committee"), dict) else {}

    for cand in candidates:
        cand["count"] = counts.get(str(cand["fingerprint"]), cand.get("count") or 1)

    recommendations = []
    if candidates:
        recommendations.append({
            "kind": "recommendation",
            "text": "investigate repeated error fingerprints",
            "authority": False,
            "knowledge_state": "HYPOTHESIZED",
        })

    invoke = should_invoke_llm(
        [{"kind": "log", "summary": c["summary"]} for c in candidates] or [{"kind": "health", "summary": "ok"}],
        api_enabled=api_enabled,
        mode=mode,
        duplicate=all(seen.get(c["fingerprint"], 0) > 1 for c in candidates) if candidates else True,
    )

    return {
        "schema": "qhos-brain-shadow-v1",
        "read_only": True,
        "change_request": None,
        "sandbox": None,
        "sandbox_run": None,
        "auto_merge": False,
        "observations": [
            {"kind": "health", "summary": str(health.get("health") or health.get("tone") or "snapshot")},
            {"kind": "git", "summary": f"dirty={git.get('dirty_n', 0)}"},
            {"kind": "hpc", "summary": f"cycle={hpc.get('cycle', 0)}"},
            {"kind": "committee", "summary": f"round={committee.get('round', 0)}"},
        ],
        "candidates": candidates[:12],
        "confirmed_incidents": [],
        "hypotheses": [],
        "recommendations": recommendations,
        "correlation": correlate(observation, relations),
        "deduplicated": True,
        "error_lines_seen": sum(counts.values()),
        "unique_fingerprints": len(counts),
        "invocation": invoke,
        "llm_called": False,
        "level3_enabled": False,
    }


def record_feedback(
    candidate: Mapping[str, Any],
    action: str,
    *,
    note: str = "",
) -> Dict[str, Any]:
    """Human feedback is evaluation evidence. Never a write/sandbox/trade."""
    act = str(action or "").upper()
    if act not in FEEDBACK_ACTIONS:
        return {**dict(candidate), "feedback_error": "unknown_action", "change_request": None, "sandbox": None}
    row = dict(candidate)
    hist = list(row.get("feedback") or [])
    hist.append({"action": act, "note": str(note)[:160], "ts": utc_ts(), "triggers_action": False})
    row["feedback"] = hist
    mapping = {
        "CONFIRM": "CONFIRMED",
        "DISMISS": "DISMISSED",
        "DUPLICATE": "DUPLICATE",
        "USEFUL": row.get("disposition") or "CANDIDATE",
        "NOT_USEFUL": "INSUFFICIENT_EVIDENCE",
        "PROMOTE_FOR_REVIEW": "CANDIDATE",
    }
    row["disposition"] = mapping.get(act, "CANDIDATE")
    if act == "CONFIRM":
        row["knowledge_state"] = "SUPPORTED"
        row["verified_at"] = utc_ts()
    elif act in {"DISMISS", "NOT_USEFUL"}:
        row["knowledge_state"] = "REJECTED"
    elif act == "PROMOTE_FOR_REVIEW":
        row["knowledge_state"] = "HYPOTHESIZED"
        row["promoted_for_review"] = True
    row["change_request"] = None
    row["sandbox"] = None
    row["promoted"] = False
    row["sandbox_opened"] = False
    return row


def strip_actions(payload: Mapping[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    out["change_request"] = None
    out["sandbox"] = None
    out["sandbox_run"] = None
    out["patches"] = {}
    return out
