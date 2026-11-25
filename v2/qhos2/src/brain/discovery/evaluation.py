"""Evaluation of Discovery quality. Pure measurement, never feedback.

Candidate precision answers "was what we promoted useful?". It cannot answer
"did we group things correctly?" or "what did we miss?". This module adds those:

* root cluster precision/recall and the over/under-dedup rates derived from them,
  measured against a labelled grouping;
* a false-negative retrospective that walks a known issue back through the
  pipeline and names the stage that lost it;
* known-issue recall with an explicit denominator.

Nothing here writes to the engine, adjusts a weight or changes a threshold.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .fingerprint import mentions_component

# ---------------------------------------------------------------------------
# Root cluster quality
# ---------------------------------------------------------------------------

FN_CATEGORIES = (
    "NO_SIGNAL",
    "NORMALIZATION_MISS",
    "SUPPRESSION_MISS",
    "DEDUP_MISS",
    "SCORING_MISS",
    "CLUSTERING_MISS",
    "INVESTIGATION_MISS",
    "NOT_A_DISCOVERY_FAILURE",
)


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def root_cluster_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Pairwise clustering quality against a labelled true grouping.

    Each row needs a ``true_root`` label and the ``root_id`` the engine assigned.
    Pairwise is the right frame here because the question is always about a pair:
    *should these two observations have shared a candidate?*

    * precision — of pairs we merged, how many belonged together;
    * recall — of pairs that belonged together, how many we merged;
    * over-dedup rate — merged pairs that did not belong together (1 - precision);
    * under-dedup rate — belonging pairs we split (1 - recall).
    """
    pairs = list(combinations(range(len(rows)), 2))
    tp = fp = fn = tn = 0
    for i, j in pairs:
        same_true = str(rows[i].get("true_root")) == str(rows[j].get("true_root"))
        same_pred = str(rows[i].get("root_id")) == str(rows[j].get("root_id"))
        if same_true and same_pred:
            tp += 1
        elif same_pred:
            fp += 1
        elif same_true:
            fn += 1
        else:
            tn += 1

    precision = _rate(tp, tp + fp)
    recall = _rate(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0
    return {
        "kind": "ROOT_CLUSTER_METRICS",
        "observations": len(rows),
        "pairs": len(pairs),
        "true_roots": len({str(r.get("true_root")) for r in rows}),
        "predicted_clusters": len({str(r.get("root_id")) for r in rows}),
        "merged_correctly": tp,
        "merged_wrongly": fp,
        "split_wrongly": fn,
        "separated_correctly": tn,
        "cluster_precision": precision,
        "cluster_recall": recall,
        "cluster_f1": f1,
        # A merge that should not have happened. The dangerous direction: it hides
        # a distinct problem inside another candidate.
        "over_dedup_rate": round(1.0 - precision, 4) if (tp + fp) else 0.0,
        # A split that should not have happened. Produces duplicate candidates.
        "under_dedup_rate": round(1.0 - recall, 4) if (tp + fn) else 0.0,
    }


# ---------------------------------------------------------------------------
# False-negative retrospective
# ---------------------------------------------------------------------------


@dataclass
class KnownIssue:
    """An issue confirmed independently of the Brain."""

    issue_id: str
    title: str
    component: str = ""
    keywords: Tuple[str, ...] = ()
    confirmed_by: str = ""
    expected_discoverable: bool = True

    def matches_text(self, text: str) -> bool:
        """Keywords decide; the component name alone is not evidence.

        A component name matches anything that mentions the component, including
        rows about an unrelated problem in it. Counting those as the issue having
        been "surfaced" inflates recall, which is the one number this audit exists
        to keep honest. Component matching is therefore only a fallback for issues
        that supply no keywords at all.
        """
        low = str(text or "").lower()
        if self.keywords:
            return any(k.lower() in low for k in self.keywords)
        return bool(self.component) and mentions_component(text, self.component)


@dataclass
class PipelineFunnel:
    """What each Discovery stage saw, for one soak session."""

    raw_summaries: List[str] = field(default_factory=list)
    normalized: List[Mapping[str, Any]] = field(default_factory=list)
    suppressed: List[Mapping[str, Any]] = field(default_factory=list)
    duplicates: List[Mapping[str, Any]] = field(default_factory=list)
    clusters: List[Mapping[str, Any]] = field(default_factory=list)
    candidates: List[Mapping[str, Any]] = field(default_factory=list)
    investigations: List[Mapping[str, Any]] = field(default_factory=list)
    threshold: float = 0.42


def retrospect_issue(issue: KnownIssue, funnel: PipelineFunnel) -> Dict[str, Any]:
    """Walk one known issue back through the pipeline and name the losing stage.

    Order matters: the earliest stage that lost the signal is the one to blame,
    because every later stage never had a chance to see it.
    """
    # Stages carry different shapes. An investigation has ``goal``/``scope`` and
    # no ``summary`` at all, so a summary-only reader silently matched nothing and
    # reported every surfaced issue as an investigation miss.
    _TEXT_KEYS = (
        "payload_summary", "summary", "root_title", "title", "problem",
        "component", "goal", "scope", "observed_problem",
    )
    _LIST_KEYS = ("evidence", "observations", "affected_components")

    def _row_text(row: Mapping[str, Any]) -> str:
        parts = [str(row.get(k) or "") for k in _TEXT_KEYS]
        for key in _LIST_KEYS:
            value = row.get(key)
            if isinstance(value, (list, tuple)):
                parts.extend(str(v) for v in value[:8])
        return " ".join(parts)

    def _texts(rows: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
        return [row for row in rows if issue.matches_text(_row_text(row))]

    raw_hits = [s for s in funnel.raw_summaries if issue.matches_text(s)]
    norm_hits = _texts(funnel.normalized)
    supp_hits = _texts(funnel.suppressed)
    dup_hits = _texts(funnel.duplicates)
    cluster_hits = _texts(funnel.clusters)
    cand_hits = _texts(funnel.candidates)
    inv_hits = _texts(funnel.investigations)

    surfaced = bool(cand_hits)
    investigated = bool(inv_hits)

    if surfaced:
        category = "NOT_A_DISCOVERY_FAILURE" if investigated else "INVESTIGATION_MISS"
        detail = (
            "promoted and investigated" if investigated
            else "promoted as a candidate but no investigation opened"
        )
    elif not raw_hits and not norm_hits:
        category = "NO_SIGNAL"
        detail = "no observation matching this issue reached the engine"
    elif raw_hits and not norm_hits and not supp_hits:
        category = "NORMALIZATION_MISS"
        detail = "raw signal present but never normalized into an observation"
    elif supp_hits:
        classes = sorted({str(r.get("noise_class") or "") for r in supp_hits if r.get("noise_class")})
        if classes == ["DUPLICATE"]:
            category = "DEDUP_MISS"
            detail = "every matching observation was suppressed as a duplicate"
        else:
            category = "SUPPRESSION_MISS"
            detail = f"suppressed as noise ({', '.join(classes) or 'unclassified'})"
    elif dup_hits and not cluster_hits:
        category = "DEDUP_MISS"
        detail = "collapsed into another root and never surfaced on its own"
    elif cluster_hits:
        best = max((float(r.get("score") or 0.0) for r in cluster_hits), default=0.0)
        if best < funnel.threshold:
            category = "SCORING_MISS"
            detail = f"clustered and scored {best:.3f}, below threshold {funnel.threshold:.2f}"
        else:
            category = "CLUSTERING_MISS"
            detail = "clustered above threshold yet absent from candidates"
    else:
        category = "CLUSTERING_MISS"
        detail = "normalized but never formed a root cluster"

    return {
        "kind": "FALSE_NEGATIVE_RETROSPECTIVE",
        "issue_id": issue.issue_id,
        "title": issue.title,
        "confirmed_by": issue.confirmed_by,
        "surfaced": surfaced,
        "investigated": investigated,
        "category": category,
        "detail": detail,
        "funnel": {
            "raw_matches": len(raw_hits),
            "normalized_matches": len(norm_hits),
            "suppressed_matches": len(supp_hits),
            "duplicate_matches": len(dup_hits),
            "cluster_matches": len(cluster_hits),
            "candidate_matches": len(cand_hits),
            "investigation_matches": len(inv_hits),
        },
        "matched_candidates": [str(r.get("root_title") or r.get("summary") or "")[:120]
                               for r in cand_hits][:4],
    }


def known_issue_recall(
    issues: Sequence[KnownIssue],
    retrospectives: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Recall over independently confirmed issues only.

    Explicitly not a global false-negative rate: the denominator is the set of
    issues we happen to know about, not the set that exists.
    """
    by_id = {str(r.get("issue_id")): r for r in retrospectives}
    considered = [i for i in issues if i.expected_discoverable]
    surfaced = [i for i in considered if (by_id.get(i.issue_id) or {}).get("surfaced")]
    excluded = [i.issue_id for i in issues if not i.expected_discoverable]

    investigated = [i for i in considered if (by_id.get(i.issue_id) or {}).get("investigated")]

    by_category: Dict[str, int] = {}
    for issue in considered:
        cat = str((by_id.get(issue.issue_id) or {}).get("category") or "NO_RETROSPECTIVE")
        by_category[cat] = by_category.get(cat, 0) + 1

    return {
        "kind": "KNOWN_ISSUE_RECALL",
        "denominator": len(considered),
        "surfaced": len(surfaced),
        "known_issue_recall": _rate(len(surfaced), len(considered)),
        # Reaching a candidate and reaching an investigation are different bars.
        # Reporting only recall hides an issue that surfaced and then stalled.
        "investigated": len(investigated),
        "investigated_rate": _rate(len(investigated), len(considered)),
        "surfaced_ids": [i.issue_id for i in surfaced],
        "missed_ids": [i.issue_id for i in considered if i not in surfaced],
        "excluded_not_discoverable": excluded,
        "by_category": dict(sorted(by_category.items())),
        "is_global_false_negative_rate": False,
        "caveat": "denominator is independently confirmed issues only, not all real issues",
    }


# ---------------------------------------------------------------------------
# Aggregation across independent sessions
# ---------------------------------------------------------------------------


def aggregate_sessions(sessions: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Macro and micro aggregates with sample sizes shown.

    Macro averages sessions equally; micro pools candidates. They diverge when
    sessions have very different candidate counts, and reporting only one of them
    would flatter whichever number is prettier.
    """
    per: List[Dict[str, Any]] = []
    for s in sessions:
        useful = int(s.get("reviewed_useful") or 0)
        noise = int(s.get("reviewed_noise") or 0)
        dup = int(s.get("reviewed_duplicate") or 0)
        uncertain = int(s.get("reviewed_uncertain") or 0)
        reviewed = useful + noise + dup + uncertain
        minutes = max(1e-9, float(s.get("duration_s") or 0.0) / 60.0)
        per.append({
            "label": s.get("label"),
            "duration_s": s.get("duration_s"),
            "signals": int(s.get("signals") or 0),
            "candidates": int(s.get("candidates") or 0),
            "reviewed": reviewed,
            "reviewed_useful": useful,
            "reviewed_noise": noise,
            "reviewed_duplicate": dup,
            "reviewed_uncertain": uncertain,
            "precision": _rate(useful, reviewed) if reviewed else None,
            "noise_escape": _rate(noise, reviewed) if reviewed else None,
            "duplicate_escape": _rate(dup, reviewed) if reviewed else None,
            "candidate_rate_per_min": round(int(s.get("candidates") or 0) / minutes, 4),
        })

    scored = [p for p in per if p["precision"] is not None]
    tot_useful = sum(p["reviewed_useful"] for p in per)
    tot_noise = sum(p["reviewed_noise"] for p in per)
    tot_dup = sum(p["reviewed_duplicate"] for p in per)
    tot_uncertain = sum(p["reviewed_uncertain"] for p in per)
    tot_reviewed = tot_useful + tot_noise + tot_dup + tot_uncertain
    tot_minutes = sum(float(p["duration_s"] or 0.0) for p in per) / 60.0

    return {
        "kind": "MULTI_SESSION_AGGREGATE",
        "sessions_n": len(per),
        "sessions_with_reviews_n": len(scored),
        "per_session": per,
        "macro_precision": (
            round(sum(p["precision"] for p in scored) / len(scored), 4) if scored else None
        ),
        "micro_precision": _rate(tot_useful, tot_reviewed) if tot_reviewed else None,
        "macro_candidate_rate_per_min": (
            round(sum(p["candidate_rate_per_min"] for p in per) / len(per), 4) if per else 0.0
        ),
        "micro_candidate_rate_per_min": (
            round(sum(p["candidates"] for p in per) / tot_minutes, 4) if tot_minutes else 0.0
        ),
        "noise_escape": _rate(tot_noise, tot_reviewed) if tot_reviewed else None,
        "duplicate_escape": _rate(tot_dup, tot_reviewed) if tot_reviewed else None,
        "reviewed_total": tot_reviewed,
        "signals_total": sum(p["signals"] for p in per),
        "candidates_total": sum(p["candidates"] for p in per),
        "minutes_total": round(tot_minutes, 2),
        "zero_candidate_sessions": [p["label"] for p in per if p["candidates"] == 0],
        "not_training_truth": True,
    }
