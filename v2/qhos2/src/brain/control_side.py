"""Control side of the Trader Brain.

Policies, permissions, validation, budgets, safety gates.
The only authority that may admit work to BrainOrchestrator.
Never calls an LLM. Never treats cognitive confidence as evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .policies import AutonomyPolicy, PolicyDecision, SENSITIVE_ACTIONS, SAFE_READ_ACTIONS
from .sandbox import SANDBOX_ENABLED, authorize_command, classify_path


WRITE_LIKE = frozenset({
    "propose_patch",
    "apply_change",
    "code_change",
    "patch",
    "write",
    "apply",
})


@dataclass
class ControlIntent:
    action: str
    paths: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    claimed_confidence: float = 0.0
    evidence_count: int = 0
    apply: bool = False
    kind: str = ""
    source: str = "heuristic"
    level: int = 2


@dataclass
class ControlVerdict:
    allowed: bool
    reason: str
    orchestrator: bool
    sandbox: bool
    claimed_confidence_ignored: bool = True
    policy: Optional[PolicyDecision] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "orchestrator": self.orchestrator,
            "sandbox": self.sandbox,
            "claimed_confidence_ignored": True,
            "level3_enabled": False,
            "sandbox_runtime_enabled": SANDBOX_ENABLED,
            "policy_reason": self.policy.reason if self.policy else "",
        }


def _deny(reason: str, *, policy: Optional[PolicyDecision] = None) -> ControlVerdict:
    return ControlVerdict(
        allowed=False,
        reason=reason,
        orchestrator=False,
        sandbox=False,
        policy=policy,
    )


def admit(intent: ControlIntent, *, policy: Optional[AutonomyPolicy] = None) -> ControlVerdict:
    """Fail-closed gate. Claimed LLM/committee confidence is ignored."""
    _ = float(intent.claimed_confidence)  # recorded by caller; never used for allow
    pol = policy or AutonomyPolicy(level=intent.level, shadow=True)
    action = str(intent.action or "observe").strip().lower()
    kind = str(intent.kind or "").strip().lower()

    if intent.apply:
        return _deny("apply_blocked")
    if SANDBOX_ENABLED:
        return _deny("refusing_enabled_runtime_sandbox")

    if action in SENSITIVE_ACTIONS or kind in SENSITIVE_ACTIONS:
        decision = pol.authorize(action or "apply_change", apply=False)
        return _deny("sensitive_action", policy=decision)

    for cmd in intent.commands:
        low = str(cmd).lower()
        if any(tok in low for tok in ("push", "merge", "deploy", "order", "oms", "live_trade")):
            return _deny("unauthorized_command")
        auth = authorize_command(cmd)
        if cmd and not auth.get("allowed") and low not in {
            "inspect_source", "inspect_config", "inspect_schema", "inspect_parser",
            "propose_patch", "validate", "compare_latency", "escalate", "observe",
            "reproduce_failure", "correlate_diff", "check_protected", "memory_revalidate",
            "investigate", "plan", "advise", "health", "audit",
        }:
            return _deny("command_not_allowlisted")

    for path in intent.paths:
        if classify_path(path) == "PROTECTED":
            return _deny("protected_path")

    write_like = action in WRITE_LIKE or kind in {"code_change", "code-change"}
    if write_like and intent.evidence_count < 1:
        return _deny("no_evidence")

    decision = pol.authorize(action if action in SAFE_READ_ACTIONS or write_like else "observe", apply=False)
    if write_like:
        # Shadow investigation may *propose* a patch; runtime still cannot apply.
        return ControlVerdict(
            allowed=True,
            reason="admitted_proposal",
            orchestrator=True,
            sandbox=True,
            policy=decision,
        )
    if not decision.allowed:
        return _deny(decision.reason, policy=decision)
    return ControlVerdict(
        allowed=True,
        reason="admitted_observe",
        orchestrator=True,
        sandbox=False,
        policy=decision,
    )


def evidence_ids_from_hypotheses(hypotheses: Sequence[Mapping[str, Any]]) -> int:
    n = 0
    for row in hypotheses:
        if not isinstance(row, dict):
            continue
        n += len(row.get("evidence_for") or [])
    return n
