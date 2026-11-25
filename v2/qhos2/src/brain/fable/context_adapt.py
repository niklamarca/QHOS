"""Context discipline: compact, continue, do not rediscover, do not dump the repo."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence


def compact_context(
    *,
    confirmed: Optional[Sequence[str]] = None,
    rejected: Optional[Sequence[str]] = None,
    uncertainties: Optional[Sequence[str]] = None,
    evidence: Optional[Sequence[Any]] = None,
    next_actions: Optional[Sequence[str]] = None,
    logs: Optional[Sequence[str]] = None,
    files: Optional[Sequence[str]] = None,
    max_logs: int = 8,
    max_files: int = 6,
) -> Dict[str, Any]:
    ev = []
    for row in evidence or []:
        if isinstance(row, Mapping):
            ev.append({
                "kind": str(row.get("kind") or "")[:40],
                "file": str(row.get("file") or row.get("target") or "")[:80],
            })
        else:
            ev.append({"kind": str(row)[:40]})
    log_rows = [str(x)[:160] for x in (logs or [])[:max_logs]]
    file_rows = [str(x)[:120] for x in (files or [])[:max_files]]
    return {
        "confirmed_facts": [str(x)[:160] for x in (confirmed or [])[:12]],
        "rejected_hypotheses": [str(x)[:160] for x in (rejected or [])[:8]],
        "uncertainties": [str(x)[:160] for x in (uncertainties or [])[:8]],
        "evidence": ev[:12],
        "next_actions": [str(x)[:120] for x in (next_actions or [])[:8]],
        "logs": log_rows,
        "files": file_rows,
        "full_repo_included": False,
        "all_logs_included": False,
    }


def context_metrics(compact: Mapping[str, Any], *, critical: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    items = (
        list(compact.get("confirmed_facts") or [])
        + list(compact.get("evidence") or [])
        + list(compact.get("files") or [])
    )
    size = len(items) + len(compact.get("logs") or [])
    crit = [str(x).lower() for x in (critical or [])]
    blob = str(compact).lower()
    misses = [c for c in crit if c and c not in blob]
    redundant = max(0, len(compact.get("logs") or []) - 8)
    relevant = max(0, size - redundant)
    return {
        "relevant_context_ratio": round(relevant / max(1, size), 4),
        "context_size": size,
        "critical_context_misses": len(misses),
        "redundant_context_items": redundant,
    }


def merge_continuation(
    previous: Mapping[str, Any],
    *,
    fresh_facts: Optional[Sequence[str]] = None,
    invalidate: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Resume-safe merge. Drops invalidated assumptions. Does not re-derive kept facts."""
    confirmed = [str(x) for x in (previous.get("confirmed_facts") or [])]
    drop = {str(x).lower() for x in (invalidate or [])}
    kept = [c for c in confirmed if c.lower() not in drop]
    for fact in fresh_facts or []:
        if str(fact) not in kept:
            kept.append(str(fact))
    out = compact_context(
        confirmed=kept,
        rejected=previous.get("rejected_hypotheses"),
        uncertainties=[u for u in (previous.get("uncertainties") or []) if str(u).lower() not in drop],
        evidence=previous.get("evidence"),
        next_actions=previous.get("next_actions"),
        logs=previous.get("logs"),
        files=previous.get("files"),
    )
    out["resumed"] = True
    out["rederived"] = False
    return out
