"""Evaluation provenance and artifact comparability.

Two artifacts produced by different policies, schemas, evaluators or providers
are not comparable, and a diff between them is not a result. This module stamps
provenance onto an artifact and refuses to compare artifacts silently when the
stamps disagree.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from brain.architecture import ARCHITECTURE_VERSION, RELEASE_CHANNEL, schema

SCHEMA = "qhos-evaluation-provenance-v1"

# Bumped when an evaluator's semantics change in a way that alters its numbers.
# The fault-matrix and hybrid-value evaluators both changed during the previous
# phase (MD-04, MD-05, MD-06), which is why this is not simply "1".
EVALUATOR_REVISIONS: Dict[str, str] = {
    "known_issue": "v2",
    "fault_matrix": "v2",
    "hybrid_incremental_value": "v2",
    "replay_fidelity": "v2",
    "discovery_quality": "v1",
    "proving_ground": "v1",
}

COMPATIBILITY = (
    "COMPARABLE",
    "COMPARABLE_WITH_CAVEATS",
    "NOT_COMPARABLE",
    "INSUFFICIENT_PROVENANCE",
)

# Fields that must match for two artifacts to be directly comparable. The
# evaluator NAME is part of identity, not just its revision: two unrelated
# evaluators that happen to sit at the same revision are still not comparable.
IDENTITY_FIELDS: Tuple[str, ...] = (
    "policy_revision",
    "observation_schema_version",
    "evaluator",
    "evaluator_revision",
    "provider",
    "provider_model",
)
# Differences here are worth reporting but do not by themselves void a diff.
CAVEAT_FIELDS: Tuple[str, ...] = ("repo_sha", "architecture_version")


def _policy() -> Dict[str, Any]:
    try:
        from brain.discovery.policy_g1 import policy_bundle

        return policy_bundle()
    except Exception:
        return {}


def stamp(
    *,
    evaluator: str,
    provider: str = "none",
    provider_model: str = "",
    artifact_schema: str = "",
    known_defects: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Provenance block to embed in any evaluation artifact."""
    policy = _policy()
    from brain.architecture import _repo_sha

    return {
        "schema": SCHEMA,
        "architecture_version": ARCHITECTURE_VERSION,
        "release_channel": RELEASE_CHANNEL,
        "repo_sha": _repo_sha(),
        "policy": policy.get("policy") or "DISCOVERY_POLICY_G1",
        "policy_revision": policy.get("revision") or "g1",
        "policy_config_hash": policy.get("config_hash"),
        "observation_schema_version": policy.get("observation_schema_version")
        or schema("observation"),
        "replay_contract_version": policy.get("replay_contract_version")
        or schema("replay_contract"),
        "artifact_schema": artifact_schema or "",
        "evaluator": evaluator,
        "evaluator_revision": EVALUATOR_REVISIONS.get(evaluator, "unregistered"),
        "provider": provider,
        "provider_model": provider_model,
        "known_defects": list(known_defects or []),
    }


def _provenance_of(artifact: Mapping[str, Any]) -> Dict[str, Any]:
    block = artifact.get("provenance")
    if isinstance(block, Mapping):
        return dict(block)
    # Artifacts written before provenance existed. Recover what is recoverable
    # rather than pretending the fields are absent-by-choice.
    policy = artifact.get("policy")
    recovered: Dict[str, Any] = {"recovered_from_artifact_body": True}
    if isinstance(policy, Mapping):
        recovered.update({
            "policy": policy.get("policy"),
            "policy_revision": policy.get("revision"),
            "policy_config_hash": policy.get("config_hash"),
            "observation_schema_version": policy.get("observation_schema_version"),
        })
    return recovered


def compare_provenance(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    left_name: str = "left",
    right_name: str = "right",
) -> Dict[str, Any]:
    """Whether two artifacts may be compared, and on what grounds.

    Never returns a bare boolean: the caller has to be able to print the reason
    next to the diff, otherwise an incomparable diff reads like a regression.
    """
    a, b = _provenance_of(left), _provenance_of(right)
    missing: List[str] = []
    mismatched: List[Dict[str, Any]] = []
    caveats: List[Dict[str, Any]] = []

    for field in IDENTITY_FIELDS:
        av, bv = a.get(field), b.get(field)
        if av is None or bv is None:
            missing.append(field)
        elif av != bv:
            mismatched.append({"field": field, left_name: av, right_name: bv})
    for field in CAVEAT_FIELDS:
        av, bv = a.get(field), b.get(field)
        if av is not None and bv is not None and av != bv:
            caveats.append({"field": field, left_name: av, right_name: bv})

    defect_ids = sorted(set(a.get("known_defects") or []) | set(b.get("known_defects") or []))

    if mismatched:
        status, reason = "NOT_COMPARABLE", (
            "differs on " + ", ".join(m["field"] for m in mismatched)
        )
    elif missing:
        status, reason = "INSUFFICIENT_PROVENANCE", (
            "cannot establish comparability; missing " + ", ".join(missing)
        )
    elif caveats or defect_ids:
        parts = []
        if caveats:
            parts.append("differs on " + ", ".join(c["field"] for c in caveats))
        if defect_ids:
            parts.append("known measurement defects " + ", ".join(defect_ids))
        status, reason = "COMPARABLE_WITH_CAVEATS", "; ".join(parts)
    else:
        status, reason = "COMPARABLE", "identical policy, schema, evaluator and provider"

    return {
        "compatibility": status,
        "reason": reason,
        "mismatched": mismatched,
        "missing": missing,
        "caveats": caveats,
        "known_defects": defect_ids,
        "safe_to_diff": status in {"COMPARABLE", "COMPARABLE_WITH_CAVEATS"},
    }


def guard_comparison(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    **kwargs: Any,
) -> Dict[str, Any]:
    """compare_provenance, but the verdict is phrased for a report line."""
    verdict = compare_provenance(left, right, **kwargs)
    verdict["headline"] = f"{verdict['compatibility']}: {verdict['reason']}"
    return verdict
