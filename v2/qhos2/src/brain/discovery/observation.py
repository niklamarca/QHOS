"""Normalize live signals. Raw payloads never go to a provider."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from brain.models import new_id, utc_ts
from brain.redact import classify_text, redact_text, CLASS_DENIED


@dataclass
class Observation:
    id: str
    timestamp: float
    source: str
    component: str
    kind: str
    severity_hint: str = "low"
    payload_summary: str = ""
    fingerprint: str = ""
    correlation_id: str = ""
    trust_level: str = "UNTRUSTED"
    sensitivity: str = "low"
    revision: str = ""
    count: int = 1
    pre_existing: bool = False
    denied: bool = False
    # Root-cause grouping (rootfp-v1).
    state: str = ""
    root_id: str = ""
    root_tier: str = ""
    root_title: str = ""
    component_family: str = ""
    instance: str = ""
    root_concrete: bool = False
    lifecycle_known: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def fingerprint_of(kind: str, component: str, summary: str) -> str:
    raw = f"{kind}|{component}|{summary}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def normalize_row(
    *,
    source: str,
    component: str,
    kind: str,
    summary: str,
    severity: str = "low",
    correlation_id: str = "",
    revision: str = "",
    timestamp: Optional[float] = None,
    state: str = "",
    lifecycle_known: bool = True,
) -> Observation:
    from .baseline import is_preexisting
    from .fingerprint import root_signature, root_title
    text = redact_text(str(summary or ""))[:240]
    denied = classify_text(text) == CLASS_DENIED
    if denied:
        text = "[REDACTED]"
        severity = "low"
    fp = fingerprint_of(kind, component, text)
    sig = root_signature(kind=kind, component=component, summary=text, state=state)
    return Observation(
        id=new_id("obs"),
        timestamp=float(timestamp or utc_ts()),
        source=str(source)[:40],
        component=str(component)[:80],
        kind=str(kind)[:40],
        severity_hint=str(severity)[:16],
        payload_summary=text,
        fingerprint=fp,
        correlation_id=str(correlation_id or fp),
        trust_level="UNTRUSTED",
        sensitivity="high" if denied else "low",
        revision=str(revision)[:40],
        pre_existing=is_preexisting(text),
        denied=denied,
        state=str(state or "")[:32],
        root_id=sig.root_id,
        root_tier=sig.tier,
        root_title=root_title(sig),
        component_family=sig.component_family,
        instance=sig.instance_hint,
        root_concrete=sig.concrete,
        lifecycle_known=bool(lifecycle_known),
    )


def observations_from_snapshot(raw: Mapping[str, Any]) -> List[Observation]:
    """Turn a perception snapshot into structured observations. No raw dump."""
    from .attestation import attestation_rows
    from .pending import derive_pending_status, is_pending_status
    out: List[Observation] = []
    # Runtime attestation is a signal source, not just a reporting surface.
    # Without this the engine cannot see that the process it is observing is
    # running code other than what is on disk (previously KI-03, NO_SIGNAL).
    att = raw.get("attestation") if isinstance(raw.get("attestation"), Mapping) else None
    att_ctx = raw.get("attestation_context") if isinstance(raw.get("attestation_context"), Mapping) else {}
    if att is not None or att_ctx.get("required"):
        for row in attestation_rows(
            att,
            context=str(att_ctx.get("deployment_context") or "dev"),
            expected_revision=str(att_ctx.get("expected_revision") or ""),
            expected_config_hash=str(att_ctx.get("expected_config_hash") or ""),
            expected_route_schema=str(att_ctx.get("expected_route_schema") or ""),
        ):
            out.append(normalize_row(
                source=str(row["source"]),
                component=str(row["component"]),
                kind=str(row["kind"]),
                summary=str(row["summary"]),
                severity=str(row["severity"]),
            ))
    tail = raw.get("error_tail") or []
    if isinstance(tail, list):
        for line in tail:
            if not str(line).strip():
                continue
            out.append(normalize_row(source="error_log", component="logs", kind="error", summary=str(line), severity="medium"))
    registry = raw.get("registry") if isinstance(raw.get("registry"), dict) else {}
    for name, info in list(registry.items())[:24]:
        if not isinstance(info, Mapping):
            continue
        healthy = info.get("healthy")
        status = str(info.get("status") or "")
        if is_pending_status(status):
            # Pending is a lifecycle state, not a health failure. PendingTracker
            # decides whether it ever deserves promotion. A module with no uptime
            # and no errors has reported no lifecycle at all.
            lifecycle_known = info.get("uptime") is not None or int(info.get("error_count") or 0) > 0
            out.append(normalize_row(
                source="registry", component=str(name), kind="state",
                summary=f"status={status}", severity="low", state=status,
                lifecycle_known=bool(lifecycle_known),
            ))
        elif healthy is False or status in {"error", "down", "unhealthy"}:
            out.append(normalize_row(source="registry", component=str(name), kind="health", summary=status or "unhealthy", severity="high"))
        elif status in {"ok", "healthy"} or healthy is True:
            out.append(normalize_row(source="registry", component=str(name), kind="heartbeat", summary="healthy", severity="low"))
    res = raw.get("resources") if isinstance(raw.get("resources"), dict) else {}
    latency = raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {}
    if latency.get("latency_ms") is not None or res.get("latency_ms") is not None:
        ms = latency.get("latency_ms") if latency.get("latency_ms") is not None else res.get("latency_ms")
        sev = "high" if float(ms or 0) >= 400 else "low"
        out.append(normalize_row(source="metrics", component=str(latency.get("component") or "runtime"), kind="latency", summary=f"latency_ms={ms}", severity=sev))
    git = raw.get("git") if isinstance(raw.get("git"), dict) else {}
    for commit in list(git.get("recent_commits") or [])[:3]:
        out.append(normalize_row(source="git", component="repository", kind="commit", summary=str(commit), severity="low"))
    hpc = raw.get("hpc") if isinstance(raw.get("hpc"), dict) else {}
    if hpc.get("kill_level"):
        out.append(normalize_row(source="hpc", component="orchestrator", kind="hpc", summary=f"kill_level={hpc.get('kill_level')}", severity="high"))
    retries = raw.get("retries") if isinstance(raw.get("retries"), dict) else {}
    if int(retries.get("count") or 0) >= 5:
        out.append(normalize_row(source="queue", component=str(retries.get("component") or "queue"), kind="retry", summary=f"retries={retries.get('count')}", severity="medium"))
    extra = raw.get("observations") if isinstance(raw.get("observations"), list) else []
    for row in extra:
        if not isinstance(row, Mapping):
            continue
        kind = str(row.get("kind") or "event")
        summary = str(row.get("summary") or row.get("body") or row.get("payload_summary") or "")
        severity = str(row.get("severity") or row.get("severity_hint") or "low")
        state = str(row.get("state") or "")
        if not state and kind in {"health", "state"}:
            state = derive_pending_status(summary)
        if state:
            # Pending is a lifecycle state on every ingest path, live or replay.
            kind = "state"
            severity = "low"
        out.append(normalize_row(
            source=str(row.get("source") or "stream"),
            component=str(row.get("component") or "unknown"),
            kind=kind,
            summary=summary,
            severity=severity,
            state=state,
            timestamp=row.get("timestamp"),
            lifecycle_known=bool(row.get("lifecycle_known", True)),
        ))
    return out
