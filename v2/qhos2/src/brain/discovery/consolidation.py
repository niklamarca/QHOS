"""Root versus secondary effect. Evidence-based, never inferred from proximity.

The multi-session phase delivered two candidates for one underlying failure:
``ccxt`` is missing, and ``ingestion`` reports pending. A reviewer saw two
independent incidents; there was one cause and one consequence. Precision fell
to 0.667 on that duplicate alone.

This stage links them without inventing causation. ``CAUSES`` is deliberately
not in the vocabulary: two candidates observed in the same window is not
evidence of a causal mechanism, and a stage that says ``CAUSES`` on correlation
teaches a reviewer to distrust every relation it emits. The strongest claim
available from telemetry alone is ``LIKELY_DERIVED_EFFECT``, and it requires a
concrete root on one side plus a benign, non-concrete state on the other.

The relation changes how a candidate is *presented*, never whether it exists.
Both rows survive; one is labelled the primary root and one the secondary
effect, and the unique-root count stops double-counting the pair.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .fingerprint import _GENERIC_COMPONENTS, component_key, mentions_component

# Relationship vocabulary. Ordered strongest to weakest claim.
SAME_ROOT = "SAME_ROOT"
LIKELY_DERIVED_EFFECT = "LIKELY_DERIVED_EFFECT"
TEMPORALLY_CORRELATED = "TEMPORALLY_CORRELATED"
INDEPENDENT = "INDEPENDENT"
UNKNOWN = "UNKNOWN"

RELATIONSHIPS = (
    SAME_ROOT, LIKELY_DERIVED_EFFECT, TEMPORALLY_CORRELATED, INDEPENDENT, UNKNOWN,
)

PRIMARY_ROOT = "PRIMARY_ROOT_CANDIDATE"
SECONDARY_EFFECT = "SECONDARY_EFFECT"

# Tiers that carry an extracted, concrete cause.
CONCRETE_TIERS = frozenset({"DEPENDENCY", "ERROR_CODE", "EXCEPTION"})
# Tiers that describe a condition rather than a cause.
EFFECT_TIERS = frozenset({"STATE", "TELEMETRY"})

# A derived-effect link needs the two candidates to overlap in time.
MAX_LINK_WINDOW_S = 900.0


def _components(candidate: Mapping[str, Any]) -> List[str]:
    out = [str(candidate.get("component") or "")]
    out.extend(str(c) for c in list(candidate.get("affected_components") or [])[:16])
    return [c for c in out if c]


def _root_text(candidate: Mapping[str, Any]) -> str:
    sig = candidate.get("root_signature") if isinstance(candidate.get("root_signature"), Mapping) else {}
    parts = [
        str(candidate.get("summary") or ""),
        str(candidate.get("root_title") or ""),
        str(sig.get("dependency") or ""),
        str(sig.get("exception_type") or ""),
        str(sig.get("error_code") or ""),
    ]
    parts.extend(str(e) for e in list(candidate.get("evidence") or [])[:8])
    return " ".join(parts)


def _overlaps(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    a_first, a_last = float(a.get("first_seen") or 0.0), float(a.get("last_seen") or 0.0)
    b_first, b_last = float(b.get("first_seen") or 0.0), float(b.get("last_seen") or 0.0)
    if not (a_last and b_last):
        # Missing timestamps must not manufacture a link.
        return False
    return abs(a_first - b_first) <= MAX_LINK_WINDOW_S or (a_first <= b_last and b_first <= a_last)


def classify_relationship(
    primary: Mapping[str, Any],
    other: Mapping[str, Any],
) -> Dict[str, Any]:
    """How ``other`` relates to ``primary``. Never asserts a causal mechanism."""
    p_root = str(primary.get("root_id") or "")
    o_root = str(other.get("root_id") or "")
    if p_root and p_root == o_root:
        return {
            "relationship": SAME_ROOT,
            "confidence": "high",
            "basis": ["identical root fingerprint"],
        }

    p_tier = str(primary.get("root_tier") or "")
    o_tier = str(other.get("root_tier") or "")
    p_concrete = p_tier in CONCRETE_TIERS
    o_effect = o_tier in EFFECT_TIERS

    basis: List[str] = []
    # The effect's component must be named by the root's own evidence. A shared
    # window is not enough; that is what TEMPORALLY_CORRELATED is for.
    #
    # Generic component labels are excluded. Every error-log observation is
    # attributed to component "logs", so allowing it would link every pair of
    # log-derived candidates to each other and the relation would carry no
    # information at all.
    named = False
    root_text = _root_text(primary)
    for comp in _components(other):
        key = component_key(comp)
        if len(key) < 4 or comp.strip().lower() in _GENERIC_COMPONENTS:
            continue
        if mentions_component(root_text, comp):
            named = True
            basis.append(f"root evidence names '{comp}'")
            break

    overlapping = _overlaps(primary, other)
    if overlapping:
        basis.append("observation windows overlap")

    if p_concrete and o_effect and named and overlapping:
        return {
            "relationship": LIKELY_DERIVED_EFFECT,
            "confidence": "medium",
            "basis": basis + [
                f"primary tier {p_tier} is a concrete cause",
                f"secondary tier {o_tier} is a condition, not a cause",
            ],
            "asserts_causation": False,
        }
    if overlapping and (named or (p_concrete and o_effect)):
        return {
            "relationship": TEMPORALLY_CORRELATED,
            "confidence": "low",
            "basis": basis or ["observation windows overlap"],
            "asserts_causation": False,
        }
    if p_tier and o_tier and p_tier != o_tier and not named:
        return {"relationship": INDEPENDENT, "confidence": "low", "basis": ["no shared evidence"]}
    return {"relationship": UNKNOWN, "confidence": "none", "basis": ["insufficient evidence"]}


def consolidate(candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Label primary roots and secondary effects, and count roots honestly.

    Nothing is removed. A secondary effect keeps its own row, its own score and
    its own evidence; it gains a link and loses its claim to be an independent
    root.
    """
    rows = [dict(c) for c in candidates]
    for row in rows:
        row.setdefault("candidate_role", PRIMARY_ROOT)
        row.setdefault("derived_from", "")
        row.setdefault("relationship", "")

    # Concrete, higher-scoring candidates are considered as roots first.
    order = sorted(
        range(len(rows)),
        key=lambda i: (
            str(rows[i].get("root_tier") or "") in CONCRETE_TIERS,
            float(rows[i].get("score") or 0.0),
        ),
        reverse=True,
    )

    relations: List[Dict[str, Any]] = []
    # TEMPORALLY_CORRELATED is symmetric. Emitting it once per ordered pair
    # would report the same weak observation twice and read as two findings.
    seen_pairs: set = set()
    for pi in order:
        primary = rows[pi]
        if primary.get("candidate_role") == SECONDARY_EFFECT:
            continue
        for oi in order:
            if oi == pi:
                continue
            other = rows[oi]
            if other.get("candidate_role") == SECONDARY_EFFECT:
                continue
            verdict = classify_relationship(primary, other)
            rel = verdict["relationship"]
            if rel in {INDEPENDENT, UNKNOWN}:
                continue
            if rel == TEMPORALLY_CORRELATED:
                pair = frozenset((str(primary.get("root_id")), str(other.get("root_id"))))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
            relations.append({
                "primary_root_id": str(primary.get("root_id") or ""),
                "secondary_root_id": str(other.get("root_id") or ""),
                "relationship": rel,
                "confidence": verdict.get("confidence"),
                "basis": verdict.get("basis"),
                "asserts_causation": False,
            })
            if rel in {SAME_ROOT, LIKELY_DERIVED_EFFECT}:
                other["candidate_role"] = SECONDARY_EFFECT
                other["derived_from"] = str(primary.get("root_id") or "")
                other["relationship"] = rel

    primaries = [r for r in rows if r.get("candidate_role") == PRIMARY_ROOT]
    secondaries = [r for r in rows if r.get("candidate_role") == SECONDARY_EFFECT]
    return {
        "kind": "CAUSAL_CONSOLIDATION",
        "candidates": rows,
        "root_incidents": len(primaries),
        "secondary_effects": len(secondaries),
        "relations": relations,
        # Without consolidation every row counted as an independent root.
        "unique_root_inflation": round(
            (len(rows) - len(primaries)) / len(rows), 4
        ) if rows else 0.0,
        "asserts_causation": False,
        "vocabulary": list(RELATIONSHIPS),
    }


def link_quality(
    consolidation: Mapping[str, Any],
    truth: Mapping[str, str],
) -> Dict[str, Any]:
    """Precision/recall of secondary-effect links against a labelled truth map.

    ``truth`` maps a secondary ``root_id`` to the primary ``root_id`` it really
    derives from. Roots absent from the map are true primaries.
    """
    predicted = {
        str(r.get("secondary_root_id")): str(r.get("primary_root_id"))
        for r in consolidation.get("relations") or []
        if r.get("relationship") in {SAME_ROOT, LIKELY_DERIVED_EFFECT}
    }
    expected = {str(k): str(v) for k, v in dict(truth or {}).items()}

    tp = sum(1 for k, v in predicted.items() if expected.get(k) == v)
    fp = len(predicted) - tp
    fn = sum(1 for k in expected if predicted.get(k) != expected[k])
    return {
        "kind": "SECONDARY_EFFECT_LINK_QUALITY",
        "predicted_links": len(predicted),
        "expected_links": len(expected),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else 1.0,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else 1.0,
    }
