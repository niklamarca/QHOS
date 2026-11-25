"""Temporal model for ``pending`` registry states.

The 30s soak promoted 78 ``storage: pending`` observations into candidates because
each observation was scored independently and ``pending`` was mapped to
``healthy=False``. A component that has not finished starting is a state
observation, not an incident. Promotion now requires temporal or corroborating
evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

PENDING_CLASSES = (
    "EXPECTED_INITIALIZATION",
    "TRANSIENT_PENDING",
    "DEGRADED_PENDING",
    "STALE_PENDING",
    "FAILED_PENDING",
    "UNKNOWN_PENDING",
)

# Classes that are normal system behaviour and must stay out of incidents.
BENIGN_PENDING = frozenset({"EXPECTED_INITIALIZATION", "TRANSIENT_PENDING"})

PENDING_STATUSES = frozenset({
    "pending", "starting", "initializing", "init", "booting", "warmup",
    "warming_up", "loading", "connecting", "not_ready", "provisioning",
})
FAILED_STATUSES = frozenset({"error", "failed", "down", "unhealthy", "crashed", "fatal"})

# Seconds a component may stay pending before it stops being expected.
DEFAULT_EXPECTED_S = 60.0
# Ingest cycles a component may stay pending before it stops being expected.
DEFAULT_EXPECTED_CYCLES = 3
# Multiplier past expected duration that marks a pending state as stale.
STALE_MULTIPLIER = 10.0
# Multiplier past expected duration that marks a pending state as degraded.
DEGRADED_MULTIPLIER = 3.0

# Components known to take longer to become ready.
EXPECTED_DURATION_S: Dict[str, float] = {
    "storage": 120.0,
    "data_lake": 180.0,
    "market_data": 120.0,
    "ingestion": 120.0,
    "orchestrator": 90.0,
}


def is_pending_status(status: str) -> bool:
    return str(status or "").strip().lower().replace("-", "_") in PENDING_STATUSES


def derive_pending_status(text: str) -> str:
    """Pull a pending status out of a health/state summary line.

    Recorded soak observations carry the status in ``payload_summary`` rather than
    a dedicated field, so replay must reach the same conclusion as live ingest.
    """
    raw = str(text or "").strip().lower()
    if "=" in raw:
        raw = raw.rsplit("=", 1)[1]
    raw = raw.strip().strip("'\"" ).replace("-", "_")
    return raw if raw in PENDING_STATUSES else ""


def expected_duration_for(component: str) -> float:
    key = str(component or "").strip().lower()
    for name, seconds in EXPECTED_DURATION_S.items():
        if name in key:
            return seconds
    return DEFAULT_EXPECTED_S


@dataclass
class PendingState:
    component: str
    status: str = "pending"
    first_seen: float = 0.0
    last_seen: float = 0.0
    cycles_pending: int = 0
    duration_s: float = 0.0
    expected_duration_s: float = DEFAULT_EXPECTED_S
    lifecycle: str = "STARTING"
    classification: str = "EXPECTED_INITIALIZATION"
    promote: bool = False
    promotion_reason: str = "expected_startup_state"
    evidence: List[str] = field(default_factory=list)
    error_evidence: bool = False
    dependent_impact: bool = False
    missed_sla: bool = False
    resolved: bool = False
    observations: int = 0
    lifecycle_known: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PendingTracker:
    """Tracks pending lifecycles across ingest cycles. Read-only bookkeeping."""

    def __init__(self, *, session_start: Optional[float] = None) -> None:
        self.states: Dict[str, PendingState] = {}
        self.session_start = session_start
        self.cycle = 0
        self.suppressed = 0
        self.promoted = 0

    # ---- lifecycle -----------------------------------------------------
    def begin_cycle(self, now: float) -> None:
        self.cycle += 1
        if self.session_start is None:
            self.session_start = now

    def observe(
        self,
        *,
        component: str,
        status: str,
        now: float,
        error_evidence: bool = False,
        dependent_impact: bool = False,
        missed_sla: bool = False,
        evidence: Optional[Iterable[str]] = None,
        lifecycle_known: bool = True,
    ) -> PendingState:
        key = str(component or "unknown")[:80]
        state = self.states.get(key)
        if state is None:
            state = PendingState(
                component=key,
                status=str(status or "pending")[:32],
                first_seen=now,
                last_seen=now,
                expected_duration_s=expected_duration_for(key),
            )
            self.states[key] = state
        state.status = str(status or "pending")[:32]
        state.last_seen = now
        state.observations += 1
        state.cycles_pending = max(state.cycles_pending + 1, 1)
        state.duration_s = round(max(0.0, now - state.first_seen), 3)
        state.lifecycle_known = bool(lifecycle_known)
        state.error_evidence = state.error_evidence or bool(error_evidence)
        state.dependent_impact = state.dependent_impact or bool(dependent_impact)
        state.missed_sla = state.missed_sla or bool(missed_sla)
        for item in list(evidence or [])[:4]:
            text = str(item)[:160]
            if text and text not in state.evidence:
                state.evidence.append(text)
        self._classify(state)
        if state.promote:
            self.promoted += 1
        else:
            self.suppressed += 1
        return state

    def resolve(self, component: str, now: float) -> None:
        state = self.states.get(str(component or "")[:80])
        if state is None:
            return
        state.resolved = True
        state.lifecycle = "READY"
        state.last_seen = now
        state.promote = False
        state.classification = "EXPECTED_INITIALIZATION"
        state.promotion_reason = "resolved_before_threshold"

    # ---- classification -------------------------------------------------
    def _classify(self, state: PendingState) -> None:
        expected = max(1.0, float(state.expected_duration_s))
        duration = float(state.duration_s)
        cycles = int(state.cycles_pending)

        if str(state.status).strip().lower() in FAILED_STATUSES or state.error_evidence:
            state.classification = "FAILED_PENDING"
            state.lifecycle = "FAILED"
            state.promote = True
            state.promotion_reason = "error_evidence_during_pending"
            return
        if state.dependent_impact or state.missed_sla:
            state.classification = "DEGRADED_PENDING"
            state.lifecycle = "DEGRADED"
            state.promote = True
            state.promotion_reason = "dependent_service_impact" if state.dependent_impact else "missed_sla"
            return
        if not state.lifecycle_known:
            # No uptime and no error count means the source never observed a
            # lifecycle. Elapsed time is then meaningless: a static default
            # cannot be aged into an incident. Corroborating evidence only.
            state.classification = "UNKNOWN_PENDING"
            state.lifecycle = "UNKNOWN"
            state.promote = False
            state.promotion_reason = "no_lifecycle_telemetry_duration_not_evidence"
            return
        if duration >= expected * STALE_MULTIPLIER:
            state.classification = "STALE_PENDING"
            state.lifecycle = "STALE"
            state.promote = True
            state.promotion_reason = f"pending_{int(duration)}s_exceeds_{int(expected * STALE_MULTIPLIER)}s_stale_threshold"
            return
        if duration >= expected * DEGRADED_MULTIPLIER:
            state.classification = "DEGRADED_PENDING"
            state.lifecycle = "DEGRADED"
            state.promote = True
            state.promotion_reason = (
                f"pending_{int(duration)}s_exceeds_{int(expected * DEGRADED_MULTIPLIER)}s_degraded_threshold"
            )
            return
        if duration > expected or cycles > DEFAULT_EXPECTED_CYCLES * 4:
            state.classification = "TRANSIENT_PENDING"
            state.lifecycle = "STARTING"
            state.promote = False
            state.promotion_reason = "pending_past_expected_but_below_degraded_threshold"
            return
        if not state.status:
            state.classification = "UNKNOWN_PENDING"
            state.lifecycle = "UNKNOWN"
            state.promote = False
            state.promotion_reason = "unknown_pending_status"
            return
        state.classification = "EXPECTED_INITIALIZATION"
        state.lifecycle = "STARTING"
        state.promote = False
        state.promotion_reason = "expected_startup_state"

    # ---- reporting ------------------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self.states.values()]

    def to_dict(self) -> Dict[str, Any]:
        by_class: Dict[str, int] = {k: 0 for k in PENDING_CLASSES}
        for state in self.states.values():
            by_class[state.classification] = by_class.get(state.classification, 0) + 1
        return {
            "schema": "qhos-pending-model-v1",
            "cycles": self.cycle,
            "tracked_components": len(self.states),
            "observations_suppressed": self.suppressed,
            "observations_promotable": self.promoted,
            "by_class": by_class,
            "states": self.snapshot()[:24],
        }


# Components sharing an identical unknown-pending shape before it is treated as
# one systemic telemetry finding rather than N component incidents.
SYSTEMIC_UNKNOWN_MIN_COMPONENTS = 5
SYSTEMIC_UNKNOWN_MIN_CYCLES = 3


def systemic_unknown_pending(tracker: "PendingTracker") -> Optional[Dict[str, Any]]:
    """One systemic finding when many components report pending with no lifecycle.

    Thirteen modules reporting ``pending / uptime=null / error_count=0`` forever is
    one fact about the status source, not thirteen component incidents.
    """
    unknown = [
        s for s in tracker.states.values()
        if s.classification == "UNKNOWN_PENDING" and not s.resolved
    ]
    if len(unknown) < SYSTEMIC_UNKNOWN_MIN_COMPONENTS:
        return None
    cycles = max((s.cycles_pending for s in unknown), default=0)
    if cycles < SYSTEMIC_UNKNOWN_MIN_CYCLES:
        return None
    components = sorted(s.component for s in unknown)
    duration = max((s.duration_s for s in unknown), default=0.0)
    return {
        "components": components,
        "component_count": len(components),
        "cycles_pending": cycles,
        "max_duration_s": duration,
        "summary": (
            f"{len(components)} components report pending with no lifecycle telemetry "
            f"(uptime unset, error_count 0) across {cycles} cycles"
        ),
        "evidence": [
            # List every component: a truncated list next to a full count reads
            # as a contradiction to any reviewer, human or model.
            f"components({len(components)})={', '.join(components)}"[:400],
            f"cycles_pending={cycles}",
            f"max_duration_s={round(duration, 1)}",
            "uptime=null and error_count=0 on every sample",
            "interpretation=status source default, not per-component health",
        ],
        "recommended_verification": "inspect_health: confirm whether the module registry is populated",
    }


def pending_evidence_summary(state: Mapping[str, Any]) -> List[str]:
    """Deterministic evidence lines for a promoted pending state."""
    out = [
        f"component={state.get('component')}",
        f"status={state.get('status')}",
        f"pending_duration_s={state.get('duration_s')}",
        f"expected_duration_s={state.get('expected_duration_s')}",
        f"cycles_pending={state.get('cycles_pending')}",
        f"class={state.get('classification')}",
    ]
    if state.get("error_evidence"):
        out.append("error_evidence=true")
    if state.get("dependent_impact"):
        out.append("dependent_impact=true")
    return out
