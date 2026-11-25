"""DISCOVERY_POLICY_G1 — the bundle frozen for controlled fault generalization.

The previous phase froze scoring under ``DISCOVERY_CALIBRATION_V2 v2.1`` and
showed it was *repeatable*: three sessions over one unchanging fault set gave
identical results. Repeatability is not generalization. This phase changes the
underlying fault instead of the window, which only means something if the policy
being measured is pinned for the whole matrix.

G1 is deliberately wider than a scoring hash. A fault matrix result depends on
what the engine can *see* and what an artifact can *carry*, not only on how a
candidate is scored, so the bundle pins four independent revisions:

* the scoring/fingerprint/pending configuration (inherited from V2.1, unchanged);
* the signal inventory, which now includes the attestation source;
* the observation schema, i.e. which decision-relevant fields survive to disk;
* the replay contract, i.e. what a replay is required to reproduce.

The scoring sub-config is asserted byte-identical to what V2.1 froze. Adding a
signal source is not tuning: it changes what reaches the engine, never how the
engine ranks what reaches it. ``test_g1_does_not_tune_v2_scoring`` fails if a
weight, threshold or fingerprint key moves under cover of this bundle.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from .revision import POLICY_REVISION as V2_REVISION, policy_config as v2_policy_config

POLICY_NAME = "DISCOVERY_POLICY_G1"
POLICY_REVISION = "g1"

# What a persisted observation must carry for a replay to reach the same
# decisions as the run that produced it. Bumped when a decision-relevant field
# is added to the artifact.
OBSERVATION_SCHEMA_VERSION = "qhos-observation-v2"

# What a replay promises to reproduce. Bumped when the comparison changes.
REPLAY_CONTRACT_VERSION = "qhos-replay-contract-v1"

# Artifact envelope version for fault-matrix runs.
ARTIFACT_SCHEMA_VERSION = "qhos-fault-matrix-v1"

# Fields whose loss silently changes a replay's decisions. Enforced by
# ``brain/discovery/soak.py`` and asserted by the replay-schema tests.
DECISION_RELEVANT_FIELDS = (
    "id",
    "timestamp",
    "source",
    "component",
    "kind",
    "severity_hint",
    "payload_summary",
    "fingerprint",
    "state",
    "lifecycle_known",
)

# Deterministic outputs a faithful replay must match exactly.
REPLAY_COMPARED_OUTPUTS = (
    "candidate_count",
    "root_ids",
    "scores",
    "suppressed",
    "promotion_reasons",
    "relationships",
)


def scoring_config() -> Dict[str, Any]:
    """The V2.1 scoring bundle, unchanged. Kept separate so it can be diffed."""
    return dict(v2_policy_config())


def policy_config() -> Dict[str, Any]:
    """Everything G1 pins, scoring included."""
    from .inventory import inventory

    return {
        "inherits_scoring_from": f"DISCOVERY_CALIBRATION_V2 {V2_REVISION}",
        "scoring": scoring_config(),
        "signal_sources": sorted(s.get("name") or "" for s in inventory().get("sources") or []),
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "decision_relevant_fields": list(DECISION_RELEVANT_FIELDS),
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "replay_compared_outputs": list(REPLAY_COMPARED_OUTPUTS),
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
    }


def _hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def config_hash() -> str:
    return _hash(policy_config())


def scoring_hash() -> str:
    """Hash of the scoring sub-config alone, for the no-tuning assertion."""
    return _hash(scoring_config())


def repo_sha() -> str:
    from brain.attest import git_revision

    return str(git_revision().get("revision") or "")


def policy_bundle() -> Dict[str, Any]:
    """Provenance stamp for every fault-matrix run and artifact."""
    from brain.attest import source_revision
    from .fingerprint import ROOT_FINGERPRINT_VERSION
    from .revision import SIGNAL_INVENTORY_REVISION, policy_revision as v2_revision
    from .score import SCORE_VERSION

    return {
        "policy": POLICY_NAME,
        "revision": POLICY_REVISION,
        "config_hash": config_hash(),
        "scoring_hash": scoring_hash(),
        "scoring_policy": v2_revision(),
        "score_version": SCORE_VERSION,
        "fingerprint_version": ROOT_FINGERPRINT_VERSION,
        "pending_policy_revision": V2_REVISION,
        "signal_inventory_revision": SIGNAL_INVENTORY_REVISION,
        "observation_schema_version": OBSERVATION_SCHEMA_VERSION,
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "repo_sha": repo_sha(),
        "source_revision": source_revision()[0],
        "source_files": source_revision()[1],
        "frozen": True,
    }


# ---------------------------------------------------------------------------
# Findings recorded against G1 during the fault matrix. Captured before any fix
# so the pre-fix behaviour stays available (§24/§25).
# ---------------------------------------------------------------------------

G1_FINDINGS: List[Dict[str, Any]] = [
    {
        "id": "G1_FINDING_01",
        "severity": "SERIOUS",
        "category": "SCORING",
        "title": "root_signature measures message parseability, not evidence quality",
        "detail": (
            "Six of 28 detectable faults produced a correct candidate that never "
            "opened an investigation. Every one of them had a non-concrete root, "
            "so the root_signature feature contributed 0.0 of its 0.14 weight and "
            "the candidate could not reach the 0.42 threshold on its first cycle. "
            "The affected rows are not low-quality evidence: a registry entry "
            "reporting healthy=false and an attestation reporting a production "
            "revision mismatch are the system of record asserting its own state. "
            "They score zero because there is no exception name to extract from a "
            "status word."
        ),
        "measured_under_g1": {
            "investigation_rate": 0.7857,
            "affected": ["F02a", "F02b", "F03b", "F08a", "F08b"],
            "scores": {"F02a": 0.358, "F02b": 0.304, "F03b": 0.382,
                       "F08a": 0.408, "F08b": 0.358},
            "threshold": 0.42,
        },
        "proposal": "DISCOVERY_POLICY_G1.1 authoritative_state (half credit)",
        "fixed": False,
    },
    {
        "id": "G1_FINDING_02",
        "severity": "MODERATE",
        "category": "FINGERPRINT",
        "title": "lowercase dotted exception names are not extracted",
        "detail": (
            "_EXCEPTION_RE requires an initial capital, so socket.gaierror falls "
            "through to the message-skeleton fallback and its root is MESSAGE tier "
            "with concrete=False. The same failure raised as ConnectionRefusedError "
            "is extracted correctly, so whether a root is concrete depends on the "
            "casing convention of the library that raised it."
        ),
        "measured_under_g1": {"affected": ["F03b"], "tier": "MESSAGE", "concrete": False},
        "proposal": "DISCOVERY_POLICY_G1.1 dotted exception extraction",
        "fixed": False,
    },
    {
        "id": "G1_FINDING_03",
        "severity": "MODERATE",
        "category": "PENDING_TEMPORAL",
        "title": "evidence-based pending promotion fires at zero elapsed duration",
        "detail": (
            "Reproduced deliberately rather than remembered. A component reporting "
            "pending with no lifecycle telemetry is promoted at severity high on "
            "its first observed cycle with pending_duration_s=0.0 and "
            "cycles_pending=1, as soon as any independent error evidence names it. "
            "The direction is right: suppression must not be absolute. The degree "
            "is not bounded by anything, so one sample and one hour of failure are "
            "reported identically."
        ),
        "measured_under_g1": {
            "probe_a_no_evidence_promoted": False,
            "probe_a_still_suppressed_after_5_cycles": True,
            "probe_b_promoted": True,
            "probe_b_duration_s": 0.0,
            "probe_b_cycles_pending": 1,
            "probe_b_severity": "high",
        },
        "proposal": "DISCOVERY_POLICY_G1.1 severity maturity (medium until cycle 2)",
        "fixed": False,
    },
    {
        "id": "G1_FINDING_04",
        "severity": "MODERATE",
        "category": "CONSOLIDATION",
        "title": "a second surface of one fault is delivered as its own root",
        "detail": (
            "A queue backlog raises both a QueueBacklogError candidate and a "
            "separate 'retries=N' candidate; a parser timeout raises both a "
            "TimeoutError candidate and a separate latency candidate. The "
            "consolidation stage links a concrete root to a benign STATE effect "
            "but not to a second MESSAGE-tier symptom, so one fault is reported as "
            "two independent roots. Duplicate escape 0.0882 (3 of 34 candidates)."
        ),
        "measured_under_g1": {
            "duplicate_escape": 0.0882,
            "affected": ["F04b", "F12a", "F12b"],
        },
        "proposal": "none yet; widening effect tiers to MESSAGE would over-link",
        "fixed": False,
    },
    {
        "id": "G1_FINDING_05",
        "severity": "MODERATE",
        "category": "REPLAY_FIDELITY",
        "title": "the intel observation projection is capped at 40 rows per cycle",
        "detail": (
            "engine.ingest returns observations[:40]. A cycle carrying more than "
            "40 observations therefore persists an artifact that cannot reproduce "
            "its own suppression count: F10a replayed 157 suppressions against 197 "
            "live, F10b 157 against 277. Candidate, root and score agreement stay "
            "at 1.000 because the truncated rows are all benign duplicates, so "
            "candidate-level retrospectives remain sound; suppression-rate "
            "retrospectives on high-volume cycles do not."
        ),
        "measured_under_g1": {
            "replay_fidelity_rate": 0.9333,
            "candidate_agreement_rate": 1.0,
            "score_agreement_rate": 1.0,
            "root_agreement_rate": 1.0,
            "affected": ["F10a", "F10b"],
            "diverged_on": ["suppressed"],
        },
        "proposal": (
            "resolved in the architecture consolidation phase. The cap was a "
            "display limit sitting on the decision path, so it was removed from "
            "the intelligence blob — now declared full_fidelity — and display "
            "bounding moved to brain.view_model, where a limit changes only what "
            "is shown. Re-measured across the same 30 artifacts: "
            "replay_fidelity_rate 1.0, diverged_on {}."
        ),
        "fixed": True,
        "measured_after_fix": {
            "replay_fidelity_rate": 1.0,
            "candidate_agreement_rate": 1.0,
            "score_agreement_rate": 1.0,
            "root_agreement_rate": 1.0,
            "diverged_on": {},
        },
        "defect_ledger_id": "MD-07",
    },
    {
        "id": "G1_MEASUREMENT_DEFECT_01",
        "severity": "SERIOUS",
        "category": "MEASUREMENT",
        "title": "the first matrix run read only the final cycle's candidates",
        "detail": (
            "A candidate is emitted in the cycle its observation is fresh and then "
            "suppressed as a duplicate, so it is absent from later cycles. Reading "
            "only the final cycle scored the engine for deliberately not repeating "
            "itself and reported F02a, F02b, F07a, F08a and F08b as MISSED when all "
            "five had produced the correct candidate. Fixed before the primary "
            "matrix by accumulating candidates across cycles, as the soak runner "
            "already did."
        ),
        "pre_fix_measurement": {
            "reported_missed": ["F02a", "F02b", "F07a", "F08a", "F08b"],
            "true_verdict": "DETECTED_CORRECT_ROOT for all five",
        },
        "fixed": True,
    },
    {
        "id": "G1_MEASUREMENT_DEFECT_02",
        "severity": "SERIOUS",
        "category": "MEASUREMENT",
        "title": "root_title smuggled the component name back into evidence matching",
        "detail": (
            "Known-issue evaluator v2 excludes component fields so a shared "
            "component name cannot create a match. It still matched on root_title, "
            "which for a STATE root is built as '{component_family} state={state}' "
            "— reintroducing the exact v1 defect through the side door. The family "
            "token is now stripped from root_title before matching."
        ),
        "fixed": True,
    },
    {
        "id": "G1_MEASUREMENT_DEFECT_03",
        "severity": "SERIOUS",
        "category": "MEASUREMENT",
        "title": "hybrid value was credited for disagreeing with an empty baseline",
        "detail": (
            "The incremental-value classifier scored the model's top hypothesis "
            "against the deterministic arm's `interpretation` field alone. For an "
            "ERROR_CODE-tier candidate that field is the log title, so overlap was "
            "trivially 0.0 and all four paid cases were awarded ROOT_CAUSE_VALUE "
            "for 'diverging' from a conclusion the engine never asserted — the "
            "inflation §44 prohibits. Overlap is now measured against the whole "
            "deterministic conclusion, the boilerplate `uncertainty` disclaimer is "
            "excluded from the substance test, and where the engine made no root "
            "claim the model must supply a mechanism grounded in the evidence sent. "
            "Re-scoring the same paid outputs moved F07b to VERIFICATION_VALUE."
        ),
        "fixed": True,
    },
    {
        "id": "G1_MEASUREMENT_DEFECT_04",
        "severity": "MODERATE",
        "category": "MEASUREMENT",
        "title": "paid replay resume treated a failed row as completed work",
        "detail": (
            "The checkpoint keyed 'done' on row presence. An ungated first attempt "
            "wrote four provider_unavailable rows; the gated rerun then skipped all "
            "four and reported zero calls and zero cost as though the budget had "
            "been spent successfully. Resume now only skips rows that actually "
            "reached the provider."
        ),
        "fixed": True,
    },
    {
        "id": "G1_MANIFEST_DEFECT_01",
        "severity": "MODERATE",
        "category": "MANIFEST",
        "title": "F13b lands on a documented pre-existing failure",
        "detail": (
            "F13b injects a collection error naming tests/test_market_clock.py, "
            "which is in KNOWN_COLLECTION_ERRORS. is_preexisting suppresses it as "
            "KNOWN_BASELINE, so the manifest's expected CANDIDATE never appears. "
            "By the letter of the manifest this is a SCORING_MISS; on adjudication "
            "it is correct engine behaviour against a badly authored manifest, and "
            "it demonstrates that baseline suppression generalizes. Left unchanged: "
            "editing a holdout after seeing its result would destroy it as evidence."
        ),
        "raw_verdict": "MISSED / SCORING_MISS",
        "adjudicated_verdict": "correct suppression of a documented baseline failure",
        "fixed": False,
    },
]


def g1_findings() -> List[Dict[str, Any]]:
    return [dict(f) for f in G1_FINDINGS]
