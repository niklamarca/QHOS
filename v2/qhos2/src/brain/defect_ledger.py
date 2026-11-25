"""Measurement defect ledger.

Every defect here was found in the *measurement* apparatus, not in the system
being measured. They are recorded centrally because a defect in an evaluator
changes what past numbers mean, and a reader needs to know that without having
to re-read the phase reports.

The critical field is `result_impact`. A defect that was fixed forward still
leaves historical artifacts wrong; saying so explicitly is the only way an old
number stays interpretable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "qhos-measurement-defects-v1"

STATES: Tuple[str, ...] = (
    "OPEN",
    "FIXED_FORWARD",
)

RESULT_IMPACT: Tuple[str, ...] = (
    "INVALIDATES_RESULT",
    "DOES_NOT_INVALIDATE_RESULT",
    "HISTORICAL_ARTIFACT_AFFECTED",
)

SEVERITIES: Tuple[str, ...] = ("CRITICAL", "SERIOUS", "MODERATE", "MINOR")

# Kept in discovery order. IDs are permanent: referenced from phase reports.
DEFECTS: Tuple[Dict[str, Any], ...] = (
    {
        "id": "MD-01",
        "title": "ground-truth matcher accepted a shared component name as a detection",
        "phase": "shadow generalization",
        "severity": "CRITICAL",
        "state": "FIXED_FORWARD",
        "result_impact": ["HISTORICAL_ARTIFACT_AFFECTED"],
        "detail": (
            "Known-issue recall matched an injected issue whenever a candidate "
            "mentioned the same component. 'dashboard appears somewhere' counted "
            "as detecting 'dashboard runtime is stale', which inflated recall "
            "with candidates that had found nothing."
        ),
        "found_by": "manual review of a 2/3 recall result that looked too good",
        "fix": (
            "known_issue.py v2 matches on independent evidence — root family, "
            "expected observation, fault signature, attestation state — and "
            "excludes component fields from the match entirely."
        ),
        "affects_artifacts": ["multi_session_review.json", "long_soak_review.json"],
        "invalidated_metrics": ["known_issue_recall (v1)"],
        "superseded_by": "brain.discovery.known_issue.known_issue_recall_v2",
    },
    {
        "id": "MD-02",
        "title": "zero-token and circuit-breaker rows counted as model results",
        "phase": "real cognitive shadow validation",
        "severity": "SERIOUS",
        "state": "FIXED_FORWARD",
        "result_impact": ["HISTORICAL_ARTIFACT_AFFECTED"],
        "detail": (
            "An empty provider packet has several very different causes — token "
            "truncation, schema rejection, timeout, an open breaker, a genuine "
            "refusal — and all of them arrived as an indistinguishable empty "
            "dict. Fallback rows were therefore counted toward Hybrid value."
        ),
        "found_by": "a Hybrid 'success' with zero output tokens",
        "fix": (
            "provider_contract.classify_output assigns an explicit output status "
            "and only VALID real-model rows set counts_as_hybrid_success."
        ),
        "affects_artifacts": ["hybrid_replay_smoke.json"],
        "invalidated_metrics": ["early hybrid success counts"],
    },
    {
        "id": "MD-03",
        "title": "replay artifacts omitted state and lifecycle_known",
        "phase": "real shadow generalization",
        "severity": "CRITICAL",
        "state": "FIXED_FORWARD",
        "result_impact": ["HISTORICAL_ARTIFACT_AFFECTED"],
        "detail": (
            "Observation persistence dropped `state` and `lifecycle_known`. "
            "Pending classification branches on both, so every replay of a "
            "recorded session took a different path from the run it was "
            "replaying. The artifacts could not be used to audit their own runs."
        ),
        "found_by": "replay of a soak session produced different candidates",
        "fix": (
            "Both fields are persisted and are part of the declared "
            "decision-relevant field set in the replay fidelity contract."
        ),
        "affects_artifacts": ["soak A/B/C sessions"],
        "historical_label": "replay_faithful=false, never re-scored",
    },
    {
        "id": "MD-04",
        "title": "the fault matrix evaluated only the final cycle's candidates",
        "phase": "controlled fault generalization",
        "severity": "SERIOUS",
        "state": "FIXED_FORWARD",
        "result_impact": ["DOES_NOT_INVALIDATE_RESULT"],
        "detail": (
            "Detection was read from the last cycle's incident list. A candidate "
            "that appeared on cycle 1 and was then correctly suppressed as a "
            "duplicate looked like a miss, under-counting five faults."
        ),
        "found_by": "five INVESTIGATION_MISS verdicts that contradicted the logs",
        "fix": "run_fault matrix accumulates candidates across all cycles.",
        "affects_artifacts": ["fault_matrix_g1_run1_premeasurementfix.json"],
        "note": "the pre-fix artifact is retained under its own name as evidence",
    },
    {
        "id": "MD-05",
        "title": "root_title reintroduced component-name matching",
        "phase": "controlled fault generalization",
        "severity": "SERIOUS",
        "state": "FIXED_FORWARD",
        "result_impact": ["DOES_NOT_INVALIDATE_RESULT"],
        "detail": (
            "The v2 evaluator excluded component fields, but still matched on "
            "root_title, which for a STATE-tier root is built as "
            "'{component_family} state={state}'. MD-01 came back through the "
            "side door of a field that was not obviously a component field."
        ),
        "found_by": "auditing v2 against the defect it was written to fix",
        "fix": "the component family is stripped from root_title before matching.",
        "affects_artifacts": [],
        "note": "caught before it produced a reported number",
    },
    {
        "id": "MD-06",
        "title": "Hybrid value credited for disagreeing with an empty baseline",
        "phase": "controlled fault generalization",
        "severity": "SERIOUS",
        "state": "FIXED_FORWARD",
        "result_impact": ["HISTORICAL_ARTIFACT_AFFECTED"],
        "detail": (
            "Incremental value scored the model's top hypothesis against the "
            "deterministic arm's `interpretation` field alone. For an "
            "ERROR_CODE-tier candidate that field is only the log title, so "
            "overlap was trivially 0.0 and every case was awarded "
            "ROOT_CAUSE_VALUE for 'diverging' from a conclusion the engine had "
            "never asserted. The constant `uncertainty` disclaimer also padded "
            "the substance test."
        ),
        "found_by": "all four paid cases scoring identically on the top class",
        "fix": (
            "Overlap is measured against the whole deterministic conclusion; "
            "boilerplate is excluded from the substance test; and where the "
            "engine asserted no root the model must supply a mechanism grounded "
            "in the evidence that was sent."
        ),
        "affects_artifacts": ["hybrid_replay_faultgen.json (re-scored offline)"],
        "invalidated_metrics": ["first-pass ROOT_CAUSE_VALUE on F07b"],
    },
    {
        "id": "MD-07",
        "title": "a display cap on the intelligence blob changed replay semantics",
        "phase": "controlled fault generalization → architecture consolidation",
        "severity": "SERIOUS",
        "state": "FIXED_FORWARD",
        "result_impact": ["HISTORICAL_ARTIFACT_AFFECTED"],
        "detail": (
            "The engine truncated `observations` to 40 rows (and candidates to "
            "12, root clusters to 12) inside the artifact that soak and fault "
            "runs persist. Because replay consumes that artifact, a limit whose "
            "only purpose was presentation silently decided what could be "
            "replayed, and high-volume benign-warning faults reported a "
            "different suppressed count on replay than they did live."
        ),
        "found_by": "replay fidelity stuck at 0.9333 with suppression as the only divergence",
        "fix": (
            "The intelligence blob is now declared full-fidelity and carries no "
            "presentation caps. Bounding moved to the layers that need it — "
            "brain.view_model for the UI and brain.context for provider packets "
            "— where a limit affects only what is shown or sent."
        ),
        "affects_artifacts": ["replay_fidelity_g1.json (F10a, F10b)"],
        "invalidated_metrics": ["replay_fidelity_rate 0.9333"],
    },
    {
        "id": "MD-08",
        "title": "paid replay resume treated a failed row as completed work",
        "phase": "controlled fault generalization",
        "severity": "MODERATE",
        "state": "FIXED_FORWARD",
        "result_impact": ["DOES_NOT_INVALIDATE_RESULT"],
        "detail": (
            "The checkpoint keyed 'done' on row presence. Four "
            "provider_unavailable rows from an ungated attempt were skipped on "
            "the gated rerun, which then reported zero calls at zero cost as "
            "though the budget had been honoured — a silent false negative on "
            "the most expensive path in the system."
        ),
        "found_by": "a paid run completing in 693ms",
        "fix": "resume only skips rows where a provider call actually happened.",
        "affects_artifacts": [],
    },
    {
        "id": "MD-09",
        "title": "brain_shadow_mode is decorative and never reaches the policy",
        "phase": "architecture consolidation",
        "severity": "MODERATE",
        "state": "OPEN",
        "result_impact": ["DOES_NOT_INVALIDATE_RESULT"],
        "detail": (
            "runtime.py constructs AutonomyPolicy(level=level, shadow=True) with "
            "the literal, so the configured brain_shadow_mode value is never "
            "read. Shadow mode is genuinely on — it is hardcoded — but every "
            "safety report that cited the flag as evidence was citing a value "
            "with no causal connection to behaviour."
        ),
        "found_by": "config consumption audit during consolidation",
        "fix": (
            "Not silently changed. Wiring the flag would make shadow mode "
            "env-disableable, which is strictly less safe than the hardcode. "
            "The flag is documented as non-authoritative and the invariant test "
            "now asserts against the hardcoded policy instead."
        ),
        "affects_artifacts": ["every phase report's safety-flag table"],
        "note": (
            "the invariant held for a better reason than the reports claimed; "
            "the claim was weak, not the behaviour"
        ),
    },
)


def defects() -> List[Dict[str, Any]]:
    return [dict(d) for d in DEFECTS]


def defect(defect_id: str) -> Optional[Dict[str, Any]]:
    for row in DEFECTS:
        if row["id"] == defect_id:
            return dict(row)
    return None


def open_defects() -> List[Dict[str, Any]]:
    return [dict(d) for d in DEFECTS if d["state"] == "OPEN"]


def affected_artifacts() -> Dict[str, List[str]]:
    """Artifact name -> defect ids that touched it. Used to gate comparisons."""
    out: Dict[str, List[str]] = {}
    for row in DEFECTS:
        for name in row.get("affects_artifacts") or []:
            out.setdefault(str(name), []).append(row["id"])
    return out


def invalidated_metrics() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for row in DEFECTS:
        for metric in row.get("invalidated_metrics") or []:
            out.setdefault(str(metric), []).append(row["id"])
    return out


def ledger() -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "states": list(STATES),
        "result_impact_classes": list(RESULT_IMPACT),
        "total": len(DEFECTS),
        "open": len(open_defects()),
        "fixed_forward": sum(1 for d in DEFECTS if d["state"] == "FIXED_FORWARD"),
        "by_severity": {
            sev: sum(1 for d in DEFECTS if d["severity"] == sev) for sev in SEVERITIES
        },
        "defects": defects(),
        "affected_artifacts": affected_artifacts(),
        "invalidated_metrics": invalidated_metrics(),
    }


def metric_is_trustworthy(metric: str) -> Dict[str, Any]:
    """Whether a named metric was produced by an evaluator with a known defect."""
    hits = invalidated_metrics().get(metric) or []
    return {
        "metric": metric,
        "trustworthy": not hits,
        "invalidated_by": hits,
        "reason": (
            "no known measurement defect affects this metric" if not hits
            else f"produced by an evaluator with known defects: {', '.join(hits)}"
        ),
    }
