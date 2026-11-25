"""Stable read-only Brain view model for the Laços control plane.

This is the data contract a frontend consumes. It exists so that a UI never has
to understand PotentialIncident, Observation, PendingTracker or the shape of the
intelligence blob — and so that presentation limits live here rather than
leaking into the decision path (see MD-07).

Three rules the projection enforces:

1. **Bounded here, nowhere else.** Every truncation is declared in
   ``PROJECTION_LIMITS`` and reported alongside the data as a ``truncated``
   flag with the true total, so a panel can never imply "that is all of them".
2. **Never invent a number.** A value that was not measured is emitted as an
   explicit ``UNKNOWN`` / ``NOT_MEASURED`` / ``NOT_AVAILABLE`` / ``NOT_VERIFIED``
   marker, not as 0, "" or a plausible default.
3. **Read-only.** The projection carries no operation that could enable the
   sandbox, raise the autonomy level, apply, deploy, trade, merge or push.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from brain.architecture import ARCHITECTURE_VERSION, AUTHORITY_LEVEL, RELEASE_CHANNEL
from brain.policies import (
    WIRE_REQUESTED_KEY,
    requested_wire_ok,
    resolve_autonomy_level,
    wire_requested,
)

SCHEMA = "qhos-brain-view-v1"

# Absence markers. A frontend can switch on these instead of guessing whether a
# zero means "measured zero" or "never measured".
UNKNOWN = "UNKNOWN"
NOT_AVAILABLE = "NOT_AVAILABLE"
NOT_MEASURED = "NOT_MEASURED"
NOT_VERIFIED = "NOT_VERIFIED"
ABSENCE_MARKERS = frozenset({UNKNOWN, NOT_AVAILABLE, NOT_MEASURED, NOT_VERIFIED})

# The only place display truncation is allowed to be defined.
PROJECTION_LIMITS: Dict[str, int] = {
    "incidents": 20,
    "improvements": 12,
    "investigations": 8,
    "root_clusters": 12,
    "telemetry_quality_issues": 8,
    "telemetry_recommendations": 8,
    "observations": 40,
    "memory": 12,
    "activity": 20,
    "provider_calls": 12,
    "evaluation_rows": 12,
}

# Categories a frontend can rely on existing, in a fixed order.
SECTIONS = (
    "system",
    "trader",
    "brain",
    "live_intelligence",
    "incidents",
    "improvements",
    "investigations",
    "cognition",
    "memory",
    "provider",
    "evaluation",
    "governance",
    "runtime_attestation",
)
REQUIRED_VIEW_ENVELOPES = (
    "incidents", "improvements", "investigations", "memory", "root_clusters",
)
REQUIRED_NESTED_ENVELOPES = (
    ("telemetry_quality", "issues"),
    ("telemetry_quality", "recommendations"),
    ("governance", "activity"),
)
AUTHORITY_NAMES = {
    0: frozenset({"OBSERVE", "observe"}),
    1: frozenset({"ADVISE", "advise"}),
    2: frozenset({"INVESTIGATE", "investigate"}),
}
FORBIDDEN_OPERATIONS = frozenset({
    "enable_sandbox", "enable_level3", "apply_change", "deploy",
    "restart_production", "submit_trade", "merge", "push",
    "change_autonomy_level",
})
ALLOWED_AVAILABLE_OPERATIONS = frozenset({"record_human_disposition"})
VIOLATION_FLAG = "brain_autonomy_level"
VIOLATION_REASONS = frozenset({
    "ARCHITECTURE_V1_LEVEL2_CEILING",
    "invalid_autonomy_level",
})
STATUS_ENUM = frozenset({
    "CANDIDATE", "SUPPORTED", "CONFIRMED", "OPEN", "CLOSED", "INVESTIGATING",
}) | ABSENCE_MARKERS
ROLE_ENUM = frozenset({"PRIMARY_ROOT_CANDIDATE", "SECONDARY_EFFECT"}) | ABSENCE_MARKERS
RELATION_ENUM = frozenset({
    "SAME_ROOT", "LIKELY_DERIVED_EFFECT", "TEMPORALLY_CORRELATED",
    "INDEPENDENT", "UNKNOWN",
}) | ABSENCE_MARKERS
HEARTBEAT_ENUM = frozenset({"running", "stale", "degraded", "offline"}) | ABSENCE_MARKERS
KIND_ENUM = frozenset({"observed", "estimated", "derived", "inferred"}) | ABSENCE_MARKERS


def _present(value: Any, *, marker: str = UNKNOWN) -> Any:
    """Return the value, or an explicit absence marker. Never a silent default.

    ``0`` and ``False`` are real measurements and pass through; ``None`` and the
    empty string are absences.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return marker
    return value


def _num(value: Any, *, marker: str = NOT_MEASURED) -> Any:
    if isinstance(value, bool) or value is None:
        return marker if value is None else value
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return marker


def bounded(rows: Optional[Sequence[Any]], key: str) -> Dict[str, Any]:
    """Apply the declared display limit and report what was hidden."""
    items = list(rows or [])
    limit = PROJECTION_LIMITS.get(key, len(items))
    shown = items[:limit]
    return {
        "items": shown,
        "total": len(items),
        "shown": len(shown),
        "limit": limit,
        "truncated": len(items) > len(shown),
    }


def field(
    value: Any,
    *,
    source: str,
    observed_at: Any = None,
    kind: str = "observed",
    authority: str = "read_only",
    confidence: Any = None,
    marker: str = UNKNOWN,
) -> Dict[str, Any]:
    """A value plus its provenance (§50).

    ``kind`` is ``observed`` when the value was read from a real source, and
    ``estimated`` / ``derived`` when it was computed. A UI that shows an
    estimate as though it were a measurement is the failure this guards.
    """
    return {
        "value": _present(value, marker=marker),
        "source": source,
        "observed_at": _present(observed_at, marker=NOT_AVAILABLE),
        "kind": kind,
        "authority": authority,
        "confidence": _present(confidence, marker=NOT_MEASURED),
        "measured": value is not None,
    }


def _staleness(last_at: Any, now: Any) -> Any:
    try:
        return round(float(now) - float(last_at), 3)
    except (TypeError, ValueError):
        return NOT_AVAILABLE


def _affected_instances(value: Any) -> Any:
    """Project Discovery breadth without inventing identities or dropping a count.

    ``PotentialIncident.affected_instances`` is an integer count
    (``len(RootCluster.instances) or 1``). A caller may already supply a
    sequence of real instance identifiers. Both are legal ``qhos-brain-view-v1``
    values for this field. ``list(count)`` is not — it TypeErrors, and
    coercing a count to ``[]`` would erase a known measurement.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return NOT_AVAILABLE
    if isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, int):
        return value if value >= 0 else UNKNOWN
    if isinstance(value, (list, tuple)):
        if all(isinstance(item, str) and item.strip() for item in value):
            return list(value)
        return UNKNOWN
    return UNKNOWN


def _incident_row(cand: Mapping[str, Any]) -> Dict[str, Any]:
    """One canonical incident row. The UI reads this, never PotentialIncident."""
    return {
        "root_id": _present(cand.get("root_id")),
        "candidate_id": _present(cand.get("candidate_id")),
        "title": _present(cand.get("root_title") or cand.get("summary")),
        "component": _present(cand.get("component")),
        "tier": _present(cand.get("root_tier")),
        "status": _present(cand.get("status")),
        "signal_class": _present(cand.get("signal_class")),
        "severity": _present(cand.get("severity")),
        "score": _num(cand.get("score")),
        "occurrence_count": _num(cand.get("occurrence_count") or cand.get("count")),
        "affected_components": list(cand.get("affected_components") or []),
        "affected_instances": _affected_instances(cand.get("affected_instances")),
        "sources": list(cand.get("sources") or []),
        "first_seen": _present(cand.get("first_seen"), marker=NOT_AVAILABLE),
        "last_seen": _present(cand.get("last_seen"), marker=NOT_AVAILABLE),
        "promotion_reason": _present(cand.get("promotion_reason")),
        "pending_class": _present(cand.get("pending_class"), marker=NOT_AVAILABLE),
        # Consolidation vocabulary. A secondary effect is not a duplicate and
        # not noise; a UI that renders them identically loses the distinction
        # the consolidation stage exists to produce.
        "role": str(cand.get("candidate_role") or "PRIMARY_ROOT_CANDIDATE"),
        "derived_from": _present(cand.get("derived_from"), marker=NOT_AVAILABLE),
        "relationship": _present(cand.get("relationship"), marker=NOT_AVAILABLE),
        "evidence": [str(e) for e in (cand.get("evidence") or [])],
        # Confirmed by a human, not by the engine.
        "human_disposition": _present(cand.get("disposition"), marker=NOT_VERIFIED),
        "is_training_truth": not bool(cand.get("not_training_truth", True)),
    }


def _improvement_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": _present(row.get("candidate_id")),
        "component": _present(row.get("component")),
        "observed_problem": _present(row.get("observed_problem")),
        "expected_benefit": _present(row.get("expected_benefit")),
        "benefit_class": _present(row.get("benefit_class")),
        "status": _present(row.get("status")),
        "uncertainty": _present(row.get("uncertainty")),
        "proposed_validation": _present(row.get("proposed_validation")),
        # Estimates must be labelled as estimates.
        "impact": field(row.get("impact_estimate"), source="heuristic", kind="estimated"),
        "effort": field(row.get("effort_estimate"), source="heuristic", kind="estimated"),
        "risk": field(row.get("risk_estimate"), source="heuristic", kind="estimated"),
        "measured": bool(row.get("measured")),
        "occurrence_count": _num(row.get("occurrence_count")),
        "auto_apply": False,
    }


def _telemetry_issue_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "root_id": _present(row.get("root_id")),
        "subtype": _present(row.get("subtype")),
        "title": _present(row.get("title")),
        "affected_components": list(row.get("affected_components") or []),
        "component_count": _num(row.get("component_count")),
        "score": _num(row.get("score")),
        # The distinction that matters: a telemetry defect is not a component
        # failure, and fixing the component would be the wrong action.
        "is_component_failure": bool(row.get("is_component_failure")),
        "interpretation": _present(row.get("interpretation")),
        "recommended_verification": _present(row.get("recommended_verification")),
        "requires_human_review": True,
    }


def _attestation_view(panel: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Frozen runtime attestation contract (§34)."""
    row = dict(panel or {})
    states = ("CURRENT", "STALE_RUNTIME", "NOT_ATTESTABLE")
    running = row.get("running_revision") or row.get("running")
    worktree = row.get("worktree_revision") or row.get("worktree")
    if not running:
        state = "NOT_ATTESTABLE"
    elif row.get("stale_runtime"):
        state = "STALE_RUNTIME"
    else:
        state = "CURRENT"
    return {
        "state": state,
        "allowed_states": list(states),
        "running_revision": _present(running, marker=NOT_AVAILABLE),
        "worktree_revision": _present(worktree, marker=NOT_AVAILABLE),
        "config_hash": _present(row.get("config_hash"), marker=NOT_AVAILABLE),
        "route_schema_version": _present(row.get("route_schema_version"), marker=NOT_AVAILABLE),
        "process_start_time": _present(row.get("process_start_time") or row.get("started_at"), marker=NOT_AVAILABLE),
        "process_uptime_s": _num(row.get("process_uptime_s")),
        "drift_reason": _present(row.get("drift_reason"), marker=NOT_AVAILABLE),
        # Context decides whether staleness matters: a changed worktree during
        # development is informational, a stale production runtime is not.
        "deployment_context": str(row.get("deployment_context") or "development"),
        "informational_only": str(row.get("deployment_context") or "development") != "production",
    }


def build_view(snapshot: Mapping[str, Any], *, now: Optional[float] = None) -> Dict[str, Any]:
    """Project a Brain snapshot into the stable read-only view model.

    Accepts the payload from ``brain.api.public_snapshot()``. Missing sections
    degrade to explicit absence markers rather than raising, because a UI
    contract that breaks when one subsystem is idle is not a contract.
    """
    import time

    snap = dict(snapshot or {})
    now = float(now if now is not None else time.time())
    intel = dict(snap.get("live_intelligence") or {})
    health = dict(snap.get("health") or {})
    autonomy = dict(snap.get("autonomy") or {})
    metrics = dict(intel.get("metrics") or {})
    last_cycle = snap.get("last_observation_at")

    authority_level, authority_name, configuration_violation = _project_autonomy(autonomy)
    incidents = [_incident_row(c) for c in (intel.get("incidents") or []) if isinstance(c, Mapping)]
    improvements = [
        _improvement_row(r) for r in (intel.get("improvements") or []) if isinstance(r, Mapping)
    ]
    tq_issues = [
        _telemetry_issue_row(r)
        for r in (intel.get("telemetry_quality_issues") or [])
        if isinstance(r, Mapping)
    ]

    return {
        "schema": SCHEMA,
        "architecture_version": ARCHITECTURE_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "generated_at": now,
        "read_only": True,
        "sections": list(SECTIONS),
        "projection_limits": dict(PROJECTION_LIMITS),
        "absence_markers": sorted(ABSENCE_MARKERS),

        "system": {
            "brain_available": bool(snap.get("available")),
            "brain_running": bool(snap.get("running")),
            "cycles": _num(snap.get("cycles")),
            "uptime_s": _num(snap.get("uptime_s")),
            "last_cycle_at": _present(last_cycle, marker=NOT_AVAILABLE),
            "staleness_s": _staleness(last_cycle, now),
            "resources": dict(snap.get("resources") or {}) or NOT_AVAILABLE,
            "schema_versions": {
                "snapshot": _present(snap.get("schema")),
                "view": SCHEMA,
            },
        },

        # Trading autonomy is a separate ladder from Brain autonomy. Collapsing
        # them in a UI would imply the Brain can trade at level 2.
        "trader": {
            "autonomy_mode": _present((snap.get("trading_autonomy") or {}).get("mode")),
            "brain_can_trade": False,
            "brain_is_not_a_trading_path": True,
            "separate_from_brain_autonomy": True,
        },

        "brain": {
            "authority_level": authority_level,
            "authority_name": authority_name,
            "shadow": True,
            "can_apply": False,
            "can_trade": False,
            "sandbox_enabled": False,
            "level3_enabled": False,
            "health_score": _num(health.get("global_score")),
            "health_tone": _present(health.get("tone")),
            "critical_issues": list(health.get("critical_issues") or []),
            "warnings": list(health.get("warnings") or []),
            "heartbeat": _present((snap.get("heartbeat") or {}).get("brain")),
            "configuration_violation": configuration_violation,
        },

        "live_intelligence": {
            "observer": _present(intel.get("observer")),
            "soak_active": bool(intel.get("soak")),
            "signals_total": _num(metrics.get("observations_total") or metrics.get("signals")),
            "candidates_total": _num(metrics.get("candidate_n")),
            "suppressed_total": _num(metrics.get("suppressed")),
            "budget": dict(intel.get("budget") or {}) or NOT_AVAILABLE,
            "llm_called": bool(intel.get("llm_called", False)),
            "pending_model": dict(intel.get("pending_model") or {}) or NOT_AVAILABLE,
            "telemetry_ledger": dict(intel.get("telemetry_ledger") or {}) or NOT_AVAILABLE,
        },

        "incidents": {
            **bounded(incidents, "incidents"),
            # Root incidents and linked secondary effects are counted apart, so
            # a panel cannot inflate the unique-incident number with effects.
            "root_incident_count": sum(
                1 for r in incidents if r["role"] == "PRIMARY_ROOT_CANDIDATE"
            ),
            "secondary_effect_count": sum(
                1 for r in incidents if r["role"] == "SECONDARY_EFFECT"
            ),
        },
        "improvements": bounded(improvements, "improvements"),
        "investigations": bounded(list(intel.get("investigations") or []), "investigations"),
        "root_clusters": bounded(list(intel.get("root_clusters") or []), "root_clusters"),
        "telemetry_quality": {
            "issues": bounded(tq_issues, "telemetry_quality_issues"),
            "recommendations": bounded(
                list(intel.get("telemetry_recommendations") or []), "telemetry_recommendations"
            ),
        },

        "cognition": {
            "mode": _present((snap.get("provider_health") or {}).get("mode")),
            "strategy": _present((snap.get("cognitive_strategy") or {}).get("strategy")),
            "default_strategy": "deterministic",
            "llm_calls_this_cycle": _num(intel.get("llm_calls"), marker=NOT_MEASURED),
            "continuous_live_llm": False,
        },

        "memory": bounded(list(snap.get("memory_tail") or []), "memory"),

        "provider": {
            "api_enabled": bool((snap.get("provider_health") or {}).get("api_enabled", False)),
            "mode": _present((snap.get("provider_health") or {}).get("mode")),
            "live_llm_shadow_enabled": bool(
                (snap.get("provider_health") or {}).get("live_llm_shadow_enabled", False)
            ),
            "paid_requires_explicit_gate": True,
            "performance": dict(snap.get("provider_performance") or {}) or NOT_MEASURED,
        },

        "evaluation": {
            "proving_ground": dict(snap.get("evaluation") or {}) or NOT_MEASURED,
            "held_out": dict(snap.get("heldout") or {}) or NOT_MEASURED,
            "real_model_experiment": dict(snap.get("real_model_experiment") or {}) or NOT_MEASURED,
        },

        "governance": {
            "change_requests_pending": len(list(snap.get("proposals") or [])),
            "automatic_change_requests": 0,
            "automatic_sandbox_runs": 0,
            "requires_human_review": True,
            "activity": bounded(
                list((snap.get("lacos") or {}).get("activity") or snap.get("audit_tail") or []),
                "activity",
            ),
        },

        "runtime_attestation": _attestation_view(intel.get("runtime_revision")),

        # §52: the contract itself declares that it contains no operations.
        "operations": {
            "available": ["record_human_disposition"],
            "unavailable_at_level_2": [
                "enable_sandbox", "enable_level3", "apply_change", "deploy",
                "restart_production", "submit_trade", "merge", "push",
                "change_autonomy_level",
            ],
        },
    }


def _is_int_level(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _sandbox_name(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "sandbox"


def _conflict_requested(autonomy: Mapping[str, Any]) -> dict:
    payload: Dict[str, Any] = {WIRE_REQUESTED_KEY: "conflict"}
    if "requested_level" in autonomy:
        payload["requested_level"] = wire_requested(autonomy.get("requested_level"))
    if "level" in autonomy:
        payload["level"] = wire_requested(autonomy.get("level"))
    if "level_name" in autonomy:
        payload["level_name"] = wire_requested(autonomy.get("level_name"))
    return payload


def _name_coherent(name: Any, enforced: Any) -> bool:
    if not isinstance(name, str):
        return False
    allowed = AUTHORITY_NAMES.get(enforced)
    return bool(allowed) and name in allowed


def _raw_requested_level(autonomy: Mapping[str, Any]) -> tuple:
    """Pick the raw authority signal. Never hide a Level 3 behind a quieter field."""
    has_req = "requested_level" in autonomy
    has_lvl = "level" in autonomy
    has_name = "level_name" in autonomy
    req = autonomy.get("requested_level") if has_req else None
    lvl = autonomy.get("level") if has_lvl else None
    name = autonomy.get("level_name") if has_name else None

    if has_name and _sandbox_name(name):
        ceiling = []
        if has_req and _is_int_level(req) and req > AUTHORITY_LEVEL:
            ceiling.append(req)
        if has_lvl and _is_int_level(lvl) and lvl > AUTHORITY_LEVEL:
            ceiling.append(lvl)
        return True, max(ceiling) if ceiling else 3

    ceiling: List[int] = []
    if has_req and _is_int_level(req) and req > AUTHORITY_LEVEL:
        ceiling.append(req)
    if has_lvl and _is_int_level(lvl) and lvl > AUTHORITY_LEVEL:
        ceiling.append(lvl)
    if ceiling:
        return True, max(ceiling)

    if not has_req and not has_lvl:
        if has_name:
            return True, _conflict_requested(autonomy)
        return False, None

    if has_req:
        enforced, _derived = resolve_autonomy_level(req)
        if has_lvl and (not _is_int_level(lvl) or lvl != enforced):
            return True, _conflict_requested(autonomy)
        if has_name and not _name_coherent(name, enforced):
            return True, _conflict_requested(autonomy)
        return True, req

    if has_name and not _name_coherent(name, resolve_autonomy_level(lvl)[0]):
        return True, _conflict_requested(autonomy)
    return True, lvl


def _project_autonomy(autonomy: Mapping[str, Any]) -> tuple:
    """Derive authority exclusively from the raw requested value."""
    if not autonomy:
        return UNKNOWN, UNKNOWN, UNKNOWN
    present, raw = _raw_requested_level(autonomy)
    if not present:
        return UNKNOWN, UNKNOWN, UNKNOWN
    level, derived = resolve_autonomy_level(raw)
    violation: Any = UNKNOWN if derived is None else dict(derived)
    return level, _authority_name(autonomy, level), violation


def _capped_authority(autonomy: Mapping[str, Any]) -> Any:
    if not autonomy:
        return UNKNOWN
    raw = autonomy.get("level")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return UNKNOWN
    if raw < 0:
        return UNKNOWN
    return min(raw, AUTHORITY_LEVEL)


def _authority_name(autonomy: Mapping[str, Any], level: Any) -> Any:
    if level in ABSENCE_MARKERS:
        return UNKNOWN
    names = {0: "observe", 1: "advise", 2: "investigate"}
    raw = autonomy.get("level_name") if autonomy else None
    allowed = AUTHORITY_NAMES.get(level)
    if isinstance(raw, str) and allowed and raw in allowed:
        return raw
    return names.get(level, UNKNOWN)


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and not isinstance(value, (str, list, tuple))


def _nonneg_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def envelope_is_valid(section: Any) -> bool:
    """Canonical envelope shape. Never infer shown/total from items.length."""
    if not _is_mapping(section):
        return False
    items = section.get("items")
    if not isinstance(items, list):
        return False
    if "shown" not in section or "total" not in section or "truncated" not in section:
        return False
    shown, total, truncated = section.get("shown"), section.get("total"), section.get("truncated")
    if not isinstance(truncated, bool):
        return False
    if not _nonneg_int(shown) or not _nonneg_int(total):
        return False
    if shown != len(items):
        return False
    if shown > total:
        return False
    if truncated and shown == total:
        return False
    if (not truncated) and shown < total:
        return False
    return True


def _valid_affected(value: Any) -> bool:
    if isinstance(value, str) and value in ABSENCE_MARKERS:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int) and not isinstance(value, bool):
        return value >= 0
    if isinstance(value, list):
        return all(isinstance(item, str) and bool(item.strip()) for item in value)
    return False


def _valid_authority_level(value: Any) -> bool:
    if isinstance(value, str) and value in ABSENCE_MARKERS:
        return True
    return _nonneg_int(value) and value <= AUTHORITY_LEVEL


def _authority_coherent(brain: Mapping[str, Any]) -> bool:
    level = brain.get("authority_level")
    name = brain.get("authority_name")
    if not _valid_authority_level(level):
        return False
    if isinstance(level, str) and level in ABSENCE_MARKERS:
        return isinstance(name, str) and name in ABSENCE_MARKERS
    allowed = AUTHORITY_NAMES.get(level)
    return isinstance(name, str) and allowed is not None and name in allowed


def _valid_enum(value: Any, allowed: frozenset) -> bool:
    try:
        return value in allowed
    except TypeError:
        return False


def _same_string_list(value: Any, expected: Sequence[str]) -> bool:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return False
    return list(value) == list(expected)


def _unique_string_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) or not item.strip() for item in value):
        return False
    return len(value) == len(set(value))


def _finite_ts(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _projection_limits_ok(value: Any) -> bool:
    if not _is_mapping(value):
        return False
    for key, expected in PROJECTION_LIMITS.items():
        raw = value.get(key)
        if not _nonneg_int(raw):
            return False
        if raw != expected:
            return False
    return True


def _absence_markers_ok(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    if any(not isinstance(item, str) for item in value):
        return False
    return set(value) == set(ABSENCE_MARKERS) and len(value) == len(ABSENCE_MARKERS)


def _operations_ok(ops: Any) -> bool:
    if not _is_mapping(ops):
        return False
    available = ops.get("available")
    unavailable = ops.get("unavailable_at_level_2")
    if not _unique_string_list(available):
        return False
    avail_set = set(available)
    if avail_set - ALLOWED_AVAILABLE_OPERATIONS or avail_set & FORBIDDEN_OPERATIONS:
        return False
    if not _unique_string_list(unavailable):
        return False
    if not FORBIDDEN_OPERATIONS.issubset(set(unavailable)):
        return False
    return True


def _violation_ok(brain: Mapping[str, Any]) -> bool:
    violation = brain.get("configuration_violation")
    if isinstance(violation, str) and violation in ABSENCE_MARKERS:
        return True
    if not _is_mapping(violation):
        return False
    if "requested" not in violation:
        return False
    stored = violation.get("requested")
    if not requested_wire_ok(stored):
        return False
    enforced, derived = resolve_autonomy_level(stored)
    if derived is None:
        return False
    if (
        violation.get("flag") != derived.get("flag")
        or violation.get("requested") != derived.get("requested")
        or violation.get("enforced") != derived.get("enforced")
        or violation.get("reason") != derived.get("reason")
    ):
        return False
    if not _nonneg_int(enforced) or enforced > AUTHORITY_LEVEL:
        return False
    if brain.get("authority_level") != derived["enforced"]:
        return False
    if derived["reason"] == "ARCHITECTURE_V1_LEVEL2_CEILING" and brain.get("authority_level") != 2:
        return False
    return True


def validate_view(doc: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Canonical qhos-brain-view-v1 structural gate. Used by transport and adapters."""
    fail = {"ok": False, "reason": "UNKNOWN", "view": None}
    if not _is_mapping(doc):
        return {"ok": False, "reason": "NOT_AVAILABLE", "view": None}
    schema = doc.get("schema")
    if not schema:
        return {"ok": False, "reason": "NOT_AVAILABLE", "view": None}
    fail = {"ok": False, "reason": "UNKNOWN", "view": None, "schema": schema}
    if schema != SCHEMA:
        return fail
    if doc.get("read_only") is not True:
        return fail
    av = doc.get("architecture_version")
    if not isinstance(av, int) or isinstance(av, bool) or av != ARCHITECTURE_VERSION:
        return fail
    if doc.get("release_channel") != RELEASE_CHANNEL:
        return fail
    if not _finite_ts(doc.get("generated_at")):
        return fail
    if not _same_string_list(doc.get("sections"), SECTIONS):
        return fail
    if not _projection_limits_ok(doc.get("projection_limits")):
        return fail
    if not _absence_markers_ok(doc.get("absence_markers")):
        return fail
    for name in SECTIONS:
        if name not in doc or not _is_mapping(doc.get(name)):
            return fail
    for name in REQUIRED_VIEW_ENVELOPES:
        if not envelope_is_valid(doc.get(name)):
            return fail
    tq = doc.get("telemetry_quality")
    if not _is_mapping(tq):
        return fail
    for parent, nested in REQUIRED_NESTED_ENVELOPES:
        section = doc.get(parent)
        if not _is_mapping(section) or not envelope_is_valid(section.get(nested)):
            return fail
    trader = doc.get("trader") or {}
    if (
        not _is_mapping(trader)
        or trader.get("brain_can_trade") is not False
        or trader.get("brain_is_not_a_trading_path") is not True
        or trader.get("separate_from_brain_autonomy") is not True
    ):
        return fail
    brain = doc.get("brain") or {}
    if not _is_mapping(brain):
        return fail
    if (
        brain.get("can_trade") is not False
        or brain.get("can_apply") is not False
        or brain.get("shadow") is not True
        or brain.get("sandbox_enabled") is not False
        or brain.get("level3_enabled") is not False
    ):
        return fail
    if not _authority_coherent(brain):
        return fail
    if not _violation_ok(brain):
        return fail
    if not _operations_ok(doc.get("operations")):
        return fail
    if not _valid_enum(brain.get("heartbeat"), HEARTBEAT_ENUM):
        return fail
    for row in list((doc.get("incidents") or {}).get("items") or []):
        if not _is_mapping(row):
            return fail
        if "affected_instances" in row and not _valid_affected(row.get("affected_instances")):
            return fail
        if not _valid_enum(row.get("status"), STATUS_ENUM):
            return fail
        if not _valid_enum(row.get("role"), ROLE_ENUM):
            return fail
        if not _valid_enum(row.get("relationship"), RELATION_ENUM):
            return fail
        if "kind" in row and not _valid_enum(row.get("kind"), KIND_ENUM):
            return fail
    for name in ("improvements", "investigations"):
        for row in list((doc.get(name) or {}).get("items") or []):
            if not _is_mapping(row) or not _valid_enum(row.get("status"), STATUS_ENUM):
                return fail
    return {"ok": True, "reason": "", "view": dict(doc)}


def view_invariants(view: Mapping[str, Any]) -> Dict[str, Any]:
    """Assertable safety properties of a projected view."""
    ops = dict(view.get("operations") or {})
    forbidden = set(FORBIDDEN_OPERATIONS)
    available = set(ops.get("available") or [])
    brain = dict(view.get("brain") or {})
    return {
        "read_only": bool(view.get("read_only")),
        "no_forbidden_operations": not (available & forbidden),
        "sandbox_off": brain.get("sandbox_enabled") is False,
        "level3_off": brain.get("level3_enabled") is False,
        "cannot_apply": brain.get("can_apply") is False,
        "cannot_trade": brain.get("can_trade") is False,
        "continuous_llm_off": (view.get("cognition") or {}).get("continuous_live_llm") is False,
        "ok": all([
            bool(view.get("read_only")),
            not (available & forbidden),
            brain.get("sandbox_enabled") is False,
            brain.get("level3_enabled") is False,
            brain.get("can_apply") is False,
            brain.get("can_trade") is False,
        ]),
    }
