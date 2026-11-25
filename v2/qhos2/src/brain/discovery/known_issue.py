"""Known-issue evaluator v2: match on evidence, never on a shared component name.

v1 counted a candidate saying ``dashboard state=pending`` as evidence that the
issue "the dashboard runs a stale Brain revision" had surfaced, because both
mention the dashboard. That inflated known-issue recall from a true 2/3 to a
reported 3/3 — the measurement said the opposite of the truth, which is worse
than not measuring.

v2 removes component names from the matching decision entirely. A detection is
only credited when the candidate carries independent evidence that it is *this*
fault:

* ``root_family`` — the tier the root fingerprint resolved to;
* ``root_signature`` — an extracted dependency, exception type or error code;
* ``fault_signature`` — required tokens that must appear in the candidate text;
* ``attestation_state`` — for runtime faults, the attestation verdict.

A component name may *narrow* a match that already has independent support. It
can never create one. ``KNOWN_ISSUE_COMPONENT_ONLY`` records the near-misses so
the difference between v1 and v2 stays visible rather than silently improving a
number.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Per-fault detection verdicts.
DETECTED_CORRECT_ROOT = "DETECTED_CORRECT_ROOT"
DETECTED_CORRECT_FAMILY = "DETECTED_CORRECT_FAMILY"
DETECTED_SYMPTOM_ONLY = "DETECTED_SYMPTOM_ONLY"
DETECTED_WRONG_ROOT = "DETECTED_WRONG_ROOT"
DUPLICATED = "DUPLICATED"
CLUSTERED_INTO_OTHER_ROOT = "CLUSTERED_INTO_OTHER_ROOT"
SUPPRESSED = "SUPPRESSED"
MISSED = "MISSED"
NO_SIGNAL = "NO_SIGNAL"

DETECTION_VERDICTS = (
    DETECTED_CORRECT_ROOT,
    DETECTED_CORRECT_FAMILY,
    DETECTED_SYMPTOM_ONLY,
    DETECTED_WRONG_ROOT,
    DUPLICATED,
    CLUSTERED_INTO_OTHER_ROOT,
    SUPPRESSED,
    MISSED,
    NO_SIGNAL,
)

# Verdicts that count as the fault having been found at all.
DETECTED_VERDICTS = frozenset({
    DETECTED_CORRECT_ROOT, DETECTED_CORRECT_FAMILY, DETECTED_SYMPTOM_ONLY, DUPLICATED,
})

# Where a fault was lost, when it was lost.
FAILURE_CATEGORIES = (
    "NO_SIGNAL",
    "NORMALIZATION_MISS",
    "SUPPRESSION_MISS",
    "SCORING_MISS",
    "CLUSTERING_MISS",
    "CONSOLIDATION_MISS",
    "INVESTIGATION_MISS",
    "NOT_A_DISCOVERY_FAILURE",
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def _candidate_text(candidate: Mapping[str, Any]) -> str:
    """Every field of a candidate that can carry root evidence.

    Deliberately excludes ``component`` and ``affected_components``: a component
    name must not be able to create a match on its own.

    ``root_title`` needs one extra step. For a STATE-tier root it is built as
    ``"{component_family} state={state}"``, so leaving it whole would smuggle
    the component name back into the matchable text and reintroduce the v1
    defect through the side door. The family token is stripped before matching.
    """
    sig = candidate.get("root_signature") if isinstance(candidate.get("root_signature"), Mapping) else {}
    family = _norm(str(sig.get("component_family") or candidate.get("component_family") or ""))
    title = _norm(str(candidate.get("root_title") or ""))
    if family:
        title = re.sub(rf"\b{re.escape(family)}\b", " ", title)

    parts: List[str] = [
        str(candidate.get("summary") or ""),
        title,
        str(candidate.get("promotion_reason") or ""),
    ]
    parts.extend(str(sig.get(k) or "") for k in (
        "dependency", "exception_type", "error_code", "normalized_message", "state",
    ))
    for item in list(candidate.get("evidence") or [])[:12]:
        parts.append(str(item))
    return _norm(" ".join(parts))


def _component_text(candidate: Mapping[str, Any]) -> str:
    parts = [str(candidate.get("component") or "")]
    parts.extend(str(c) for c in list(candidate.get("affected_components") or [])[:16])
    return _norm(" ".join(parts))


@dataclass
class KnownIssueV2:
    """An independently confirmed issue, described by evidence not by name."""

    issue_id: str
    title: str
    # At least one of these must be non-empty, or the issue is unmatchable and
    # says so rather than matching everything.
    required_tokens: Tuple[str, ...] = ()
    any_tokens: Tuple[str, ...] = ()
    root_family: str = ""
    component_hint: str = ""
    confirmed_by: str = ""
    expected_signal_sources: Tuple[str, ...] = ()

    def matchable(self) -> bool:
        return bool(self.required_tokens or self.any_tokens or self.root_family)

    def match(self, candidate: Mapping[str, Any]) -> Dict[str, Any]:
        """Evidence-based match verdict for one candidate."""
        text = _candidate_text(candidate)
        family = str(candidate.get("root_tier") or "")

        required_hits = [t for t in self.required_tokens if _norm(t) and _norm(t) in text]
        any_hits = [t for t in self.any_tokens if _norm(t) and _norm(t) in text]
        family_hit = bool(self.root_family) and family == self.root_family

        required_ok = len(required_hits) == len(self.required_tokens)
        token_support = required_ok and (not self.any_tokens or bool(any_hits))
        independent = bool(token_support and (self.required_tokens or self.any_tokens))

        component_only = False
        if not independent and self.component_hint:
            component_only = _norm(self.component_hint) in _component_text(candidate)

        if independent and family_hit:
            verdict = DETECTED_CORRECT_ROOT
        elif independent:
            verdict = DETECTED_CORRECT_FAMILY if not self.root_family else DETECTED_SYMPTOM_ONLY
        elif family_hit and not (self.required_tokens or self.any_tokens):
            verdict = DETECTED_CORRECT_FAMILY
        else:
            verdict = ""

        return {
            "issue_id": self.issue_id,
            "matched": bool(verdict),
            "verdict": verdict,
            "independent_evidence": independent,
            "required_hits": required_hits,
            "any_hits": any_hits,
            "root_family_hit": family_hit,
            # Recorded, never credited: this is exactly what v1 got wrong.
            "component_only_near_miss": component_only,
        }


def evaluate_known_issue(
    issue: KnownIssueV2,
    candidates: Sequence[Mapping[str, Any]],
    *,
    observations: Optional[Sequence[Mapping[str, Any]]] = None,
    investigations: Optional[Sequence[Mapping[str, Any]]] = None,
    suppressed_texts: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """One issue against one run. Reports where it was lost if it was lost."""
    if not issue.matchable():
        return {
            "issue_id": issue.issue_id,
            "verdict": MISSED,
            "category": "NOT_A_DISCOVERY_FAILURE",
            "reason": "issue has no independent matching evidence defined",
            "matchable": False,
        }

    matches = [issue.match(c) for c in candidates]
    hits = [(c, m) for c, m in zip(candidates, matches) if m["matched"]]
    near_misses = [m for m in matches if m["component_only_near_miss"] and not m["matched"]]

    # Did any signal for this issue reach the engine at all?
    obs_rows = list(observations or [])
    signal_sources = {
        str(o.get("source") or "") for o in obs_rows
        if _issue_in_text(issue, str(o.get("payload_summary") or o.get("summary") or ""))
    }
    suppressed_hit = any(_issue_in_text(issue, t) for t in (suppressed_texts or []))

    if not hits:
        if not signal_sources and not suppressed_hit:
            category, verdict = "NO_SIGNAL", NO_SIGNAL
        elif suppressed_hit:
            category, verdict = "SUPPRESSION_MISS", SUPPRESSED
        else:
            # Signal arrived and was normalized, but nothing was promoted.
            category, verdict = "SCORING_MISS", MISSED
        return {
            "issue_id": issue.issue_id,
            "title": issue.title,
            "verdict": verdict,
            "category": category,
            "matchable": True,
            "signal_sources": sorted(s for s in signal_sources if s),
            "expected_signal_sources": list(issue.expected_signal_sources),
            "component_only_near_misses": len(near_misses),
            "surfaced": False,
            "investigated": False,
        }

    best = max(hits, key=lambda pair: _verdict_rank(pair[1]["verdict"]))
    candidate, match = best
    verdict = match["verdict"]
    if len(hits) > 1:
        verdict = DUPLICATED if verdict != DETECTED_CORRECT_ROOT else verdict

    investigated = _investigated(candidate, investigations or [])
    if verdict == DETECTED_CORRECT_ROOT and investigated:
        category = "NOT_A_DISCOVERY_FAILURE"
    elif verdict == DETECTED_CORRECT_ROOT:
        category = "INVESTIGATION_MISS"
    elif verdict == DUPLICATED:
        category = "CONSOLIDATION_MISS"
    elif verdict == DETECTED_SYMPTOM_ONLY:
        category = "CLUSTERING_MISS"
    else:
        category = "NOT_A_DISCOVERY_FAILURE"

    return {
        "issue_id": issue.issue_id,
        "title": issue.title,
        "verdict": verdict,
        "category": category,
        "matchable": True,
        "matched_candidates": len(hits),
        "matched_root_id": str(candidate.get("root_id") or ""),
        "matched_root_title": str(candidate.get("root_title") or ""),
        "matched_score": float(candidate.get("score") or 0.0),
        "independent_evidence": match["independent_evidence"],
        "required_hits": match["required_hits"],
        "root_family_hit": match["root_family_hit"],
        "signal_sources": sorted(s for s in signal_sources if s),
        "expected_signal_sources": list(issue.expected_signal_sources),
        "component_only_near_misses": len(near_misses),
        "surfaced": True,
        "investigated": investigated,
    }


def _issue_in_text(issue: KnownIssueV2, text: str) -> bool:
    blob = _norm(text)
    if not blob:
        return False
    if issue.required_tokens:
        return all(_norm(t) in blob for t in issue.required_tokens)
    return any(_norm(t) in blob for t in issue.any_tokens)


_VERDICT_RANK = {
    DETECTED_CORRECT_ROOT: 4,
    DETECTED_CORRECT_FAMILY: 3,
    DETECTED_SYMPTOM_ONLY: 2,
    DETECTED_WRONG_ROOT: 1,
}


def _verdict_rank(verdict: str) -> int:
    return _VERDICT_RANK.get(str(verdict), 0)


def _investigated(candidate: Mapping[str, Any], investigations: Sequence[Mapping[str, Any]]) -> bool:
    cid = str(candidate.get("candidate_id") or "")
    root = str(candidate.get("root_id") or "")
    for inv in investigations:
        if cid and str(inv.get("candidate_id") or "") == cid:
            return True
        blob = _norm(" ".join(str(inv.get(k) or "") for k in ("goal", "scope", "candidate_id")))
        if root and _norm(root) in blob:
            return True
    return str(candidate.get("status") or "").upper() == "INVESTIGATING"


def known_issue_recall_v2(results: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    """Recall over independently confirmed issues, with the denominator shown.

    This is not a global false-negative rate and is never reported as one: it
    covers only issues we already know about.
    """
    rows = [dict(r) for r in results]
    matchable = [r for r in rows if r.get("matchable")]
    surfaced = [r for r in matchable if r.get("surfaced")]
    investigated = [r for r in matchable if r.get("investigated")]
    exact = [r for r in matchable if r.get("verdict") == DETECTED_CORRECT_ROOT]

    by_verdict: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    near_misses = 0
    for row in rows:
        by_verdict[str(row.get("verdict"))] = by_verdict.get(str(row.get("verdict")), 0) + 1
        by_category[str(row.get("category"))] = by_category.get(str(row.get("category")), 0) + 1
        near_misses += int(row.get("component_only_near_misses") or 0)

    n = len(matchable)
    return {
        "kind": "KNOWN_ISSUE_RECALL_V2",
        "denominator": n,
        "unmatchable_excluded": len(rows) - n,
        "surfaced": len(surfaced),
        "investigated": len(investigated),
        "exact_root": len(exact),
        "known_issue_recall": round(len(surfaced) / n, 4) if n else 0.0,
        "investigated_rate": round(len(investigated) / n, 4) if n else 0.0,
        "exact_root_recall": round(len(exact) / n, 4) if n else 0.0,
        "by_verdict": dict(sorted(by_verdict.items())),
        "by_category": dict(sorted(by_category.items())),
        # v1 would have credited these. Reported so the correction stays visible.
        "component_only_near_misses_rejected": near_misses,
        "is_global_false_negative_rate": False,
        "note": "recall over independently confirmed issues only; not a global FN rate",
    }
