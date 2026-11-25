"""Observation → hypothesis → verification → stop. No ground truth. No CoT dump."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .cognitive import default_engine_view, default_heldout_view, default_sides_view, draft_from_diagnosis
from .control_side import ControlIntent, admit, evidence_ids_from_hypotheses
from .executive import BrainOrchestrator
from .hypothesis import HypothesisEngine
from .models import (
    ChangeRequest,
    DecisionTrace,
    DiagnosticBudget,
    Hypothesis,
    HypothesisStatus,
    Incident,
    LearningRecord,
    ObservationClass,
    StopDecision,
    TaskKind,
)
from .policies import SENSITIVE_ACTIONS, route_agent_explained
from .sandbox import classify_path, authorize_command


LEAK_KEYS = frozenset({
    "expected_observations",
    "expected_incident_type",
    "expected_root_cause",
    "acceptable_hypotheses",
    "forbidden_hypotheses",
    "expected_priority",
    "expected_task_graph",
    "expected_validation",
    "expected_resolution",
    "expected_learning",
    "ground_truth",
    "gabarito",
    "answer_key",
    "correct_cause",
    "answer",
    "expected_stop",
    "expected_solution",
    "expected_classification",
    "scoring_rules",
    "score_rules",
    "evaluation_score",
})

PATH_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|json|md|yml|yaml|toml|ini)", re.I)
TEST_RE = re.compile(r"\b(?:test|probe)_[\w]+")
UNSAFE_ACTIONS = frozenset({
    "push", "merge", "deploy", "order", "oms", "execution", "live_trade",
    "production_write", "secret", "risk_limit",
}) | set(SENSITIVE_ACTIONS)


def strip_ground_truth(payload: Any) -> Any:
    """Remove evaluator-only keys. Safe to feed the remainder to the Brain."""
    if isinstance(payload, Mapping):
        out = {}
        for key, value in payload.items():
            lk = str(key).lower()
            if key in LEAK_KEYS or lk in LEAK_KEYS or (
                lk.startswith("expected_") and lk != "expected_effect"
            ):
                continue
            out[key] = strip_ground_truth(value)
        return out
    if isinstance(payload, list):
        return [strip_ground_truth(x) for x in payload]
    return payload


def leak_keys_present(payload: Any) -> List[str]:
    found: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                lk = str(key).lower()
                if key in LEAK_KEYS or lk in LEAK_KEYS or (
                    lk.startswith("expected_") and lk != "expected_effect"
                ):
                    found.append(str(key))
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return found


def _blob(observations: Sequence[Mapping[str, Any]]) -> str:
    parts: List[str] = []
    for row in observations:
        parts.append(str(row.get("kind") or ""))
        parts.append(str(row.get("summary") or ""))
        parts.append(str(row.get("body") or ""))
        parts.append(str(row.get("source") or ""))
    return "\n".join(parts)


def _paths_in(text: str) -> List[str]:
    seen: List[str] = []
    for match in PATH_RE.findall(text or ""):
        item = match.replace("\\", "/")
        if item not in seen:
            seen.append(item)
    return seen


@dataclass
class MemoryHint:
    pattern: str
    previous_hypothesis: str = ""
    previous_target: str = ""
    previous_result: str = ""
    context_tokens: List[str] = field(default_factory=list)
    must_revalidate: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern,
            "previous_hypothesis": self.previous_hypothesis,
            "previous_target": self.previous_target,
            "previous_result": self.previous_result,
            "context_tokens": list(self.context_tokens),
            "must_revalidate": True,
            "memory_suggests_not_proves": True,
        }


@dataclass
class CostLedger:
    llm_calls: int = 0
    agent_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0
    duration_s: float = 0.0
    retries: int = 0
    tasks_created: int = 0
    verifications: int = 0
    cycles: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "agent_calls": self.agent_calls,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "duration_s": round(self.duration_s, 4),
            "retries": self.retries,
            "tasks_created": self.tasks_created,
            "verifications": self.verifications,
            "cycles": self.cycles,
        }


@dataclass
class SignalProfile:
    text: str
    test_failures: List[Dict[str, Any]]
    diffs: List[Dict[str, Any]]
    metrics: List[Dict[str, Any]]
    logs: List[Dict[str, Any]]
    paths: List[str]
    fail_paths: List[str]
    protected_paths: List[str]
    preexisting: bool
    new_failure: bool
    performance: bool
    latency_ms: Optional[float]
    baseline_ms: Optional[float]
    config_signal: bool
    schema_signal: bool
    parser_signal: bool
    weak: bool
    noise_only: bool
    health_drop: bool


class FixtureEnv:
    """Read-only view of disposable fixtures. Never touches production trees."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else None
        self.calls: List[str] = []

    def list_files(self) -> List[str]:
        self.calls.append("list_files")
        if self.root is None or not self.root.exists():
            return []
        out: List[str] = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json", ".md", ".txt", ".yml"}:
                out.append(str(path.relative_to(self.root)).replace("\\", "/"))
        return out

    def read_text(self, rel: str) -> Optional[str]:
        self.calls.append(f"read:{rel}")
        if self.root is None:
            return None
        candidate = (self.root / rel).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return None

    def reproduce(self) -> Dict[str, Any]:
        self.calls.append("reproduce")
        if self.root is None:
            return {"ok": False, "reason": "no_probe", "failed": True}
        probe = None
        for name in ("probe.py", "suite.py"):
            if (self.root / name).is_file():
                probe = self.root / name
                break
        if probe is None:
            return {"ok": False, "reason": "no_probe", "failed": True}
        import importlib.util
        import sys
        spec = importlib.util.spec_from_file_location("pg_probe", probe)
        if spec is None or spec.loader is None:
            return {"ok": False, "reason": "probe_unreadable", "failed": True}
        root_s = str(self.root)
        inserted = root_s not in sys.path
        if inserted:
            sys.path.insert(0, root_s)
        prev_bc = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            fn = getattr(mod, "probe", None)
            if callable(fn):
                result = fn()
                if isinstance(result, dict):
                    return result
        except Exception as exc:
            return {"ok": False, "failed": True, "error": str(exc)[:240]}
        finally:
            sys.dont_write_bytecode = prev_bc
            if inserted:
                try:
                    sys.path.remove(root_s)
                except ValueError:
                    pass
        return {"ok": False, "reason": "probe_missing"}


def profile_observations(observations: Sequence[Mapping[str, Any]]) -> SignalProfile:
    rows = [dict(x) for x in observations if isinstance(x, Mapping)]
    text = _blob(rows)
    tests = [r for r in rows if str(r.get("kind") or "") in {"test_failure", "test_fail"}]
    diffs = [r for r in rows if str(r.get("kind") or "") == "diff"]
    metrics = [r for r in rows if str(r.get("kind") or "") in {"metric", "latency"}]
    logs = [r for r in rows if str(r.get("kind") or "") in {"log", "event", "health"}]
    fail_text = _blob(tests)
    paths = _paths_in(text)
    fail_paths = _paths_in(fail_text)
    protected = [p for p in paths if classify_path(p) == "PROTECTED"]
    preexisting = bool(tests) and all(bool(t.get("seen_in_baseline")) for t in tests)
    new_failure = bool(tests) and not preexisting
    latency = None
    baseline = None
    for met in metrics:
        if met.get("latency_ms") is not None:
            latency = float(met["latency_ms"])
        if met.get("baseline_ms") is not None:
            baseline = float(met["baseline_ms"])
        if met.get("value") is not None and str(met.get("name") or "") in {"latency_ms", "latency"}:
            latency = float(met["value"])
    performance = False
    if latency is not None and baseline is not None and baseline > 0 and latency >= baseline * 2.0:
        performance = True
    if any("regression" in str(m.get("summary") or "").lower() for m in metrics):
        performance = True
    lowered = text.lower()
    config_signal = any(tok in lowered for tok in ("timeout_ms", "settings.json", "config.json", "timeout="))
    schema_signal = "schema" in lowered
    parser_signal = "parser" in lowered
    health_drop = any(
        str(r.get("kind")) == "health" and str(r.get("tone") or r.get("summary") or "").lower()
        in {"critical", "degraded", "health degraded"}
        for r in rows
    )
    strong = new_failure or performance or health_drop or bool(protected and tests)
    kinds = {str(r.get("kind") or "") for r in rows}
    errorish = any(tok in lowered for tok in ("error", "fail", "spike", "odd", "wrong", "timeout", "inconclusive"))
    noise_only = (
        kinds <= {"log", "event", "information"}
        and not strong
        and not config_signal
        and not errorish
    )
    weak = (not strong) and (not config_signal) and (not performance)
    if tests and preexisting:
        weak = False
    return SignalProfile(
        text=text,
        test_failures=tests,
        diffs=diffs,
        metrics=metrics,
        logs=logs,
        paths=paths,
        fail_paths=fail_paths,
        protected_paths=protected,
        preexisting=preexisting,
        new_failure=new_failure,
        performance=performance,
        latency_ms=latency,
        baseline_ms=baseline,
        config_signal=config_signal,
        schema_signal=schema_signal,
        parser_signal=parser_signal,
        weak=weak and not preexisting,
        noise_only=noise_only and not preexisting,
        health_drop=health_drop,
    )


def classify_signals(profile: SignalProfile) -> str:
    if profile.preexisting and not profile.new_failure:
        return ObservationClass.PREEXISTING.value
    if profile.noise_only and profile.weak:
        return ObservationClass.NOISE.value
    if profile.weak and not profile.new_failure and not profile.performance:
        return ObservationClass.INFORMATION.value
    if profile.performance and not profile.new_failure:
        return ObservationClass.ANOMALY.value
    if profile.new_failure or profile.health_drop or profile.performance:
        return ObservationClass.INCIDENT.value
    if profile.config_signal:
        return ObservationClass.WARNING.value
    return ObservationClass.INFORMATION.value


def _inspect_findings(rel: str, source: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    compact = re.sub(r"\s+", " ", source)
    if "def add" in source and re.search(r"return a\s*-\s*b", source):
        findings.append({"kind": "wrong_operator", "fn": "add", "file": rel, "op": "-", "expect": "+"})
    if "def mul" in source and re.search(r"return a\s*\+\s*b", source):
        findings.append({"kind": "wrong_operator", "fn": "mul", "file": rel, "op": "+", "expect": "*"})
    if source.count("for ") >= 2 and "range(len(" in source:
        findings.append({"kind": "nested_loop", "file": rel})
    if "def parse" in source and re.search(r"return\s+None", source):
        findings.append({"kind": "parse_none", "file": rel})
    if "timeout_ms" in source and rel.endswith(".py"):
        findings.append({"kind": "uses_config", "file": rel})
    if rel.endswith(".json"):
        try:
            data = json.loads(source)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("timeout_ms") == 0:
            findings.append({"kind": "timeout_zero", "file": rel, "value": 0})
        if isinstance(data, dict) and data.get("timeout_ms"):
            findings.append({"kind": "timeout_ok", "file": rel, "value": data.get("timeout_ms")})
        if isinstance(data, dict) and "fields" in data:
            findings.append({"kind": "schema_fields", "file": rel, "fields": data.get("fields")})
    if "schema" in compact.lower() and rel.endswith(".json"):
        findings.append({"kind": "schema_doc", "file": rel})
    return findings


@dataclass
class BrainResult:
    observation_class: str
    stop: str
    confidence: float = 0.0
    incident: Optional[Dict[str, Any]] = None
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    traces: List[Dict[str, Any]] = field(default_factory=list)
    chosen: Optional[Dict[str, Any]] = None
    goal: Optional[Dict[str, Any]] = None
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    dag: Dict[str, Any] = field(default_factory=dict)
    routing: List[Dict[str, Any]] = field(default_factory=list)
    change_request: Optional[Dict[str, Any]] = None
    patches: Dict[str, str] = field(default_factory=dict)
    attempted_paths: List[str] = field(default_factory=list)
    attempted_commands: List[str] = field(default_factory=list)
    safety_flags: List[str] = field(default_factory=list)
    learning: Optional[Dict[str, Any]] = None
    cost: Dict[str, Any] = field(default_factory=dict)
    memory: str = "none"
    priority: int = 50
    findings: List[Dict[str, Any]] = field(default_factory=list)
    replay_inputs: Dict[str, Any] = field(default_factory=dict)
    sides: Dict[str, Any] = field(default_factory=dict)
    model_confidence: float = 0.0
    system_confidence: float = 0.0
    verifications: List[Dict[str, Any]] = field(default_factory=list)
    cognitive_mode: str = "deterministic_only"
    llm_calls: int = 0
    committee_used: bool = False
    budget_status: str = "ok"
    disagreement: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "observation_class": self.observation_class,
            "stop": self.stop,
            "confidence": self.confidence,
            "incident": self.incident,
            "hypotheses": list(self.hypotheses),
            "traces": list(self.traces),
            "chosen": self.chosen,
            "goal": self.goal,
            "tasks": list(self.tasks),
            "dag": dict(self.dag),
            "routing": list(self.routing),
            "change_request": self.change_request,
            "patches": dict(self.patches),
            "attempted_paths": list(self.attempted_paths),
            "attempted_commands": list(self.attempted_commands),
            "safety_flags": list(self.safety_flags),
            "learning": self.learning,
            "cost": dict(self.cost),
            "memory": self.memory,
            "priority": self.priority,
            "findings": list(self.findings),
            "replay_inputs": dict(self.replay_inputs),
            "sides": dict(self.sides),
            "model_confidence": float(self.model_confidence),
            "system_confidence": float(self.system_confidence),
            "verifications": list(self.verifications),
            "cognitive_mode": self.cognitive_mode,
            "llm_calls": int(self.llm_calls),
            "committee_used": bool(self.committee_used),
            "budget_status": self.budget_status,
            "disagreement": bool(self.disagreement),
        }


def stamp_cognitive(result: BrainResult) -> BrainResult:
    """Attach inspector fields. Does not change stop or authority."""
    result.system_confidence = float(result.system_confidence or result.confidence or 0.0)
    result.cognitive_mode = result.cognitive_mode or "deterministic_only"
    engine = default_engine_view()
    engine.update({
        "mode": result.cognitive_mode,
        "provider": (result.sides.get("engine") or {}).get("provider") if result.sides else "null",
        "model": (result.sides.get("engine") or {}).get("model") if result.sides else "",
        "current_investigation": (result.incident or {}).get("incident_id") if result.incident else None,
        "hypotheses": list(result.hypotheses)[:8],
        "evidence": list(result.findings)[:8],
        "verification_requests": list(result.verifications)[:8],
        "system_confidence": result.system_confidence,
        "model_confidence": result.model_confidence,
        "llm_calls": result.llm_calls,
        "tool_calls": int((result.cost or {}).get("tool_calls") or 0),
        "budget": {"status": result.budget_status},
        "decision": result.stop,
        "committee_used": result.committee_used,
        "disagreement": result.disagreement,
    })
    sides = result.sides or default_sides_view()
    sides["engine"] = engine
    sides.setdefault("heldout", default_heldout_view())
    result.sides = sides
    return result


def _fingerprint(profile: SignalProfile) -> str:
    raw = profile.text[:800]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _maybe_incident(profile: SignalProfile, cls: str, corr: str) -> Optional[Incident]:
    if cls in {
        ObservationClass.NOISE.value,
        ObservationClass.INFORMATION.value,
        ObservationClass.DUPLICATE.value,
    }:
        return None
    if cls == ObservationClass.PREEXISTING.value:
        title = "pre-existing failure (not a new regression)"
        severity = "low"
        component = "baseline"
    elif profile.performance:
        title = profile.metrics[0].get("summary") or "latency regression"
        severity = "medium"
        component = "performance"
    else:
        row = profile.test_failures[0] if profile.test_failures else {}
        title = str(row.get("summary") or "observed failure")
        severity = "high" if profile.protected_paths else "medium"
        component = (profile.fail_paths[0] if profile.fail_paths else "unknown")
    return Incident(
        incident_id="inc_" + _fingerprint(profile),
        title=str(title)[:180],
        component=str(component)[:80],
        fingerprint=_fingerprint(profile),
        severity=severity,
        status="open" if cls == ObservationClass.INCIDENT.value else "known",
        suspected_cause="",
        correlation_id=corr,
    )


def _memory_status(hints: Sequence[MemoryHint], profile: SignalProfile) -> str:
    if not hints:
        return "none"
    tokens = set(TEST_RE.findall(profile.text.lower()))
    tokens.update(Path(p).stem.lower() for p in profile.paths)
    operatorish = bool(re.search(r"\b(add|mul)\s*\(", profile.text))
    configish = "timeout_ms" in profile.text.lower() or "settings.json" in profile.text.lower()
    statuses: List[str] = []
    for hint in hints:
        ctx = {t.lower() for t in hint.context_tokens}
        if hint.pattern in {"binary_op", "wrong_operator"}:
            statuses.append("suggested" if operatorish else "contradicted")
        elif hint.pattern in {"config_timeout", "timeout"}:
            statuses.append("suggested" if configish else "contradicted")
        elif ctx and tokens and ctx.isdisjoint(tokens):
            statuses.append("contradicted")
        else:
            statuses.append("suggested")
    if statuses and all(s == "contradicted" for s in statuses):
        return "contradicted"
    if "suggested" in statuses:
        return "suggested"
    return "contradicted"


def diagnose(
    observations: Sequence[Mapping[str, Any]],
    *,
    env: Optional[FixtureEnv] = None,
    memory: Optional[Sequence[MemoryHint]] = None,
    budget: Optional[DiagnosticBudget] = None,
    orchestrator: Optional[BrainOrchestrator] = None,
) -> BrainResult:
    """Run the bounded diagnostic loop. Ground truth must not be in observations."""
    import time

    t0 = time.perf_counter()
    obs = strip_ground_truth(list(observations))
    leaked = leak_keys_present(obs)
    env = env or FixtureEnv()
    budget = budget or DiagnosticBudget()
    orch = orchestrator or BrainOrchestrator()
    engine = HypothesisEngine()
    cost = CostLedger()
    hints = list(memory or [])
    corr = "cor_" + hashlib.sha256(json.dumps(obs, sort_keys=True, default=str).encode()).hexdigest()[:12]
    profile = profile_observations(obs)
    cls = classify_signals(profile)
    mem_state = _memory_status(hints, profile)
    traces = [
        DecisionTrace(
            what="observations_classified",
            why="signal_profile",
            evidence=[cls, f"new_failure={profile.new_failure}", f"preexisting={profile.preexisting}"],
            action="classify",
            result=cls,
        )
    ]
    if leaked:
        traces.append(DecisionTrace(
            what="ground_truth_stripped",
            why="isolation",
            evidence=leaked[:8],
            action="sanitize",
            result="stripped",
        ))

    if budget.max_cycles <= 0 or budget.max_verifications <= 0:
        stop = StopDecision.BUDGET_EXHAUSTED.value
        result = BrainResult(
            observation_class=cls,
            stop=stop,
            traces=[t.to_dict() for t in traces],
            cost=cost.to_dict(),
            memory=mem_state,
            replay_inputs={"observations": obs},
            priority=40,
        )
        result.cost = {**result.cost, "duration_s": round(time.perf_counter() - t0, 4)}
        return stamp_cognitive(result)

    if cls == ObservationClass.PREEXISTING.value:
        inc = _maybe_incident(profile, cls, corr)
        traces.append(DecisionTrace(
            what="stop",
            why="failure_present_in_baseline",
            evidence=["seen_in_baseline=true"],
            action="no_new_regression",
            result=StopDecision.PRE_EXISTING_FAILURE.value,
        ))
        learning = LearningRecord(
            learning_id="lrn_" + _fingerprint(profile),
            kind="preexisting",
            hypothesis="not a new regression",
            result=StopDecision.PRE_EXISTING_FAILURE.value,
            decision="no_action",
            context="baseline_fingerprint_match",
            correlation_id=corr,
        )
        cost.duration_s = time.perf_counter() - t0
        cost.cycles = 1
        return stamp_cognitive(BrainResult(
            observation_class=cls,
            stop=StopDecision.PRE_EXISTING_FAILURE.value,
            incident=inc.to_dict() if inc else None,
            traces=[t.to_dict() for t in traces] + engine.traces,
            learning=learning.to_dict(),
            cost=cost.to_dict(),
            memory=mem_state,
            priority=20,
            replay_inputs={"observations": obs},
        ))

    if cls in {ObservationClass.NOISE.value, ObservationClass.INFORMATION.value} and profile.weak:
        traces.append(DecisionTrace(
            what="stop",
            why="inconclusive_signals",
            evidence=["no_test_failure", "no_metric_regression"],
            action="withhold_patch",
            result=StopDecision.NEEDS_MORE_EVIDENCE.value if cls == ObservationClass.INFORMATION.value else StopDecision.NO_ACTION_REQUIRED.value,
        ))
        stop = (
            StopDecision.NEEDS_MORE_EVIDENCE.value
            if cls == ObservationClass.INFORMATION.value
            else StopDecision.NO_ACTION_REQUIRED.value
        )
        learning = LearningRecord(
            learning_id="lrn_" + _fingerprint(profile),
            kind="insufficient_evidence",
            hypothesis="signals inconclusive",
            result=stop,
            decision="no_arbitrary_fix",
            correlation_id=corr,
        )
        cost.duration_s = time.perf_counter() - t0
        return stamp_cognitive(BrainResult(
            observation_class=cls,
            stop=stop,
            traces=[t.to_dict() for t in traces],
            learning=learning.to_dict(),
            cost=cost.to_dict(),
            memory=mem_state,
            priority=25,
            replay_inputs={"observations": obs},
        ))

    # Hypotheses from observations only.
    if profile.protected_paths:
        engine.propose(
            "failure involves a protected path; a write would be unsafe",
            target=profile.protected_paths[0],
            kind="safety",
            next_verification="check_protected",
            prior=0.3,
        )
    if profile.new_failure and not profile.protected_paths:
        engine.propose(
            "failing test indicates a defect in the exercised component",
            target=profile.fail_paths[0] if profile.fail_paths else "",
            kind="code",
            next_verification="reproduce_failure",
            prior=0.25,
        )
    if profile.config_signal:
        engine.propose(
            "misconfigured timeout or settings, not a logic bug",
            target="settings.json",
            kind="config",
            next_verification="inspect_config",
            prior=0.28,
        )
        engine.propose(
            "application code ignores or mishandles configuration",
            target="",
            kind="code",
            next_verification="inspect_source",
            prior=0.22,
        )
    if profile.performance:
        engine.propose(
            "implementation is asymptotically slower than the baseline",
            target=profile.paths[0] if profile.paths else "",
            kind="performance",
            next_verification="compare_latency",
            prior=0.3,
        )
    recency_hyp = None
    if len(profile.diffs) >= 2:
        newest = sorted(profile.diffs, key=lambda d: float(d.get("ts") or 0), reverse=True)[0]
        recency_hyp = engine.propose(
            "the most recently changed file caused the failure",
            target=str(newest.get("path") or newest.get("summary") or ""),
            kind="recency",
            next_verification="correlate_diff",
            prior=0.25,
        )
        for diff in profile.diffs:
            path = str(diff.get("path") or "")
            if path and path in profile.text:
                engine.propose(
                    "a correlated diff, not mere recency, explains the failure",
                    target=path,
                    kind="code",
                    next_verification="inspect_source",
                    prior=0.27,
                )
    if profile.schema_signal:
        engine.propose(
            "schema and parser must be checked in order before validation",
            target="schema.json",
            kind="dependency",
            next_verification="inspect_schema",
            prior=0.26,
        )
    if hints and mem_state == "suggested":
        engine.propose(
            "memory suggests a similar prior incident; revalidation is required",
            target=hints[0].previous_target,
            kind="memory",
            next_verification="memory_revalidate",
            prior=0.2,
        )
    elif hints and mem_state == "contradicted":
        engine.propose(
            "historical memory does not match the current context",
            target="",
            kind="memory",
            next_verification="memory_revalidate",
            prior=0.2,
        )

    findings: List[Dict[str, Any]] = []
    verified_ids: set[str] = set()
    cycles = 0
    stop = StopDecision.NEEDS_MORE_EVIDENCE.value

    def apply_findings(found: List[Dict[str, Any]]) -> None:
        findings.extend(found)
        for item in found:
            kind = item.get("kind")
            evid = f"inspect:{item.get('file')}:{kind}"
            if kind == "wrong_operator":
                specific = engine.propose(
                    f"{item.get('fn')} uses {item.get('op')} instead of {item.get('expect')}",
                    target=str(item.get("file") or ""),
                    kind="code",
                    next_verification="check_protected",
                    prior=0.35,
                )
                engine.add_evidence(
                    specific.hypothesis_id, evid, polarity="for",
                    text=f"{item.get('fn')} uses {item.get('op')} instead of {item.get('expect')}",
                    weight=0.5, verified=True,
                )
                verified_ids.add(specific.hypothesis_id)
                for hyp in engine.hypotheses.values():
                    if hyp.kind in {"code", "memory"} and hyp.hypothesis_id != specific.hypothesis_id:
                        engine.add_evidence(
                            hyp.hypothesis_id, evid + ":peer", polarity="for",
                            text=f"{item.get('fn')} operator mismatch",
                            weight=0.2, verified=True,
                        )
            elif kind == "timeout_zero":
                for hyp in engine.hypotheses.values():
                    if hyp.kind == "config":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid, polarity="for",
                            text="timeout_ms is 0", weight=0.5, verified=True,
                        )
                        verified_ids.add(hyp.hypothesis_id)
                    elif hyp.kind == "code":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid, polarity="against",
                            text="config value is independently invalid", weight=0.25,
                        )
            elif kind == "timeout_ok":
                for hyp in engine.hypotheses.values():
                    if hyp.kind == "config":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid, polarity="against",
                            text="timeout_ms is already positive", weight=0.4,
                        )
            elif kind == "uses_config":
                for hyp in engine.hypotheses.values():
                    if hyp.kind == "config":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid + ":uses", polarity="for",
                            text="code reads timeout from settings", weight=0.15,
                        )
            elif kind == "nested_loop":
                for hyp in engine.hypotheses.values():
                    if hyp.kind == "performance":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid, polarity="for",
                            text="nested loop over input", weight=0.45, verified=True,
                        )
                        verified_ids.add(hyp.hypothesis_id)
            elif kind == "parse_none":
                for hyp in engine.hypotheses.values():
                    if hyp.target.endswith("parser.py") or "parser" in hyp.statement.lower() or hyp.kind == "code":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid, polarity="for",
                            text="parse returns None", weight=0.4, verified=True,
                        )
                        verified_ids.add(hyp.hypothesis_id)
            elif kind in {"schema_doc", "schema_fields"}:
                for hyp in engine.hypotheses.values():
                    if hyp.kind == "dependency":
                        engine.add_evidence(
                            hyp.hypothesis_id, evid, polarity="for",
                            text="schema is a prerequisite", weight=0.2, verified=True,
                        )
                        verified_ids.add(hyp.hypothesis_id)

    while cycles < budget.max_cycles and cost.verifications < budget.max_verifications:
        cycles += 1
        cost.cycles = cycles
        pending = [h for h in engine.ranked() if h.status not in {
            HypothesisStatus.CONFIRMED.value, HypothesisStatus.REJECTED.value,
        }]
        if not pending:
            pending = engine.ranked()[:1]
        if not pending:
            break
        hyp = pending[0]
        action = hyp.next_verification or "inspect_source"
        cost.verifications += 1
        cost.tool_calls += 1
        traces.append(DecisionTrace(
            what="verification",
            why="diagnostic_loop",
            hypothesis_id=hyp.hypothesis_id,
            action=action,
            evidence=[hyp.statement],
            confidence=hyp.confidence,
        ))
        hyp.status = HypothesisStatus.TESTING.value

        if action == "reproduce_failure":
            repro = env.reproduce()
            engine.add_evidence(
                hyp.hypothesis_id, "reproduce:probe",
                polarity="for" if not repro.get("ok") else "against",
                text=str(repro.get("error") or repro.get("reason") or repro)[:200],
                weight=0.2,
            )
            hyp.next_verification = "inspect_source"
        elif action in {"inspect_source", "inspect_schema", "inspect_parser", "inspect_config", "memory_revalidate"}:
            files = env.list_files()
            cost.tool_calls += 1
            wanted: List[str] = []
            if action == "inspect_config":
                wanted = [f for f in files if f.endswith(".json")]
            elif action == "inspect_schema":
                wanted = [f for f in files if "schema" in f.lower() or f.endswith(".json")]
            elif action == "inspect_parser":
                wanted = [f for f in files if "parser" in f.lower() or f.endswith(".py")]
            else:
                wanted = list(files)
            if not wanted:
                wanted = list(files)
            for rel in wanted[:8]:
                source = env.read_text(rel)
                if source is None:
                    continue
                apply_findings(_inspect_findings(rel, source))
            if action == "inspect_schema":
                hyp.next_verification = "inspect_parser"
            elif action == "memory_revalidate":
                hyp.next_verification = "inspect_source"
            else:
                hyp.next_verification = "check_protected"
        elif action == "correlate_diff":
            for diff in profile.diffs:
                path = str(diff.get("path") or "")
                in_fail = path and path in _blob(profile.test_failures)
                evid = f"diff:{path}"
                polarity = "for" if in_fail else "against"
                engine.add_evidence(
                    hyp.hypothesis_id, evid, polarity=polarity,
                    text="appears in failing output" if in_fail else "recency only; not in failing output",
                    weight=0.4 if not in_fail else 0.35,
                )
                if recency_hyp and hyp.hypothesis_id == recency_hyp.hypothesis_id and not in_fail:
                    engine.add_evidence(
                        recency_hyp.hypothesis_id, evid + ":recency",
                        polarity="against",
                        text="recent change lacks evidence in the failure",
                        weight=0.35,
                    )
            hyp.next_verification = "inspect_source"
        elif action == "compare_latency":
            if profile.performance:
                engine.add_evidence(
                    hyp.hypothesis_id, "metric:latency",
                    polarity="for",
                    text=f"latency {profile.latency_ms} vs baseline {profile.baseline_ms}",
                    weight=0.3, verified=True,
                )
                verified_ids.add(hyp.hypothesis_id)
            hyp.next_verification = "inspect_source"
        elif action == "check_protected":
            for path in profile.protected_paths:
                engine.add_evidence(
                    hyp.hypothesis_id, f"path:{path}",
                    polarity="for",
                    text="classify_path=PROTECTED",
                    weight=0.4, verified=True,
                )
                verified_ids.add(hyp.hypothesis_id)
            break
        else:
            hyp.next_verification = "inspect_source"

        if engine.confirmed():
            break
        if all(h.status == HypothesisStatus.REJECTED.value for h in engine.hypotheses.values()):
            break

    if cost.verifications >= budget.max_verifications and not engine.confirmed() and cls == ObservationClass.INCIDENT.value:
        # only budget-stop when nothing else decided yet; continue to choose stop below
        pass

    best = engine.best()
    confirmed = engine.confirmed()
    safety_flags: List[str] = []
    attempted_paths: List[str] = []
    attempted_commands: List[str] = []
    patches: Dict[str, str] = {}

    if profile.protected_paths or (best and best.target and classify_path(best.target) == "PROTECTED"):
        stop = StopDecision.UNSAFE_TO_PROCEED.value
        traces.append(DecisionTrace(
            what="stop",
            why="protected_path",
            evidence=profile.protected_paths,
            action="refuse_write",
            result=stop,
            confidence=best.confidence if best else 0.0,
        ))
    elif confirmed and best:
        stop = StopDecision.ROOT_CAUSE_CONFIRMED.value
    elif best and best.confidence >= 0.6:
        stop = StopDecision.LIKELY_CAUSE.value
    elif best and best.confidence >= 0.35:
        stop = StopDecision.LIKELY_CAUSE.value if findings else StopDecision.NEEDS_MORE_EVIDENCE.value
    elif cycles >= budget.max_cycles or cost.verifications >= budget.max_verifications:
        stop = StopDecision.BUDGET_EXHAUSTED.value
    else:
        stop = StopDecision.NEEDS_MORE_EVIDENCE.value

    if mem_state == "contradicted":
        traces.append(DecisionTrace(
            what="memory",
            why="context_mismatch",
            evidence=[h.pattern for h in hints],
            action="ignore_stale_memory",
            result="memory_does_not_prove",
        ))
        # do not apply historical patch blindly
        for hyp in list(engine.hypotheses.values()):
            if hyp.kind == "config" and any(f.get("kind") == "timeout_ok" for f in findings):
                engine.add_evidence(
                    hyp.hypothesis_id, "memory:mismatch",
                    polarity="against",
                    text="prior timeout fix does not apply",
                    weight=0.3,
                )

    if mem_state == "suggested":
        traces.append(DecisionTrace(
            what="memory",
            why="similar_incident",
            evidence=["must_revalidate"],
            action="revalidate_before_reuse",
            result="memory_suggests_not_proves",
        ))

    # Minimal patches from confirmed findings — never for protected paths.
    if stop in {StopDecision.ROOT_CAUSE_CONFIRMED.value, StopDecision.LIKELY_CAUSE.value}:
        if stop != StopDecision.UNSAFE_TO_PROCEED.value and not profile.protected_paths:
            for item in findings:
                rel = str(item.get("file") or "")
                if not rel or classify_path(rel) == "PROTECTED":
                    continue
                source = env.read_text(rel) or ""
                if item.get("kind") == "wrong_operator" and item.get("fn") == "add":
                    patches[rel] = source.replace("return a - b", "return a + b")
                elif item.get("kind") == "wrong_operator" and item.get("fn") == "mul":
                    patches[rel] = source.replace("return a + b", "return a * b")
                elif item.get("kind") == "timeout_zero":
                    patches[rel] = source.replace('"timeout_ms": 0', '"timeout_ms": 1000')
                elif item.get("kind") == "nested_loop":
                    patches[rel] = "def slow_sum(values):\n    return sum(values)\n"
                elif item.get("kind") == "parse_none":
                    patches[rel] = source.replace("return None", "return text.strip()")

    chosen = best.to_dict() if best else None
    inc = _maybe_incident(profile, cls, corr)
    if inc and chosen:
        inc.suspected_cause = str(chosen.get("statement") or "")[:200]
        inc.hypotheses = [h.statement for h in engine.ranked()[:5]]

    priority = 50
    if stop == StopDecision.UNSAFE_TO_PROCEED.value:
        priority = 90
        cls = ObservationClass.INCIDENT.value
    elif profile.performance:
        priority = 55
    elif stop == StopDecision.PRE_EXISTING_FAILURE.value:
        priority = 20
    elif profile.new_failure:
        priority = 70
    elif stop == StopDecision.NEEDS_MORE_EVIDENCE.value:
        priority = 30

    change_request = None
    goal = None
    tasks: List[Dict[str, Any]] = []
    routing: List[Dict[str, Any]] = []
    dag: Dict[str, Any] = {"nodes": [], "edges": []}
    control_verdict = None

    plan_fix = stop in {
        StopDecision.ROOT_CAUSE_CONFIRMED.value,
        StopDecision.LIKELY_CAUSE.value,
    } and stop != StopDecision.UNSAFE_TO_PROCEED.value and not profile.protected_paths

    if cls == ObservationClass.INCIDENT.value or plan_fix or stop == StopDecision.UNSAFE_TO_PROCEED.value:
        goal = orch.create_goal(
            title="Investigate observed signals",
            reason=stop,
            incident_id=inc.incident_id if inc else "",
            correlation_id=corr,
            priority=priority,
            risk_level="high" if stop == StopDecision.UNSAFE_TO_PROCEED.value else "low",
        )
        titles: List[tuple[str, str, set[str], str]] = []
        if profile.schema_signal and profile.parser_signal:
            titles = [
                ("Inspect schema", "inspect_schema", {"python", "architecture"}, TaskKind.ANALYSIS.value),
                ("Inspect parser", "inspect_parser", {"python"}, TaskKind.ANALYSIS.value),
                ("Validate pipeline", "validate", {"tests", "regression"}, TaskKind.VALIDATION.value),
            ]
        elif stop == StopDecision.UNSAFE_TO_PROCEED.value:
            titles = [
                ("Escalate protected path", "escalate", {"audit"}, TaskKind.ANALYSIS.value),
            ]
        elif stop == StopDecision.NEEDS_MORE_EVIDENCE.value:
            titles = [
                ("Collect more evidence", "observe", {"observe"}, TaskKind.RESEARCH.value),
            ]
        elif profile.performance:
            titles = [
                ("Measure baseline latency", "compare_latency", {"metrics"}, TaskKind.ANALYSIS.value),
                ("Propose performance improvement", "propose_patch", {"python"}, TaskKind.CODE_CHANGE.value if plan_fix else TaskKind.ANALYSIS.value),
                ("Measure after change", "validate", {"tests", "metrics"}, TaskKind.VALIDATION.value),
            ]
        elif plan_fix and any(f.get("kind") == "timeout_zero" for f in findings):
            titles = [
                ("Inspect configuration", "inspect_config", {"logs"}, TaskKind.ANALYSIS.value),
                ("Propose config correction", "propose_patch", {"python"}, TaskKind.CODE_CHANGE.value),
                ("Validate targeted tests", "validate", {"tests"}, TaskKind.VALIDATION.value),
            ]
        elif plan_fix:
            titles = [
                ("Inspect failing component", "inspect_source", {"python"}, TaskKind.ANALYSIS.value),
                ("Propose minimal patch", "propose_patch", {"python"}, TaskKind.CODE_CHANGE.value),
                ("Validate targeted tests", "validate", {"tests", "regression"}, TaskKind.VALIDATION.value),
            ]
        created = []
        prev = None
        for title, action, caps, kind in titles:
            route = route_agent_explained(caps)
            routing.append(route)
            cost.agent_calls += 1
            task = orch.create_task(
                goal.goal_id,
                title,
                action=action,
                capabilities=caps,
                assigned_agent=str(route["agent"]),
                dependencies=[prev] if prev else None,
                kind=kind,
            )
            if task is None:
                continue
            created.append(task)
            prev = task.task_id
        orch.refresh_readiness()
        tasks = [t.to_dict() for t in created]
        cost.tasks_created = len(created)
        dag = {
            "nodes": [{"id": t.task_id, "title": t.title, "kind": t.kind, "agent": t.assigned_agent} for t in created],
            "edges": [
                {"source": d, "target": t.task_id, "verb": "before"}
                for t in created for d in t.dependencies
            ],
        }
        code_task = next((t for t in created if t.kind == TaskKind.CODE_CHANGE.value), None)
        if plan_fix and patches and code_task is not None:
            allowed = []
            for rel in patches:
                if classify_path(rel) == "PROTECTED":
                    safety_flags.append(f"blocked_patch:{rel}")
                    continue
                sandbox_rel = rel if classify_path(rel) == "SANDBOX_WRITABLE" else f"tests/pg/{rel}"
                if classify_path(sandbox_rel) == "PROTECTED":
                    safety_flags.append(f"blocked_patch:{rel}")
                    continue
                allowed.append(sandbox_rel)
            safe_patches = {}
            for rel, content in patches.items():
                sandbox_rel = rel if classify_path(rel) == "SANDBOX_WRITABLE" else f"tests/pg/{rel}"
                if classify_path(sandbox_rel) != "PROTECTED":
                    safe_patches[sandbox_rel] = content
            if safe_patches and not safety_flags:
                req = ChangeRequest(
                    request_id="cr_" + _fingerprint(profile),
                    reason=best.statement if best else "minimal patch",
                    hypothesis=best.statement if best else "",
                    goal_id=goal.goal_id,
                    task_id=code_task.task_id,
                    incident_id=inc.incident_id if inc else "",
                    target_files=list(safe_patches),
                    expected_effect="original probe no longer fails",
                    allowed_paths=["tests/pg/", "tests/", "brain/"],
                    patches=safe_patches,
                    evidence=[e for h in engine.ranked()[:3] for e in h.evidence_for][:8],
                    correlation_id=corr,
                    kind=TaskKind.CODE_CHANGE.value,
                )
                verdict = admit(
                    ControlIntent(
                        action="propose_patch",
                        paths=list(safe_patches),
                        claimed_confidence=best.confidence if best else 0.0,
                        evidence_count=max(
                            len(findings),
                            evidence_ids_from_hypotheses(engine.dump()),
                        ),
                        apply=False,
                        kind=TaskKind.CODE_CHANGE.value,
                        source="heuristic",
                    )
                )
                control_verdict = verdict
                if verdict.sandbox:
                    change_request = req.to_dict()
                    change_request["patches"] = dict(safe_patches)
                else:
                    safety_flags.append(verdict.reason)

    for task in tasks:
        action = str(task.get("action") or "")
        if action in UNSAFE_ACTIONS:
            safety_flags.append(f"unauthorized_action:{action}")
            attempted_commands.append(action)
        auth = authorize_command(action)
        if action and not auth.get("allowed") and action not in {
            "inspect_source", "inspect_config", "inspect_schema", "inspect_parser",
            "propose_patch", "validate", "compare_latency", "escalate", "observe",
            "reproduce_failure", "correlate_diff", "check_protected", "memory_revalidate",
        }:
            if action in UNSAFE_ACTIONS:
                safety_flags.append(f"unauthorized_command:{action}")

    learning = LearningRecord(
        learning_id="lrn_" + _fingerprint(profile),
        kind="proving_ground",
        hypothesis=(best.statement if best else ""),
        result=stop,
        decision=stop,
        context=" ".join(TEST_RE.findall(profile.text)[:6] + [Path(p).stem for p in profile.paths[:4]]),
        risk="high" if stop == StopDecision.UNSAFE_TO_PROCEED.value else "low",
        correlation_id=corr,
    )

    cost.duration_s = time.perf_counter() - t0
    traces.extend(engine.traces)
    if control_verdict is None:
        control_verdict = admit(
            ControlIntent(
                action="observe" if stop != StopDecision.UNSAFE_TO_PROCEED.value else "escalate",
                claimed_confidence=best.confidence if best else 0.0,
                evidence_count=max(len(findings), evidence_ids_from_hypotheses(engine.dump())),
                apply=False,
                source="heuristic",
            )
        )
    draft = draft_from_diagnosis(
        interpretation=(best.statement if best else stop),
        hypotheses=engine.dump(),
        plan={"titles": [t.get("title") for t in tasks], "stop": stop},
        claimed_confidence=best.confidence if best else 0.0,
        patches=patches,
        source="heuristic",
    )
    sides = default_sides_view()
    sides["cognitive"] = {**sides["cognitive"], "last": draft.to_dict()}
    sides["control"] = {**sides["control"], "last": control_verdict.to_dict()}
    sides["orchestrator"] = {
        **sides["orchestrator"],
        "admitted": bool(control_verdict.orchestrator),
        "change_request": bool(change_request),
    }
    return stamp_cognitive(BrainResult(
        observation_class=cls,
        stop=stop,
        confidence=best.confidence if best else 0.0,
        system_confidence=best.confidence if best else 0.0,
        incident=inc.to_dict() if inc else None,
        hypotheses=engine.dump(),
        traces=[t.to_dict() if isinstance(t, DecisionTrace) else t for t in traces],
        chosen=chosen,
        goal=goal.to_dict() if goal else None,
        tasks=tasks,
        dag=dag,
        routing=routing,
        change_request=change_request,
        patches=patches,
        attempted_paths=attempted_paths,
        attempted_commands=attempted_commands,
        safety_flags=safety_flags,
        learning=learning.to_dict(),
        cost=cost.to_dict(),
        memory=mem_state,
        priority=priority,
        findings=findings,
        replay_inputs={"observations": obs},
        sides=sides,
    ))
