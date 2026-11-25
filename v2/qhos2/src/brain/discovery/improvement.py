"""ImprovementCandidate — intelligence, never a ChangeRequest."""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from brain.models import new_id, utc_ts


IMPROVEMENT_STATES = (
    "OBSERVED",
    "CANDIDATE",
    "EVIDENCE_GATHERING",
    "SUPPORTED",
    "PRIORITIZED",
    "DISMISSED",
    "DUPLICATE",
    "STALE",
    "NEEDS_BENCHMARK",
)

GENERIC_BANNED = (
    "refactor code",
    "improve tests",
    "add caching",
    "write better docs",
    "optimize everything",
)

BENEFIT_CLASSES = (
    "RELIABILITY",
    "LATENCY",
    "THROUGHPUT",
    "COST",
    "REVIEW_EFFICIENCY",
    "DATA_QUALITY",
    "UNKNOWN",
)

_BENEFIT_KEYWORDS = (
    ("LATENCY", ("latency", "p99", "p95", "slow", "response time")),
    ("THROUGHPUT", ("throughput", "wasted cycles", "retries", "backlog", "queue")),
    ("COST", ("cost", "spend", "token", "budget")),
    ("REVIEW_EFFICIENCY", ("investigations", "review", "triage", "noise")),
    ("DATA_QUALITY", ("data quality", "missing data", "gap", "stale data")),
    ("RELIABILITY", ("reliability", "failure", "error", "crash", "unavailable", "degradation")),
)

_PATTERN_KEYWORDS = (
    ("MISSING_DEPENDENCY", ("no module named", "modulenotfounderror", "importerror", "missing dependency")),
    ("RECURRING_DEGRADATION", ("recurring degradation", "repeat", "recurrence")),
    ("LATENCY_PERSISTENCE", ("latency",)),
    ("RETRY_PRESSURE", ("retry", "retries")),
    ("PENDING_LIFECYCLE", ("pending", "initializing", "not ready")),
)


def benefit_class(text: str) -> str:
    low = str(text or "").lower()
    for name, keywords in _BENEFIT_KEYWORDS:
        if any(k in low for k in keywords):
            return name
    return "UNKNOWN"


def problem_pattern(text: str) -> str:
    low = str(text or "").lower()
    for name, keywords in _PATTERN_KEYWORDS:
        if any(k in low for k in keywords):
            return name
    return "OTHER"


def improvement_fingerprint(
    *,
    component: str,
    problem: str,
    benefit: str,
    evidence_family: str = "",
) -> str:
    """Deterministic identity: component + pattern + benefit class + evidence family.

    Two improvement rows describing the same recurring pattern on the same
    component collapse, even when their counts differ between soak cycles.
    """
    from .fingerprint import component_family

    parts = (
        "impfp-v1",
        component_family(component),
        problem_pattern(problem),
        benefit_class(benefit),
        str(evidence_family or "").upper()[:32],
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


@dataclass
class ImprovementCandidate:
    candidate_id: str
    component: str
    observed_problem: str
    evidence: List[str]
    expected_benefit: str
    uncertainty: str
    proposed_validation: str
    status: str = "CANDIDATE"
    impact_estimate: str = ""
    confidence_estimate: float = 0.0
    effort_estimate: str = "unknown"
    risk_estimate: str = "unknown"
    measured: bool = False
    generic: bool = False
    change_request: None = None
    sandbox: None = None
    first_seen: float = 0.0
    last_seen: float = 0.0
    fingerprint: str = ""
    pattern: str = ""
    benefit_class: str = ""
    evidence_family: str = ""
    occurrence_count: int = 1

    def __post_init__(self) -> None:
        if not self.candidate_id:
            self.candidate_id = new_id("imp")
        if not self.first_seen:
            self.first_seen = utc_ts()
        if not self.last_seen:
            self.last_seen = self.first_seen
        if not self.pattern:
            self.pattern = problem_pattern(self.observed_problem)
        if not self.benefit_class:
            self.benefit_class = benefit_class(self.expected_benefit)
        if not self.fingerprint:
            self.fingerprint = improvement_fingerprint(
                component=self.component,
                problem=self.observed_problem,
                benefit=self.expected_benefit,
                evidence_family=self.evidence_family,
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "change_request": None,
            "sandbox": None,
            "auto_apply": False,
            "kind": "improvement_candidate",
            "estimate_vs_measured": "ESTIMATE" if not self.measured else "MEASURED",
        }


def is_generic(text: str) -> bool:
    low = str(text or "").strip().lower()
    return low in GENERIC_BANNED or (len(low) < 12 and "cache" in low and "evidence" not in low)


def dedupe_improvements(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse equivalent improvement rows by normalized fingerprint.

    Keeps the first row's identity so a reviewer's decision stays attached, and
    accumulates occurrence count plus merged evidence.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for row in rows or []:
        fp = str(row.get("fingerprint") or "") or improvement_fingerprint(
            component=str(row.get("component") or ""),
            problem=str(row.get("observed_problem") or ""),
            benefit=str(row.get("expected_benefit") or ""),
            evidence_family=str(row.get("evidence_family") or ""),
        )
        current = merged.get(fp)
        if current is None:
            merged[fp] = {**dict(row), "fingerprint": fp, "occurrence_count": int(row.get("occurrence_count") or 1)}
            continue
        current["occurrence_count"] = int(current.get("occurrence_count") or 1) + int(row.get("occurrence_count") or 1)
        current["last_seen"] = max(
            float(current.get("last_seen") or 0.0), float(row.get("last_seen") or row.get("first_seen") or 0.0)
        )
        for item in row.get("evidence") or []:
            if item not in current["evidence"] and len(current["evidence"]) < 8:
                current["evidence"].append(item)
    return list(merged.values())


def make_improvement(
    *,
    component: str,
    problem: str,
    evidence: List[str],
    benefit: str,
    validation: str,
    uncertainty: str = "unmeasured",
    evidence_family: str = "",
) -> ImprovementCandidate | None:
    if is_generic(problem) or not evidence:
        return None
    return ImprovementCandidate(
        evidence_family=str(evidence_family or "")[:32],
        candidate_id=new_id("imp"),
        component=component,
        observed_problem=problem[:240],
        evidence=[str(e)[:160] for e in evidence[:8]],
        expected_benefit=benefit[:160],
        uncertainty=uncertainty[:80],
        proposed_validation=validation[:200],
        confidence_estimate=min(0.6, 0.15 * len(evidence)),
        impact_estimate="unmeasured",
        effort_estimate="unmeasured",
        risk_estimate="read_only_recommendation",
    )
