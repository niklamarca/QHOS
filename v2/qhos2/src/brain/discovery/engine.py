"""DiscoveryEngine: notice → score → candidate. Read-only. No LLM by default."""
from __future__ import annotations

from dataclasses import fields
from typing import Any, Dict, List, Mapping, Optional, Sequence

from brain.invoke import should_invoke_llm
from brain.models import utc_ts
from brain.redact import redact_text

from .baseline import is_preexisting
from .budget import CognitiveGovernor
from .cluster import cluster_candidates, correlate_without_cause
from .fingerprint import (
    component_key,
    component_mentions,
    group_by_root,
    mentions_component,
    root_title,
)
from .fp_ledger import FalseNegativeFoundation, FalsePositiveLedger
from .improvement import dedupe_improvements, make_improvement
from .incident import PotentialIncident
from .investigation import action_allowed, open_investigation
from .metrics import SignalMetrics, classify_noise, telemetry_quality_findings
from .observation import Observation, observations_from_snapshot
from .observation import fingerprint_of
from .pending import (
    BENIGN_PENDING,
    PendingTracker,
    is_pending_status,
    pending_evidence_summary,
    systemic_unknown_pending,
)
from .policy import classify_family, recommend_strategy
from .policy_g11 import AUTHORITATIVE_SOURCES
from .research import propose_research
from .score import discovery_score, score_feature_audit
from .telemetry import (
    telemetry_quality_issues,
    telemetry_quality_ledger,
    telemetry_quality_recommendations,
)


# A runtime revision that matches the worktree is a heartbeat: it confirms the
# absence of a problem and must not consume candidate budget every cycle.
NOISE_KINDS = frozenset({"heartbeat", "healthy", "info", "RUNTIME_REVISION_MATCH"})
NOISE_TEXT = ("ok", "healthy", "heartbeat", "all green", "routine")
THRESHOLD = 0.42
INTELLIGENCE_SCHEMA = "qhos-brain-intelligence-v1"


# --- optional Fable boundary ------------------------------------------------
# Fable is EXPERIMENTAL and must be removable without breaking deterministic
# Discovery. Discovery uses exactly two things from it — an effort ceiling and a
# write-resume guard — and reads exactly one field from each. Both are imported
# defensively with a deterministic fallback so that the core degrades rather
# than failing to import if Fable is disabled or deleted.

EFFORT_POLICY_FABLE = "brain.fable.effort"
EFFORT_POLICY_FALLBACK = "deterministic_fallback"


def _effort_policy_with_source() -> tuple:
    """(policy, source). The source is reported in the intel so a silent fallback
    (QHOS-AUD-291: e.g. a SyntaxError in brain.fable.effort) stays visible."""
    try:
        from brain.fable.effort import CognitiveEffortPolicy
    except Exception:
        class _DeterministicEffortCeiling:
            """Fallback ceiling. Never MAX, never enables Level 3."""

            def decide(self, observations: Sequence[Mapping[str, Any]], **_: Any) -> Dict[str, Any]:
                return {"level": "LOW", "source": EFFORT_POLICY_FALLBACK}

        return _DeterministicEffortCeiling(), EFFORT_POLICY_FALLBACK
    return CognitiveEffortPolicy(), EFFORT_POLICY_FABLE


def _effort_policy() -> Any:
    return _effort_policy_with_source()[0]


def _blocks_write_resume(payload: Mapping[str, Any]) -> bool:
    """Whether an investigation must not be resumed because it holds a write.

    Without Fable the answer is taken straight from the persisted flag, which is
    the conservative reading: refuse to resume anything that looks like a write.
    """
    try:
        from brain.fable.resume import resume_investigation
    except Exception:
        return bool(payload.get("write_pending"))
    return bool(resume_investigation(dict(payload)).get("write_auto_resume"))


class DiscoveryEngine:
    def __init__(self) -> None:
        self.seen: Dict[str, Dict[str, Any]] = {}
        self.governor = CognitiveGovernor()
        self.fp = FalsePositiveLedger()
        self.fn = FalseNegativeFoundation()
        self.effort_policy, self.effort_policy_source = _effort_policy_with_source()
        self.history: Dict[str, int] = {}
        self.last_intel: Dict[str, Any] = {}
        self.learnings: List[Dict[str, Any]] = []
        self.last_investigations: List[Dict[str, Any]] = []
        self.signal_metrics = SignalMetrics()
        # Root-cause bookkeeping across ingest cycles.
        self.roots: Dict[str, Dict[str, Any]] = {}
        self.pending = PendingTracker()
        self.improvement_seen: Dict[str, Dict[str, Any]] = {}
        self.reviews: List[Dict[str, Any]] = []
        self._systemic_emitted = False

    def ingest(
        self,
        raw: Mapping[str, Any],
        *,
        health: Optional[Mapping[str, Any]] = None,
        graph: Optional[Mapping[str, Any]] = None,
        api_enabled: bool = False,
        soak: bool = False,
    ) -> Dict[str, Any]:
        t0 = utc_ts()
        now = t0
        obs = observations_from_snapshot(raw)
        self.pending.begin_cycle(now)
        error_components = {
            o.component_family for o in obs if o.kind == "error" and o.component_family
        } | {o.component_family for o in obs if o.kind == "health" and o.component_family}
        # Error observations are attributed to the log source ("logs"), not to the
        # component that failed, so family comparison alone can never link an
        # error to a registry entry. Match on what the text actually names.
        error_evidence_rows = [
            o for o in obs
            if o.kind in {"error", "health"} and not o.denied
            and str(o.severity_hint or "").lower() in {"high", "critical", "medium"}
        ]
        error_mentions: set = set()
        for row in error_evidence_rows:
            error_mentions |= component_mentions(row.payload_summary)
            error_mentions |= component_mentions(row.component)
        noise = 0
        kept: List[Observation] = []
        pending_states: List[Dict[str, Any]] = []
        pending_suppressed_cycle = 0
        pending_promoted_cycle = 0
        for row in obs:
            if row.denied:
                noise += 1
                self.signal_metrics.note_observation(
                    source=row.source, component=row.component, kind=row.kind, summary=row.payload_summary,
                    accepted=False, noise_class=classify_noise(kind=row.kind, summary=row.payload_summary, denied=True),
                )
                continue
            if row.kind in NOISE_KINDS or row.payload_summary.lower().strip() in NOISE_TEXT:
                noise += 1
                self.signal_metrics.note_observation(
                    source=row.source, component=row.component, kind=row.kind, summary=row.payload_summary,
                    accepted=False, noise_class="HEARTBEAT",
                )
                continue
            if row.pre_existing or is_preexisting(row.payload_summary):
                row.pre_existing = True
                noise += 1
                self.signal_metrics.note_observation(
                    source=row.source, component=row.component, kind=row.kind, summary=row.payload_summary,
                    accepted=False, noise_class="KNOWN_BASELINE",
                )
                continue
            if row.kind in {"state", "health"} and is_pending_status(row.state or row.payload_summary):
                # Independent evidence must be able to override a benign state
                # class, otherwise UNKNOWN_PENDING suppression is absolute and
                # a genuinely broken component stays invisible.
                own_key = component_key(row.component)
                has_error = bool(own_key) and own_key in error_mentions
                cited = [
                    o.payload_summary[:200] for o in error_evidence_rows
                    if mentions_component(o.payload_summary, row.component)
                    or mentions_component(o.component, row.component)
                ][:3]
                state = self.pending.observe(
                    component=row.component,
                    status=row.state or "pending",
                    now=row.timestamp or now,
                    error_evidence=has_error,
                    dependent_impact=row.component_family in error_components,
                    evidence=[row.payload_summary] + cited,
                    lifecycle_known=bool(getattr(row, "lifecycle_known", True)),
                )
                pending_states.append(state.to_dict())
                if not state.promote:
                    # Expected/transient startup is a state observation, not an incident.
                    noise += 1
                    pending_suppressed_cycle += 1
                    self.signal_metrics.pending_suppressed += 1
                    self.signal_metrics.note_observation(
                        source=row.source, component=row.component, kind=row.kind, summary=row.payload_summary,
                        accepted=False, noise_class="EXPECTED_STATE",
                    )
                    continue
                from .policy_g11 import pending_severity
                base_severity = "high" if state.classification in {"FAILED_PENDING", "STALE_PENDING"} else "medium"
                # G1.1 proposal hook: hold an unconfirmed first-cycle promotion
                # at medium. Returns base_severity unchanged under G1.
                row.severity_hint = pending_severity(state.to_dict(), base_severity)
                row.payload_summary = (
                    f"{row.component} {state.classification} for {state.duration_s}s "
                    f"(expected {state.expected_duration_s}s, cycles={state.cycles_pending})"
                )[:240]
                pending_promoted_cycle += 1
                self.signal_metrics.pending_promoted += 1
            kept.append(row)

        # ---- exact-duplicate suppression, then root-cause collapse ----------
        duplicates = 0
        fresh: List[Observation] = []
        cycle_seen: Dict[str, bool] = {}
        dup_by_root: Dict[str, int] = {}
        dup_families: set = set()
        for row in kept:
            prev = self.seen.get(row.fingerprint)
            if prev is not None or cycle_seen.get(row.fingerprint):
                if isinstance(prev, dict):
                    prev["count"] = int(prev.get("count") or 1) + 1
                    prev["last_seen"] = now
                dup_by_root[row.root_id] = dup_by_root.get(row.root_id, 0) + 1
                if isinstance(prev, dict) and prev.get("accepted") and row.component_family:
                    # A repeat of a real problem is recurrence evidence.
                    dup_families.add(row.component_family)
                duplicates += 1
                self.signal_metrics.note_observation(
                    source=row.source, component=row.component, kind=row.kind, summary=row.payload_summary,
                    accepted=False, duplicate=True, noise_class="DUPLICATE",
                )
                continue
            cycle_seen[row.fingerprint] = True
            fresh.append(row)

        clusters_by_root = group_by_root(fresh, state_of=lambda o: getattr(o, "state", ""))
        occurrences_total = sum(c.occurrence_count for c in clusters_by_root.values())
        root_collapsed = max(0, occurrences_total - len(clusters_by_root))
        self.signal_metrics.root_clusters = len(clusters_by_root)
        self.signal_metrics.root_collapsed += root_collapsed

        incidents: List[PotentialIncident] = []
        accepted_families: set = set()
        for root_id, cluster in clusters_by_root.items():
            lead = fresh[0]
            for row in fresh:
                if row.root_id == root_id:
                    lead = row
                    break
            sig = cluster.signature
            prior = self.roots.get(root_id)
            cycles_observed = int((prior or {}).get("cycles_observed") or 0) + 1
            pending_class = ""
            for state in pending_states:
                if str(state.get("component")) == lead.component:
                    pending_class = str(state.get("classification") or "")
                    break
            rec = {
                "fingerprint": lead.fingerprint,
                "root_id": root_id,
                "component": lead.component,
                "component_family": sig.component_family,
                "summary": lead.payload_summary,
                "severity": cluster.max_severity,
                "count": cluster.occurrence_count + dup_by_root.get(root_id, 0) + int((prior or {}).get("count") or 0),
                "novelty": "NEW" if prior is None else "RECURRING",
                "pre_existing": lead.pre_existing,
                "cross_signals": max(0, len(cluster.sources) - 1),
                "historical": self.history.get(sig.component_family, 0),
                "observation_ids": [o["observation_id"] for o in cluster.occurrences][:24],
                "root_concrete": sig.concrete,
                "affected_instances": cluster.affected_instances,
                "cycles_observed": cycles_observed,
                "expected_state": pending_class in BENIGN_PENDING,
                # Whether the row is the system of record asserting a component
                # state, rather than a parsed message. Recorded under G1 (which
                # ignores it) so the G1.1 proposal can be evaluated without
                # changing what the engine observes.
                "authoritative_source": lead.source in AUTHORITATIVE_SOURCES,
                "source": lead.source,
            }
            scored = discovery_score(rec)
            rec["score"] = scored["score"]
            rec["score_components"] = scored["score_components"]
            rec["promotion_reason"] = scored["promotion_reason"]
            rec["last_seen"] = cluster.last_seen or now
            self.roots[root_id] = rec
            for row in fresh:
                if row.root_id == root_id:
                    self.seen[row.fingerprint] = rec

            accepted = not (scored["score"] < THRESHOLD and cluster.max_severity == "low")
            rec["accepted"] = accepted
            for occ in cluster.occurrences:
                self.signal_metrics.note_observation(
                    source=str(occ.get("source") or lead.source), component=str(occ.get("component") or ""),
                    kind=lead.kind, summary=str(occ.get("summary") or ""),
                    accepted=accepted, candidate=False,
                    noise_class=None if accepted else "LOW_VALUE",
                )
            if not accepted:
                noise += cluster.occurrence_count
                continue
            accepted_families.add(sig.component_family)
            self.signal_metrics.candidates += 1
            self.signal_metrics.by_source[lead.source]["candidate_producing"] += 1

            family = classify_family(lead.payload_summary, components=len(cluster.components))
            route = recommend_strategy(family)
            effort = self.effort_policy.decide(
                [{"kind": lead.kind, "summary": lead.payload_summary, "body": ""}],
            )
            evidence = [lead.payload_summary]
            if sig.dependency:
                evidence.append(f"root_dependency={sig.dependency}")
            if sig.exception_type:
                evidence.append(f"root_exception={sig.exception_type}")
            if cluster.affected_instances > 1:
                evidence.append(f"affected_instances={cluster.affected_instances}: {', '.join(cluster.instances[:8])}")
            for state in pending_states:
                if str(state.get("component")) == lead.component:
                    evidence.extend(pending_evidence_summary(state))
                    break
            cand = PotentialIncident(
                candidate_id="",
                fingerprint=lead.fingerprint,
                component=lead.component,
                summary=lead.payload_summary,
                status="CANDIDATE",
                signal_class="POTENTIAL_INCIDENT" if lead.kind != "latency" else "ANOMALY",
                novelty=str(rec["novelty"]),
                count=int(rec["count"]),
                score=scored["score"],
                severity=cluster.max_severity,
                evidence=evidence[:8],
                observation_ids=list(rec["observation_ids"]),
                strategy=route["strategy"],
                effort=str((effort.get("level") or "LOW")),
                root_id=root_id,
                root_title=root_title(sig),
                root_tier=sig.tier,
                root_signature=sig.to_dict(),
                occurrences=list(cluster.occurrences)[:40],
                occurrence_count=cluster.occurrence_count,
                affected_instances=cluster.affected_instances,
                affected_components=list(cluster.components)[:16],
                sources=list(cluster.sources)[:8],
                score_components=scored["score_components"],
                promotion_reason=scored["promotion_reason"],
                pending_class=pending_class,
                first_seen=cluster.first_seen or now,
                last_seen=cluster.last_seen or now,
            )
            incidents.append(cand)

        # Many components reporting pending with no lifecycle telemetry is one
        # systemic fact about the status source, not one incident per component.
        systemic = systemic_unknown_pending(self.pending)
        if systemic and not self._systemic_emitted:
            self._systemic_emitted = True
            root_id = "sys-" + fingerprint_of("systemic_unknown_pending", "registry", systemic["summary"])
            scored = discovery_score({
                "severity": "medium", "count": systemic["component_count"], "novelty": "NEW",
                "component": "registry", "component_family": "registry",
                "cross_signals": 0, "historical": 0, "root_concrete": False,
                "affected_instances": systemic["component_count"],
                "cycles_observed": systemic["cycles_pending"],
            })
            incidents.append(PotentialIncident(
                candidate_id="",
                fingerprint=root_id,
                component="registry",
                summary=systemic["summary"][:240],
                signal_class="POTENTIAL_INCIDENT",
                count=systemic["component_count"],
                score=scored["score"],
                severity="medium",
                evidence=systemic["evidence"],
                strategy="deterministic",
                effort="LOW",
                root_id=root_id,
                root_title=f"registry reports {systemic['component_count']} modules pending with no lifecycle telemetry",
                root_tier="TELEMETRY",
                root_signature={"tier": "TELEMETRY", "component_family": "registry", "concrete": False},
                occurrence_count=systemic["component_count"],
                affected_instances=systemic["component_count"],
                affected_components=list(systemic["components"])[:16],
                sources=["registry"],
                score_components=scored["score_components"],
                promotion_reason="systemic_unknown_pending: " + scored["promotion_reason"],
                pending_class="UNKNOWN_PENDING",
            ))
            self.signal_metrics.candidates += 1

        # Recurrence counts families that actually produced a problem, so benign
        # traffic (commits, heartbeats, expected pending) never earns recurrence.
        for fam in accepted_families | dup_families:
            self.history[fam] = self.history.get(fam, 0) + 1

        clusters = cluster_candidates([c.to_dict() for c in incidents])
        commits = [o.payload_summary for o in obs if o.kind == "commit"]
        errors = [o.payload_summary for o in kept if o.kind in {"error", "health", "latency"}]
        corr = correlate_without_cause(
            errors=errors,
            commits=commits,
            latency_changed=any(o.kind == "latency" for o in kept),
        )

        improvements = self._improvements(kept, incidents)
        self.signal_metrics.improvements = len(improvements)
        investigations: List[Dict[str, Any]] = []
        for cand in incidents:
            if cand.score < THRESHOLD:
                continue
            if not self.governor.admit_investigation(effort=cand.effort):
                continue
            inv = open_investigation(cand.to_dict(), strategy=cand.strategy, effort=cand.effort)
            cand.status = "INVESTIGATING"
            investigations.append(inv.to_dict())
            self.signal_metrics.investigations_opened += 1
            self.signal_metrics.investigations_completed += 1
            self.governor.release(effort=cand.effort)
        self.signal_metrics.clusters = len(clusters)

        invoke = should_invoke_llm(
            [{"kind": c.signal_class.lower(), "summary": c.summary, "severity": c.severity} for c in incidents]
            or [{"kind": "health", "summary": "ok"}],
            api_enabled=api_enabled,
            mode="deterministic_only" if not api_enabled else "hybrid",
            duplicate=duplicates > 0 and not incidents,
        )
        # Live default never calls a remote model even if invoke.allowed.
        llm_called = False
        if invoke.get("invoke") and api_enabled and incidents:
            fp = incidents[0].fingerprint
            if self.governor.allow_llm(fp, now):
                # Still do not call. Mark eligible only.
                invoke = {**invoke, "eligible": True, "called": False}
            else:
                invoke = {**invoke, "allowed": False, "reason": "budget", "invoke": False}

        research = []
        for cand in incidents:
            need = propose_research(cand.summary, cand.component)
            if need:
                research.append(need.to_dict())

        relations = []
        if graph and isinstance(graph.get("relations"), list):
            relations = list(graph.get("relations") or [])[:8]
        kg = []
        for cand in incidents[:6]:
            kg.append({"source": cand.candidate_id, "verb": "BASED_ON", "target": (cand.observation_ids or ["obs"])[0], "causal": False})
            kg.append({"source": cand.candidate_id, "verb": "AFFECTS", "target": cand.component, "causal": False})
        for inv in investigations[:4]:
            kg.append({"source": inv.get("investigation_id"), "verb": "INVESTIGATES", "target": inv.get("candidate_id"), "causal": False})
        for imp in improvements[:4]:
            kg.append({"source": imp.get("candidate_id"), "verb": "TARGETS", "target": imp.get("component"), "causal": False})

        self.signal_metrics.self_health["processing_latency_s"] = round(utc_ts() - t0, 6)
        signal_metrics = self.signal_metrics.to_dict()
        ledger = telemetry_quality_ledger(
            signal_metrics,
            reviews=self.reviews,
            candidates=[c.to_dict() for c in incidents],
        )
        metrics = {
            "signals": len(obs),
            "kept": len(kept),
            "noise_suppressed": noise,
            "duplicates": duplicates,
            "root_clusters": len(clusters_by_root),
            "root_collapsed_occurrences": root_collapsed,
            "occurrences_total": occurrences_total,
            "pending_suppressed": pending_suppressed_cycle,
            "pending_promoted": pending_promoted_cycle,
            "pending_suppressed_total": self.signal_metrics.pending_suppressed,
            "pending_promoted_total": self.signal_metrics.pending_promoted,
            "candidate_n": len(incidents),
            "improvement_n": len(improvements),
            "investigations": len(investigations),
            "llm_called": llm_called,
            "generic_improvement_rate": 0.0,
            "noise_taxonomy": signal_metrics.get("noise_taxonomy"),
            "source_quality": signal_metrics.get("source_quality"),
            "component_distribution": signal_metrics.get("component_distribution"),
        }
        # This blob is the FULL internal decision artifact, not a display
        # payload. It is what soak/fault runs persist and what replay consumes,
        # so nothing here may be truncated for presentation: a display cap on
        # this path silently changes replay semantics (see MD-07). Bounding for
        # UI and for provider context happens in their own projection layers
        # (`brain.view_model`, `brain.context`), where a limit affects only what
        # is shown or sent, never what was decided.
        intel = {
            "schema": INTELLIGENCE_SCHEMA,
            "read_only": True,
            "full_fidelity": True,
            "observations": [o.to_dict() for o in obs if not o.denied],
            "incidents": [c.to_dict() for c in incidents],
            "improvements": list(improvements),
            "investigations": list(investigations),
            "clusters": clusters,
            "correlations": corr,
            "knowledge_graph": kg + relations,
            "research_needs": research[:4],
            "invocation": invoke,
            "llm_called": False,
            "budget": self.governor.to_dict(),
            "metrics": metrics,
            "signal_metrics": signal_metrics,
            "telemetry_quality": telemetry_quality_findings(signal_metrics),
            "telemetry_ledger": ledger,
            "telemetry_recommendations": telemetry_quality_recommendations(ledger),
            # An observability defect read with incident semantics invites the
            # wrong fix, so it is surfaced under its own kind as well.
            "telemetry_quality_issues": telemetry_quality_issues(
                [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in incidents]
            ),
            "pending_model": self.pending.to_dict(),
            "root_clusters": [c.to_dict() for c in clusters_by_root.values()],
            "score_feature_audit": score_feature_audit(self.reviews),
            "fp_ledger": self.fp.summary(),
            "fn_foundation": self.fn.to_dict(),
            "learnings": list(self.learnings)[-8:],
            "strategy_policy": "static",
            "effort_policy_source": self.effort_policy_source,
            "autonomous_routing": False,
            "change_request": None,
            "sandbox": None,
            "sandbox_run": None,
            "writes": 0,
            "level3_enabled": False,
            "soak": bool(soak),
            "health_tone": (health or {}).get("tone") if isinstance(health, Mapping) else None,
        }
        self.last_investigations = investigations
        self.last_intel = intel
        return intel

    def checkpoint(self) -> Dict[str, Any]:
        return {
            "seen": dict(self.seen),
            "history": dict(self.history),
            "roots": dict(self.roots),
            "pending_states": self.pending.snapshot(),
            "improvement_seen": dict(self.improvement_seen),
            "investigations": list(self.last_investigations),
            "write_pending": False,
            "read_only": True,
            "level3_enabled": False,
        }

    def resume(self, blob: Mapping[str, Any]) -> Dict[str, Any]:
        """Resume read-only investigation state. Never resumes a write."""
        from .pending import PendingState
        self.seen = {str(k): dict(v) for k, v in (blob.get("seen") or {}).items() if isinstance(v, Mapping)}
        self.history = {str(k): int(v) for k, v in (blob.get("history") or {}).items()}
        self.roots = {str(k): dict(v) for k, v in (blob.get("roots") or {}).items() if isinstance(v, Mapping)}
        self.improvement_seen = {
            str(k): dict(v) for k, v in (blob.get("improvement_seen") or {}).items() if isinstance(v, Mapping)
        }
        known = {f.name for f in fields(PendingState)}
        for row in blob.get("pending_states") or []:
            if not isinstance(row, Mapping):
                continue
            state = PendingState(**{k: v for k, v in row.items() if k in known})
            self.pending.states[state.component] = state
        resumed: List[Dict[str, Any]] = []
        for inv in blob.get("investigations") or []:
            if not isinstance(inv, Mapping):
                continue
            if inv.get("write_pending"):
                continue
            if _blocks_write_resume({
                "snapshot_id": str(inv.get("investigation_id") or "linv"),
                "confirmed_facts": list(inv.get("evidence") or []),
                "rejected_hypotheses": [],
                "uncertainties": [],
                "evidence": list(inv.get("evidence") or []),
                "next_actions": [
                    str(t.get("action"))
                    for t in (inv.get("tasks") or [])
                    if isinstance(t, Mapping)
                ],
                "write_pending": False,
            }):
                continue
            resumed.append({**dict(inv), "resumed": True, "read_only": True, "write_pending": False})
        self.last_investigations = resumed
        return {
            "resumed": True,
            "investigations": resumed,
            "write_auto_resume": False,
            "writes": 0,
            "change_request": None,
            "sandbox": None,
        }

    def _improvements(
        self,
        kept: Sequence[Observation],
        incidents: Sequence[PotentialIncident],
    ) -> List[Dict[str, Any]]:
        # Recurrence is temporal (ingest cycles), not a single-burst duplicate count.
        # Grouping is by root family so one root cause yields one improvement row.
        by_root: Dict[str, Dict[str, Any]] = {}
        for rec in self.roots.values():
            fam = str(rec.get("component_family") or rec.get("component") or "")
            if not fam or not rec.get("accepted"):
                continue
            slot = by_root.setdefault(fam, {"count": 0, "component": str(rec.get("component") or fam), "tier": ""})
            slot["count"] += int(rec.get("count") or 1)
            slot["tier"] = str((rec.get("score_components") and rec.get("root_tier")) or slot["tier"])
        out: List[Dict[str, Any]] = []
        for fam, slot in by_root.items():
            hist = self.history.get(fam, 0)
            if hist < 3:
                continue
            n = int(slot["count"])
            imp = make_improvement(
                component=str(slot["component"]),
                problem=f"recurring degradation on {fam} (n={max(n, hist)})",
                evidence=[f"recurrence={hist}", f"current_count={n}"],
                benefit="reduce repeat investigations",
                validation="compare incident rate over next soak window",
                evidence_family="RECURRENCE",
            )
            if imp:
                out.append(imp.to_dict())
        lat = [o for o in kept if o.kind == "latency"]
        if len(lat) >= 2:
            imp = make_improvement(
                component=lat[0].component,
                problem="latency samples persist above hint threshold",
                evidence=[o.payload_summary for o in lat[:4]],
                benefit="lower p99 if confirmed",
                validation="benchmark current vs experimental cache/path — do not launch",
                evidence_family="LATENCY_SAMPLES",
            )
            if imp:
                out.append(imp.to_dict())
        retries = [o for o in kept if o.kind == "retry"]
        if retries:
            imp = make_improvement(
                component=retries[0].component,
                problem="high retry frequency observed",
                evidence=[o.payload_summary for o in retries[:3]],
                benefit="fewer wasted cycles",
                validation="count retries in next soak session",
                evidence_family="RETRY_PRESSURE",
            )
            if imp:
                out.append(imp.to_dict())
        deps = [o for o in kept if getattr(o, "root_tier", "") == "DEPENDENCY"]
        if deps:
            imp = make_improvement(
                component=deps[0].component,
                problem=f"missing dependency blocks {len({o.component for o in deps})} component(s): {deps[0].root_title}",
                evidence=[o.payload_summary for o in deps[:3]] + [f"instances={len({o.instance for o in deps if o.instance})}"],
                benefit="restore data source reliability",
                validation="verify import availability in the runtime environment before any change",
                evidence_family="MISSING_DEPENDENCY",
            )
            if imp:
                out.append(imp.to_dict())

        # Collapse equivalent rows, then carry occurrence counts across cycles.
        deduped = dedupe_improvements(out)
        merged: List[Dict[str, Any]] = []
        for row in deduped:
            fp = str(row.get("fingerprint") or "")
            prior = self.improvement_seen.get(fp)
            if prior:
                row["candidate_id"] = prior.get("candidate_id") or row.get("candidate_id")
                row["first_seen"] = prior.get("first_seen") or row.get("first_seen")
                row["occurrence_count"] = int(prior.get("occurrence_count") or 1) + int(row.get("occurrence_count") or 1)
                row["status"] = "SUPPORTED" if row["occurrence_count"] >= 3 else row.get("status") or "CANDIDATE"
            self.improvement_seen[fp] = row
            merged.append(row)
        return merged

    def apply_feedback(self, fingerprint: str, action: str, note: str = "") -> Dict[str, Any]:
        from brain.shadow import record_feedback
        rows = list((self.last_intel or {}).get("incidents") or [])
        found = None
        updated = []
        for row in rows:
            if str(row.get("fingerprint") or "") == fingerprint:
                found = record_feedback(row, action, note=note)
                updated.append(found)
            else:
                updated.append(row)
        if found:
            useful = str(action).upper() in {"CONFIRM", "USEFUL", "PROMOTE_FOR_REVIEW"}
            noise = str(action).upper() in {"DISMISS", "NOT_USEFUL"}
            self.fp.record({
                **found,
                "human_disposition": action,
                "final_disposition": found.get("disposition"),
                "useful": useful,
                "noise": noise,
            })
            self.learnings.append({
                "fingerprint": fingerprint,
                "disposition": str(action).upper(),
                "useful": useful,
                "noise": noise,
                "universal_rule": False,
                "note": str(note)[:160],
            })
            # Reviews feed evaluation only (ledger, feature audit). Never weights.
            self.reviews.append({
                "fingerprint": fingerprint,
                "root_id": str(found.get("root_id") or ""),
                "candidate_id": str(found.get("candidate_id") or ""),
                "human_disposition": str(action).upper(),
                "brain_score": float(found.get("score") or 0.0),
                "score_components": found.get("score_components") or {},
                "sources": list(found.get("sources") or []),
                "auto_applied": False,
            })
            self.last_intel["incidents"] = updated
            self.last_intel["learnings"] = list(self.learnings)[-8:]
            self.last_intel["fp_ledger"] = self.fp.summary()
        return {"ok": found is not None, "candidate": found, "change_request": None, "sandbox": None}

    def safety_invariants(self) -> Dict[str, Any]:
        intel = self.last_intel or {}
        return {
            "change_requests": 0 if intel.get("change_request") is None else 1,
            "sandbox_runs": 0 if intel.get("sandbox") is None else 1,
            "production_writes": int(intel.get("writes") or 0),
            "trading_actions": 0,
            "autonomy_changes": 0,
            "ok": True,
        }
