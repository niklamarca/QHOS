"""Replay fidelity contract.

Sessions A, B and C of the previous phase could not audit themselves. Their
artifacts dropped ``state`` and ``lifecycle_known``, so replaying a session took
a different branch than the run it replayed: 14 candidates where the run
produced 3. The retrospective built on those replays was therefore measuring a
different pipeline than the one under test, and nothing in the artifact said so.

Two things prevent a repeat. ``schema_completeness`` checks *before* a run that
the artifact will carry every decision-relevant field. ``compare_runs`` checks
*after* a replay that it reached the same deterministic outputs, and produces
the ``replay_faithful`` verdict with a reason. A divergence is always reported;
it is never rounded to "close enough".

Historical artifacts are read, never rewritten. An artifact recorded under the
old schema stays ``replay_faithful=false`` permanently, because that is what it
is.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .policy_g1 import (
    DECISION_RELEVANT_FIELDS,
    OBSERVATION_SCHEMA_VERSION,
    REPLAY_COMPARED_OUTPUTS,
    REPLAY_CONTRACT_VERSION,
)


def schema_completeness(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Whether one persisted observation can drive a faithful replay."""
    present = [f for f in DECISION_RELEVANT_FIELDS if f in row]
    missing = [f for f in DECISION_RELEVANT_FIELDS if f not in row]
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "complete": not missing,
        "present": present,
        "missing": missing,
    }


def artifact_schema_report(observations: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Schema completeness across a whole artifact."""
    rows = list(observations or [])
    if not rows:
        return {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "observations": 0,
            "complete": False,
            "reason": "artifact carries no observations",
            "missing_fields": list(DECISION_RELEVANT_FIELDS),
        }
    missing: Dict[str, int] = {}
    for row in rows:
        for field in schema_completeness(row)["missing"]:
            missing[field] = missing.get(field, 0) + 1
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "observations": len(rows),
        "complete": not missing,
        "missing_fields": dict(sorted(missing.items())),
        "reason": "all decision-relevant fields present" if not missing
                  else f"missing {', '.join(sorted(missing))}",
    }


def _summarize(intel: Mapping[str, Any]) -> Dict[str, Any]:
    incidents = [dict(c) for c in (intel.get("incidents") or [])]
    metrics = intel.get("metrics") or {}
    return {
        "candidate_count": len(incidents),
        "root_ids": sorted(str(c.get("root_id") or "") for c in incidents),
        "scores": {
            str(c.get("root_id") or ""): round(float(c.get("score") or 0.0), 4)
            for c in incidents
        },
        "suppressed": int(metrics.get("noise_suppressed") or 0),
        "promotion_reasons": {
            str(c.get("root_id") or ""): str(c.get("promotion_reason") or "")
            for c in incidents
        },
        "relationships": sorted(
            f"{c.get('root_id')}:{c.get('candidate_role') or 'PRIMARY_ROOT_CANDIDATE'}"
            for c in incidents
        ),
    }


def compare_runs(live: Mapping[str, Any], replay: Mapping[str, Any]) -> Dict[str, Any]:
    """Deterministic-output comparison between a live run and its replay."""
    a = _summarize(live)
    b = _summarize(replay)

    diffs: List[str] = []
    for key in REPLAY_COMPARED_OUTPUTS:
        if a.get(key) != b.get(key):
            diffs.append(key)

    agreement: Dict[str, Any] = {
        "candidate_agreement": a["candidate_count"] == b["candidate_count"],
        "root_agreement": a["root_ids"] == b["root_ids"],
        "score_agreement": a["scores"] == b["scores"],
        "suppression_agreement": a["suppressed"] == b["suppressed"],
    }

    faithful = not diffs
    if faithful:
        reason = "replay reproduced every compared deterministic output"
    else:
        reason = "replay diverged on " + ", ".join(diffs)
        if not agreement["candidate_agreement"]:
            reason += (
                f" (live {a['candidate_count']} candidates, "
                f"replay {b['candidate_count']})"
            )

    return {
        "kind": "REPLAY_FIDELITY",
        "replay_contract_version": REPLAY_CONTRACT_VERSION,
        "replay_faithful": faithful,
        "reason": reason,
        "diverged_on": diffs,
        "compared": list(REPLAY_COMPARED_OUTPUTS),
        "live": a,
        "replay": b,
        **agreement,
    }


def fidelity_rate(reports: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = list(reports or [])
    faithful = [r for r in rows if r.get("replay_faithful")]
    by_diff: Dict[str, int] = {}
    for row in rows:
        for key in row.get("diverged_on") or []:
            by_diff[str(key)] = by_diff.get(str(key), 0) + 1
    n = len(rows)
    return {
        "kind": "REPLAY_FIDELITY_RATE",
        "artifacts": n,
        "faithful": len(faithful),
        "replay_fidelity_rate": round(len(faithful) / n, 4) if n else 0.0,
        "candidate_agreement_rate": round(
            sum(1 for r in rows if r.get("candidate_agreement")) / n, 4) if n else 0.0,
        "score_agreement_rate": round(
            sum(1 for r in rows if r.get("score_agreement")) / n, 4) if n else 0.0,
        "root_agreement_rate": round(
            sum(1 for r in rows if r.get("root_agreement")) / n, 4) if n else 0.0,
        "diverged_on": dict(sorted(by_diff.items())),
    }
