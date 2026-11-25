"""Frozen Discovery calibration policy record.

A soak result is only scientifically attributable if the policy that produced it
is identifiable. This module pins the exact configuration under a named revision
and hashes it, so a session artifact can say *which* Discovery produced it and a
test can fail when constants move without a revision bump.

The hash is computed from the live constants, not from a copy. Editing a weight
in ``score.py`` changes ``config_hash`` and breaks ``test_frozen_calibration_*``
until the revision is bumped deliberately.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping

from .fingerprint import ROOT_FINGERPRINT_VERSION, ROOT_TIERS
from .pending import (
    DEFAULT_EXPECTED_CYCLES,
    DEFAULT_EXPECTED_S,
    DEGRADED_MULTIPLIER,
    EXPECTED_DURATION_S,
    PENDING_CLASSES,
    STALE_MULTIPLIER,
    SYSTEMIC_UNKNOWN_MIN_COMPONENTS,
    SYSTEMIC_UNKNOWN_MIN_CYCLES,
)
from .score import EXPECTED_STATE_PENALTY, SCORE_VERSION, SCORE_WEIGHTS

POLICY_NAME = "DISCOVERY_CALIBRATION_V2"

# Bumped when the frozen configuration changes deliberately. See CHANGES below.
POLICY_REVISION = "v2.1"

SIGNAL_INVENTORY_REVISION = "signal-inventory-2026-09-03"

# Promotion threshold lives in engine.py; imported lazily to avoid a cycle.
def _threshold() -> float:
    from .engine import THRESHOLD

    return float(THRESHOLD)


def policy_config() -> Dict[str, Any]:
    """The exact knobs that decide what becomes a candidate."""
    return {
        "score_version": SCORE_VERSION,
        "score_weights": dict(sorted(SCORE_WEIGHTS.items())),
        "expected_state_penalty": EXPECTED_STATE_PENALTY,
        "promotion_threshold": _threshold(),
        "fingerprint_version": ROOT_FINGERPRINT_VERSION,
        "root_tiers": list(ROOT_TIERS),
        "pending_classes": list(PENDING_CLASSES),
        "pending_default_expected_s": DEFAULT_EXPECTED_S,
        "pending_default_expected_cycles": DEFAULT_EXPECTED_CYCLES,
        "pending_degraded_multiplier": DEGRADED_MULTIPLIER,
        "pending_stale_multiplier": STALE_MULTIPLIER,
        "pending_expected_duration_s": dict(sorted(EXPECTED_DURATION_S.items())),
        "systemic_unknown_min_components": SYSTEMIC_UNKNOWN_MIN_COMPONENTS,
        "systemic_unknown_min_cycles": SYSTEMIC_UNKNOWN_MIN_CYCLES,
        "signal_inventory_revision": SIGNAL_INVENTORY_REVISION,
    }


def config_hash() -> str:
    raw = json.dumps(policy_config(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def policy_revision() -> Dict[str, Any]:
    """Provenance stamp for a soak session or replay artifact."""
    return {
        "policy": POLICY_NAME,
        "revision": POLICY_REVISION,
        "config_hash": config_hash(),
        "score_version": SCORE_VERSION,
        "fingerprint_version": ROOT_FINGERPRINT_VERSION,
        "signal_inventory_revision": SIGNAL_INVENTORY_REVISION,
        "frozen": True,
    }


# ---------------------------------------------------------------------------
# Recorded findings against a frozen revision.
# ---------------------------------------------------------------------------

CALIBRATION_FINDINGS: List[Dict[str, Any]] = [
    {
        "id": "CALIBRATION_V2_FAILURE_01",
        "severity": "SERIOUS",
        "found_in_revision": "v2.0",
        "fixed_in_revision": "v2.1",
        "category": "FALSE_NEGATIVE",
        "title": "UNKNOWN_PENDING suppression was absolute on the real signal path",
        "detail": (
            "v2.0 could only override UNKNOWN_PENDING suppression via "
            "dependent_impact, which the engine derived by matching a pending "
            "component's family against error_components. Every error_tail "
            "observation is attributed to component 'logs', so error_components "
            "was always {'logs'} and could never match a registry component "
            "family. error_evidence was never passed at all. A component "
            "reporting pending with no lifecycle telemetry therefore stayed "
            "suppressed no matter how much independent error evidence existed, "
            "contradicting the requirement that suppression not be absolute."
        ),
        "pre_fix_measurement": {
            "scenario": "risk_engine pending (uptime=null, error_count=0) + 4 cycles of "
                        "RiskLimitBreachError code=RL07 in error_log naming risk_engine",
            "pending_classification": "UNKNOWN_PENDING",
            "promoted": False,
            "promotion_reason": "no_lifecycle_telemetry_duration_not_evidence",
            "known_issue_recall": 0.0,
        },
        "fix": (
            "Canonical component matching (component_key) plus explicit "
            "attribution of error text to named components, so an independently "
            "evidenced failure sets error_evidence and promotes FAILED_PENDING."
        ),
        "affects_score_weights": False,
        "affects_fingerprint_keys": False,
        "direction": "adds promotions only when independent evidence exists",
    },
    {
        "id": "MEASUREMENT_DEFECT_01",
        "severity": "SERIOUS",
        "found_in_revision": "v2.1",
        "fixed_in_revision": "v2.1",
        "category": "MEASUREMENT",
        "title": "the false-negative audit could not read the investigation stage",
        "detail": (
            "retrospect_issue matched stage rows on summary-shaped keys only. An "
            "Investigation record carries goal/scope/evidence and no summary, so "
            "the investigation stage matched nothing for every issue and each "
            "surfaced issue was reported as INVESTIGATION_MISS. Known-issue "
            "recall read 1.0 while every category read as a miss."
        ),
        "pre_fix_measurement": {
            "known_issue_recall": 1.0,
            "by_category": {"INVESTIGATION_MISS": 3},
            "contradiction": "recall 1.0 alongside three reported misses",
        },
        "fix": "read each stage in its own shape; report investigated separately from surfaced",
        "affects_score_weights": False,
        "affects_fingerprint_keys": False,
        "direction": "measurement only; the engine is unchanged",
    },
    {
        "id": "MEASUREMENT_DEFECT_02",
        "severity": "SERIOUS",
        "found_in_revision": "v2.1",
        "fixed_in_revision": "v2.1",
        "category": "MEASUREMENT",
        "title": "component-name matching inflated known-issue recall",
        "detail": (
            "KnownIssue.matches_text treated a component-name mention as evidence "
            "that the issue had surfaced, even when keywords were supplied. "
            "'dashboard runs a stale Brain revision' was scored as surfaced "
            "because a candidate said 'dashboard state=pending'. Recall read "
            "3/3 when the honest answer was 2/3 with one NO_SIGNAL."
        ),
        "pre_fix_measurement": {"known_issue_recall": 1.0, "true_value": 0.6667},
        "fix": "keywords decide when supplied; component is only a keyword-free fallback",
        "affects_score_weights": False,
        "affects_fingerprint_keys": False,
        "direction": "measurement only; lowers a previously reported number",
    },
    {
        "id": "OBSERVABILITY_DEFECT_01",
        "severity": "MODERATE",
        "found_in_revision": "v2.1",
        "fixed_in_revision": "v2.1 (future sessions only)",
        "category": "REPLAY_FIDELITY",
        "title": "the soak artifact dropped the fields pending classification depends on",
        "detail": (
            "sanitize_observation_row persisted neither state nor "
            "lifecycle_known. Replaying a recorded session therefore defaulted "
            "lifecycle_known to True and took the EXPECTED_INITIALIZATION branch "
            "for rows that had been UNKNOWN_PENDING live, producing 14 candidates "
            "where the live run produced 3. The artifact could not be used to "
            "audit the run it came from."
        ),
        "pre_fix_measurement": {
            "live_candidates": 3,
            "replay_candidates": 14,
            "sessions_affected": ["A", "B", "C"],
        },
        "fix": (
            "persist state and lifecycle_known; flag replay_faithful=false when a "
            "replay reaches a different candidate count than the run it replays. "
            "Sessions A/B/C were recorded before the fix and stay unfaithful."
        ),
        "affects_score_weights": False,
        "affects_fingerprint_keys": False,
        "direction": "artifact fidelity only; the live pipeline is unchanged",
    },
]

CHANGES: Dict[str, List[str]] = {
    "v2.0": [
        "root fingerprinting (rootfp-v1)",
        "pending temporal model + UNKNOWN_PENDING lifecycle rule",
        "systemic unknown-pending collapse",
        "discovery-score-v2 with explicit feature contributions",
    ],
    "v2.1": [
        "CALIBRATION_V2_FAILURE_01: canonical component matching so independent "
        "error evidence can override UNKNOWN_PENDING suppression",
        "MEASUREMENT_DEFECT_01/02 and OBSERVABILITY_DEFECT_01: evaluation and "
        "artifact fixes only. policy_config() is unchanged, so config_hash stays "
        "04f2ec81fc26e408 across the whole multi-session validation.",
    ],
}


def calibration_findings() -> List[Dict[str, Any]]:
    return [dict(f) for f in CALIBRATION_FINDINGS]


def session_provenance(extra: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    """Provenance block embedded in every soak session artifact."""
    out: Dict[str, Any] = {
        "schema": "qhos-session-provenance-v1",
        "discovery_policy": policy_revision(),
        "llm_enabled": False,
        "level3_enabled": False,
        "sandbox_enabled": False,
    }
    out.update(dict(extra or {}))
    return out
