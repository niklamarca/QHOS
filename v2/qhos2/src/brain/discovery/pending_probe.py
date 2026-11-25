"""Temporal maturity experiments for UNKNOWN_PENDING under frozen G1.

The previous phase promoted ``ingestion`` at ``pending_duration_s = 0.0`` with
``cycles_pending = 1`` at severity ``high``. That was recorded as a known
weakness and deliberately not fixed, so the behaviour could be measured rather
than remembered.

Three experiments separate the two things that got conflated in that finding:
whether *independent evidence* justifies promotion, and whether *elapsed time*
does. G1's answer is that evidence alone is sufficient and time contributes
nothing, which is right in direction and unbounded in degree — a single cycle
with zero elapsed pending time promotes at the same severity as a component
that has been failing for an hour.

A maturity proposal is scored here but not applied. Applying it would change
promotion behaviour mid-matrix, and the matrix result would then describe a
policy no artifact records.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from .engine import DiscoveryEngine

PROBE_A = "single_cycle_no_evidence"
PROBE_B = "single_cycle_independent_evidence"
PROBE_C = "multi_cycle_downstream_degradation"


def _snapshot(
    *,
    component: str = "ingestion",
    status: str = "pending",
    error_lines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "registry": {
            component: {
                "healthy": None,
                "status": status,
                # No uptime and no error count: the source never observed a
                # lifecycle, which is what makes this UNKNOWN_PENDING.
                "uptime": None,
                "error_count": 0,
            },
            "storage": {"healthy": True, "status": "ok", "uptime": 900.0, "error_count": 0},
        },
        "error_tail": list(error_lines or []),
    }


def _pending_state(intel: Mapping[str, Any], component: str) -> Dict[str, Any]:
    for state in (intel.get("pending_model") or {}).get("states") or []:
        if str(state.get("component")) == component:
            return dict(state)
    return {}


def _promoted(intel: Mapping[str, Any], component: str) -> List[Dict[str, Any]]:
    out = []
    for cand in intel.get("incidents") or []:
        if str(cand.get("component")) == component:
            out.append(dict(cand))
    return out


def run_probe(
    name: str,
    *,
    cycles: int,
    error_lines: Optional[List[str]] = None,
    component: str = "ingestion",
) -> Dict[str, Any]:
    engine = DiscoveryEngine()
    intel: Dict[str, Any] = {}
    per_cycle: List[Dict[str, Any]] = []
    for cycle in range(1, cycles + 1):
        intel = engine.ingest(
            _snapshot(component=component, error_lines=error_lines), api_enabled=False,
        )
        state = _pending_state(intel, component)
        promoted = _promoted(intel, component)
        per_cycle.append({
            "cycle": cycle,
            "classification": state.get("classification"),
            "promotion_reason": state.get("promotion_reason"),
            "pending_duration_s": state.get("duration_s"),
            "cycles_pending": state.get("cycles_pending"),
            "error_evidence": state.get("error_evidence"),
            "dependent_impact": state.get("dependent_impact"),
            "promoted": bool(promoted),
            "severity": promoted[0].get("severity") if promoted else None,
            "score": round(float(promoted[0].get("score") or 0.0), 4) if promoted else None,
        })
    final = per_cycle[-1] if per_cycle else {}
    first_promotion = next((r for r in per_cycle if r["promoted"]), None)
    return {
        "probe": name,
        "component": component,
        "cycles": cycles,
        "per_cycle": per_cycle,
        "promoted_at_all": any(r["promoted"] for r in per_cycle),
        "first_promotion_cycle": first_promotion["cycle"] if first_promotion else 0,
        "first_promotion_duration_s": first_promotion["pending_duration_s"] if first_promotion else None,
        "first_promotion_severity": first_promotion["severity"] if first_promotion else None,
        "final_classification": final.get("classification"),
        "final_promotion_reason": final.get("promotion_reason"),
    }


def run_pending_probes() -> Dict[str, Any]:
    """Experiments A, B and C. Read-only, deterministic, LLM off."""
    a = run_probe(PROBE_A, cycles=1)
    b = run_probe(
        PROBE_B, cycles=1,
        error_lines=[
            "2026-09-03 20:11:02.100 | ERROR | core.ingestion | IngestionStalledError "
            "code=ING_09 ingestion worker has not committed an offset in 900s",
        ],
    )
    c = run_probe(
        PROBE_C, cycles=5,
        error_lines=[
            "2026-09-03 20:11:02.100 | ERROR | execution.router | downstream dependency "
            "unavailable: ingestion returned no data for 41 consecutive requests",
        ],
    )
    a_multi = run_probe(PROBE_A + "_five_cycles", cycles=5)

    return {
        "kind": "UNKNOWN_PENDING_TEMPORAL_PROBE",
        "probes": {PROBE_A: a, PROBE_B: b, PROBE_C: c, PROBE_A + "_five_cycles": a_multi},
        "findings": {
            "suppression_holds_without_evidence": not a["promoted_at_all"],
            "suppression_still_holds_after_five_cycles": not a_multi["promoted_at_all"],
            "evidence_overrides_suppression": b["promoted_at_all"],
            "promotes_at_zero_elapsed_duration": (
                b["promoted_at_all"] and float(b["first_promotion_duration_s"] or 0.0) == 0.0
            ),
            "first_promotion_severity": b["first_promotion_severity"],
            "downstream_degradation_promotes": c["promoted_at_all"],
        },
        "policy_unchanged": True,
    }


# ---------------------------------------------------------------------------
# Maturity proposal. Scored, not applied.
# ---------------------------------------------------------------------------

# A component observed pending in exactly one cycle has been seen once. That is
# enough to raise a candidate when independent evidence exists, and not enough
# to call it high severity: nothing yet distinguishes a failure from a sample.
MATURITY_MIN_CYCLES_FOR_HIGH = 2


def maturity_severity(state: Mapping[str, Any], current: str) -> Dict[str, Any]:
    """Severity a maturity-aware policy would assign. Advisory only.

    Deliberately not a seconds constant. Elapsed time is exactly the signal that
    is meaningless here: a component with no lifecycle telemetry reports the
    same static default forever, so ageing it measures how long we have been
    reading a default rather than how long anything has been broken. Cycles
    observed, independent evidence and downstream impact are all real.
    """
    cycles = int(state.get("cycles_pending") or 0)
    evidence = bool(state.get("error_evidence"))
    downstream = bool(state.get("dependent_impact") or state.get("missed_sla"))

    if not evidence and not downstream:
        return {"severity": current, "changed": False, "reason": "not a promoted state"}

    if cycles >= MATURITY_MIN_CYCLES_FOR_HIGH or downstream:
        return {
            "severity": current,
            "changed": False,
            "reason": f"corroborated across {cycles} cycles"
                      if cycles >= MATURITY_MIN_CYCLES_FOR_HIGH
                      else "downstream impact observed",
        }
    return {
        "severity": "medium" if current == "high" else current,
        "changed": current == "high",
        "reason": (
            "promoted on independent evidence in its first observed cycle; "
            "severity held at medium until a second cycle confirms it is not a "
            "single sample"
        ),
        "promotes": True,
    }
