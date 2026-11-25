"""Long-run cognitive resume. Writes never auto-resume."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .context_adapt import merge_continuation
from .snapshot import InvestigationSnapshot


def resume_investigation(
    snapshot: Mapping[str, Any],
    *,
    env_files: Optional[Sequence[str]] = None,
    invalidate: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    snap = InvestigationSnapshot.from_dict(dict(snapshot))
    stale = []
    env = {str(x).lower() for x in (env_files or [])}
    if env:
        for fact in list(snap.confirmed_facts):
            if any(tok in fact.lower() for tok in (".py", ".json")) and env:
                name = fact.split()[-1].lower()
                if name not in env and name.split("/")[-1] not in env:
                    stale.append(fact)
    dropped = list(invalidate or []) + stale
    compact = merge_continuation(
        {
            "confirmed_facts": snap.confirmed_facts,
            "rejected_hypotheses": snap.rejected_hypotheses,
            "uncertainties": snap.uncertainties,
            "evidence": snap.evidence,
            "next_actions": snap.next_actions,
        },
        invalidate=dropped,
    )
    write_resume = False
    return {
        "snapshot_id": snap.snapshot_id,
        "loaded": True,
        "freshness": "stale" if stale else "valid",
        "invalidated": dropped,
        "context": compact,
        "next_actions": [a for a in snap.next_actions if "write" not in a.lower() and "apply" not in a.lower()],
        "write_auto_resume": write_resume,
        "write_pending_requires_human": bool(snap.write_pending),
    }


def completed_eval_keys(artifact: Mapping[str, Any]) -> set:
    keys = set()
    for row in artifact.get("results") or []:
        if not isinstance(row, Mapping):
            continue
        keys.add((str(row.get("scenario_id") or ""), int(row.get("run_index") or 0)))
    return keys
