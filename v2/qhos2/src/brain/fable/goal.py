"""Goal boundary, task contracts, continue-what-can-continue."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


@dataclass
class GoalBoundary:
    goal: str
    allowed_exploration: List[str] = field(default_factory=list)
    forbidden_expansion: List[str] = field(default_factory=list)
    completion_criteria: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def in_scope(self, action: str, target: str = "") -> bool:
        blob = f"{action} {target}".lower()
        for tok in self.forbidden_expansion:
            if tok.lower() in blob:
                return False
        return True


DEFAULT_FORBIDDEN = (
    "git push", "level 5", "sandbox_enabled", "refactor entire",
    "disable risk", "governance/control", "delete the test",
)


def default_boundary(goal: str) -> GoalBoundary:
    return GoalBoundary(
        goal=goal[:240],
        allowed_exploration=["inspect", "correlate", "reproduce"],
        forbidden_expansion=list(DEFAULT_FORBIDDEN),
        completion_criteria=["observed_evidence_or_honest_stop"],
    )


@dataclass
class TaskContract:
    what: str
    why: str
    input: str = ""
    scope: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    expected_output: str = ""
    done_when: str = ""
    verify_how: str = ""
    budget: Dict[str, Any] = field(default_factory=dict)
    status: str = "READY"
    depends_on: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def continue_independent(tasks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    blocked = [t for t in tasks if str(t.get("status") or "") in {"BLOCKED", "BLOCKED_NEEDS_EVIDENCE"}]
    ready = [t for t in tasks if str(t.get("status") or "READY") == "READY" and not t.get("depends_on")]
    return {
        "blocked": blocked,
        "ready": ready,
        "continue": bool(ready),
        "goal_blocked": bool(blocked) and not ready,
    }


def runtime_delegation_allowed() -> bool:
    return False
