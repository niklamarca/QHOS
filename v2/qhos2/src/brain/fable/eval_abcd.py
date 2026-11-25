"""A/B/C/D comparison. C may be reused from a compatible REAL_HYBRID artifact."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from brain.cognitive_eval import (
    EvalMode,
    cognitive_gain,
    compare_abc,
    false_negative_rate,
    run_mode,
)
from brain.eval_taxonomy import family_of, tags_for
from brain.diagnose import leak_keys_present
from brain.gateway import CognitiveGateway
from brain.proving import GoldenScenario

from .strategy import FableDerivedStrategy


class AbcdMode:
    A = EvalMode.DETERMINISTIC_ONLY.value
    B = EvalMode.OFFLINE_HYBRID.value
    C = EvalMode.REAL_HYBRID.value
    D = "fable_derived"


def fable_gain(real: Optional[Dict[str, Any]], fable: Optional[Dict[str, Any]]) -> str:
    """PRIMARY comparison: FABLE_DERIVED vs CURRENT REAL HYBRID."""
    if not fable:
        return "NEUTRAL"
    if not real:
        return cognitive_gain({"metrics": {"root_cause": 0, "stop_decision": 0, "false_positive": 0}, "verification_efficiency": {}}, fable)
    if fable.get("violations") or (fable.get("metrics") or {}).get("safety", 1) < 1:
        return "NEGATIVE"
    r_root = float((real.get("metrics") or {}).get("root_cause") or 0.0)
    f_root = float((fable.get("metrics") or {}).get("root_cause") or 0.0)
    r_stop = float((real.get("metrics") or {}).get("stop_decision") or 0.0)
    f_stop = float((fable.get("metrics") or {}).get("stop_decision") or 0.0)
    r_fp = float((real.get("metrics") or {}).get("false_positive") or 0.0)
    f_fp = float((fable.get("metrics") or {}).get("false_positive") or 0.0)
    def _ve(block: Dict[str, Any]) -> float:
        ve = block.get("verification_efficiency")
        return float((ve or {}).get("score") or 0.0) if isinstance(ve, dict) else 0.0
    r_ve, f_ve = _ve(real), _ve(fable)
    r_mem = float(((real.get("replay") or {}).get("memory_metrics") or {}).get("precision") or 0.0)
    f_mem = float(((fable.get("replay") or {}).get("memory_metrics") or {}).get("precision") or 0.0)
    r_claim = float((real.get("completion_claim_accuracy") or 1.0))
    f_claim = float((fable.get("completion_claim_accuracy") or 1.0))
    worse = f_root + 0.02 < r_root or f_stop + 0.02 < r_stop or f_fp > r_fp + 0.05
    better = (
        f_root > r_root + 0.02
        or f_stop > r_stop + 0.02
        or f_ve > r_ve + 0.08
        or f_mem > r_mem + 0.08
        or f_claim > r_claim + 0.05
    )
    if worse:
        return "NEGATIVE"
    if better:
        return "POSITIVE"
    return "NEUTRAL"


def classify_fable_value(gains: List[str], safety_ok: bool) -> str:
    if not safety_ok:
        return "NEGATIVE"
    pos, neg, neu = gains.count("POSITIVE"), gains.count("NEGATIVE"), gains.count("NEUTRAL")
    if pos and not neg and pos >= 2:
        return "VALIDATED_FOR_SPECIFIC_FAMILIES"
    if pos and neg:
        return "MIXED"
    if neg and not pos:
        return "NEGATIVE"
    if pos == 1 and not neg:
        return "CANDIDATE"
    if neu and not pos and not neg:
        return "NO_MEASURED_VALUE"
    return "CANDIDATE"


def run_fable_mode(
    scenario: GoldenScenario,
    *,
    gateway: Optional[CognitiveGateway] = None,
    memory=None,
    budget=None,
    run_id: str = "",
) -> Any:
    from brain.cognitive_eval import ModeRun, evaluate_result, verification_efficiency, verification_classes
    from brain.cognitive_eval import hallucination_ledger, unsupported_claims, context_fingerprint, patch_is_invalid_fix
    from brain.diagnose import FixtureEnv
    import time
    from brain.eval_taxonomy import memory_hints_for

    leaked = leak_keys_present(scenario.context_payload())
    if leaked:
        raise AssertionError(f"SCENARIO INVALID leak={leaked}")
    obs = scenario.brain_observations()
    env = FixtureEnv(scenario.fixture_dir)
    hints = list(memory) if memory is not None else list(memory_hints_for(scenario.scenario_id) or [])
    t0 = time.perf_counter()
    result = FableDerivedStrategy().run(obs, env=env, memory=hints, budget=budget, gateway=gateway, mode="hybrid")
    latency = time.perf_counter() - t0
    report = evaluate_result(result, scenario.ground_truth, scenario_id=scenario.scenario_id)
    if scenario.ground_truth.get("forbid_tamper") and patch_is_invalid_fix(result):
        report.notes.append("INVALID_FIX")
        report.metrics.patch_correctness = 0.0
        report.passed = False
    ve = verification_efficiency(result)
    vclass = verification_classes(result, obs)
    halls = hallucination_ledger(result, scenario)
    unsupported = unsupported_claims(result)
    cost = result.cost or {}
    run = ModeRun(
        mode=AbcdMode.D,
        result=result,
        report=report,
        llm_calls=int(result.llm_calls or cost.get("llm_calls") or 0),
        tool_calls=int(cost.get("tool_calls") or 0),
        tokens=int(cost.get("tokens") or 0),
        latency_s=round(latency, 4),
    )
    fable_side = (result.sides or {}).get("fable") or {}
    run.result.replay_inputs = {
        **run.result.replay_inputs,
        "eval_mode": AbcdMode.D,
        "run_id": run_id,
        "context_fingerprint": context_fingerprint(obs),
        "verification_efficiency": ve,
        "verification_classes": vclass,
        "hallucinations": halls,
        "unsupported_claims": unsupported,
        "memory_metrics": (result.replay_inputs or {}).get("memory_metrics") or {},
        "context_metrics": (result.replay_inputs or {}).get("context_metrics") or {},
        "effort": (result.replay_inputs or {}).get("effort"),
        "completion_state": fable_side.get("completion_state"),
        "completion_claim_accuracy": fable_side.get("completion_claim_accuracy"),
        "prior_run_fed": False,
    }
    return run


def _mode_dict(run: Any) -> Dict[str, Any]:
    d = run.to_dict()
    d["replay"] = getattr(run.result, "replay_inputs", None) or {}
    d["completion_state"] = ((run.result.sides or {}).get("fable") or {}).get("completion_state")
    d["completion_claim_accuracy"] = ((run.result.sides or {}).get("fable") or {}).get("completion_claim_accuracy")
    d["effort"] = (run.result.replay_inputs or {}).get("effort")
    d["sides"] = run.result.sides
    return d


def run_abcd(
    scenario: GoldenScenario,
    *,
    memory=None,
    real_gateway: Optional[CognitiveGateway] = None,
    allow_real: bool = False,
    reuse_c: Optional[Dict[str, Any]] = None,
    run_fable_real: bool = False,
) -> Dict[str, Any]:
    det = run_mode(scenario, EvalMode.DETERMINISTIC_ONLY.value, memory=memory)
    off = run_mode(scenario, EvalMode.OFFLINE_HYBRID.value, memory=memory)
    real_d = reuse_c
    if allow_real and reuse_c is None:
        if real_gateway is not None:
            real_gateway.reset_session()
        real = run_mode(scenario, EvalMode.REAL_HYBRID.value, memory=memory, gateway=real_gateway)
        real_d = _mode_dict(real)
    if run_fable_real and allow_real:
        if real_gateway is not None:
            real_gateway.reset_session()
        fab = run_fable_mode(scenario, gateway=real_gateway, memory=memory)
    else:
        from brain.gateway import CognitiveGateway
        from brain.providers import OfflineProvider
        fab = run_fable_mode(
            scenario,
            gateway=CognitiveGateway(mode="hybrid", provider=OfflineProvider()),
            memory=memory,
        )
    fab_d = _mode_dict(fab)
    det_d = _mode_dict(det)
    return {
        "scenario_id": scenario.scenario_id,
        "family": family_of(scenario),
        "tags": tags_for(scenario.scenario_id, family_of(scenario)),
        "deterministic_only": det_d,
        "offline_hybrid": _mode_dict(off),
        "real_hybrid": real_d,
        "fable_derived": fab_d,
        "cognitive_gain": cognitive_gain(det_d, real_d),
        "fable_gain": fable_gain(real_d, fab_d),
        "real_attempted": bool(allow_real and reuse_c is None),
        "c_reused": reuse_c is not None,
        "fable_real": bool(run_fable_real and allow_real),
        "level3_enabled": False,
        "leaked": leak_keys_present(scenario.context_payload()),
        "independent": True,
    }


def lookup_reused_c(baseline: Mapping[str, Any], scenario_id: str, run_index: int) -> Optional[Dict[str, Any]]:
    rows = list(baseline.get("results") or [])
    exact = [
        r for r in rows
        if str(r.get("scenario_id") or "") == scenario_id and int(r.get("run_index") or 0) == run_index
    ]
    if exact and exact[0].get("real_hybrid"):
        return exact[0]["real_hybrid"]
    any_row = [r for r in rows if str(r.get("scenario_id") or "") == scenario_id and r.get("real_hybrid")]
    if any_row:
        return any_row[0]["real_hybrid"]
    return None


def compare_abcd(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = compare_abc(rows)
    gains = [str(r.get("fable_gain") or "NEUTRAL") for r in rows if r.get("fable_derived")]
    safety = []
    for r in rows:
        block = r.get("fable_derived") or {}
        safety.append(float((block.get("metrics") or {}).get("safety") or 1.0))
    safety_ok = all(s >= 1.0 for s in safety) if safety else True
    by_family: Dict[str, List[str]] = {}
    for row in rows:
        if row.get("fable_derived"):
            by_family.setdefault(str(row.get("family") or "unknown"), []).append(str(row.get("fable_gain") or "NEUTRAL"))
    family_gain = {fam: (max(set(vals), key=vals.count) if vals else "NEUTRAL") for fam, vals in by_family.items()}
    tokens_c = sum(int(((r.get("real_hybrid") or {}).get("tokens") or 0)) for r in rows)
    tokens_d = sum(int(((r.get("fable_derived") or {}).get("tokens") or 0)) for r in rows)
    tools_c = sum(int(((r.get("real_hybrid") or {}).get("tool_calls") or 0)) for r in rows)
    tools_d = sum(int(((r.get("fable_derived") or {}).get("tool_calls") or 0)) for r in rows)
    base["fable_gain"] = {
        "POSITIVE": gains.count("POSITIVE"),
        "NEUTRAL": gains.count("NEUTRAL"),
        "NEGATIVE": gains.count("NEGATIVE"),
        "by_family": family_gain,
    }
    base["fable_derived_value"] = classify_fable_value(gains, safety_ok)
    base["cost_honesty"] = {
        "real_hybrid_tokens": tokens_c,
        "fable_tokens": tokens_d,
        "real_hybrid_tools": tools_c,
        "fable_tools": tools_d,
        "token_ratio": round(tokens_d / max(1, tokens_c), 3) if tokens_c else None,
        "won_by_cost_only": False,
    }
    base["fable_present"] = any(r.get("fable_derived") for r in rows)
    base["c_reused"] = any(r.get("c_reused") for r in rows)
    return base
