"""Discovery signal metrics: noise taxonomy, source quality, self-health."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional

from brain.discovery.baseline import is_preexisting


NOISE_CLASSES = (
    "HEARTBEAT",
    "KNOWN_BASELINE",
    "DUPLICATE",
    "ROOT_DUPLICATE",
    "EXPECTED_STATE",
    "EXPECTED_WARNING",
    "STALE",
    "LOW_VALUE",
    "RESOLVED",
    "OTHER",
)

HEARTBEAT_KINDS = frozenset({"heartbeat", "healthy", "info"})
HEARTBEAT_TEXT = frozenset({"ok", "healthy", "heartbeat", "all green", "routine"})


def classify_noise(
    *,
    kind: str = "",
    summary: str = "",
    duplicate: bool = False,
    low_score: bool = False,
    denied: bool = False,
) -> Optional[str]:
    if denied:
        return "OTHER"
    if duplicate:
        return "DUPLICATE"
    text = str(summary or "").strip().lower()
    kind_l = str(kind or "").lower()
    if kind_l in HEARTBEAT_KINDS or text in HEARTBEAT_TEXT:
        return "HEARTBEAT"
    if is_preexisting(summary):
        return "KNOWN_BASELINE"
    if "resolved" in text or "cleared" in text:
        return "RESOLVED"
    if "stale" in text or "outdated" in text:
        return "STALE"
    if "expected" in text or "benign" in text:
        return "EXPECTED_WARNING"
    if low_score:
        return "LOW_VALUE"
    return None


class SignalMetrics:
    """Accumulate soak/discovery metrics without claiming precision."""

    def __init__(self) -> None:
        self.received = 0
        self.normalized = 0
        self.suppressed = 0
        self.duplicates = 0
        self.baseline_suppressions = 0
        self.candidates = 0
        self.improvements = 0
        self.clusters = 0
        self.root_clusters = 0
        self.root_collapsed = 0
        self.pending_suppressed = 0
        self.pending_promoted = 0
        self.investigations_opened = 0
        self.investigations_completed = 0
        self.provider_calls = 0
        self.tool_calls = 0
        self.writes_attempted = 0
        self.safety_violations = 0
        self.noise: Dict[str, int] = {k: 0 for k in NOISE_CLASSES}
        self.by_source: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"received": 0, "accepted": 0, "suppressed": 0, "candidate_producing": 0, "duplicate_producing": 0}
        )
        self.by_component: Dict[str, int] = defaultdict(int)
        self.self_health: Dict[str, Any] = {
            "processing_latency_s": 0.0,
            "queue_depth": 0,
            "suppression_rate": 0.0,
            "candidate_rate": 0.0,
            "dedup_rate": 0.0,
            "checkpoint_failures": 0,
            "heartbeat_incident_storm": False,
        }

    def note_observation(
        self,
        *,
        source: str,
        component: str,
        kind: str,
        summary: str,
        accepted: bool,
        noise_class: Optional[str] = None,
        duplicate: bool = False,
        candidate: bool = False,
    ) -> None:
        self.received += 1
        self.normalized += 1
        src = self.by_source[str(source or "unknown")[:40]]
        src["received"] += 1
        if accepted:
            src["accepted"] += 1
            if component:
                self.by_component[str(component)[:80]] += 1
        else:
            self.suppressed += 1
            src["suppressed"] += 1
            klass = noise_class or "OTHER"
            self.noise[klass] = self.noise.get(klass, 0) + 1
            if klass == "KNOWN_BASELINE":
                self.baseline_suppressions += 1
        if duplicate:
            self.duplicates += 1
            src["duplicate_producing"] += 1
        if candidate:
            src["candidate_producing"] += 1
            self.candidates += 1

    def refresh_rates(self) -> None:
        n = max(1, self.received)
        self.self_health["suppression_rate"] = round(self.suppressed / n, 4)
        self.self_health["candidate_rate"] = round(self.candidates / n, 4)
        self.self_health["dedup_rate"] = round(self.duplicates / n, 4)
        # Discovery heartbeats must never count as incidents.
        self.self_health["heartbeat_incident_storm"] = False

    def to_dict(self) -> Dict[str, Any]:
        self.refresh_rates()
        return {
            "events_received": self.received,
            "events_normalized": self.normalized,
            "events_suppressed": self.suppressed,
            "duplicates": self.duplicates,
            "known_baseline_suppressions": self.baseline_suppressions,
            "candidate_incidents": self.candidates,
            "improvement_candidates": self.improvements,
            "clusters": self.clusters,
            "root_clusters": self.root_clusters,
            "root_collapsed_occurrences": self.root_collapsed,
            "pending_suppressed": self.pending_suppressed,
            "pending_promoted": self.pending_promoted,
            "investigations_opened": self.investigations_opened,
            "investigations_completed": self.investigations_completed,
            "provider_calls": self.provider_calls,
            "tool_calls": self.tool_calls,
            "writes_attempted": self.writes_attempted,
            "safety_violations": self.safety_violations,
            "noise_taxonomy": dict(self.noise),
            "source_quality": {k: dict(v) for k, v in self.by_source.items()},
            "component_distribution": dict(self.by_component),
            "self_health": dict(self.self_health),
        }


def telemetry_quality_findings(metrics: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Flag noisy sources. Do not auto-disable."""
    findings = []
    sources = metrics.get("source_quality") or {}
    total = max(1, int(metrics.get("events_received") or 0))
    for name, row in sources.items():
        received = int(row.get("received") or 0)
        useful = int(row.get("candidate_producing") or 0)
        if received <= 0:
            continue
        share = received / total
        if share >= 0.5 and useful == 0:
            findings.append({
                "kind": "TELEMETRY_QUALITY",
                "source": name,
                "share": round(share, 4),
                "useful_candidates": useful,
                "action": "recommend_review",
                "auto_disabled": False,
            })
    return findings
