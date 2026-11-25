"""Autonomy, approval, budgets, locks and loop guards.

This ladder governs Brain investigations only. QHOS V2 has no trading autonomy
(the V1 governance.control ladder does not exist here) and this module never
grants execution.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import AutonomyLevel

# Frozen Architecture v1: Brain autonomy cannot exceed INVESTIGATE.
ARCHITECTURE_LEVEL_CAP = AutonomyLevel.INVESTIGATE
SCHEMA_PHASE_CAP = ARCHITECTURE_LEVEL_CAP

# JSON-safe form for raw values that JSON would coerce (integral floats).
WIRE_REQUESTED_KEY = "__qhos_requested__"
WIRE_FLOAT_TAG = "float"
WIRE_CONFLICT_TAG = "conflict"
WIRE_TAGS = frozenset({WIRE_FLOAT_TAG, WIRE_CONFLICT_TAG})
WIRE_FLOAT_KEYS = frozenset({WIRE_REQUESTED_KEY, "literal"})
WIRE_CONFLICT_KEYS = frozenset({
    WIRE_REQUESTED_KEY, "requested_level", "level", "level_name",
})


def _float_literal(value: float) -> str:
    if value != value:
        return "nan"
    if value == float("inf"):
        return "inf"
    if value == float("-inf"):
        return "-inf"
    return repr(value)


def wire_requested(value: Any) -> Any:
    """Canonical JSON-safe form of a raw requested autonomy value.

    Integral floats such as ``2.0`` must not become the integer ``2`` after
    ``json.dumps`` / ``JSON.parse``. They travel as a mapping so the violation
    stays visible and is never silently coerced to a permitted level.
    """
    if isinstance(value, float):
        return {WIRE_REQUESTED_KEY: WIRE_FLOAT_TAG, "literal": _float_literal(value)}
    return value


def _is_wire_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and not isinstance(value, (str, bytes, list, tuple))


def _canonical_float_literal(literal: str) -> bool:
    if literal in {"nan", "inf", "-inf"}:
        return True
    try:
        parsed = float(literal)
    except (TypeError, ValueError):
        return False
    return _float_literal(parsed) == literal


def reserved_requested_ok(value: Any) -> bool:
    """True when a reserved ``__qhos_requested__`` marker is canonical."""
    if not _is_wire_mapping(value) or WIRE_REQUESTED_KEY not in value:
        return False
    tag = value.get(WIRE_REQUESTED_KEY)
    keys = set(value.keys())
    if tag == WIRE_FLOAT_TAG:
        if keys != WIRE_FLOAT_KEYS:
            return False
        literal = value.get("literal")
        return isinstance(literal, str) and _canonical_float_literal(literal)
    if tag == WIRE_CONFLICT_TAG:
        if WIRE_REQUESTED_KEY not in keys or not keys <= WIRE_CONFLICT_KEYS:
            return False
        for key in ("requested_level", "level", "level_name"):
            if key in value and not requested_wire_ok(value.get(key)):
                return False
        return True
    return False


def requested_wire_ok(value: Any) -> bool:
    """Public ``configuration_violation.requested`` grammar.

    Bare floats are forbidden: they must use the documented float marker.
    A reserved ``__qhos_requested__`` mapping must be a known tag with the
    exact required fields. Non-reserved mappings and lists stay allowed as
    explicit invalid raw values.
    """
    if isinstance(value, float):
        return False
    if _is_wire_mapping(value):
        if WIRE_REQUESTED_KEY not in value:
            return True
        return reserved_requested_ok(value)
    return True


def resolve_autonomy_level(requested: Any) -> tuple:
    """Map a config value onto the frozen Level-2 ceiling.

    Only non-bool integers 0, 1, and 2 are directly valid. Integers above 2
    enforce Level 2 and record ``ARCHITECTURE_V1_LEVEL2_CEILING``. Negative
    integers enforce 0 and record ``invalid_autonomy_level``. Strings, floats,
    bools, null, mappings, and sequences are invalid and enforce Level 2 with
    a violation. Floats are recorded in the JSON-safe wire form so ``2.0``
    cannot be confused with Level 2 after serialization. Level 3 / Sandbox is
    never produced.
    """
    requested = wire_requested(requested)
    if isinstance(requested, bool) or not isinstance(requested, int):
        return 2, {
            "flag": "brain_autonomy_level",
            "requested": requested,
            "enforced": 2,
            "reason": "invalid_autonomy_level",
        }
    if requested < 0:
        return 0, {
            "flag": "brain_autonomy_level",
            "requested": requested,
            "enforced": 0,
            "reason": "invalid_autonomy_level",
        }
    if requested > int(ARCHITECTURE_LEVEL_CAP):
        return int(ARCHITECTURE_LEVEL_CAP), {
            "flag": "brain_autonomy_level",
            "requested": requested,
            "enforced": int(ARCHITECTURE_LEVEL_CAP),
            "reason": "ARCHITECTURE_V1_LEVEL2_CEILING",
        }
    return requested, None

SENSITIVE_ACTIONS: frozenset[str] = frozenset({
    "production_write",
    "live_trade",
    "order",
    "oms",
    "execution",
    "capital",
    "position",
    "risk_limit",
    "risk_parameter",
    "broker",
    "exchange",
    "credential",
    "secret",
    "token",
    "api_key",
    "firewall",
    "iam",
    "permission",
    "production_db",
    "destructive_migration",
    "data_deletion",
    "irreversible",
    "apply_change",
})

SAFE_READ_ACTIONS: frozenset[str] = frozenset({
    "observe",
    "advise",
    "investigate",
    "health",
    "audit",
    "summarize",
    "plan",
})


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    approval_required: bool = False
    sandbox_only: bool = True
    level: int = 0


class AutonomyPolicy:
    """Read many / write little. Sensitive actions are hard-blocked this phase."""

    def __init__(self, level: int = 2, shadow: bool = True):
        self.shadow = bool(shadow)
        self.requested_level = level
        enforced, violation = resolve_autonomy_level(level)
        self.level = enforced
        self.configuration_violation = violation

    @staticmethod
    def _clamp(level: int) -> int:
        enforced, _violation = resolve_autonomy_level(level)
        return enforced

    def set_level(self, level: int) -> bool:
        """Accept only integers 0–2. Malformed, negative, or above-ceiling refused."""
        if not isinstance(level, int) or isinstance(level, bool) or level < 0 or level > int(ARCHITECTURE_LEVEL_CAP):
            return False
        self.level = level
        self.requested_level = level
        self.configuration_violation = None
        return True

    def authorize(self, action: str, *, apply: bool = False) -> PolicyDecision:
        action = str(action or "").strip().lower()
        if action in SENSITIVE_ACTIONS or apply:
            return PolicyDecision(
                allowed=False,
                reason="sensitive_or_apply_hard_block",
                approval_required=True,
                sandbox_only=True,
                level=self.level,
            )
        if self.level <= int(AutonomyLevel.OBSERVE) and action not in {"observe", "health", "audit"}:
            return PolicyDecision(
                allowed=False,
                reason="level_0_observe_only",
                level=self.level,
            )
        if self.level == int(AutonomyLevel.ADVISE) and action not in SAFE_READ_ACTIONS:
            return PolicyDecision(
                allowed=False,
                reason="level_1_advise_only",
                approval_required=True,
                level=self.level,
            )
        if action in SAFE_READ_ACTIONS:
            return PolicyDecision(
                allowed=True,
                reason="safe_read",
                sandbox_only=True,
                level=self.level,
            )
        if self.level >= int(AutonomyLevel.INVESTIGATE):
            return PolicyDecision(
                allowed=True,
                reason="investigate_read_path",
                sandbox_only=True,
                level=self.level,
            )
        return PolicyDecision(
            allowed=False,
            reason="denied_default",
            approval_required=True,
            level=self.level,
        )

    def status(self) -> Dict[str, object]:
        return {
            "level": self.level,
            "requested_level": self.requested_level,
            "level_name": AutonomyLevel(self.level).name.lower(),
            "shadow": self.shadow,
            "phase_cap": int(SCHEMA_PHASE_CAP),
            "can_apply": False,
            "can_trade": False,
            "trading_autonomy_is_separate": True,
            "configuration_violation": self.configuration_violation,
        }


@dataclass
class Budget:
    max_concurrent_tasks: int = 8
    max_retries: int = 2
    max_tasks_per_goal: int = 32
    max_depth: int = 6
    max_cycles_per_minute: int = 4
    max_children_per_task: int = 4


class CircuitBreaker:
    """Generic breaker. Subclasses declare scope. reset() is explicit."""

    scope = "generic"

    def __init__(self, fail_limit: int = 8, cooldown_s: float = 300.0):
        self.fail_limit = fail_limit
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.open_until = 0.0
        self.last_failure_at = 0.0

    def record_failure(self, now: float) -> None:
        self.failures += 1
        self.last_failure_at = float(now)
        if self.failures >= self.fail_limit:
            self.open_until = now + self.cooldown_s

    def record_success(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def is_open(self, now: float) -> bool:
        return now < self.open_until

    def reset(self) -> None:
        """Clear failures, open state, cooldown, and timestamps."""
        self.failures = 0
        self.open_until = 0.0
        self.last_failure_at = 0.0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "scope": self.scope,
            "failures": self.failures,
            "open_until": self.open_until,
            "fail_limit": self.fail_limit,
            "cooldown_s": self.cooldown_s,
            "last_failure_at": self.last_failure_at,
        }


class SessionCircuitBreaker(CircuitBreaker):
    """Per evaluation/session. reset_session() must clear this.

    Scenario N timeout must not suppress scientifically independent scenario N+1.
    """

    scope = "session"


class ProviderHealthCircuitBreaker(CircuitBreaker):
    """Persistent live-provider health. NOT cleared by reset_session()."""

    scope = "provider_health"


class ResourceLocks:
    """One writer per resource. Read-only observe tasks do not lock."""

    def __init__(self) -> None:
        self._held: Dict[str, str] = {}

    def acquire(self, resource: str, owner: str) -> bool:
        resource = str(resource or "").strip()
        if not resource:
            return True
        current = self._held.get(resource)
        if current is None or current == owner:
            self._held[resource] = owner
            return True
        return False

    def release(self, resource: str, owner: str) -> None:
        resource = str(resource or "").strip()
        if resource and self._held.get(resource) == owner:
            self._held.pop(resource, None)

    def holder(self, resource: str) -> Optional[str]:
        return self._held.get(str(resource or "").strip())


class LoopGuard:
    def __init__(self, budget: Budget) -> None:
        self.budget = budget
        self._fingerprints: Set[str] = set()
        self._children: Dict[str, int] = {}
        self._per_goal: Dict[str, int] = {}

    def seen(self, fingerprint: str) -> bool:
        return fingerprint in self._fingerprints

    def remember(self, fingerprint: str) -> None:
        if fingerprint:
            self._fingerprints.add(fingerprint)

    def can_spawn(self, *, parent_task: str, goal_id: str, depth: int) -> str:
        if depth > self.budget.max_depth:
            return "max_depth"
        if parent_task and self._children.get(parent_task, 0) >= self.budget.max_children_per_task:
            return "max_children"
        if goal_id and self._per_goal.get(goal_id, 0) >= self.budget.max_tasks_per_goal:
            return "max_tasks_per_goal"
        return ""

    def spawned(self, *, parent_task: str, goal_id: str) -> None:
        if parent_task:
            self._children[parent_task] = self._children.get(parent_task, 0) + 1
        if goal_id:
            self._per_goal[goal_id] = self._per_goal.get(goal_id, 0) + 1


AGENT_CAPABILITIES: Dict[str, Set[str]] = {
    "system": {"observe", "health", "audit", "plan"},
    "architecture": {"python", "architecture", "api"},
    "observability": {"logs", "metrics", "health"},
    "qa": {"tests", "regression"},
}


def route_agent(capabilities: Set[str]) -> str:
    """Deterministic capability router. No LLM, no history yet."""
    return route_agent_explained(capabilities)["agent"]


def route_agent_explained(capabilities: Set[str]) -> Dict[str, object]:
    """Return chosen agent plus why, capabilities, and alternatives."""
    needed = {str(x).lower() for x in capabilities if x}
    scored = []
    for agent, caps in AGENT_CAPABILITIES.items():
        n = len(needed & caps) if needed else (1 if agent == "system" else 0)
        scored.append((n, agent, sorted(caps)))
    scored.sort(key=lambda row: (-row[0], row[1]))
    if not needed:
        chosen = "system"
        why = "default_observe"
        overlap: List[str] = []
    else:
        chosen = scored[0][1] if scored[0][0] else "system"
        overlap = sorted(needed & AGENT_CAPABILITIES.get(chosen, set()))
        why = "capability_overlap:" + ",".join(overlap) if overlap else "fallback_system"
    alternatives = [a for n, a, _ in scored if a != chosen][:3]
    return {
        "agent": chosen,
        "why": why,
        "capabilities": overlap or sorted(needed),
        "alternatives": alternatives,
        "needed": sorted(needed),
    }
