"""Group related failures without over-aggregating or claiming cause."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Sequence

_TOKEN = re.compile(r"[a-z][a-z0-9_]{3,}")


def _tokens(text: str) -> set:
    return set(_TOKEN.findall(str(text or "").lower()))


def cluster_candidates(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Same component or a strong shared token → one cluster. Avoid one-bucket."""
    unused = [dict(r) for r in rows]
    clusters: List[Dict[str, Any]] = []
    while unused:
        seed = unused.pop(0)
        members = [seed]
        seed_tok = _tokens(f"{seed.get('component')} {seed.get('summary')}")
        rest = []
        for row in unused:
            same_comp = str(row.get("component") or "") == str(seed.get("component") or "") and seed.get("component")
            shared = seed_tok & _tokens(f"{row.get('component')} {row.get('summary')}")
            if same_comp or len(shared) >= 2:
                members.append(row)
            else:
                rest.append(row)
        unused = rest
        clusters.append({
            "cluster_id": f"cl_{len(clusters)+1}",
            "members": members,
            "size": len(members),
            "component": seed.get("component"),
            "investigation_hint": "one_investigation" if len(members) >= 2 else "single",
            "causality": "not_assumed",
        })
    return clusters


def correlate_without_cause(
    *,
    errors: Sequence[str],
    commits: Sequence[str],
    latency_changed: bool = False,
) -> Dict[str, Any]:
    links = []
    if errors and commits:
        links.append({"kind": "temporal_cooccurrence", "causality": "not_assumed", "errors": list(errors)[:3], "commits": list(commits)[:3]})
    if latency_changed and errors:
        links.append({"kind": "latency_cooccurrence", "causality": "not_assumed"})
    return {"links": links, "causal": False}
