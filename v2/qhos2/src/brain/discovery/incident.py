"""PotentialIncident — not every signal is an Incident."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from brain.models import new_id, utc_ts


INCIDENT_STATES = (
    "OBSERVED",
    "CANDIDATE",
    "INVESTIGATING",
    "SUPPORTED",
    "CONFIRMED",
    "DISMISSED",
    "DUPLICATE",
    "STALE",
    "INSUFFICIENT_EVIDENCE",
)

SIGNAL_CLASSES = (
    "ANOMALY",
    "WARNING",
    "POTENTIAL_INCIDENT",
    "CONFIRMED_INCIDENT",
    "IMPROVEMENT_SIGNAL",
    "NO_ACTION",
)


@dataclass
class IncidentOccurrence:
    """One observed instance of a root cause.

    Many occurrences support a single ``PotentialIncident``. Occurrences are
    evidence rows, never candidates in their own right.
    """

    occurrence_id: str
    root_id: str
    observation_id: str = ""
    component: str = ""
    instance: str = ""
    source: str = ""
    timestamp: float = 0.0
    severity: str = "low"
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.occurrence_id:
            self.occurrence_id = new_id("occ")
        if not self.timestamp:
            self.timestamp = utc_ts()

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "kind": "incident_occurrence", "candidate": False}


@dataclass
class PotentialIncident:
    candidate_id: str
    fingerprint: str
    component: str
    summary: str
    status: str = "CANDIDATE"
    signal_class: str = "POTENTIAL_INCIDENT"
    novelty: str = "NEW"
    first_seen: float = 0.0
    last_seen: float = 0.0
    count: int = 1
    score: float = 0.0
    severity: str = "low"
    evidence: List[str] = field(default_factory=list)
    observation_ids: List[str] = field(default_factory=list)
    strategy: str = "deterministic"
    effort: str = "LOW"
    # Root-cause grouping (rootfp-v1). Many occurrences, one candidate.
    root_id: str = ""
    root_title: str = ""
    root_tier: str = ""
    root_signature: Dict[str, Any] = field(default_factory=dict)
    occurrences: List[Dict[str, Any]] = field(default_factory=list)
    occurrence_count: int = 1
    affected_instances: int = 1
    affected_components: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    # Score explanation (discovery-score-v2).
    score_components: Dict[str, Any] = field(default_factory=dict)
    promotion_reason: str = ""
    pending_class: str = ""
    change_request: None = None
    sandbox: None = None

    def __post_init__(self) -> None:
        if not self.candidate_id:
            self.candidate_id = new_id("pinc")
        now = utc_ts()
        if not self.first_seen:
            self.first_seen = now
        if not self.last_seen:
            self.last_seen = now
        if not self.affected_components and self.component:
            self.affected_components = [self.component]

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "change_request": None,
            "sandbox": None,
            "kind": "potential_incident",
            "confirmed": self.status == "CONFIRMED",
            "promoted": False,
        }
