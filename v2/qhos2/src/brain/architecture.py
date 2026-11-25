"""Brain architecture identity: version, schema registry, feature and authority maps.

This module is configuration and provenance, not marketing. Everything a
consumer needs in order to decide whether two artifacts are comparable, or
whether a capability is allowed to run, is declared here once.

It deliberately has no imports from the rest of Brain beyond stdlib and a lazy
revision lookup, so that anything may import it without creating a cycle.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

ARCHITECTURE_VERSION = 1
RELEASE_CHANNEL = "LEVEL2_SHADOW_RC"
FREEZE_DATE = "2026-09-03"

# --- schema registry --------------------------------------------------------
# Every persisted or transported schema string, in one place. Renaming a value
# here breaks historical artifacts, so treat these as append-only: add a new
# version rather than editing an existing one.
SCHEMAS: Dict[str, str] = {
    "brain_snapshot": "qhos-brain-v1",
    "lacos_contract": "qhos-brain-lacos-v1",
    "cognitive": "qhos-brain-cognitive-v1",
    "shadow": "qhos-brain-shadow-v1",
    "intelligence": "qhos-brain-intelligence-v1",
    "pending_model": "qhos-pending-model-v1",
    "runtime_attestation": "qhos-runtime-attestation-v1",
    "route": "qhos-brain-v1",
    "soak_session": "qhos-brain-soak-v1",
    "replay": "qhos-brain-replay-v1",
    "review": "qhos-brain-review-v1",
    "telemetry_ledger": "qhos-telemetry-ledger-v1",
    "fault_matrix": "qhos-fault-matrix-v1",
    "observation": "qhos-observation-v2",
    "replay_contract": "qhos-replay-contract-v1",
    "score": "discovery-score-v2",
    "fingerprint": "rootfp-v1",
    # Introduced by this consolidation phase.
    "view_model": "qhos-brain-view-v1",
    "event_stream": "qhos-brain-events-v1",
    "defect_ledger": "qhos-measurement-defects-v1",
    "provenance": "qhos-evaluation-provenance-v1",
}

# --- feature status ---------------------------------------------------------
FEATURE_STATES: Tuple[str, ...] = (
    "ACTIVE",
    "AVAILABLE_OPT_IN",
    "EXPERIMENTAL",
    "CAPABLE_NOT_ENABLED",
    "DISABLED",
    "DEFERRED",
    "DEPRECATED",
)

FEATURES: Dict[str, Dict[str, Any]] = {
    "DETERMINISTIC_DISCOVERY": {
        "status": "ACTIVE",
        "detail": "Default and only always-on reasoning path. No LLM, no network, read-only.",
        "evidence": "validated on 15 controlled fault classes x 2 manifestations",
        "requires": [],
    },
    "RUNTIME_ATTESTATION": {
        "status": "ACTIVE",
        "detail": "First-class Observation signal source; closed the KI-03 NO_SIGNAL gap.",
        "evidence": "attestation source coverage 1.0 on faults expecting it",
        "requires": [],
    },
    "REAL_HYBRID": {
        "status": "AVAILABLE_OPT_IN",
        "detail": "Paid remote LLM. Requires explicit flags plus per-run call/token/timeout budgets.",
        "evidence": "3 of 4 information-rich cases showed value; n too small to validate",
        "requires": ["brain_llm_api_enabled", "explicit invocation gate"],
        "evidence_gated": True,
    },
    "FABLE_DERIVED": {
        "status": "EXPERIMENTAL",
        "detail": "Alternative investigation strategy. Never the default; removable.",
        "evidence": "NO_MEASURED_VALUE",
        "requires": ["explicit strategy='fable_derived'"],
        "removable_without_breaking_core": True,
    },
    "LEVEL3_SANDBOX": {
        "status": "CAPABLE_NOT_ENABLED",
        "detail": "Isolated git worktree execution. Hardcoded off; not env-configurable.",
        "evidence": "not evaluated",
        "requires": ["source change to brain/sandbox.py SANDBOX_ENABLED"],
    },
    "CONTINUOUS_LIVE_LLM": {
        "status": "DISABLED",
        "detail": "No observation cycle ever calls a remote model.",
        "evidence": "asserted by tests",
        "requires": [],
    },
    "LIVE_SHADOW_SOAK": {
        "status": "AVAILABLE_OPT_IN",
        "detail": "Bounded read-only observation collection. Off unless explicitly enabled.",
        "evidence": "3 real sessions, 60 minutes, 16,461 signals",
        "requires": ["brain_live_shadow_soak_enabled"],
    },
    "LEGACY_SHADOW_CANDIDATES": {
        "status": "DEPRECATED",
        "detail": (
            "brain/shadow.py builds a second, root-unaware candidate shape that "
            "the UI renders alongside Discovery candidates. Superseded by "
            "PotentialIncident; retained only because the shadow feedback "
            "endpoint and its dispositions still key off it."
        ),
        "evidence": "see architecture_contracts.md, candidate shape divergence",
        "requires": [],
        "replaced_by": "brain.discovery.incident.PotentialIncident",
    },
}

# --- authority map ----------------------------------------------------------
# Brain recommendation != execution authorization. This table is the whole
# claim, stated so it can be tested rather than trusted.
AUTHORITY_LEVEL = 2
AUTHORITY_NAME = "INVESTIGATE"

CAN_READ: Tuple[str, ...] = (
    "process and host resource telemetry",
    "component registry and health",
    "log tails and error tails",
    "git worktree and running-process revision (attestation)",
    "its own memory, audit and evaluation artifacts",
    "queue depth and stream lag counters",
)
CAN_RECOMMEND: Tuple[str, ...] = (
    "potential incidents with a root candidate and evidence",
    "improvement candidates",
    "read-only verification requests",
    "telemetry quality issues and observability improvements",
    "threshold calibration proposals (never self-applied)",
)
CAN_EXECUTE_IN_TEST: Tuple[str, ...] = (
    "deterministic replay of a persisted observation stream",
    "controlled fault injection against in-memory dev snapshots",
    "read-only verification actions from an allowlist",
)
CAN_EXECUTE_IN_SANDBOX: Tuple[str, ...] = (
    "nothing at Level 2 — sandbox is capable but hardcoded off",
)
NEVER_AT_LEVEL_2: Tuple[str, ...] = (
    "apply a code change",
    "open or merge a pull request, or push",
    "deploy or restart production",
    "submit, modify or cancel an order",
    "change risk limits or kill-switch state",
    "mutate OMS or broker state",
    "change its own autonomy level",
    "enable the sandbox or Level 3",
    "call a paid model without an explicit per-run gate",
    "write production configuration or credentials",
)

# --- non-goals --------------------------------------------------------------
NON_GOALS: Tuple[str, ...] = (
    "automatic remediation",
    "production autonomy",
    "a guaranteed false-negative rate",
    "universal LLM superiority over deterministic analysis",
    "Fable superiority",
    "continuous remote cognition",
    "self-modification",
)


def schema(name: str) -> str:
    """Look up a registered schema string. Unknown names are a programming error."""
    if name not in SCHEMAS:
        raise KeyError(f"unregistered schema '{name}'; add it to architecture.SCHEMAS")
    return SCHEMAS[name]


def feature_status(name: str) -> str:
    entry = FEATURES.get(name)
    return str(entry.get("status")) if entry else "UNKNOWN"


def active_features() -> List[str]:
    return sorted(k for k, v in FEATURES.items() if v.get("status") == "ACTIVE")


def experimental_features() -> List[str]:
    return sorted(
        k for k, v in FEATURES.items()
        if v.get("status") in {"EXPERIMENTAL", "CAPABLE_NOT_ENABLED", "DEFERRED"}
    )


def authority_map() -> Dict[str, Any]:
    return {
        "level": AUTHORITY_LEVEL,
        "level_name": AUTHORITY_NAME,
        "recommendation_is_not_authorization": True,
        "can_read": list(CAN_READ),
        "can_recommend": list(CAN_RECOMMEND),
        "can_execute_in_test": list(CAN_EXECUTE_IN_TEST),
        "can_execute_in_sandbox": list(CAN_EXECUTE_IN_SANDBOX),
        "never_at_level_2": list(NEVER_AT_LEVEL_2),
    }


def _repo_sha() -> str:
    try:
        from brain.attest import git_revision

        return str((git_revision() or {}).get("revision") or "unknown")[:12]
    except Exception:
        return "unknown"


def _discovery_policy() -> Dict[str, Any]:
    try:
        from brain.discovery.policy_g1 import policy_bundle

        return policy_bundle()
    except Exception:
        return {}


def identity() -> Dict[str, Any]:
    """The architecture identity stamp. Cheap enough to attach to any artifact."""
    return {
        "architecture_version": ARCHITECTURE_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "freeze_date": FREEZE_DATE,
        "repo_sha": _repo_sha(),
        "authority_level": AUTHORITY_LEVEL,
        "schemas": dict(SCHEMAS),
    }


def identity_hash() -> str:
    """Stable digest of the identity that does NOT include the volatile repo SHA."""
    stamp = {k: v for k, v in identity().items() if k != "repo_sha"}
    blob = json.dumps(stamp, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def release_manifest(
    *,
    test_baseline: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Machine-readable RC manifest. Consumed by tests and by the RC document."""
    policy = _discovery_policy()
    return {
        "schema": "qhos-brain-rc-manifest-v1",
        "architecture_version": ARCHITECTURE_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "freeze_date": FREEZE_DATE,
        "identity_hash": identity_hash(),
        "repo_sha": _repo_sha(),
        "schemas": dict(SCHEMAS),
        "discovery_policy": {
            "active": policy.get("policy") or "DISCOVERY_POLICY_G1",
            "revision": policy.get("revision"),
            "config_hash": policy.get("config_hash"),
            "scoring_hash": policy.get("scoring_hash"),
            "frozen": bool(policy.get("frozen", True)),
            "proposed_not_active": ["DISCOVERY_POLICY_G1.1"],
        },
        "features": {k: v.get("status") for k, v in sorted(FEATURES.items())},
        "default_strategy": "deterministic",
        "available_strategies": ["deterministic", "real_hybrid", "fable_derived"],
        "authority": authority_map(),
        "sandbox_enabled": False,
        "level3_enabled": False,
        "continuous_live_llm": False,
        "paid_calls_require_explicit_gate": True,
        "non_goals": list(NON_GOALS),
        "test_baseline": dict(test_baseline or {}),
    }
