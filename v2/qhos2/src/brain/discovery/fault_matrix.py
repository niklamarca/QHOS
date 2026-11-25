"""Run the controlled fault matrix under a frozen policy. LLM off, read-only.

Each manifest gets its own ``DiscoveryEngine`` and its own control arm. The
control arm matters more than it looks: without it, every candidate a fault run
produces would have to be assumed fault-related, and per-run precision would be
meaningless. Running the identical clean snapshot through an identical fresh
engine gives a background candidate set to subtract, so a false positive during
a fault run is genuinely a false positive and not a pre-existing condition.

Detection latency is reported in cycles, not seconds. These cycles are driven as
fast as the interpreter allows, so wall time measures Python and not Discovery;
cycles are the honest unit and are what a live deployment would multiply by its
own interval.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .consolidation import consolidate
from .engine import DiscoveryEngine
from .fault_catalog import fault_manifests
from .faults import (
    EXPECT_MULTIPLE,
    EXPECT_SUPPRESSED,
    FaultInjectionHarness,
    FaultManifest,
    FaultTarget,
    clean_snapshot,
)
from .known_issue import (
    CLUSTERED_INTO_OTHER_ROOT,
    DETECTED_CORRECT_FAMILY,
    DETECTED_CORRECT_ROOT,
    DETECTED_SYMPTOM_ONLY,
    DETECTED_WRONG_ROOT,
    DUPLICATED,
    MISSED,
    NO_SIGNAL,
    SUPPRESSED,
    _candidate_text,
    _norm,
)
from .policy_g1 import policy_bundle

# Cycles per run. Four is enough for temporal maturity (which needs >1 cycle)
# and for the pending model to accumulate, without turning the matrix into a
# soak.
DEFAULT_CYCLES = 4


def _root_match(candidate: Mapping[str, Any], tokens: Sequence[str]) -> List[str]:
    text = _candidate_text(candidate)
    return [t for t in tokens if _norm(t) and _norm(t) in text]


def _classify_run(
    manifest: FaultManifest,
    fault_candidates: Sequence[Mapping[str, Any]],
    background_roots: Sequence[str],
    observations: Sequence[Mapping[str, Any]],
    investigations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Detection verdict for one fault run."""
    tokens = list(manifest.expected_root_match)
    matched = [
        c for c in fault_candidates
        if tokens and len(_root_match(c, tokens)) > 0
    ]
    # Did any observation carry the fault at all?
    signal_rows = [
        o for o in observations
        if tokens and any(
            _norm(t) in _norm(str(o.get("payload_summary") or "")) for t in tokens
        )
    ]
    signal_sources = sorted({str(o.get("source") or "") for o in signal_rows if o.get("source")})

    # A suppression-expected manifest is graded on the absence of candidates.
    if manifest.expected_outcome == EXPECT_SUPPRESSED:
        extra = [c for c in fault_candidates if str(c.get("root_id")) not in set(background_roots)]
        return {
            "verdict": SUPPRESSED if not extra else DETECTED_WRONG_ROOT,
            "category": "NOT_A_DISCOVERY_FAILURE" if not extra else "SUPPRESSION_MISS",
            "expected_suppression": True,
            "unexpected_candidates": len(extra),
            "signal_sources": signal_sources,
            "signal_observations": len(signal_rows),
            "matched_candidates": 0,
        }

    if not matched:
        if not signal_rows:
            verdict, category = NO_SIGNAL, "NO_SIGNAL"
        else:
            # Signal reached the engine but nothing carrying it was promoted.
            verdict, category = MISSED, "SCORING_MISS"
        return {
            "verdict": verdict,
            "category": category,
            "signal_sources": signal_sources,
            "signal_observations": len(signal_rows),
            "matched_candidates": 0,
        }

    family_hit = any(
        str(c.get("root_tier") or "") == manifest.expected_root_family for c in matched
    )
    expected_n = int(manifest.expected_candidates or 1)

    # Distinct roots the fault should have produced, per the manifest.
    distinct_roots = {str(c.get("root_id") or "") for c in matched}
    primaries = [c for c in matched if c.get("candidate_role") != "SECONDARY_EFFECT"]

    if manifest.expected_outcome == EXPECT_MULTIPLE:
        # Each expected token must land on its own root, or two distinct causes
        # were collapsed into one.
        per_token = {t: {str(c.get("root_id")) for c in matched if _root_match(c, [t])}
                     for t in tokens}
        collapsed = len({frozenset(v) for v in per_token.values() if v}) < len(
            [v for v in per_token.values() if v]
        )
        if collapsed or len(distinct_roots) < expected_n:
            verdict, category = CLUSTERED_INTO_OTHER_ROOT, "CLUSTERING_MISS"
        elif family_hit:
            verdict, category = DETECTED_CORRECT_ROOT, "NOT_A_DISCOVERY_FAILURE"
        else:
            verdict, category = DETECTED_CORRECT_FAMILY, "NOT_A_DISCOVERY_FAILURE"
    elif len(primaries) > expected_n:
        verdict, category = DUPLICATED, "CONSOLIDATION_MISS"
    elif family_hit:
        verdict, category = DETECTED_CORRECT_ROOT, "NOT_A_DISCOVERY_FAILURE"
    else:
        verdict, category = DETECTED_SYMPTOM_ONLY, "CLUSTERING_MISS"

    investigated = any(
        str(inv.get("candidate_id") or "") in {str(c.get("candidate_id") or "") for c in matched}
        for inv in investigations
    )
    if verdict in {DETECTED_CORRECT_ROOT, DETECTED_CORRECT_FAMILY} and not investigated:
        category = "INVESTIGATION_MISS"

    return {
        "verdict": verdict,
        "category": category,
        "matched_candidates": len(matched),
        "distinct_roots": len(distinct_roots),
        "primary_roots": len(primaries),
        "expected_candidates": expected_n,
        "root_family_hit": family_hit,
        "investigated": investigated,
        "signal_sources": signal_sources,
        "signal_observations": len(signal_rows),
        "matched_root_ids": sorted(distinct_roots),
        "matched_root_titles": sorted({str(c.get("root_title") or "") for c in matched}),
        "matched_scores": sorted(round(float(c.get("score") or 0.0), 4) for c in matched),
    }


def run_control(cycles: int = DEFAULT_CYCLES) -> Dict[str, Any]:
    """The clean arm. Establishes the background candidate set."""
    engine = DiscoveryEngine()
    intel: Dict[str, Any] = {}
    observations: List[Dict[str, Any]] = []
    for cycle in range(1, int(cycles) + 1):
        intel = engine.ingest(clean_snapshot(), api_enabled=False)
        observations.extend(intel.get("observations") or [])
    candidates = [dict(c) for c in (intel.get("incidents") or [])]
    return {
        "cycles": int(cycles),
        "candidates": candidates,
        "root_ids": sorted(str(c.get("root_id") or "") for c in candidates),
        "observations": len(observations),
        "sources": sorted({str(o.get("source") or "") for o in observations if o.get("source")}),
    }


def run_fault(
    manifest: FaultManifest,
    *,
    harness: FaultInjectionHarness,
    cycles: int = DEFAULT_CYCLES,
    background_roots: Sequence[str] = (),
) -> Dict[str, Any]:
    """One fault, one fresh engine, ``cycles`` ingests. Read-only throughout."""
    engine = DiscoveryEngine()
    started = time.time()
    manifest.started_ts = started

    observations: List[Dict[str, Any]] = []
    per_cycle: List[Dict[str, Any]] = []
    cycles_to_observation = 0
    cycles_to_candidate = 0
    t_observation = 0.0
    t_candidate = 0.0
    intel: Dict[str, Any] = {}
    tokens = list(manifest.expected_root_match)
    background = set(background_roots)
    # A candidate is emitted in the cycle its observation is fresh and then
    # suppressed as a duplicate, so it is absent from later cycles' intel.
    # Reading only the final cycle scores the engine for forgetting something
    # it deliberately stopped repeating; candidates accumulate across the run,
    # exactly as the soak runner does.
    all_candidates: Dict[str, Dict[str, Any]] = {}
    all_investigations: Dict[str, Dict[str, Any]] = {}

    for cycle in range(1, int(cycles) + 1):
        snapshot = harness.apply(manifest, clean_snapshot(), cycle, now=time.time())
        intel = engine.ingest(snapshot, api_enabled=False)
        rows = list(intel.get("observations") or [])
        observations.extend(rows)

        if not cycles_to_observation and tokens:
            if any(
                any(_norm(t) in _norm(str(o.get("payload_summary") or "")) for t in tokens)
                for o in rows
            ):
                cycles_to_observation = cycle
                t_observation = round(time.time() - started, 4)

        candidates_now = [dict(c) for c in (intel.get("incidents") or [])]
        for cand in candidates_now:
            key = str(cand.get("root_id") or cand.get("fingerprint") or "")
            prior = all_candidates.get(key)
            if prior is None or float(cand.get("score") or 0) >= float(prior.get("score") or 0):
                all_candidates[key] = {**cand, "first_seen_cycle":
                                       (prior or {}).get("first_seen_cycle", cycle)}
        for inv in intel.get("investigations") or []:
            all_investigations[str(inv.get("investigation_id") or len(all_investigations))] = dict(inv)

        fault_now = [
            c for c in candidates_now
            if str(c.get("root_id")) not in background and (
                not tokens or _root_match(c, tokens)
            )
        ]
        if not cycles_to_candidate and fault_now:
            cycles_to_candidate = cycle
            t_candidate = round(time.time() - started, 4)

        metrics = intel.get("metrics") or {}
        per_cycle.append({
            "cycle": cycle,
            "signals": int(metrics.get("signals") or 0),
            "noise_suppressed": int(metrics.get("noise_suppressed") or 0),
            "duplicates": int(metrics.get("duplicates") or 0),
            "candidates": int(metrics.get("candidate_n") or 0),
            "investigations": int(metrics.get("investigations") or 0),
            "root_clusters": int(metrics.get("root_clusters") or 0),
        })

    manifest.ended_ts = time.time()
    candidates = list(all_candidates.values())
    consolidation = consolidate(candidates)
    candidates = consolidation["candidates"]

    fault_candidates = [
        c for c in candidates
        if str(c.get("root_id")) not in background and (not tokens or _root_match(c, tokens))
    ]
    fault_ids = {id(c) for c in fault_candidates}
    others = [
        c for c in candidates
        if str(c.get("root_id")) not in background and id(c) not in fault_ids
    ]
    # A secondary effect the consolidation stage linked to the fault root, and a
    # second-order symptom the manifest declared, are both consequences of the
    # injected fault. Counting them as noise would report a correct causal link
    # as a precision failure.
    linked = {
        str(r.get("secondary_root_id")) for r in consolidation["relations"]
        if r.get("relationship") in {"SAME_ROOT", "LIKELY_DERIVED_EFFECT"}
        and str(r.get("primary_root_id")) in {str(c.get("root_id")) for c in fault_candidates}
    }
    symptoms = [
        c for c in others
        if str(c.get("root_id")) in linked
        or (manifest.symptom_tokens and _root_match(c, list(manifest.symptom_tokens)))
    ]
    symptom_ids = {id(c) for c in symptoms}
    unrelated = [c for c in others if id(c) not in symptom_ids]
    investigations = list(all_investigations.values())

    detection = _classify_run(
        manifest, fault_candidates, sorted(background), observations, investigations,
    )

    # Precision is over candidates a reviewer would call wrong: unlinked
    # secondary symptoms are an under-consolidation cost, reported separately.
    total_new = len(fault_candidates) + len(symptoms) + len(unrelated)
    return {
        "fault": manifest.evaluator_record(),
        "cycles": int(cycles),
        "detection": detection,
        "cycles_to_observation": cycles_to_observation,
        "cycles_to_candidate": cycles_to_candidate,
        "seconds_to_observation": t_observation,
        "seconds_to_candidate": t_candidate,
        "observations": len(observations),
        "observation_sources": sorted({
            str(o.get("source") or "") for o in observations if o.get("source")
        }),
        "candidates_total": len(candidates),
        "candidates_fault_related": len(fault_candidates),
        "candidates_fault_symptom": len(symptoms),
        "candidates_unrelated": len(unrelated),
        "symptom_titles": sorted({str(c.get("root_title") or "") for c in symptoms}),
        "unrelated_titles": sorted({str(c.get("root_title") or "") for c in unrelated}),
        "run_precision": round(
            (len(fault_candidates) + len(symptoms)) / total_new, 4) if total_new else 1.0,
        # Second-order surfaces of the same fault that were delivered as their
        # own root rather than linked to it.
        "unconsolidated_symptoms": len([c for c in symptoms if str(c.get("root_id")) not in linked]),
        "consolidation": {
            "root_incidents": consolidation["root_incidents"],
            "secondary_effects": consolidation["secondary_effects"],
            "unique_root_inflation": consolidation["unique_root_inflation"],
            "relations": consolidation["relations"],
        },
        "per_cycle": per_cycle,
        "final_metrics": intel.get("metrics") or {},
        # Full records for the fault root and its consequences. A Hybrid replay
        # must receive the evidence the engine actually had, not a summary row:
        # the previous phase sent projections and the model spent hypotheses on
        # gaps that were artefacts of the projection.
        "candidates_full": [dict(c) for c in fault_candidates + symptoms],
        "candidates_detail": [
            {
                "root_id": c.get("root_id"),
                "root_title": c.get("root_title"),
                "root_tier": c.get("root_tier"),
                "score": c.get("score"),
                "severity": c.get("severity"),
                "occurrence_count": c.get("occurrence_count"),
                "affected_components": c.get("affected_components"),
                "candidate_role": c.get("candidate_role"),
                "derived_from": c.get("derived_from"),
                "status": c.get("status"),
                "sources": c.get("sources"),
            }
            for c in candidates
        ],
        "llm_called": False,
        "writes": 0,
    }


def run_matrix(
    *,
    cycles: int = DEFAULT_CYCLES,
    cohort: str = "",
    manifests: Optional[Sequence[FaultManifest]] = None,
) -> Dict[str, Any]:
    """The full matrix, control arm first. LLM off, no sandbox, no writes."""
    rows = list(manifests if manifests is not None else fault_manifests(cohort))
    control = run_control(cycles=cycles)
    background = control["root_ids"]

    target = FaultTarget(label="dev-inprocess-matrix", port=0, is_dev=True)
    results: List[Dict[str, Any]] = []
    with FaultInjectionHarness(enabled=True, target=target,
                               max_faults=len(rows) * cycles + 8,
                               max_cycles=max(cycles, 8)) as harness:
        for manifest in rows:
            results.append(run_fault(
                manifest, harness=harness, cycles=cycles, background_roots=background,
            ))
        isolation = harness.isolation_report()
        audit = harness.audit_records()
        cleanup = harness.cleanup()

    return {
        "schema": "qhos-fault-matrix-v1",
        "policy": policy_bundle(),
        "cycles_per_fault": int(cycles),
        "control": control,
        "results": results,
        "isolation": isolation,
        "cleanup": cleanup,
        "audit_records": len(audit),
        "llm_enabled": False,
        "level3_enabled": False,
        "sandbox_enabled": False,
        "read_only": True,
        "production_targets": 0,
    }
