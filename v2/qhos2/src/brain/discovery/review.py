"""Reviewed-disposition dataset. Evidence, not training truth."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from brain.eval_paths import atomic_write_json
from brain.models import utc_ts
from brain.redact import redact_mapping

from .revision import policy_revision
from .soak import shadow_dir


REVIEW_DISPOSITIONS = (
    "CONFIRMED",
    "DISMISSED",
    "DUPLICATE",
    "USEFUL",
    "NOT_USEFUL",
    "UNCERTAIN",
    "NEEDS_MORE_EVIDENCE",
)

NOT_TRAINING_TRUTH = True


def review_id_for(session_id: str, candidate_type: str, candidate_id: str) -> str:
    """Stable identity for one reviewed candidate in one session."""
    import hashlib

    key = f"{session_id}|{candidate_type}|{candidate_id}".encode("utf-8", "replace")
    return "rev_" + hashlib.sha256(key).hexdigest()[:16]


def review_record(
    *,
    candidate: Mapping[str, Any],
    session_id: str,
    candidate_type: str,
    disposition: str,
    notes: str = "",
    review_source: str = "session_reviewer",
    repo_revision: str = "",
    inventory_revision: str = "",
    recommendation: str = "",
) -> Dict[str, Any]:
    act = str(disposition or "").upper()
    if act not in REVIEW_DISPOSITIONS:
        act = "UNCERTAIN"
    candidate_id = str(candidate.get("candidate_id") or candidate.get("fingerprint") or "")[:40]
    session = str(session_id)[:40]
    row = {
        # Derived, not random: one candidate reviewed once in one session is one
        # row. A re-run of the review must revise that row, not append a second
        # copy, or the reviewed denominator silently inflates.
        "review_id": review_id_for(session, candidate_type, candidate_id),
        "candidate_id": candidate_id,
        "session_id": session,
        "candidate_type": str(candidate_type)[:32],
        "signals": list(candidate.get("evidence") or candidate.get("signals") or [])[:8],
        "component": str(candidate.get("component") or "")[:80],
        "brain_score": float(candidate.get("score") or 0.0),
        "brain_recommendation": str(recommendation or candidate.get("strategy") or "")[:160],
        "human_disposition": act,
        "review_notes": str(notes)[:400],
        "final_category": act,
        # The first disposition is never overwritten, so a later re-review stays
        # visible as a revision rather than silently replacing the original.
        "original_disposition": act,
        "revision_history": [],
        "review_timestamp": utc_ts(),
        "review_source": str(review_source)[:40],
        "repository_revision": str(repo_revision)[:16],
        "inventory_revision": str(inventory_revision)[:16],
        # Which Discovery produced this candidate. Without it a pooled dataset
        # mixes policies and the numbers stop being attributable.
        "discovery_policy": policy_revision(),
        "label": "reviewed_evidence_dataset",
        "training_truth": False,
        "ground_truth": False,
        "automatic_policy": False,
        "not_training_truth": True,
        "change_request": None,
        "sandbox": None,
    }
    return redact_mapping(row)


def persist_reviews(rows: List[Mapping[str, Any]], *, session_id: str) -> Path:
    dest = shadow_dir() / f"reviews_{session_id}.json"
    atomic_write_json(dest, {
        "schema": "qhos-brain-review-v1",
        "not_training_truth": True,
        "ground_truth": False,
        "discovery_policy": policy_revision(),
        "rows": [dict(r) for r in rows],
    })
    return dest


CUMULATIVE_DATASET = "reviews_cumulative.json"

# Sessions reviewed before the policy was frozen carry no revision of their own.
# They stay in the dataset because dropping them would hide the earlier, worse
# numbers, but they must never be attributed to the frozen policy.
PRE_FREEZE_POLICY = {"policy": "PRE_FREEZE", "revision": "pre_v2_freeze", "config_hash": ""}


def import_session_review_files(*, dataset: str = CUMULATIVE_DATASET) -> Dict[str, Any]:
    """Fold per-session review files into the cumulative dataset.

    Earlier phases wrote one file per session and no cumulative view. Reporting
    growth across phases needs them in one place, and re-reviewing them by hand
    would fabricate dispositions that were already made.
    """
    import json

    folder = shadow_dir()
    imported: List[Dict[str, Any]] = []
    for path in sorted(folder.glob("reviews_*.json")):
        if path.name == dataset:
            continue
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        default_policy = blob.get("discovery_policy") or PRE_FREEZE_POLICY
        for row in blob.get("rows") or []:
            row = dict(row)
            if not isinstance(row.get("discovery_policy"), Mapping):
                row["discovery_policy"] = dict(default_policy)
            row.setdefault("original_disposition", row.get("human_disposition"))
            row.setdefault("revision_history", [])
            row["imported_from"] = path.name
            imported.append(row)
    return append_reviews(imported, dataset=dataset)


def revise_review(
    row: Mapping[str, Any],
    *,
    disposition: str,
    reason: str,
    review_source: str = "re_review",
) -> Dict[str, Any]:
    """Change a disposition while preserving the one it replaces.

    A silently replaced label makes an earlier reported precision impossible to
    reproduce, so the previous value moves into ``revision_history`` instead.
    """
    act = str(disposition or "").upper()
    if act not in REVIEW_DISPOSITIONS:
        act = "UNCERTAIN"
    out = dict(row)
    previous = str(out.get("human_disposition") or "")
    history = list(out.get("revision_history") or [])
    if act == previous:
        return out
    history.append({
        "from": previous,
        "to": act,
        "reason": str(reason)[:300],
        "revised_at": utc_ts(),
        "review_source": str(review_source)[:40],
    })
    out.setdefault("original_disposition", previous)
    out["human_disposition"] = act
    out["final_category"] = act
    out["revision_history"] = history[:8]
    out["revised"] = True
    return out


def append_reviews(
    rows: List[Mapping[str, Any]],
    *,
    dataset: str = CUMULATIVE_DATASET,
) -> Dict[str, Any]:
    """Append to the cumulative dataset. Existing sessions are never dropped.

    Re-reviewed rows replace their own earlier copy by ``review_id`` — which
    carries the revision history with it — but a row from another session is
    left untouched.
    """
    import json

    dest = shadow_dir() / dataset
    existing: List[Dict[str, Any]] = []
    if dest.exists():
        try:
            blob = json.loads(dest.read_text(encoding="utf-8"))
            existing = [dict(r) for r in (blob.get("rows") or [])]
        except (OSError, ValueError):
            existing = []

    by_id = {str(r.get("review_id")): r for r in existing}
    added = 0
    revised = 0
    for row in rows:
        key = str(row.get("review_id"))
        if key in by_id:
            revised += 1
        else:
            added += 1
        by_id[key] = dict(row)

    merged = list(by_id.values())
    sessions = sorted({str(r.get("session_id") or "") for r in merged if r.get("session_id")})
    policies = sorted({
        str((r.get("discovery_policy") or {}).get("revision") or "")
        for r in merged if isinstance(r.get("discovery_policy"), Mapping)
    })
    atomic_write_json(dest, {
        "schema": "qhos-brain-review-cumulative-v1",
        "not_training_truth": True,
        "ground_truth": False,
        "append_only": True,
        "sessions": sessions,
        "policy_revisions": [p for p in policies if p],
        "rows": merged,
    })
    return {
        "path": str(dest),
        "total_rows": len(merged),
        "added": added,
        "updated": revised,
        "sessions": sessions,
        "policy_revisions": [p for p in policies if p],
    }


def precision_from_reviews(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    """Do not claim precision before reviewed dispositions exist."""
    if not rows:
        return {
            "candidate_precision": None,
            "dismissal_rate": None,
            "noise_escape_rate": None,
            "duplicate_escape_rate": None,
            "useful_recommendation_rate": None,
            "claimed": False,
            "reason": "no_reviewed_dispositions",
            "not_training_truth": True,
        }
    n = len(rows)
    useful = sum(1 for r in rows if str(r.get("human_disposition")) in {"CONFIRMED", "USEFUL"})
    dismissed = sum(1 for r in rows if str(r.get("human_disposition")) == "DISMISSED")
    noise = sum(1 for r in rows if str(r.get("human_disposition")) in {"DISMISSED", "NOT_USEFUL"})
    dups = sum(1 for r in rows if str(r.get("human_disposition")) == "DUPLICATE")
    return {
        "n": n,
        "candidate_precision": round(useful / n, 4),
        "dismissal_rate": round(dismissed / n, 4),
        "noise_escape_rate": round(noise / n, 4),
        "duplicate_escape_rate": round(dups / n, 4),
        "useful_recommendation_rate": round(useful / n, 4),
        "claimed": True,
        "not_training_truth": True,
    }


def incident_review_artifact(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "incident_review",
        "noticed": str(candidate.get("summary") or "")[:200],
        "why_it_mattered": f"score={candidate.get('score')} severity={candidate.get('severity')} component={candidate.get('component')}",
        "supporting_observations": list(candidate.get("observation_ids") or candidate.get("evidence") or [])[:6],
        "what_it_thinks": str(candidate.get("strategy") or "deterministic")[:80],
        "what_it_does_not_know": "causality not assumed; no confirmed root cause",
        "recommended_next_verification": "inspect_logs / inspect_health — do not patch",
        "write_patch": False,
        "chain_of_thought": None,
        "change_request": None,
    }


def improvement_review_artifact(candidate: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "improvement_review",
        "observed_pattern": str(candidate.get("observed_problem") or "")[:200],
        "evidence": list(candidate.get("evidence") or [])[:6],
        "expected_benefit": str(candidate.get("expected_benefit") or "")[:160],
        "estimate_vs_measured": str(candidate.get("estimate_vs_measured") or "ESTIMATE"),
        "uncertainty": str(candidate.get("uncertainty") or "unmeasured")[:80],
        "proposed_benchmark": str(candidate.get("proposed_validation") or "")[:200],
        "write_patch": False,
        "change_request": None,
    }


def strip_review_labels(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Hide human disposition from replay reasoning."""
    banned = {
        "human_disposition", "disposition", "final_category", "review_notes",
        "review_id", "final_disposition", "useful", "noise", "label",
        "training_truth", "ground_truth",
        # Revision fields carry the same judgement as the disposition itself.
        "original_disposition", "revision_history", "revised",
    }
    return {k: v for k, v in dict(row).items() if k not in banned}
