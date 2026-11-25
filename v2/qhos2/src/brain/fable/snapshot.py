"""InvestigationSnapshot — structured continuity across context windows."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from brain.models import new_id, utc_ts


@dataclass
class InvestigationSnapshot:
    snapshot_id: str = ""
    goal: str = ""
    incident: str = ""
    current_frame: str = ""
    confirmed_facts: List[str] = field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    rejected_hypotheses: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    uncertainties: List[str] = field(default_factory=list)
    task_dag: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)
    last_checkpoint: float = 0.0
    next_actions: List[str] = field(default_factory=list)
    source_revision: str = ""
    freshness: str = "unknown"
    write_pending: bool = False
    strategy: str = "fable_derived"

    def __post_init__(self) -> None:
        if not self.snapshot_id:
            self.snapshot_id = new_id("inv")
        if not self.last_checkpoint:
            self.last_checkpoint = utc_ts()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "InvestigationSnapshot":
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        return cls(**known)
