"""Deterministic discovery score. Model confidence is never priority.

v2 changes (explicit, tested against the prior soak, synthetic streams and
holdout scenarios in ``tests/test_brain_calibration.py``):

* raw ``frequency`` weight cut 0.10 -> 0.04 — a log line repeated per symbol is
  one root cause, not twenty problems;
* new ``root_signature`` feature (weight 0.14) rewards a concretely extracted
  exception type / missing dependency / error code;
* new ``root_occurrences`` feature (weight 0.06) rewards breadth across distinct
  instances rather than raw line count;
* new ``temporal_maturity`` feature (weight 0.04) rewards signals observed across
  more than one ingest cycle;
* explicit ``expected_state`` penalty for benign pending/startup states.

Every component is returned with its weight, value and contribution so a reviewer
can see why one candidate outranked another. No hidden constants.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

SCORE_VERSION = "discovery-score-v2"

IMPORTANT = frozenset({
    "risk_engine", "orchestrator", "storage", "execution", "parser", "oms",
    "ingestion", "market_data",
})

# Weights sum to 1.0. Keep it that way if you edit them.
SCORE_WEIGHTS: Dict[str, float] = {
    "novelty": 0.14,
    "severity": 0.18,
    "persistence": 0.10,
    "frequency": 0.04,
    "importance": 0.10,
    "cross_signal": 0.08,
    "recurrence": 0.06,
    "data_quality": 0.06,
    "root_signature": 0.14,
    "root_occurrences": 0.06,
    "temporal_maturity": 0.04,
}

# Applied after the weighted sum. Benign startup states are state observations.
EXPECTED_STATE_PENALTY = 0.30

SEVERITY_SCALE = {"low": 0.2, "medium": 0.5, "high": 0.8, "critical": 1.0}


def _features(row: Mapping[str, Any]) -> Dict[str, float]:
    count = int(row.get("count") or 1)
    comp = str(row.get("component") or "")
    fam = str(row.get("component_family") or "")
    important = any(
        tok in comp or tok in fam for tok in IMPORTANT
    ) or comp in IMPORTANT or fam in IMPORTANT
    instances = int(row.get("affected_instances") or 0)
    cycles = int(row.get("cycles_observed") or 0)
    # G1.1 proposal hook. Returns the G1 value unchanged unless a caller has
    # explicitly opted in, so the frozen policy is unaffected by its presence.
    from .policy_g11 import root_signature_value
    root_sig = root_signature_value(row, 1.0 if row.get("root_concrete") else 0.0)
    return {
        "novelty": 1.0 if str(row.get("novelty") or "NEW") == "NEW" else 0.2,
        "severity": SEVERITY_SCALE.get(str(row.get("severity") or "low"), 0.2),
        "persistence": min(1.0, 0.15 * max(0, count - 1)),
        "frequency": min(1.0, count / 20.0),
        "importance": 0.8 if important else 0.3,
        "cross_signal": min(1.0, 0.25 * int(row.get("cross_signals") or 0)),
        "recurrence": min(1.0, 0.2 * int(row.get("historical") or 0)),
        "data_quality": 0.2 if row.get("pre_existing") else 0.7,
        "root_signature": root_sig,
        "root_occurrences": min(1.0, 0.2 * max(0, instances - 1)),
        "temporal_maturity": min(1.0, 0.34 * max(0, cycles - 1)),
    }


def discovery_score(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Score a candidate row. Deterministic and fully explained."""
    if row.get("denied"):
        return {
            "score": 0.0,
            "priority": 0,
            "reason": "denied_sensitive",
            "promotion_reason": "denied_sensitive",
            "factors": {},
            "score_components": {},
            "score_version": SCORE_VERSION,
            "used_model_confidence": False,
        }

    values = _features(row)
    components: Dict[str, Dict[str, float]] = {}
    base = 0.0
    for name, weight in SCORE_WEIGHTS.items():
        value = values[name]
        contribution = round(weight * value, 6)
        base += contribution
        components[name] = {
            "value": round(value, 4),
            "weight": weight,
            "contribution": contribution,
        }

    penalty = 0.0
    expected_state = bool(row.get("expected_state"))
    if expected_state:
        penalty = EXPECTED_STATE_PENALTY
    components["expected_state_penalty"] = {
        "value": 1.0 if expected_state else 0.0,
        "weight": -EXPECTED_STATE_PENALTY,
        "contribution": round(-penalty, 6),
    }

    total = max(0.0, min(1.0, base - penalty))

    # Explicitly unused: model_confidence never influences priority.
    _ = row.get("model_confidence")

    top = sorted(
        ((k, v["contribution"]) for k, v in components.items() if v["contribution"]),
        key=lambda kv: abs(kv[1]),
        reverse=True,
    )[:3]
    reason_parts = [f"{k}={v:+.3f}" for k, v in top]
    if expected_state:
        reason_parts.append(f"expected_state_penalty=-{EXPECTED_STATE_PENALTY:.2f}")

    return {
        "score": round(total, 4),
        "score_total": round(total, 4),
        "priority": int(round(total * 100)),
        "factors": {k: v["value"] for k, v in components.items()},
        "score_components": components,
        "score_base": round(base, 4),
        "score_penalty": round(penalty, 4),
        "promotion_reason": "; ".join(reason_parts) or "no_contributing_features",
        "score_version": SCORE_VERSION,
        "used_model_confidence": False,
    }


def score_feature_audit(rows: Any) -> Dict[str, Any]:
    """Mean contribution per feature split by reviewed disposition.

    Evaluation only. Never feeds back into ``SCORE_WEIGHTS`` automatically.
    """
    buckets: Dict[str, Dict[str, list]] = {"useful": {}, "noise": {}}
    counts = {"useful": 0, "noise": 0}
    for row in rows or []:
        disposition = str(row.get("human_disposition") or row.get("disposition") or "").upper()
        if disposition in {"USEFUL", "CONFIRMED"}:
            bucket = "useful"
        elif disposition in {"NOT_USEFUL", "DISMISSED", "DUPLICATE"}:
            bucket = "noise"
        else:
            continue
        counts[bucket] += 1
        comps = row.get("score_components") or {}
        for name, detail in comps.items():
            if not isinstance(detail, Mapping):
                continue
            buckets[bucket].setdefault(name, []).append(float(detail.get("contribution") or 0.0))

    def _means(bucket: str) -> Dict[str, float]:
        return {
            name: round(sum(vals) / len(vals), 4)
            for name, vals in buckets[bucket].items()
            if vals
        }

    useful = _means("useful")
    noise = _means("noise")
    separation = {
        name: round(useful.get(name, 0.0) - noise.get(name, 0.0), 4)
        for name in set(useful) | set(noise)
    }
    return {
        "kind": "SCORE_FEATURE_AUDIT",
        "score_version": SCORE_VERSION,
        "useful_n": counts["useful"],
        "noise_n": counts["noise"],
        "useful_mean_contribution": useful,
        "noise_mean_contribution": noise,
        "separation": dict(sorted(separation.items(), key=lambda kv: kv[1], reverse=True)),
        "weights": dict(SCORE_WEIGHTS),
        "auto_applied": False,
    }
