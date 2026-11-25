"""Read-only investigation created when a candidate crosses threshold."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from brain.models import new_id, utc_ts


ALLOWED_ACTIONS = frozenset({
    "inspect_logs", "inspect_metrics", "inspect_git", "inspect_tests",
    "inspect_graph", "inspect_source", "inspect_health", "query_memory",
})
DENIED_ACTIONS = frozenset({
    "patch", "restart", "write_config", "deploy", "trade", "apply", "push", "merge",
})


@dataclass
class Investigation:
    investigation_id: str
    candidate_id: str
    goal: str
    scope: str
    observations: List[str] = field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    memory_context: List[str] = field(default_factory=list)
    strategy: str = "deterministic"
    effort: str = "LOW"
    budget: Dict[str, Any] = field(default_factory=dict)
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "OPEN"
    traces: List[Dict[str, Any]] = field(default_factory=list)
    write_pending: bool = False
    read_only: bool = True

    def __post_init__(self) -> None:
        if not self.investigation_id:
            self.investigation_id = new_id("linv")

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "change_request": None, "sandbox": None, "read_only": True}


def open_investigation(candidate: Dict[str, Any], *, strategy: str, effort: str) -> Investigation:
    tasks = [
        {"action": "inspect_logs", "status": "READY"},
        {"action": "inspect_health", "status": "READY"},
    ]
    return Investigation(
        investigation_id=new_id("linv"),
        candidate_id=str(candidate.get("candidate_id") or ""),
        goal=f"investigate {candidate.get('component') or 'signal'}",
        scope="read_only",
        observations=list(candidate.get("observation_ids") or [])[:8],
        evidence=list(candidate.get("evidence") or [])[:8],
        strategy=strategy,
        effort=effort,
        budget={"tools": 6, "llm": 0, "writes": 0},
        tasks=tasks,
        traces=[{
            "what": "investigation_opened",
            "why": "candidate_threshold",
            "evidence": [str(candidate.get("fingerprint") or "")],
            "action": "observe",
            "result": "read_only",
            "ts": utc_ts(),
        }],
        write_pending=False,
        read_only=True,
    )


def action_allowed(action: str) -> bool:
    act = str(action or "").lower()
    if act in DENIED_ACTIONS:
        return False
    return act in ALLOWED_ACTIONS
