"""Structured Fable reasoning output. Schema-validated. No chain-of-thought."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


REQUIRED_FIELDS = (
    "incident_assessment",
    "problem_frame",
    "cognitive_effort",
    "context_summary",
    "memory_used",
    "hypotheses",
    "uncertainties",
    "verification_requests",
    "disconfirming_checks",
    "parallel_tasks",
    "agent_tasks",
    "evidence_ids",
    "decision",
    "system_confidence",
    "model_confidence",
    "stop_decision",
    "plan",
    "verification_result",
    "budget_used",
    "completion_state",
)


@dataclass
class FableReasoningResult:
    incident_assessment: str = ""
    problem_frame: str = ""
    cognitive_effort: str = "LOW"
    context_summary: Dict[str, Any] = field(default_factory=dict)
    memory_used: List[Dict[str, Any]] = field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    verification_requests: List[Dict[str, Any]] = field(default_factory=list)
    disconfirming_checks: List[Dict[str, Any]] = field(default_factory=list)
    parallel_tasks: List[Dict[str, Any]] = field(default_factory=list)
    agent_tasks: List[Dict[str, Any]] = field(default_factory=list)
    evidence_ids: List[str] = field(default_factory=list)
    decision: str = ""
    system_confidence: float = 0.0
    model_confidence: float = 0.0
    stop_decision: str = ""
    plan: List[str] = field(default_factory=list)
    verification_result: Dict[str, Any] = field(default_factory=dict)
    budget_used: Dict[str, Any] = field(default_factory=dict)
    completion_state: str = "NOT_VERIFIED"
    fresh_verifier: Optional[Dict[str, Any]] = None
    goal_boundary: Optional[Dict[str, Any]] = None
    snapshot_id: str = ""
    strategy: str = "fable_derived"
    chain_of_thought: None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_assessment": self.incident_assessment[:400],
            "problem_frame": self.problem_frame[:400],
            "cognitive_effort": self.cognitive_effort,
            "context_summary": dict(self.context_summary),
            "memory_used": list(self.memory_used)[:12],
            "hypotheses": list(self.hypotheses)[:8],
            "uncertainties": list(self.uncertainties)[:8],
            "verification_requests": list(self.verification_requests)[:8],
            "disconfirming_checks": list(self.disconfirming_checks)[:8],
            "parallel_tasks": list(self.parallel_tasks)[:8],
            "agent_tasks": list(self.agent_tasks)[:4],
            "evidence_ids": list(self.evidence_ids)[:16],
            "decision": self.decision[:240],
            "system_confidence": float(self.system_confidence),
            "model_confidence": float(self.model_confidence),
            "stop_decision": self.stop_decision,
            "plan": list(self.plan)[:8],
            "verification_result": dict(self.verification_result),
            "budget_used": dict(self.budget_used),
            "completion_state": self.completion_state,
            "fresh_verifier": self.fresh_verifier,
            "goal_boundary": self.goal_boundary,
            "snapshot_id": self.snapshot_id,
            "strategy": self.strategy,
            "chain_of_thought": None,
        }


def validate_fable_result(payload: Any) -> List[str]:
    if not isinstance(payload, dict):
        return ["not_a_dict"]
    missing = [k for k in REQUIRED_FIELDS if k not in payload]
    errors = [f"missing:{k}" for k in missing]
    if payload.get("chain_of_thought"):
        errors.append("cot_stored")
    effort = str(payload.get("cognitive_effort") or "")
    if effort and effort not in {"LOW", "MEDIUM", "HIGH", "XHIGH", "MAX"}:
        errors.append("bad_effort")
    return errors
