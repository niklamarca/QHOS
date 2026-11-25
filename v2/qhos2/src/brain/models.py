"""Serializable domain model for the Trader Brain.

Deterministic types only. LLM output is stored as a proposal, never as fact.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utc_ts() -> float:
    return time.time()


class AutonomyLevel(int, Enum):
    OBSERVE = 0
    ADVISE = 1
    INVESTIGATE = 2
    SANDBOX = 3
    GUARDED = 4
    EXPANDED = 5


class Causality(str, Enum):
    OBSERVED = "observed"
    CORRELATED = "correlated"
    SUSPECTED = "suspected"
    PROBABLE = "probable"
    CONFIRMED = "confirmed"


class TaskStatus(str, Enum):
    DISCOVERED = "discovered"
    PLANNED = "planned"
    QUEUED = "queued"
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    WAITING_VALIDATION = "waiting_validation"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ROLLED_BACK = "rolled_back"


class MemoryKind(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    DECISION = "decision"
    FAILURE = "failure"
    IMPROVEMENT = "improvement"
    AGENT = "agent"


class HealthTone(str, Enum):
    OK = "ok"
    WARNING = "warning"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class GoalOrigin(str, Enum):
    HEALTH = "health"
    IMPROVEMENT = "improvement"
    RESEARCH = "research"
    HUMAN = "human"
    SYSTEM = "system"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ProposalStatus(str, Enum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_INVESTIGATION = "needs_investigation"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ACKNOWLEDGED = "acknowledged"  # human said OK; still not merged


class TaskKind(str, Enum):
    ANALYSIS = "analysis"
    CODE_CHANGE = "code_change"
    VALIDATION = "validation"
    RESEARCH = "research"


class SandboxStatus(str, Enum):
    DISABLED = "disabled"
    CREATED = "created"
    BASELINE_RUNNING = "baseline_running"
    BASELINE_READY = "baseline_ready"
    WORKTREE_CREATED = "worktree_created"
    CHANGES_PENDING = "changes_pending"
    CHANGES_APPLIED = "changes_applied"
    VALIDATING = "validating"
    VALIDATED = "validated"
    FAILED = "failed"
    READY_FOR_REVIEW = "ready_for_review"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CLEANED = "cleaned"
    INTERRUPTED = "interrupted"
    NEEDS_INVESTIGATION = "needs_investigation"


IN_FLIGHT_SANDBOX = {
    SandboxStatus.BASELINE_RUNNING,
    SandboxStatus.WORKTREE_CREATED,
    SandboxStatus.CHANGES_PENDING,
    SandboxStatus.CHANGES_APPLIED,
    SandboxStatus.VALIDATING,
}

SANDBOX_TRANSITIONS: dict[str, set[str]] = {
    SandboxStatus.DISABLED.value: {SandboxStatus.CLEANED.value},
    SandboxStatus.CREATED.value: {
        SandboxStatus.BASELINE_RUNNING.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.CLEANED.value,
        SandboxStatus.INTERRUPTED.value,
    },
    SandboxStatus.BASELINE_RUNNING.value: {
        SandboxStatus.BASELINE_READY.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.INTERRUPTED.value,
    },
    SandboxStatus.BASELINE_READY.value: {
        SandboxStatus.WORKTREE_CREATED.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.CLEANED.value,
    },
    SandboxStatus.WORKTREE_CREATED.value: {
        SandboxStatus.CHANGES_PENDING.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.CLEANED.value,
        SandboxStatus.INTERRUPTED.value,
    },
    SandboxStatus.CHANGES_PENDING.value: {
        SandboxStatus.CHANGES_APPLIED.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.CLEANED.value,
        SandboxStatus.INTERRUPTED.value,
    },
    SandboxStatus.CHANGES_APPLIED.value: {
        SandboxStatus.VALIDATING.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.INTERRUPTED.value,
    },
    SandboxStatus.VALIDATING.value: {
        SandboxStatus.VALIDATED.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.NEEDS_INVESTIGATION.value,
        SandboxStatus.INTERRUPTED.value,
    },
    SandboxStatus.VALIDATED.value: {
        SandboxStatus.READY_FOR_REVIEW.value,
        SandboxStatus.FAILED.value,
        SandboxStatus.NEEDS_INVESTIGATION.value,
    },
    SandboxStatus.READY_FOR_REVIEW.value: {
        SandboxStatus.REJECTED.value,
        SandboxStatus.EXPIRED.value,
        SandboxStatus.CLEANED.value,
    },
    SandboxStatus.FAILED.value: {SandboxStatus.CLEANED.value, SandboxStatus.REJECTED.value},
    SandboxStatus.REJECTED.value: {SandboxStatus.CLEANED.value},
    SandboxStatus.NEEDS_INVESTIGATION.value: {
        SandboxStatus.FAILED.value,
        SandboxStatus.CLEANED.value,
        SandboxStatus.REJECTED.value,
    },
    SandboxStatus.INTERRUPTED.value: {SandboxStatus.CLEANED.value, SandboxStatus.FAILED.value},
    SandboxStatus.EXPIRED.value: {SandboxStatus.CLEANED.value},
    SandboxStatus.CLEANED.value: set(),
}


def can_transition(current: str, nxt: str) -> bool:
    return nxt in SANDBOX_TRANSITIONS.get(str(current), set())


TERMINAL_TASK = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.ROLLED_BACK,
}


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass
class Evidence:
    signal: str
    source: str
    detail: str = ""
    weight: float = 1.0
    observed_at: float = field(default_factory=utc_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.signal,
            "source": self.source,
            "detail": self.detail,
            "weight": float(self.weight),
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Evidence":
        return cls(
            signal=str(raw.get("signal") or ""),
            source=str(raw.get("source") or ""),
            detail=str(raw.get("detail") or ""),
            weight=float(raw.get("weight") or 0.0),
            observed_at=float(raw.get("observed_at") or utc_ts()),
        )


@dataclass
class Confidence:
    """Score in [0, 1] that must be justified by evidence, never guessed."""

    score: float
    evidence: List[Evidence] = field(default_factory=list)
    method: str = "weighted_signals"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(max(0.0, min(1.0, float(self.score))), 4),
            "method": self.method,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Confidence":
        ev = raw.get("evidence") or []
        return cls(
            score=float(raw.get("score") or 0.0),
            method=str(raw.get("method") or "weighted_signals"),
            evidence=[Evidence.from_dict(x) for x in ev if isinstance(x, dict)],
        )


def confidence_from_evidence(evidence: List[Evidence], *, cap: float = 0.95) -> Confidence:
    """Deterministic confidence: saturating sum of normalised weights.

    Empty evidence → 0. Never returns 1.0 from this helper (cap default 0.95)
    because the Brain does not confirm causes without an explicit human mark.
    """
    if not evidence:
        return Confidence(score=0.0, evidence=[], method="weighted_signals")
    total = sum(max(0.0, item.weight) for item in evidence)
    score = cap * (1.0 - pow(2.718281828, -total / max(len(evidence), 1)))
    return Confidence(score=min(cap, max(0.0, score)), evidence=list(evidence))


@dataclass
class Incident:
    incident_id: str
    title: str
    component: str
    fingerprint: str
    severity: str = "warning"
    status: str = "open"
    causality: Causality = Causality.OBSERVED
    suspected_cause: str = ""
    confidence: Confidence = field(default_factory=lambda: Confidence(0.0))
    goal_id: str = ""
    hypotheses: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)
    created_at: float = field(default_factory=utc_ts)
    updated_at: float = field(default_factory=utc_ts)
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "title": self.title,
            "component": self.component,
            "fingerprint": self.fingerprint,
            "severity": self.severity,
            "status": self.status,
            "causality": _enum_value(self.causality),
            "suspected_cause": self.suspected_cause,
            "confidence": self.confidence.to_dict(),
            "goal_id": self.goal_id,
            "hypotheses": list(self.hypotheses),
            "evidence": [item.to_dict() for item in self.evidence],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Incident":
        causality = raw.get("causality") or Causality.OBSERVED.value
        try:
            causal = Causality(causality)
        except ValueError:
            causal = Causality.OBSERVED
        conf_raw = raw.get("confidence") if isinstance(raw.get("confidence"), dict) else {}
        return cls(
            incident_id=str(raw.get("incident_id") or new_id("inc")),
            title=str(raw.get("title") or ""),
            component=str(raw.get("component") or ""),
            fingerprint=str(raw.get("fingerprint") or ""),
            severity=str(raw.get("severity") or "warning"),
            status=str(raw.get("status") or "open"),
            causality=causal,
            suspected_cause=str(raw.get("suspected_cause") or ""),
            confidence=Confidence.from_dict(conf_raw),
            goal_id=str(raw.get("goal_id") or ""),
            hypotheses=[str(x) for x in (raw.get("hypotheses") or []) if x],
            evidence=[
                Evidence.from_dict(x) for x in (raw.get("evidence") or [])
                if isinstance(x, dict)
            ],
            created_at=float(raw.get("created_at") or utc_ts()),
            updated_at=float(raw.get("updated_at") or utc_ts()),
            correlation_id=str(raw.get("correlation_id") or ""),
        )


@dataclass
class Goal:
    goal_id: str
    title: str
    reason: str
    priority: int = 50
    risk_level: str = "low"
    status: str = "open"
    incident_id: str = ""
    origin: str = GoalOrigin.SYSTEM.value
    progress: float = 0.0
    correlation_id: str = ""
    created_at: float = field(default_factory=utc_ts)
    updated_at: float = field(default_factory=utc_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "reason": self.reason,
            "priority": int(self.priority),
            "risk_level": self.risk_level,
            "status": self.status,
            "incident_id": self.incident_id,
            "origin": self.origin,
            "progress": float(self.progress),
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Goal":
        return cls(
            goal_id=str(raw.get("goal_id") or new_id("goal")),
            title=str(raw.get("title") or ""),
            reason=str(raw.get("reason") or ""),
            priority=int(raw.get("priority") or 50),
            risk_level=str(raw.get("risk_level") or "low"),
            status=str(raw.get("status") or "open"),
            incident_id=str(raw.get("incident_id") or ""),
            origin=str(raw.get("origin") or GoalOrigin.SYSTEM.value),
            progress=float(raw.get("progress") or 0.0),
            correlation_id=str(raw.get("correlation_id") or ""),
            created_at=float(raw.get("created_at") or utc_ts()),
            updated_at=float(raw.get("updated_at") or utc_ts()),
        )


@dataclass
class Task:
    task_id: str
    title: str
    description: str = ""
    parent_goal: str = ""
    parent_task: str = ""
    status: TaskStatus = TaskStatus.PLANNED
    priority: int = 50
    risk_level: str = "low"
    assigned_agent: str = "system"
    action: str = "observe"
    dependencies: List[str] = field(default_factory=list)
    fingerprint: str = ""
    retry_count: int = 0
    max_retries: int = 2
    approval_required: bool = False
    approved: bool = False
    resource: str = ""
    depth: int = 0
    input_context: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = ""
    kind: str = TaskKind.ANALYSIS.value
    created_at: float = field(default_factory=utc_ts)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "parent_goal": self.parent_goal,
            "parent_task": self.parent_task,
            "status": _enum_value(self.status),
            "priority": int(self.priority),
            "risk_level": self.risk_level,
            "assigned_agent": self.assigned_agent,
            "action": self.action,
            "kind": self.kind,
            "dependencies": list(self.dependencies),
            "fingerprint": self.fingerprint,
            "retry_count": int(self.retry_count),
            "max_retries": int(self.max_retries),
            "approval_required": bool(self.approval_required),
            "approved": bool(self.approved),
            "resource": self.resource,
            "depth": int(self.depth),
            "input_context": dict(self.input_context),
            "output": dict(self.output),
            "correlation_id": self.correlation_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Task":
        status_raw = raw.get("status") or TaskStatus.PLANNED.value
        try:
            status = TaskStatus(status_raw)
        except ValueError:
            status = TaskStatus.PLANNED
        deps = raw.get("dependencies") or []
        return cls(
            task_id=str(raw.get("task_id") or new_id("task")),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            parent_goal=str(raw.get("parent_goal") or ""),
            parent_task=str(raw.get("parent_task") or ""),
            status=status,
            priority=int(raw.get("priority") or 50),
            risk_level=str(raw.get("risk_level") or "low"),
            assigned_agent=str(raw.get("assigned_agent") or "system"),
            action=str(raw.get("action") or "observe"),
            dependencies=[str(x) for x in deps if x],
            fingerprint=str(raw.get("fingerprint") or ""),
            retry_count=int(raw.get("retry_count") or 0),
            max_retries=int(raw.get("max_retries") or 2),
            approval_required=bool(raw.get("approval_required")),
            approved=bool(raw.get("approved")),
            resource=str(raw.get("resource") or ""),
            depth=int(raw.get("depth") or 0),
            input_context=dict(raw.get("input_context") or {}),
            output=dict(raw.get("output") or {}),
            correlation_id=str(raw.get("correlation_id") or ""),
            kind=str(raw.get("kind") or TaskKind.ANALYSIS.value),
            created_at=float(raw.get("created_at") or utc_ts()),
            started_at=raw.get("started_at"),
            finished_at=raw.get("finished_at"),
        )


@dataclass
class AuditEvent:
    audit_id: str
    kind: str
    actor: str
    subject: str
    correlation_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=utc_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "kind": self.kind,
            "actor": self.actor,
            "subject": self.subject,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload),
            "ts": self.ts,
        }


@dataclass
class MemoryRecord:
    memory_id: str
    kind: MemoryKind
    title: str
    body: str
    tags: List[str] = field(default_factory=list)
    correlation_id: str = ""
    ts: float = field(default_factory=utc_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": _enum_value(self.kind),
            "title": self.title,
            "body": self.body,
            "tags": list(self.tags),
            "correlation_id": self.correlation_id,
            "ts": self.ts,
        }


@dataclass
class Approval:
    approval_id: str
    action: str
    reason: str
    risk: str = "low"
    component: str = ""
    task_id: str = ""
    status: str = ApprovalStatus.PENDING.value
    requested_by: str = "brain"
    requested_at: float = field(default_factory=utc_ts)
    evidence: List[str] = field(default_factory=list)
    sandbox_result: str = ""
    rollback_plan: str = "discard sandbox worktree; no production revert needed"
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approval_id": self.approval_id,
            "action": self.action,
            "reason": self.reason,
            "risk": self.risk,
            "component": self.component,
            "task_id": self.task_id,
            "status": self.status,
            "requested_by": self.requested_by,
            "requested_at": self.requested_at,
            "evidence": list(self.evidence),
            "sandbox_result": self.sandbox_result,
            "rollback_plan": self.rollback_plan,
            "correlation_id": self.correlation_id,
            "applies_to_production": False,
            "applies_to_trading": False,
        }


@dataclass
class Decision:
    decision_id: str
    what: str
    why: str
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.0
    risk: str = "low"
    action: str = "observe"
    status: str = "shadow"
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "what": self.what,
            "why": self.why,
            "evidence": list(self.evidence),
            "confidence": float(self.confidence),
            "risk": self.risk,
            "action": self.action,
            "status": self.status,
            "correlation_id": self.correlation_id,
        }


@dataclass
class ChangeRequest:
    request_id: str
    reason: str
    hypothesis: str = ""
    goal_id: str = ""
    task_id: str = ""
    incident_id: str = ""
    target_files: List[str] = field(default_factory=list)
    expected_effect: str = ""
    risk_level: str = "low"
    requested_by: str = "brain"
    evidence: List[str] = field(default_factory=list)
    validation_plan: List[str] = field(default_factory=lambda: ["syntax", "targeted", "brain"])
    rollback_plan: str = "discard isolated worktree; never merge"
    allowed_paths: List[str] = field(default_factory=list)
    patches: Dict[str, str] = field(default_factory=dict)
    correlation_id: str = ""
    kind: str = TaskKind.CODE_CHANGE.value

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "incident_id": self.incident_id,
            "reason": self.reason,
            "hypothesis": self.hypothesis,
            "target_files": list(self.target_files),
            "expected_effect": self.expected_effect,
            "risk_level": self.risk_level,
            "requested_by": self.requested_by,
            "evidence": list(self.evidence),
            "validation_plan": list(self.validation_plan),
            "rollback_plan": self.rollback_plan,
            "allowed_paths": list(self.allowed_paths),
            "patch_files": sorted(self.patches),
            "correlation_id": self.correlation_id,
            "kind": self.kind,
        }


@dataclass
class ValidationResult:
    status: str
    baseline_passed: int = 0
    current_passed: int = 0
    preexisting_failures: List[str] = field(default_factory=list)
    new_failures: List[str] = field(default_factory=list)
    fixed_failures: List[str] = field(default_factory=list)
    unchanged_failures: List[str] = field(default_factory=list)
    new_passes: List[str] = field(default_factory=list)
    collection_errors: List[str] = field(default_factory=list)
    regressions: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    diff_summary: str = ""
    commands_executed: List[str] = field(default_factory=list)
    duration_s: float = 0.0
    evidence: List[str] = field(default_factory=list)
    sandbox_success: bool = False
    production_success: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "baseline_passed": int(self.baseline_passed),
            "current_passed": int(self.current_passed),
            "preexisting_failures": list(self.preexisting_failures),
            "new_failures": list(self.new_failures),
            "fixed_failures": list(self.fixed_failures),
            "unchanged_failures": list(self.unchanged_failures),
            "new_passes": list(self.new_passes),
            "collection_errors": list(self.collection_errors),
            "regressions": list(self.regressions),
            "changed_files": list(self.changed_files),
            "diff_summary": self.diff_summary,
            "commands_executed": list(self.commands_executed),
            "duration_s": float(self.duration_s),
            "evidence": list(self.evidence),
            "sandbox_success": bool(self.sandbox_success),
            "production_success": False,
        }


@dataclass
class LearningRecord:
    learning_id: str
    kind: str
    proposal_id: str = ""
    sandbox_run_id: str = ""
    hypothesis: str = ""
    result: str = ""
    decision: str = ""
    human_feedback: str = ""
    context: str = ""
    risk: str = "low"
    sandbox_success: bool = False
    production_success: bool = False
    correlation_id: str = ""
    ts: float = field(default_factory=utc_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "learning_id": self.learning_id,
            "kind": self.kind,
            "proposal_id": self.proposal_id,
            "sandbox_run_id": self.sandbox_run_id,
            "hypothesis": self.hypothesis,
            "result": self.result,
            "decision": self.decision,
            "human_feedback": self.human_feedback,
            "context": self.context,
            "risk": self.risk,
            "sandbox_success": bool(self.sandbox_success),
            "production_success": False,
            "applied": False,
            "correlation_id": self.correlation_id,
            "ts": self.ts,
        }


@dataclass
class ChangeProposal:
    proposal_id: str
    goal_id: str = ""
    sandbox_run_id: str = ""
    request_id: str = ""
    title: str = ""
    why: str = ""
    what: str = ""
    expected_benefit: str = ""
    files_changed: int = 0
    changed_files: List[str] = field(default_factory=list)
    tests_summary: str = ""
    benchmark_summary: str = ""
    diff_summary: str = ""
    baseline: Dict[str, Any] = field(default_factory=dict)
    new_failures: List[str] = field(default_factory=list)
    fixed_failures: List[str] = field(default_factory=list)
    rollback_plan: str = "discard isolated worktree; never merge"
    evidence: List[str] = field(default_factory=list)
    audit_ids: List[str] = field(default_factory=list)
    risk: str = "low"
    confidence: float = 0.0
    status: str = ProposalStatus.DRAFT.value
    human_decision: str = ""
    auto_apply: bool = False
    correlation_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "goal_id": self.goal_id,
            "sandbox_run_id": self.sandbox_run_id,
            "request_id": self.request_id,
            "title": self.title,
            "why": self.why,
            "what": self.what,
            "expected_benefit": self.expected_benefit,
            "files_changed": int(self.files_changed),
            "changed_files": list(self.changed_files),
            "tests_summary": self.tests_summary,
            "benchmark_summary": self.benchmark_summary,
            "diff_summary": self.diff_summary,
            "baseline": dict(self.baseline),
            "new_failures": list(self.new_failures),
            "fixed_failures": list(self.fixed_failures),
            "rollback_plan": self.rollback_plan,
            "evidence": list(self.evidence),
            "audit_ids": list(self.audit_ids),
            "risk": self.risk,
            "confidence": float(self.confidence),
            "status": self.status,
            "human_decision": self.human_decision,
            "auto_apply": False,
            "merge": False,
            "deploy": False,
            "push": False,
            "correlation_id": self.correlation_id,
        }


@dataclass
class SandboxRun:
    run_id: str
    goal_id: str = ""
    task_id: str = ""
    request_id: str = ""
    base_commit: str = ""
    branch: str = ""
    worktree: str = ""
    status: str = SandboxStatus.DISABLED.value
    files_changed: List[str] = field(default_factory=list)
    commands_executed: List[str] = field(default_factory=list)
    tests_run: int = 0
    tests_result: Dict[str, Any] = field(default_factory=dict)
    benchmark_result: Dict[str, Any] = field(default_factory=dict)
    diff_summary: str = ""
    baseline: Dict[str, Any] = field(default_factory=dict)
    validation_result: Dict[str, Any] = field(default_factory=dict)
    risk: str = "low"
    validation: str = ""
    rollback_status: str = "not_needed"
    cleanup_status: str = "idle"
    fingerprint: str = ""
    changes_applied: bool = False
    proposal_id: str = ""
    correlation_id: str = ""
    fail_reason: str = ""
    created_at: float = field(default_factory=utc_ts)
    updated_at: float = field(default_factory=utc_ts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "base_commit": self.base_commit,
            "branch": self.branch,
            "worktree": self.worktree,
            "status": self.status,
            "files_changed": list(self.files_changed),
            "commands_executed": list(self.commands_executed),
            "tests_run": int(self.tests_run),
            "tests_result": dict(self.tests_result),
            "benchmark_result": dict(self.benchmark_result),
            "diff_summary": self.diff_summary,
            "baseline": dict(self.baseline),
            "validation_result": dict(self.validation_result),
            "risk": self.risk,
            "validation": self.validation,
            "rollback_status": self.rollback_status,
            "cleanup_status": self.cleanup_status,
            "fingerprint": self.fingerprint,
            "changes_applied": bool(self.changes_applied),
            "proposal_id": self.proposal_id,
            "correlation_id": self.correlation_id,
            "fail_reason": self.fail_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "auto_merge": False,
            "auto_push": False,
            "auto_deploy": False,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SandboxRun":
        return cls(
            run_id=str(raw.get("run_id") or new_id("sbx")),
            goal_id=str(raw.get("goal_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            request_id=str(raw.get("request_id") or ""),
            base_commit=str(raw.get("base_commit") or ""),
            branch=str(raw.get("branch") or ""),
            worktree=str(raw.get("worktree") or ""),
            status=str(raw.get("status") or SandboxStatus.DISABLED.value),
            files_changed=[str(x) for x in (raw.get("files_changed") or []) if x],
            commands_executed=[str(x) for x in (raw.get("commands_executed") or []) if x],
            tests_run=int(raw.get("tests_run") or 0),
            tests_result=dict(raw.get("tests_result") or {}),
            benchmark_result=dict(raw.get("benchmark_result") or {}),
            diff_summary=str(raw.get("diff_summary") or ""),
            baseline=dict(raw.get("baseline") or {}),
            validation_result=dict(raw.get("validation_result") or {}),
            risk=str(raw.get("risk") or "low"),
            validation=str(raw.get("validation") or ""),
            rollback_status=str(raw.get("rollback_status") or "not_needed"),
            cleanup_status=str(raw.get("cleanup_status") or "idle"),
            fingerprint=str(raw.get("fingerprint") or ""),
            changes_applied=bool(raw.get("changes_applied")),
            proposal_id=str(raw.get("proposal_id") or ""),
            correlation_id=str(raw.get("correlation_id") or ""),
            fail_reason=str(raw.get("fail_reason") or ""),
            created_at=float(raw.get("created_at") or utc_ts()),
            updated_at=float(raw.get("updated_at") or utc_ts()),
        )


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    CONFIRMED = "confirmed"


class StopDecision(str, Enum):
    ROOT_CAUSE_CONFIRMED = "ROOT_CAUSE_CONFIRMED"
    LIKELY_CAUSE = "LIKELY_CAUSE"
    NEEDS_MORE_EVIDENCE = "NEEDS_MORE_EVIDENCE"
    NO_ACTION_REQUIRED = "NO_ACTION_REQUIRED"
    PRE_EXISTING_FAILURE = "PRE_EXISTING_FAILURE"
    UNSAFE_TO_PROCEED = "UNSAFE_TO_PROCEED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class ObservationClass(str, Enum):
    INCIDENT = "incident"
    WARNING = "warning"
    ANOMALY = "anomaly"
    INFORMATION = "information"
    DUPLICATE = "duplicate"
    PREEXISTING = "preexisting"
    NOISE = "noise"


@dataclass
class Hypothesis:
    hypothesis_id: str
    statement: str
    evidence_for: List[str] = field(default_factory=list)
    evidence_against: List[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = HypothesisStatus.PROPOSED.value
    next_verification: str = ""
    target: str = ""
    kind: str = "code"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "evidence_for": list(self.evidence_for),
            "evidence_against": list(self.evidence_against),
            "confidence": round(float(self.confidence), 4),
            "status": self.status,
            "next_verification": self.next_verification,
            "target": self.target,
            "kind": self.kind,
        }


@dataclass
class DecisionTrace:
    """Auditable decision. Never stores free-form chain-of-thought."""

    what: str
    why: str
    evidence: List[str] = field(default_factory=list)
    hypothesis_id: str = ""
    action: str = ""
    result: str = ""
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "what": self.what,
            "why": self.why,
            "evidence": list(self.evidence),
            "hypothesis_id": self.hypothesis_id,
            "action": self.action,
            "result": self.result,
            "confidence": float(self.confidence),
        }


@dataclass
class DiagnosticBudget:
    max_cycles: int = 6
    max_hypotheses: int = 5
    max_tasks: int = 8
    max_agent_calls: int = 4
    max_verifications: int = 6
    max_duration_s: float = 30.0


class CognitiveMode(str, Enum):
    DETERMINISTIC_ONLY = "deterministic_only"
    HYBRID = "hybrid"
    LLM_ASSISTED = "llm_assisted"


@dataclass
class VerificationRequest:
    request_id: str
    hypothesis_id: str = ""
    action_type: str = "inspect_file"
    target: str = ""
    reason: str = ""
    expected_information: str = ""
    risk: str = "low"
    estimated_cost: float = 1.0
    disconfirming: bool = False
    expected_information_gain: str = "MEDIUM"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "hypothesis_id": self.hypothesis_id,
            "action_type": self.action_type,
            "target": self.target,
            "reason": self.reason,
            "expected_information": self.expected_information,
            "risk": self.risk,
            "estimated_cost": float(self.estimated_cost),
            "disconfirming": bool(self.disconfirming),
            "expected_information_gain": str(self.expected_information_gain or "MEDIUM").upper(),
        }
