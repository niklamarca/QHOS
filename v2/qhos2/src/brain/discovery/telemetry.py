"""Telemetry quality ledger.

Per-source accounting of what a signal source actually produced, joined with the
reviewed disposition dataset. Answers "which source is wasting review time?".

Recommendations only. A source is never disabled automatically — that would be a
write to system configuration, which the Brain has no authority to make.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

USEFUL_DISPOSITIONS = frozenset({"USEFUL", "CONFIRMED"})
NOISE_DISPOSITIONS = frozenset({"NOT_USEFUL", "DISMISSED", "DUPLICATE"})

# A source needs this many reviewed candidates before quality claims are made.
MIN_REVIEWED_FOR_CLAIM = 3
# Share of total traffic above which a zero-yield source is worth flagging.
DOMINANT_SHARE = 0.25


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def telemetry_quality_ledger(
    signal_metrics: Mapping[str, Any],
    *,
    reviews: Optional[Sequence[Mapping[str, Any]]] = None,
    candidates: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the per-source ledger. Pure function over recorded evidence."""
    sources = dict(signal_metrics.get("source_quality") or {})
    total_received = max(0, int(signal_metrics.get("events_received") or 0))

    # Map candidate identity -> source(s) so reviews can be attributed.
    cand_sources: Dict[str, List[str]] = {}
    for cand in candidates or []:
        keys = [
            str(cand.get("fingerprint") or ""),
            str(cand.get("root_id") or ""),
            str(cand.get("candidate_id") or ""),
        ]
        srcs = [str(s) for s in (cand.get("sources") or []) if s]
        if not srcs and cand.get("source"):
            srcs = [str(cand.get("source"))]
        for key in keys:
            if key:
                cand_sources[key] = srcs

    reviewed_useful: Dict[str, int] = {}
    reviewed_noise: Dict[str, int] = {}
    unattributed = {"useful": 0, "noise": 0}
    for review in reviews or []:
        disposition = str(review.get("human_disposition") or review.get("disposition") or "").upper()
        if disposition in USEFUL_DISPOSITIONS:
            bucket = reviewed_useful
            miss = "useful"
        elif disposition in NOISE_DISPOSITIONS:
            bucket = reviewed_noise
            miss = "noise"
        else:
            continue
        srcs = (
            [str(s) for s in (review.get("sources") or []) if s]
            or ([str(review.get("source"))] if review.get("source") else [])
            or cand_sources.get(str(review.get("fingerprint") or ""), [])
            or cand_sources.get(str(review.get("root_id") or ""), [])
            or cand_sources.get(str(review.get("candidate_id") or ""), [])
        )
        if not srcs:
            unattributed[miss] += 1
            continue
        for src in srcs:
            bucket[src] = bucket.get(src, 0) + 1

    rows: List[Dict[str, Any]] = []
    for name, raw in sources.items():
        received = int(raw.get("received") or 0)
        suppressed = int(raw.get("suppressed") or 0)
        duplicates = int(raw.get("duplicate_producing") or 0)
        cands = int(raw.get("candidate_producing") or 0)
        useful = int(reviewed_useful.get(name, 0))
        noise = int(reviewed_noise.get(name, 0))
        rows.append({
            "source": name,
            "observations": received,
            "share_of_traffic": _rate(received, total_received),
            "duplicates": duplicates,
            "suppressed": suppressed,
            "candidates": cands,
            "reviewed_useful": useful,
            "reviewed_noise": noise,
            "reviewed_total": useful + noise,
            "candidate_yield": _rate(cands, received),
            "useful_yield": _rate(useful, received),
            "review_precision": _rate(useful, useful + noise),
            "suppression_rate": _rate(suppressed, received),
            "duplicate_rate": _rate(duplicates, received),
        })
    rows.sort(key=lambda r: r["observations"], reverse=True)
    return {
        "kind": "TELEMETRY_QUALITY_LEDGER",
        "schema": "qhos-telemetry-ledger-v1",
        "events_received": total_received,
        "sources": rows,
        "unattributed_reviews": dict(unattributed),
        "auto_disable_enabled": False,
    }


def telemetry_quality_recommendations(ledger: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Human-facing recommendations. Never disables a source."""
    out: List[Dict[str, Any]] = []
    for row in ledger.get("sources") or []:
        source = str(row.get("source") or "")
        received = int(row.get("observations") or 0)
        if received <= 0:
            continue
        share = float(row.get("share_of_traffic") or 0.0)
        candidates = int(row.get("candidates") or 0)
        useful = int(row.get("reviewed_useful") or 0)
        noise = int(row.get("reviewed_noise") or 0)
        reviewed = useful + noise

        recommendation: Optional[str] = None
        reason = ""
        evidence_strength = "WEAK"
        if reviewed >= MIN_REVIEWED_FOR_CLAIM and useful == 0:
            recommendation = "REVIEW_SOURCE_MAPPING"
            reason = f"{noise} reviewed candidates, none useful"
            evidence_strength = "REVIEWED"
        elif share >= DOMINANT_SHARE and candidates == 0:
            recommendation = "CONSIDER_SAMPLING"
            reason = f"{share:.0%} of traffic produced no candidates"
            evidence_strength = "OBSERVED"
        elif float(row.get("duplicate_rate") or 0.0) >= 0.5:
            recommendation = "REVIEW_DEDUP_KEY"
            reason = f"{float(row.get('duplicate_rate')):.0%} of observations were duplicates"
            evidence_strength = "OBSERVED"
        if recommendation is None:
            continue
        out.append({
            "kind": "TELEMETRY_QUALITY_RECOMMENDATION",
            "source": source,
            "recommendation": recommendation,
            "reason": reason,
            "evidence_strength": evidence_strength,
            "observations": received,
            "candidates": candidates,
            "reviewed_useful": useful,
            "reviewed_noise": noise,
            "auto_applied": False,
            "auto_disabled": False,
            "requires_human_review": True,
        })
    return out


def source_health(
    ledger: Mapping[str, Any],
    *,
    now: Optional[float] = None,
    last_seen: Optional[Mapping[str, float]] = None,
    expected_components: Optional[Mapping[str, int]] = None,
    observed_components: Optional[Mapping[str, int]] = None,
    semantic_findings: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Per-source coverage, freshness and semantic validity.

    A source can be perfectly reachable and still be worthless. The registry was
    up the whole time and answered every poll — with a static default that meant
    nothing. Reachability is therefore not a health measure; these three are:

    * coverage — how much of what the source should describe it actually did;
    * freshness — how long since it produced anything;
    * semantic validity — whether its values carry real information.
    """
    now = float(now if now is not None else 0.0)
    flagged = {str(f.get("source") or "") for f in (semantic_findings or [])}
    rows: List[Dict[str, Any]] = []
    for row in ledger.get("sources") or []:
        name = str(row.get("source") or "")
        observed = int((observed_components or {}).get(name, 0) or 0)
        expected = int((expected_components or {}).get(name, 0) or 0)
        seen_at = float((last_seen or {}).get(name, 0.0) or 0.0)
        age = round(max(0.0, now - seen_at), 1) if (now and seen_at) else None

        semantically_valid = name not in flagged
        # Everything suppressed and nothing promoted means the source produced
        # traffic without information.
        received = int(row.get("observations") or 0)
        inert = received > 0 and int(row.get("candidates") or 0) == 0 and (
            float(row.get("suppression_rate") or 0.0) >= 0.99
        )
        rows.append({
            "source": name,
            "observations": received,
            "coverage": _rate(observed, expected) if expected else None,
            "components_observed": observed,
            "components_expected": expected or None,
            "last_seen_age_s": age,
            "fresh": (age is not None and age <= 120.0) if age is not None else None,
            "semantically_valid": semantically_valid,
            "semantic_findings": [
                str(f.get("finding") or f.get("summary") or "")[:160]
                for f in (semantic_findings or []) if str(f.get("source") or "") == name
            ][:3],
            "inert_traffic": inert,
            "candidate_yield": row.get("candidate_yield"),
            "useful_yield": row.get("useful_yield"),
            "noise_yield": _rate(
                int(row.get("reviewed_noise") or 0),
                int(row.get("reviewed_total") or 0),
            ),
            "reachable_but_misleading": bool(received and not semantically_valid),
        })
    return {
        "kind": "SIGNAL_SOURCE_HEALTH",
        "schema": "qhos-source-health-v1",
        "sources": rows,
        "misleading_sources": [r["source"] for r in rows if r["reachable_but_misleading"]],
        "inert_sources": [r["source"] for r in rows if r["inert_traffic"]],
        "auto_disable_enabled": False,
    }


TELEMETRY_QUALITY_ISSUE = "TELEMETRY_QUALITY_ISSUE"


def telemetry_quality_issues(candidates: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Project TELEMETRY-tier candidates as telemetry-quality issues.

    A registry that reports every module as pending is not a component failure —
    it is a defect in the observability surface, and reading it with incident
    semantics invites the wrong response ("restart storage") to the wrong
    problem ("the registry is empty").

    This is a view over what the frozen engine already promoted, not a change to
    what gets promoted.
    """
    out: List[Dict[str, Any]] = []
    for cand in candidates or []:
        if str(cand.get("root_tier") or "").upper() != "TELEMETRY":
            continue
        out.append({
            "kind": TELEMETRY_QUALITY_ISSUE,
            "subtype": "OBSERVABILITY_DEFECT",
            "root_id": cand.get("root_id"),
            "title": cand.get("root_title") or cand.get("summary"),
            "affected_components": list(cand.get("affected_components") or [])[:24],
            "component_count": int(cand.get("affected_instances") or 0),
            "evidence": list(cand.get("evidence") or [])[:8],
            "score": cand.get("score"),
            "is_component_failure": False,
            "interpretation": (
                "a defect in the telemetry surface, not a failure of the listed "
                "components; treating it as a component incident would prompt the "
                "wrong remediation"
            ),
            "recommended_verification": cand.get("recommended_verification")
            or "inspect_health: confirm whether the module registry is populated",
            "write_patch": False,
            "requires_human_review": True,
        })
    return out


def panel_rows(ledger: Mapping[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    """Compact rows for the Laços telemetry-quality inspector. No controls."""
    rows = []
    for row in list(ledger.get("sources") or [])[:limit]:
        rows.append({
            "source": row.get("source"),
            "observations": row.get("observations"),
            "suppressed": row.get("suppressed"),
            "candidates": row.get("candidates"),
            "useful_reviewed": row.get("reviewed_useful"),
            "noise_reviewed": row.get("reviewed_noise"),
            "candidate_yield": row.get("candidate_yield"),
            "read_only": True,
        })
    return rows
