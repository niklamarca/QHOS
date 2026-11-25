"""Generalization metrics over a fault-matrix artifact.

Four recall numbers are reported separately and never averaged into one
percentage. They answer different questions and a single figure hides which one
failed: whether the signal arrived, whether anything was promoted, whether the
promotion landed in the right family, and whether it landed on the right root.
A pipeline can score 1.0 on the first and 0.0 on the last.

Per-class results come before every aggregate, because an aggregate over fifteen
classes is exactly the number that lets one broken class disappear.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from .known_issue import (
    CLUSTERED_INTO_OTHER_ROOT,
    DETECTED_CORRECT_FAMILY,
    DETECTED_CORRECT_ROOT,
    DETECTED_SYMPTOM_ONLY,
    DUPLICATED,
    MISSED,
    NO_SIGNAL,
    SUPPRESSED,
)

DETECTED = frozenset({
    DETECTED_CORRECT_ROOT, DETECTED_CORRECT_FAMILY, DETECTED_SYMPTOM_ONLY,
    DUPLICATED, CLUSTERED_INTO_OTHER_ROOT,
})


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def per_fault_rows(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """One row per manifest. Always reported before any aggregate."""
    out: List[Dict[str, Any]] = []
    for result in payload.get("results") or []:
        fault = result["fault"]
        det = result["detection"]
        expects_candidate = fault["expected_outcome"] != "SUPPRESSED"
        out.append({
            "fault_id": fault["fault_id"],
            "fault_class": fault["fault_class"],
            "cohort": fault["cohort"],
            "target": fault["target_component"],
            "expected_outcome": fault["expected_outcome"],
            "expected_signals": fault["expected_signals"],
            "actual_signals": result["observation_sources"],
            "signal_seen": bool(det.get("signal_observations")),
            "verdict": det["verdict"],
            "category": det["category"],
            "expects_candidate": expects_candidate,
            "candidate": bool(det.get("matched_candidates")),
            "family_hit": bool(det.get("root_family_hit")),
            "exact_root": det["verdict"] == DETECTED_CORRECT_ROOT,
            "investigated": bool(det.get("investigated")),
            "cycles_to_observation": result["cycles_to_observation"],
            "cycles_to_candidate": result["cycles_to_candidate"],
            "seconds_to_candidate": result["seconds_to_candidate"],
            "fault_related": result["candidates_fault_related"],
            "fault_symptom": result.get("candidates_fault_symptom", 0),
            "unrelated": result["candidates_unrelated"],
            "unconsolidated_symptoms": result.get("unconsolidated_symptoms", 0),
            "run_precision": result["run_precision"],
            "root_incidents": result["consolidation"]["root_incidents"],
            "secondary_effects": result["consolidation"]["secondary_effects"],
            "unique_root_inflation": result["consolidation"]["unique_root_inflation"],
            "expected_candidates": fault["expected_candidates"],
            "distinct_roots": det.get("distinct_roots", 0),
            "primary_roots": det.get("primary_roots", 0),
        })
    return out


def recall_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """The four recall numbers, each with its own explicit denominator."""
    detection_rows = [r for r in rows if r["expects_candidate"]]
    n = len(detection_rows)
    signal = sum(1 for r in detection_rows if r["signal_seen"])
    candidate = sum(1 for r in detection_rows if r["candidate"])
    family = sum(1 for r in detection_rows if r["family_hit"])
    exact = sum(1 for r in detection_rows if r["exact_root"])
    investigated = sum(1 for r in detection_rows if r["investigated"])
    return {
        "kind": "GENERALIZATION_RECALL",
        "denominator": n,
        "suppression_cases_excluded": len(rows) - n,
        "signal_recall": _rate(signal, n),
        "candidate_recall": _rate(candidate, n),
        "root_family_recall": _rate(family, n),
        "exact_root_recall": _rate(exact, n),
        "investigation_rate": _rate(investigated, n),
        "counts": {
            "signal": signal, "candidate": candidate,
            "family": family, "exact_root": exact, "investigated": investigated,
        },
    }


def precision_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Precision, noise escape and duplicate escape over every new candidate."""
    related = sum(r["fault_related"] for r in rows)
    symptom = sum(r["fault_symptom"] for r in rows)
    unrelated = sum(r["unrelated"] for r in rows)
    unconsolidated = sum(r["unconsolidated_symptoms"] for r in rows)
    total = related + symptom + unrelated
    suppression_rows = [r for r in rows if not r["expects_candidate"]]
    suppression_breaches = sum(
        r["fault_related"] + r["fault_symptom"] + r["unrelated"] for r in suppression_rows
    )
    return {
        "kind": "GENERALIZATION_PRECISION",
        "candidates_total": total,
        "fault_root_candidates": related,
        "fault_symptom_candidates": symptom,
        "unrelated_candidates": unrelated,
        # A candidate a reviewer would call correct: the fault root, or a real
        # consequence of it.
        "candidate_precision": _rate(related + symptom, total),
        "noise_escape": _rate(unrelated, total),
        # A second surface of the same fault delivered as its own root.
        "duplicate_escape": _rate(unconsolidated, total),
        "suppression_cases": len(suppression_rows),
        "suppression_breaches": suppression_breaches,
    }


def dedup_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Over- and under-dedup, judged against what each manifest expected."""
    multi = [r for r in rows if r["expected_candidates"] > 1]
    single = [r for r in rows if r["expected_candidates"] == 1 and r["expects_candidate"]]
    over = [r for r in multi if r["distinct_roots"] < r["expected_candidates"]]
    under = [r for r in single if r["primary_roots"] > 1]
    detected = [r for r in rows if r["expects_candidate"] and r["candidate"]]
    correct_cluster = [r for r in detected if r["exact_root"] or r["family_hit"]]
    return {
        "kind": "ROOT_CLUSTER_QUALITY",
        "multi_root_cases": len(multi),
        "single_root_cases": len(single),
        "over_dedup_cases": [r["fault_id"] for r in over],
        "under_dedup_cases": [r["fault_id"] for r in under],
        "over_dedup_rate": _rate(len(over), len(multi)),
        "under_dedup_rate": _rate(len(under), len(single)),
        "root_cluster_precision": _rate(len(correct_cluster), len(detected)),
        "root_cluster_recall": _rate(
            len(correct_cluster), len([r for r in rows if r["expects_candidate"]])),
    }


def latency_metrics(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Detection latency in cycles. Cycles, not seconds, is the honest unit here.

    The matrix drives cycles as fast as the interpreter allows, so wall time
    measures Python rather than Discovery. A deployment multiplies the cycle
    count by its own interval.
    """
    obs = [r["cycles_to_observation"] for r in rows if r["cycles_to_observation"]]
    cand = [r["cycles_to_candidate"] for r in rows if r["cycles_to_candidate"]]
    return {
        "kind": "DETECTION_LATENCY",
        "unit": "ingest cycles",
        "observation_cases": len(obs),
        "candidate_cases": len(cand),
        "cycles_to_observation_max": max(obs) if obs else 0,
        "cycles_to_observation_mean": round(sum(obs) / len(obs), 3) if obs else 0.0,
        "cycles_to_candidate_max": max(cand) if cand else 0,
        "cycles_to_candidate_mean": round(sum(cand) / len(cand), 3) if cand else 0.0,
        "slower_than_one_cycle": [
            r["fault_id"] for r in rows if (r["cycles_to_candidate"] or 0) > 1
        ],
        "wall_seconds_note": "wall time reflects interpreter speed, not deployment latency",
    }


def observability_metrics(
    rows: Sequence[Mapping[str, Any]],
    payload: Mapping[str, Any],
) -> Dict[str, Any]:
    """Coverage: which sources fired, and which faults produced nothing at all."""
    no_signal = [r["fault_id"] for r in rows if r["expects_candidate"] and not r["signal_seen"]]
    expected_sources: Dict[str, int] = {}
    delivered_sources: Dict[str, int] = {}
    for row in rows:
        actual = set(row["actual_signals"])
        for src in row["expected_signals"]:
            expected_sources[src] = expected_sources.get(src, 0) + 1
            if src in actual:
                delivered_sources[src] = delivered_sources.get(src, 0) + 1
    attest_rows = [r for r in rows if "attestation" in r["expected_signals"]]
    return {
        "kind": "OBSERVABILITY_COVERAGE",
        "fault_classes_with_no_signal": sorted({
            r["fault_class"] for r in rows
            if r["expects_candidate"] and not r["signal_seen"]
        }),
        "no_signal_faults": no_signal,
        "signal_source_coverage": {
            src: {
                "expected_in": expected_sources[src],
                "delivered_in": delivered_sources.get(src, 0),
                "coverage": _rate(delivered_sources.get(src, 0), expected_sources[src]),
            }
            for src in sorted(expected_sources)
        },
        "attestation_coverage": {
            "faults_expecting_attestation": len(attest_rows),
            "attestation_delivered": sum(
                1 for r in attest_rows if "attestation" in r["actual_signals"]),
            "control_sources": payload.get("control", {}).get("sources") or [],
        },
    }


def failure_taxonomy(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_category: Dict[str, List[str]] = {}
    by_verdict: Dict[str, List[str]] = {}
    for row in rows:
        by_category.setdefault(row["category"], []).append(row["fault_id"])
        by_verdict.setdefault(row["verdict"], []).append(row["fault_id"])
    return {
        "kind": "FAILURE_TAXONOMY",
        "by_category": {k: sorted(v) for k, v in sorted(by_category.items())},
        "by_verdict": {k: sorted(v) for k, v in sorted(by_verdict.items())},
    }


def cohort_split(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Development and holdout reported apart: they are not the same evidence."""
    out: Dict[str, Any] = {"kind": "COHORT_SPLIT"}
    for cohort in ("development", "holdout"):
        subset = [r for r in rows if r["cohort"] == cohort]
        out[cohort] = {
            "manifests": len(subset),
            **{k: v for k, v in recall_metrics(subset).items()
               if k not in {"kind", "counts"}},
            "candidate_precision": precision_metrics(subset)["candidate_precision"],
            "noise_escape": precision_metrics(subset)["noise_escape"],
        }
    return out


def by_class(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    classes: Dict[str, List[Mapping[str, Any]]] = {}
    for row in rows:
        classes.setdefault(row["fault_class"], []).append(row)
    out: List[Dict[str, Any]] = []
    for name in sorted(classes):
        subset = classes[name]
        detection = [r for r in subset if r["expects_candidate"]]
        out.append({
            "fault_class": name,
            "manifests": len(subset),
            "signal_recall": _rate(sum(1 for r in detection if r["signal_seen"]), len(detection)),
            "candidate_recall": _rate(sum(1 for r in detection if r["candidate"]), len(detection)),
            "exact_root_recall": _rate(sum(1 for r in detection if r["exact_root"]), len(detection)),
            "investigated": sum(1 for r in subset if r["investigated"]),
            "verdicts": sorted({r["verdict"] for r in subset}),
            "categories": sorted({r["category"] for r in subset}),
            "max_cycles_to_candidate": max((r["cycles_to_candidate"] or 0) for r in subset),
        })
    return out


def matrix_metrics(payload: Mapping[str, Any]) -> Dict[str, Any]:
    rows = per_fault_rows(payload)
    return {
        "kind": "FAULT_MATRIX_METRICS",
        "policy": payload.get("policy"),
        "manifests": len(rows),
        "per_fault": rows,
        "per_class": by_class(rows),
        "cohorts": cohort_split(rows),
        "recall": recall_metrics(rows),
        "precision": precision_metrics(rows),
        "clustering": dedup_metrics(rows),
        "latency": latency_metrics(rows),
        "observability": observability_metrics(rows, payload),
        "taxonomy": failure_taxonomy(rows),
        "control_background_candidates": len(payload.get("control", {}).get("candidates") or []),
    }
