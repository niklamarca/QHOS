"""DEV-ONLY controlled fault injection.

Three real soak sessions showed the calibration was repeatable against one
unchanging fault set. They could not show it generalizes, because the machine
never produced a different fault. This harness produces them deliberately.

Isolation, by construction rather than by policy
------------------------------------------------
A fault here is a **snapshot overlay**: a pure function from a perception
snapshot to a new perception snapshot. The harness never writes a file, opens a
socket, spawns a process, touches an environment variable or mutates its input.
There is no code path from this module to the machine, so "do not fill disks,
kill production, corrupt databases" is not a rule the harness follows — it is
a capability the harness does not have.

Two further guards are enforced anyway, because a future edit could add one:

* the harness refuses to construct against a target that is not explicitly
  marked dev, and refuses production ports and forbidden subsystems outright;
* every overlay is scanned for manifest leakage before it is returned, so
  Discovery can never read the fault id, class or expected answer out of an
  observation it is asked to diagnose.

Evaluator-only metadata
-----------------------
``FaultManifest`` carries the expected signals, expected root family and
expected Discovery behaviour. None of it enters the snapshot. Discovery sees
telemetry that looks exactly like telemetry.
"""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# Fault classes under test. Names are evaluator vocabulary and never reach a
# snapshot.
FAULT_CLASSES = (
    "MISSING_DEPENDENCY",
    "STALE_RUNTIME_REVISION",
    "CONNECTION_REFUSED",
    "REQUEST_TIMEOUT",
    "DATA_SCHEMA_MISMATCH",
    "DATA_QUALITY_ANOMALY",
    "TELEMETRY_STALE_OR_MISLEADING",
    "HEALTH_DEGRADATION",
    "UNKNOWN_PENDING_WITH_REAL_INDEPENDENT_FAILURE",
    "HIGH_FREQUENCY_BENIGN_WARNING",
    "PROVIDER_DEGRADED",
    "QUEUE_BACKLOG",
    "TEST_REGRESSION",
    "MULTI_COMPONENT_SHARED_ROOT",
    "MULTIPLE_SIMILAR_BUT_DISTINCT_ROOTS",
)

# Expected Discovery behaviour for a manifest.
EXPECT_CANDIDATE = "CANDIDATE"
EXPECT_SUPPRESSED = "SUPPRESSED"
EXPECT_MULTIPLE = "MULTIPLE_CANDIDATES"
EXPECTED_OUTCOMES = (EXPECT_CANDIDATE, EXPECT_SUPPRESSED, EXPECT_MULTIPLE)

COHORT_DEVELOPMENT = "development"
COHORT_HOLDOUT = "holdout"

# Ports that must never be a fault target.
PRODUCTION_PORTS = frozenset({8000, 443, 80})

# Subsystems that must never be a fault target, even in dev.
FORBIDDEN_TARGET_SUBSYSTEMS = frozenset({
    "oms", "order_management", "risk", "risk_engine", "broker", "exchange",
    "credentials", "secrets", "production_db", "market_data_store", "data_lake",
    "kill_switch", "execution_engine", "trading",
})


class FaultHarnessError(RuntimeError):
    """Raised when the harness is asked to do something it must refuse."""


@dataclass(frozen=True)
class FaultTarget:
    """Where a fault may be injected. Dev must be asserted, never inferred."""

    label: str
    port: int = 0
    is_dev: bool = False
    subsystem: str = "perception_snapshot"

    def validate(self) -> None:
        if not self.is_dev:
            raise FaultHarnessError(
                f"fault target {self.label!r} is not marked dev; refusing to inject"
            )
        if int(self.port or 0) in PRODUCTION_PORTS:
            raise FaultHarnessError(
                f"port {self.port} is a production port; refusing to inject"
            )
        sub = re.sub(r"[^a-z0-9_]+", "_", str(self.subsystem or "").lower())
        if sub in FORBIDDEN_TARGET_SUBSYSTEMS:
            raise FaultHarnessError(
                f"subsystem {self.subsystem!r} is never a valid fault target"
            )
        if "prod" in str(self.label or "").lower():
            raise FaultHarnessError(
                f"target label {self.label!r} names production; refusing to inject"
            )


@dataclass
class FaultManifest:
    """One fault, with its evaluator-only expectations.

    ``overlay`` receives a snapshot and a cycle index and returns a new
    snapshot. It must be pure: the harness deep-copies before calling it and
    verifies the input is unchanged afterwards.
    """

    fault_id: str
    fault_class: str
    target_component: str
    injection_method: str
    expected_signals: Tuple[str, ...]
    expected_outcome: str
    expected_root_family: str
    cleanup: str
    cohort: str
    overlay: Callable[[Dict[str, Any], int], Dict[str, Any]]
    expected_candidates: int = 1
    expected_root_match: Tuple[str, ...] = ()
    # Second-order surfaces of the *same* injected fault: a retry counter that
    # climbs because the queue is backed up, a latency metric that rises because
    # the parser is timing out. A candidate carrying one of these is not a false
    # positive — the fault really did cause it — but it is not an independent
    # root either. Kept separate so under-consolidation is not reported as noise.
    symptom_tokens: Tuple[str, ...] = ()
    rationale: str = ""
    started_ts: Optional[float] = None
    ended_ts: Optional[float] = None

    def evaluator_record(self) -> Dict[str, Any]:
        """Manifest without the callable. Evaluator-side only."""
        out = {k: v for k, v in asdict(self).items() if k != "overlay"}
        out["expected_signals"] = list(self.expected_signals)
        out["expected_root_match"] = list(self.expected_root_match)
        out["symptom_tokens"] = list(self.symptom_tokens)
        return out


# ---------------------------------------------------------------------------
# Snapshot construction
# ---------------------------------------------------------------------------

HEALTHY_MODULES = (
    "storage", "execution", "ingestion", "orchestrator", "dashboard",
    "market_data", "parser", "reporting",
)


def clean_snapshot(*, uptime_s: float = 900.0) -> Dict[str, Any]:
    """A healthy dev snapshot. The control arm for every fault run.

    Every module reports a real lifecycle (non-null uptime), so nothing here is
    UNKNOWN_PENDING and the background candidate set is empty. Any candidate a
    fault run produces that the control arm did not is attributable.
    """
    return {
        "registry": {
            name: {
                "healthy": True,
                "status": "ok",
                "uptime": uptime_s,
                "error_count": 0,
                "last_error": None,
            }
            for name in HEALTHY_MODULES
        },
        "error_tail": [],
        "metrics": {"component": "runtime", "latency_ms": 40},
        "git": {"recent_commits": []},
        "hpc": {"kill_level": None},
        "retries": {"count": 0, "component": "queue"},
        "observations": [],
        "attestation": {
            "running_revision": "aaaa111122223333",
            "worktree_revision": "aaaa111122223333",
            "stale_runtime": False,
            "drift_reason": "running_matches_worktree",
            "running": {
                "discovery_policy": {"config_hash": "04f2ec81fc26e408"},
                "route_schema_version": "qhos-brain-v1",
            },
        },
        "attestation_context": {
            "deployment_context": "dev",
            "expected_config_hash": "04f2ec81fc26e408",
            "expected_route_schema": "qhos-brain-v1",
        },
    }


def _errors(snapshot: Dict[str, Any], lines: Sequence[str]) -> Dict[str, Any]:
    snapshot.setdefault("error_tail", [])
    snapshot["error_tail"] = list(snapshot["error_tail"]) + list(lines)
    return snapshot


def _module(snapshot: Dict[str, Any], name: str, **fields: Any) -> Dict[str, Any]:
    registry = dict(snapshot.get("registry") or {})
    entry = dict(registry.get(name) or {})
    entry.update(fields)
    registry[name] = entry
    snapshot["registry"] = registry
    return snapshot


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


@dataclass
class InjectionRecord:
    fault_id: str
    fault_class: str
    cycle: int
    ts: float
    target: str
    reverted: bool = False


class FaultInjectionHarness:
    """Applies fault overlays to snapshots. Dev-only, bounded, reversible.

    Reversibility is structural: the harness owns no state on the machine, so
    ``cleanup`` is an assertion that nothing leaked rather than an undo.
    """

    MAX_FAULTS_DEFAULT = 64
    MAX_CYCLES_DEFAULT = 200

    def __init__(
        self,
        *,
        enabled: bool = False,
        target: Optional[FaultTarget] = None,
        max_faults: int = MAX_FAULTS_DEFAULT,
        max_cycles: int = MAX_CYCLES_DEFAULT,
    ) -> None:
        if not enabled:
            raise FaultHarnessError(
                "FaultInjectionHarness must be constructed with enabled=True; "
                "it is never on by default"
            )
        self.target = target or FaultTarget(label="dev-inprocess", is_dev=True)
        self.target.validate()
        self.max_faults = int(max_faults)
        self.max_cycles = int(max_cycles)
        self.audit: List[InjectionRecord] = []
        self.active: Optional[FaultManifest] = None
        self._closed = False

    # -- lifecycle -------------------------------------------------------
    def __enter__(self) -> "FaultInjectionHarness":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.cleanup()

    def cleanup(self) -> Dict[str, Any]:
        for record in self.audit:
            record.reverted = True
        self.active = None
        self._closed = True
        return {
            "cleaned": True,
            "injections": len(self.audit),
            "residual_state": None,
            "filesystem_writes": 0,
            "network_calls": 0,
            "processes_spawned": 0,
        }

    # -- injection -------------------------------------------------------
    def apply(
        self,
        manifest: FaultManifest,
        base: Mapping[str, Any],
        cycle: int,
        *,
        now: float = 0.0,
    ) -> Dict[str, Any]:
        """Return ``base`` with ``manifest`` applied. ``base`` is not mutated."""
        if self._closed:
            raise FaultHarnessError("harness is closed")
        if len(self.audit) >= self.max_faults:
            raise FaultHarnessError(f"injection budget exhausted ({self.max_faults})")
        if int(cycle) > self.max_cycles:
            raise FaultHarnessError(f"cycle budget exhausted ({self.max_cycles})")

        before = copy.deepcopy(dict(base))
        working = copy.deepcopy(dict(base))
        out = manifest.overlay(working, int(cycle))
        if before != dict(base):
            raise FaultHarnessError(
                f"overlay for {manifest.fault_id} mutated the base snapshot"
            )
        if not isinstance(out, dict):
            raise FaultHarnessError(f"overlay for {manifest.fault_id} returned a non-snapshot")
        assert_no_manifest_leakage(out, manifest)
        self.active = manifest
        self.audit.append(InjectionRecord(
            fault_id=manifest.fault_id,
            fault_class=manifest.fault_class,
            cycle=int(cycle),
            ts=float(now),
            target=self.target.label,
        ))
        return out

    def audit_records(self) -> List[Dict[str, Any]]:
        return [asdict(r) for r in self.audit]

    def isolation_report(self) -> Dict[str, Any]:
        return {
            "kind": "FAULT_HARNESS_ISOLATION",
            "target": asdict(self.target),
            "production_ports_denied": sorted(PRODUCTION_PORTS),
            "forbidden_subsystems": sorted(FORBIDDEN_TARGET_SUBSYSTEMS),
            "mechanism": "pure snapshot overlay",
            "filesystem_writes": 0,
            "network_calls": 0,
            "processes_spawned": 0,
            "os_mutations": 0,
            "injections": len(self.audit),
            "bounded": {"max_faults": self.max_faults, "max_cycles": self.max_cycles},
            "reversible": True,
        }


# ---------------------------------------------------------------------------
# Leakage guard
# ---------------------------------------------------------------------------

# Literal evaluator vocabulary. Deliberately narrow: a blanket ban on tokens
# like "expected_" would also reject legitimate telemetry such as a deployment
# reporting its expected_config_hash, and a guard that cries wolf gets disabled.
_LEAK_TOKENS = ("fault_id", "fault_class", "cohort", "expected_outcome",
                "expected_root", "evaluator_record")

# Fixture identifiers such as F01a. Discovery branching on one of these would
# make every matrix result meaningless.
_FIXTURE_ID_RE = re.compile(r"\bf\d{2}[a-z]\b")


def _walk_text(node: Any, out: List[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            out.append(str(key))
            _walk_text(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _walk_text(item, out)
    elif node is not None:
        out.append(str(node))


def assert_no_manifest_leakage(snapshot: Mapping[str, Any], manifest: FaultManifest) -> None:
    """Discovery must never be able to read the answer out of the question.

    A detector that can branch on ``F01`` proves nothing about generalization,
    so this runs on every overlay rather than only in tests.
    """
    parts: List[str] = []
    _walk_text(snapshot, parts)
    blob = " ".join(parts).lower()

    # The manifest's own identity and its own stated answer.
    for needle in (
        manifest.fault_id,
        manifest.fault_class,
        manifest.cohort,
        manifest.expected_outcome,
        manifest.rationale,
        manifest.injection_method,
    ):
        if needle and str(needle).lower() in blob:
            raise FaultHarnessError(
                f"manifest metadata {needle!r} leaked into the snapshot for {manifest.fault_id}"
            )
    for token in _LEAK_TOKENS:
        if token in blob:
            raise FaultHarnessError(
                f"evaluator token {token!r} leaked into the snapshot for {manifest.fault_id}"
            )
    hit = _FIXTURE_ID_RE.search(blob)
    if hit:
        raise FaultHarnessError(
            f"fixture identifier {hit.group(0)!r} leaked into the snapshot for {manifest.fault_id}"
        )
