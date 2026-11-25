"""Brain runtime: lifecycle, observation cycle, isolation from the trader.

If this module raises, callers must swallow the error. The quantitative
server stays up. Shadow mode is forced on for this phase.
"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from brain.local_config import config
from brain.local_events import Event, EventType, event_bus
from brain.local_logger import get_logger

from . import SCHEMA
from .attest import attestation_panel
from .events import BrainEventKind, publish_brain_event, publish_stats
from .executive import BrainOrchestrator
from .health import evaluate
from .memory import BrainStore
from .models import (
    AuditEvent,
    AutonomyLevel,
    Causality,
    Incident,
    MemoryKind,
    new_id,
    utc_ts,
)
from .sandbox import SandboxExecutor
from .policies import AutonomyPolicy, Budget, CircuitBreaker
from .perception import collect_observation
from .redact import sanitize_error, trace_tail
from .cognitive import default_engine_view, default_heldout_view, default_sides_view
from .evaluate import default_evaluation_view, evaluation_view
from .discovery.engine import DiscoveryEngine
from .discovery.soak import SoakSession, soak_enabled
from .ledger import ProviderLedger
from .shadow import observe_live, record_feedback, strip_actions

log = get_logger("Brain")

WATCHED = (
    EventType.MODULE_ERROR,
    EventType.JOB_FAILED,
    EventType.KILL_SWITCH_L1,
    EventType.KILL_SWITCH_L2,
    EventType.KILL_SWITCH_L3,
    EventType.RISK_LIMIT_BREACH,
    EventType.SYSTEM_STARTED,
    EventType.SYSTEM_SHUTDOWN,
)

_runtime: Optional["BrainRuntime"] = None
_bind_owner: Any = None


def peek_brain() -> Optional["BrainRuntime"]:
    return _runtime


def brain_bind_owner() -> Any:
    return _bind_owner


def get_brain() -> "BrainRuntime":
    global _runtime
    if _runtime is None:
        _runtime = BrainRuntime()
    return _runtime


def bind_brain(runtime: "BrainRuntime", owner: Any = None) -> "BrainRuntime":
    global _runtime, _bind_owner
    if (
        _runtime is not None
        and _runtime is not runtime
        and getattr(_runtime, "running", False)
    ):
        raise RuntimeError("brain_already_bound")
    if (
        _bind_owner is not None
        and owner is not None
        and owner is not _bind_owner
        and _runtime is not None
        and getattr(_runtime, "running", False)
    ):
        raise RuntimeError("brain_already_bound")
    _runtime = runtime
    if owner is not None:
        _bind_owner = owner
    return runtime


def unbind_brain(runtime: Optional["BrainRuntime"] = None, owner: Any = None) -> None:
    """Clear the process bind so peek_brain() cannot expose a stopped runtime."""
    global _runtime, _bind_owner
    if _bind_owner is not None and owner is not _bind_owner:
        return
    if runtime is None or _runtime is runtime:
        _runtime = None
        _bind_owner = None


def reset_brain_for_tests() -> None:
    global _runtime, _bind_owner
    _runtime = None
    _bind_owner = None
    try:
        from . import api as brain_api
        brain_api._offline_snap = {}
        brain_api._offline_ts = 0.0
    except Exception:
        pass


class BrainRuntime:
    def __init__(
        self,
        lake: Optional[Path] = None,
        *,
        read_only: bool = False,
        observer: Any = None,
    ) -> None:
        root = Path(lake) if lake is not None else Path(config.data_lake_dir) / "brain"
        # The service injects a read-only HTTP/filesystem observer. Without one,
        # only filesystem/host evidence is collected; no Trader modules load.
        self._observer = observer
        self.store = BrainStore(root, read_only=read_only)
        self.read_only = bool(read_only)
        self.policy = AutonomyPolicy(
            level=getattr(config, "brain_autonomy_level", 2),
            shadow=True,
        )
        budget = Budget(
            max_concurrent_tasks=int(getattr(config, "brain_max_concurrent_tasks", 8) or 8),
            max_retries=int(getattr(config, "brain_max_retries", 2) or 2),
            max_tasks_per_goal=int(getattr(config, "brain_max_tasks_per_goal", 32) or 32),
            max_depth=int(getattr(config, "brain_max_depth", 6) or 6),
        )
        self.orchestrator = BrainOrchestrator(budget)
        self.sandbox = SandboxExecutor(enabled=False, state_dir=root / "sandbox")
        self.breaker = CircuitBreaker()
        self.incidents: Dict[str, Incident] = {}
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._stop_count = 0
        self._handlers: List[tuple] = []
        self._cycle_count = 0
        self._started_at: Optional[float] = None
        self._last_cycle_at: Optional[float] = None
        self._last_observation: Dict[str, Any] = {}
        self._last_health: Dict[str, Any] = {}
        self._observations_total = 0
        self._task_retries_total = 0
        self._snap: Dict[str, Any] = {}
        self._snap_ts = 0.0
        self._evaluation: Dict[str, Any] = default_evaluation_view()
        self._shadow: Dict[str, Any] = {}
        self._shadow_seen: Dict[str, int] = {}
        self._cognitive: Dict[str, Any] = default_engine_view()
        self._heldout: Dict[str, Any] = default_heldout_view()
        self.ledger = ProviderLedger()
        self.discovery = DiscoveryEngine()
        self._intelligence: Dict[str, Any] = {}
        self._soak: Optional[SoakSession] = SoakSession() if soak_enabled() else None
        self.interval = max(15, int(getattr(config, "brain_cycle_interval_s", 60) or 60))
        self._load()

    @property
    def running(self) -> bool:
        return self._running

    def _load(self) -> None:
        blob = self.store.load_state()
        self.orchestrator.load(blob.get("executive") if isinstance(blob.get("executive"), dict) else {})
        for row in blob.get("incidents") or []:
            if isinstance(row, dict):
                inc = Incident.from_dict(row)
                self.incidents[inc.fingerprint] = inc
        self._cycle_count = int(blob.get("cycle_count") or 0)

    def attach_evaluation(self, view: Dict[str, Any]) -> None:
        """Test/harness only. Production cycle never runs the proving ground."""
        self._evaluation = evaluation_view(view)
        self._snap_ts = 0.0

    def record_shadow_feedback(self, fingerprint: str, action: str, note: str = "") -> Dict[str, Any]:
        """Metadata only. Cannot open sandbox, change code, trade, or raise autonomy."""
        cands = list((self._shadow or {}).get("candidates") or [])
        found = None
        updated = []
        for row in cands:
            if str(row.get("fingerprint") or "") == fingerprint:
                found = record_feedback(row, action, note=note)
                updated.append(found)
            else:
                updated.append(row)
        if found is None:
            return {"ok": False, "reason": "unknown_candidate", "change_request": None, "sandbox": None}
        self._shadow = {**(self._shadow or {}), "candidates": updated, "change_request": None, "sandbox": None}
        self.discovery.apply_feedback(fingerprint, action, note)
        self._audit("SHADOW_FEEDBACK", fingerprint, {"action": str(action).upper(), "triggers_action": False})
        self._snap_ts = 0.0
        return {"ok": True, "candidate": found, "change_request": None, "sandbox": None, "level3_enabled": False}

    def attach_heldout(self, view: Dict[str, Any]) -> None:
        """Test/harness only. Does not enable Level 3 or hybrid in production."""
        self._heldout = {**default_heldout_view(), **dict(view or {})}
        self._heldout["level3_enabled"] = False
        self._snap_ts = 0.0

    def _experiment_view(self) -> Dict[str, Any]:
        try:
            from .cognitive_eval import compact_experiment_view
            from .eval_paths import load_latest_artifact
            art = load_latest_artifact()
            if not art:
                return {
                    "active": False,
                    "real_model_value": "UNPROVEN",
                    "level3_enabled": False,
                    "secrets_present": False,
                }
            return compact_experiment_view(art)
        except Exception:
            return {"active": False, "real_model_value": "UNPROVEN", "level3_enabled": False}

    def _strategy_view(self) -> Dict[str, Any]:
        from brain.fable.concepts import completeness_report
        from brain.strategies import DEFAULT_RUNTIME_STRATEGY
        exp = self._experiment_view()
        return {
            "current": str(getattr(config, "brain_cognitive_mode", "deterministic_only") or "deterministic_only"),
            "default_runtime": DEFAULT_RUNTIME_STRATEGY,
            "fable_derived_status": "available_not_default",
            "fable_default": False,
            "cognitive_effort": None,
            "memory_selected": 0,
            "context_size": 0,
            "hypotheses": 0,
            "verification_requests": 0,
            "parallel_investigations": 0,
            "fresh_verifier": False,
            "completion_state": None,
            "observed_evidence": False,
            "strategy_ledger": {"autonomous_routing": False},
            "fable_derived_value": exp.get("fable_derived_value") or "",
            "matrix_complete": completeness_report().get("complete"),
            "level3_enabled": False,
        }

    def _relate_intelligence(self, intel: Mapping[str, Any]) -> None:
        """Persist non-causal discovery edges. Never writes CAUSES."""
        graph = self.store.graph
        failures: List[str] = []
        for edge in list(intel.get("knowledge_graph") or [])[:16]:
            if not isinstance(edge, Mapping):
                continue
            if edge.get("causal"):
                continue
            src = str(edge.get("source") or "")
            verb = str(edge.get("verb") or "RELATED")
            tgt = str(edge.get("target") or "")
            if not src or not tgt or verb.upper() in {"CAUSES", "CAUSED", "ROOT_CAUSE"}:
                continue
            try:
                graph.upsert_entity(src, "discovery")
                graph.upsert_entity(tgt, "discovery")
                graph.relate(src, verb, tgt)
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {sanitize_error(exc)}")
        try:
            graph.prune_ephemeral(
                max_keep=int(getattr(config, "brain_graph_ephemeral_max", 48) or 48),
            )
        except Exception as exc:
            failures.append(f"prune: {type(exc).__name__}: {sanitize_error(exc)}")
        if failures:
            # One line per cycle, not per edge; the graph stays best-effort but not silent.
            log.warning("brain knowledge graph update failed %s time(s): %s", len(failures), failures[0])

    def _intelligence_view(self) -> Dict[str, Any]:
        intel = dict(self._intelligence or {})
        return {
            "observer": "ok" if self._last_cycle_at else "idle",
            "soak": bool(self._soak),
            "incidents": list(intel.get("incidents") or [])[:8],
            "improvements": list(intel.get("improvements") or [])[:8],
            "investigations": list(intel.get("investigations") or [])[:4],
            "budget": intel.get("budget") or {},
            "metrics": intel.get("metrics") or {},
            "root_clusters": list(intel.get("root_clusters") or [])[:8],
            "pending_model": intel.get("pending_model") or {},
            "telemetry_ledger": intel.get("telemetry_ledger") or {},
            "telemetry_recommendations": list(intel.get("telemetry_recommendations") or [])[:4],
            "telemetry_quality_issues": list(intel.get("telemetry_quality_issues") or [])[:4],
            "score_feature_audit": intel.get("score_feature_audit") or {},
            # Which code this process actually loaded. A served page can contain
            # new panels while the interpreter runs an older revision.
            "runtime_revision": attestation_panel(),
            "llm_called": False,
            "change_request": None,
            "sandbox": None,
            "level3_enabled": False,
            "read_only": True,
        }

    def _persist(self) -> None:
        self.store.save_state({
            "schema": SCHEMA,
            "cycle_count": self._cycle_count,
            "autonomy": self.policy.status(),
            "executive": self.orchestrator.dump(),
            "incidents": [i.to_dict() for i in self.incidents.values()],
            "updated_at": utc_ts(),
        })

    def _audit(self, kind: str, subject: str, payload: Optional[Dict[str, Any]] = None, correlation_id: str = "") -> None:
        event = AuditEvent(
            audit_id=new_id("aud"),
            kind=kind,
            actor="brain",
            subject=subject,
            correlation_id=correlation_id or new_id("cor"),
            payload=payload or {},
        )
        self.store.record_audit(event)

    def _on_bus_event(self, event: Event) -> None:
        if not self.store.mark_processed(event.event_id):
            return
        try:
            if event.type in (EventType.MODULE_ERROR, EventType.JOB_FAILED, EventType.RISK_LIMIT_BREACH):
                self._note_external_error(event)
        except Exception as exc:
            log.debug("brain bus handler: %s", exc)

    def _note_external_error(self, event: Event) -> None:
        component = str(event.source or "unknown")
        title = f"{event.type.name} from {component}"
        self.open_incident(
            title=title,
            component=component,
            severity="warning",
            evidence_detail=str(event.payload)[:240],
        )

    def _subscribe(self) -> None:
        if self._handlers:
            return
        for et in WATCHED:
            event_bus.subscribe(et, self._on_bus_event)
            self._handlers.append((et, self._on_bus_event))

    def _unsubscribe(self) -> None:
        for et, handler in self._handlers:
            try:
                event_bus.unsubscribe(et, handler)
            except Exception:
                pass
        self._handlers.clear()

    def _heartbeat_live(self) -> bool:
        return bool(self._task) and not self._task.done()

    async def start(self) -> None:
        if self.read_only:
            return
        if self._running and self._heartbeat_live():
            return
        if self._running:
            try:
                loop = asyncio.get_running_loop()
                if not self._heartbeat_live():
                    self._task = loop.create_task(self._loop(), name="qhos-brain")
            except RuntimeError:
                self._task = None
            return
        self._running = True
        self._started_at = utc_ts()
        try:
            self._subscribe()
            self._audit("BRAIN_STARTED", "runtime", {"level": self.policy.level, "shadow": True})
            if self.policy.configuration_violation:
                self._audit(
                    "CONFIGURATION_VIOLATION",
                    "brain_autonomy_level",
                    dict(self.policy.configuration_violation),
                )
            await self.cycle()
            try:
                loop = asyncio.get_running_loop()
                self._task = loop.create_task(self._loop(), name="qhos-brain")
            except RuntimeError:
                self._task = None
        except Exception:
            await self.stop()
            raise
        log.info("Brain started (shadow, level=%s) — isolated from trading path", self.policy.level)

    async def stop(self) -> None:
        active = self._running or self._heartbeat_live() or bool(self._handlers)
        if not active:
            return
        self._running = False
        self._unsubscribe()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        try:
            if self._soak is not None:
                self._soak.close()
                self._soak = None
        except Exception as exc:
            log.warning("brain soak session close failed: %s: %s", type(exc).__name__, sanitize_error(exc))
        try:
            self._persist()
        except Exception as exc:
            # The last cycles' state may be lost: say so in the log and in the audit trail.
            detail = sanitize_error(exc)
            log.warning("brain state persist failed on stop: %s: %s | %s", type(exc).__name__, detail, trace_tail(exc))
            try:
                self._audit("PERSIST_FAILED", "runtime", {"error": type(exc).__name__, "detail": detail, "phase": "stop"})
            except Exception as audit_exc:
                log.warning("brain audit trail unavailable on stop: %s: %s", type(audit_exc).__name__, sanitize_error(audit_exc))
        self._stop_count += 1
        self._audit("BRAIN_STOPPED", "runtime", {"cycles": self._cycle_count})
        log.info("Brain stopped after %s cycles", self._cycle_count)

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                if not self._running:
                    break
                await self.cycle()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                # Sanitized cause and frame tail only: no exc_info, whose
                # traceback text would repeat an unredacted message.
                log.warning("brain loop error: %s: %s | %s", type(exc).__name__,
                            sanitize_error(exc), trace_tail(exc))
                self.breaker.record_failure(utc_ts())

    async def cycle(self) -> Dict[str, Any]:
        """Observe → health → maybe open an investigation. Never writes trader state."""
        now = utc_ts()
        if self.breaker.is_open(now):
            return self.snapshot(force=True)
        try:
            return await self._cycle_body(now)
        except Exception as exc:
            detail = sanitize_error(exc)
            where = trace_tail(exc)
            log.warning("brain cycle error: %s: %s | %s", type(exc).__name__, detail, where)
            self.breaker.record_failure(now)
            self._audit("CYCLE_FAILED", "runtime", {"error": type(exc).__name__, "detail": detail, "where": where})
            return self.snapshot(force=True)

    async def _cycle_body(self, now: float) -> Dict[str, Any]:
        publish_brain_event(BrainEventKind.CYCLE_STARTED, payload={"n": self._cycle_count + 1})
        observation = self._observer() if self._observer is not None else collect_observation()
        health = evaluate(observation)
        self._last_observation = observation
        self._last_health = health
        self._cycle_count += 1
        self._last_cycle_at = now
        self._observations_total += 1
        self._seed_world(observation)
        graph = self.store.graph.to_dict()
        self._shadow = strip_actions(observe_live(
            observation,
            relations=graph.get("relations") if isinstance(graph, dict) else [],
            seen=self._shadow_seen,
            api_enabled=bool(getattr(config, "brain_llm_api_enabled", False)),
            mode=str(getattr(config, "brain_cognitive_mode", "deterministic_only") or "deterministic_only"),
        ))
        intel = self.discovery.ingest(
            observation,
            health=health,
            graph=graph if isinstance(graph, dict) else {},
            api_enabled=False,
            soak=bool(self._soak),
        )
        intel["change_request"] = None
        intel["sandbox"] = None
        intel["llm_called"] = False
        self._intelligence = intel
        self._relate_intelligence(intel)
        if self._soak is not None:
            self._soak.record_cycle(intel, list(intel.get("observations") or []))
        # Live shadow never calls a remote model from the default cycle.
        if isinstance(self._shadow, dict):
            # Foundation flag only. This phase never calls a remote model live.
            self._shadow["live_llm_shadow_enabled"] = bool(
                getattr(config, "brain_live_llm_shadow_enabled", False)
            )
            self._shadow["llm_called"] = False
            self._shadow["change_request"] = None
            self._shadow["sandbox"] = None
        self._cognitive = {
            **default_engine_view(),
            "mode": str(getattr(config, "brain_cognitive_mode", "deterministic_only") or "deterministic_only"),
            "current_investigation": None,
            "decision": "observe_only",
        }
        self._maybe_investigate(health)
        started = self.orchestrator.schedule(now=now)
        for task in started:
            decision = self.policy.authorize(task.action, apply=False)
            if not decision.allowed:
                self.orchestrator.fail(task.task_id, decision.reason)
                continue
            try:
                output = self._execute_safe(task.action, observation, health)
                self.orchestrator.complete(task.task_id, output)
            except Exception as exc:
                before = task.retry_count
                self.orchestrator.fail(task.task_id, type(exc).__name__)
                if task.retry_count > before:
                    self._task_retries_total += 1
        self.breaker.record_success()
        self._persist()
        publish_brain_event(
            BrainEventKind.CYCLE_FINISHED,
            payload={"n": self._cycle_count, "tone": health.get("tone")},
        )
        return self.snapshot(force=True)

    def _execute_safe(self, action: str, observation: Dict[str, Any], health: Dict[str, Any]) -> Dict[str, Any]:
        if action in {"observe", "health", "investigate", "audit", "plan", "advise"}:
            return {
                "ok": True,
                "tone": health.get("tone"),
                "global_score": health.get("global_score"),
                "modules": len(observation.get("registry") or {}),
            }
        raise PermissionError("action_not_executable_in_shadow")

    def _seed_world(self, observation: Dict[str, Any]) -> None:
        graph = self.store.graph
        graph.upsert_entity("trader", "system", label="QHOS")
        graph.upsert_entity("brain", "intelligence", label="Trader Brain", shadow=True)
        graph.relate("brain", "OBSERVES", "trader")
        registry = observation.get("registry") if isinstance(observation.get("registry"), dict) else {}
        for name, info in registry.items():
            graph.upsert_entity(name, "component", **(info if isinstance(info, dict) else {}))
            graph.relate("trader", "CONTAINS", name)
            graph.relate("brain", "OBSERVES", name)
        git = observation.get("git") if isinstance(observation.get("git"), dict) else {}
        for i, commit in enumerate(git.get("recent_commits") or []):
            cid = f"commit:{commit.split(' ', 1)[0]}"
            graph.upsert_entity(cid, "commit", message=commit)
            if i == 0:
                graph.relate(cid, "HEAD_OF", "trader")

    def _incident_fp(self, component: str, title: str) -> str:
        raw = f"{component}|{title}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:16]

    def open_incident(
        self,
        title: str,
        component: str,
        *,
        severity: str = "warning",
        evidence_detail: str = "",
    ) -> Incident:
        fp = self._incident_fp(component, title)
        existing = self.incidents.get(fp)
        if existing and existing.status in {"open", "investigating"}:
            existing.updated_at = utc_ts()
            return existing
        correlation = new_id("cor")
        from .models import Evidence
        inc = Incident(
            incident_id=new_id("inc"),
            title=title,
            component=component,
            fingerprint=fp,
            severity=severity,
            status="open",
            causality=Causality.OBSERVED,
            suspected_cause="",
            evidence=[Evidence("observation", "brain.perception", evidence_detail[:240], 1.0)] if evidence_detail else [],
            hypotheses=[],
            correlation_id=correlation,
        )
        self.incidents[fp] = inc
        self.store.graph.upsert_entity(inc.incident_id, "incident", title=title)
        self.store.graph.relate(inc.incident_id, "AFFECTED", component)
        self._audit("INCIDENT_CREATED", component, {"title": title, "severity": severity}, correlation)
        publish_brain_event(
            BrainEventKind.INCIDENT_CREATED,
            payload={"incident_id": inc.incident_id, "component": component},
            correlation_id=correlation,
        )
        if self.policy.level >= int(AutonomyLevel.INVESTIGATE) and not self.read_only:
            goal = self.orchestrator.create_goal(
                title=f"Investigate {component}",
                reason=title,
                incident_id=inc.incident_id,
                correlation_id=correlation,
                priority=20 if severity == "critical" else 50,
                risk_level="medium" if severity == "critical" else "low",
                origin="health",
            )
            inc.goal_id = goal.goal_id
            inc.status = "investigating"
            task = self.orchestrator.create_task(
                goal.goal_id,
                f"Collect evidence for {component}",
                action="observe",
                description=evidence_detail[:400],
            )
            self.store.graph.upsert_entity(goal.goal_id, "goal", title=goal.title)
            self.store.graph.relate(inc.incident_id, "SPAWNED", goal.goal_id)
            self.store.graph.relate(goal.goal_id, "INVESTIGATED", inc.incident_id)
            if task is not None:
                self.store.graph.upsert_entity(task.task_id, "task", title=task.title)
                self.store.graph.relate(goal.goal_id, "PLANNED", task.task_id)
                self.store.graph.relate(task.assigned_agent or "system", "EXECUTED", task.task_id)
            self.store.remember(
                MemoryKind.EPISODIC,
                title,
                f"component={component} severity={severity} {evidence_detail}"[:1000],
                tags=["incident", component],
                correlation_id=correlation,
            )
        return inc

    def _maybe_investigate(self, health: Dict[str, Any]) -> None:
        if self.policy.level < int(AutonomyLevel.INVESTIGATE):
            return
        from .health import BOOT_PENDING_STATUSES
        registry = {}
        ready = False
        if isinstance(self._last_observation, dict):
            raw = self._last_observation.get("registry")
            if isinstance(raw, dict):
                registry = raw
            ready = bool(self._last_observation.get("application_ready"))
        for issue in (health.get("critical_issues") or [])[:4]:
            component = str(issue.get("component") or "unknown")
            info = registry.get(component)
            status = str(info.get("status") or "") if isinstance(info, dict) else ""
            if status in BOOT_PENDING_STATUSES and not ready:
                continue
            self.open_incident(
                title=f"Critical: {component}",
                component=component,
                severity="critical",
                evidence_detail=str(health.get("tone")),
            )

    def snapshot(self, force: bool = False) -> Dict[str, Any]:
        now = utc_ts()
        if not force and self._snap and now - self._snap_ts < 15:
            return self._snap
        from .control_plane import build_control_plane, heartbeat_state
        health = self._last_health or evaluate(self._last_observation or {})
        obs = self._last_observation or {}
        # snapshot() runs on pool threads while the loop thread inserts incidents,
        # goals and tasks. list(d.values()) copies in C without releasing the GIL;
        # iterating the live view while calling to_dict() does not (QHOS-AUD-281).
        tasks = [t.to_dict() for t in list(self.orchestrator.tasks.values())]
        goals = [g.to_dict() for g in list(self.orchestrator.goals.values())]
        for goal in goals:
            related = [t for t in tasks if t.get("parent_goal") == goal.get("goal_id")]
            done = sum(1 for t in related if t.get("status") in {"completed", "cancelled", "rolled_back"})
            goal["task_count"] = len(related)
            goal["progress"] = round(done / len(related), 4) if related else 0.0
        hb_brain = heartbeat_state(
            running=self._running,
            last_cycle_at=self._last_cycle_at,
            interval_s=self.interval,
            health_tone=str((health or {}).get("tone") or ""),
            now=now,
        )
        metrics = {
            "cycles_total": self._cycle_count,
            "observations_total": self._observations_total,
            "incidents_total": len(self.incidents),
            "tasks_total": self.orchestrator.metrics.get("tasks_created", 0),
            "task_failures_total": self.orchestrator.metrics.get("tasks_failed", 0),
            "task_retries_total": self._task_retries_total,
            "false_positive_count": None,
            "sandbox_runs_total": len(self.sandbox.runs),
            "sandbox_success_total": sum(
                1 for r in list(self.sandbox.runs.values())
                if r.status == "ready_for_review"
            ),
            "approvals_pending": self.orchestrator.metrics.get("approvals_pending", 0),
            "brain_event_rate": round(self._cycle_count / max(now - (self._started_at or now), 1.0), 4),
            "brain_loop_duration_s": self.interval,
            "last_successful_cycle": self._last_cycle_at,
        }
        graph_full = self.store.graph.to_dict()
        payload = {
            "schema": SCHEMA,
            "available": True,
            "running": self._running,
            "shadow": True,
            "autonomy": self.policy.status(),
            "trading_autonomy": obs.get("trading_autonomy") or {},
            "cycles": self._cycle_count,
            "uptime_s": round(now - self._started_at, 1) if self._started_at else 0,
            "health": health,
            "resources": obs.get("resources") or {},
            "incidents": [i.to_dict() for i in list(self.incidents.values())[-20:]],
            "goals": goals,
            "tasks": tasks,
            "orchestrator": {
                **self.orchestrator.metrics,
                "name": "BrainOrchestrator",
                "not_hpc_orchestrator": True,
            },
            "graph": graph_full.get("counts"),
            "relations": graph_full.get("relations") or [],
            "memory_tail": self.store.memory.tail(12),
            "audit_tail": self.store.audit.tail(20),
            "git": obs.get("git") or {},
            "last_observation_at": self._last_cycle_at,
            "heartbeat": {
                "brain": hb_brain,
                "trader": "separate",
                "age_s": round(now - self._last_cycle_at, 1) if self._last_cycle_at else None,
                "last_cycle_at": self._last_cycle_at,
            },
            "metrics": metrics,
            "sandbox": {
                **self.sandbox.status(),
                "runs": [r.to_dict() for r in list(self.sandbox.runs.values())[-8:]],
            },
            "proposals": [p.to_dict() for p in list(self.sandbox.proposals.values())],
            "learnings": [x.to_dict() for x in list(self.sandbox.learnings.values())[-8:]],
            "level3": {"capable": True, "enabled": False, "shadow_blocks_real_writes": True},
            "evaluation": evaluation_view(self._evaluation),
            "cognitive": dict(self._cognitive or default_engine_view()),
            "heldout": dict(self._heldout or default_heldout_view()),
            "live_shadow": dict(self._shadow or {}),
            "provider_performance": self.ledger.summary(),
            "real_model_experiment": self._experiment_view(),
            "cognitive_strategy": self._strategy_view(),
            "live_intelligence": self._intelligence_view(),
            "provider_health": {
                "api_enabled": bool(getattr(config, "brain_llm_api_enabled", False)),
                "evolution_isolated": True,
                "mode": str(getattr(config, "brain_cognitive_mode", "deterministic_only")),
                "live_llm_shadow_enabled": bool(getattr(config, "brain_live_llm_shadow_enabled", False)),
                "level3_enabled": False,
            },
            "sides": {
                **default_sides_view(),
                "engine": dict(self._cognitive or default_engine_view()),
                "heldout": dict(self._heldout or default_heldout_view()),
                "shadow": {
                    "candidates": len((self._shadow or {}).get("candidates") or []),
                    "change_request": None,
                    "sandbox": None,
                },
            },
            "event_bus": {
                "kind": "in-process",
                "limitation": "Brain-local events; external Trader evidence arrives through the observer",
                "publish_stats": dict(publish_stats),
                "handler_failures": getattr(event_bus, "handler_failures", 0),
            },
            "not_a_trading_path": True,
            "supervision_only": True,
        }
        payload["lacos"] = build_control_plane(payload)
        self._snap, self._snap_ts = payload, now
        return payload
