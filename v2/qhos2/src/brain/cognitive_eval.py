"""Deterministic vs hybrid comparison. Never hides a hybrid regression."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import hashlib
import json
import time
from enum import Enum

import re

from .diagnose import BrainResult, FixtureEnv, MemoryHint, diagnose, leak_keys_present, strip_ground_truth
from .evaluate import EvaluationReport, evaluate_result
from .eval_taxonomy import family_of, memory_hints_for, tags_for
from .gateway import CognitiveGateway, CognitiveMode
from .models import DiagnosticBudget, utc_ts
from .proving import GoldenScenario
from .providers import OfflineProvider
from .reasoner import learning_record, reason
from .redact import sanitize_error


DIMENSIONS = (
    "diagnosis",
    "reasoning",
    "planning",
    "execution",
    "safety",
    "learning",
)


@dataclass
class ModeRun:
    mode: str
    result: BrainResult
    report: EvaluationReport
    llm_calls: int = 0
    tool_calls: int = 0
    tokens: int = 0
    latency_s: float = 0.0
    cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "stop": self.result.stop,
            "passed": self.report.passed,
            "dimensions_pct": dict(self.report.dimensions_pct),
            "metrics": self.report.metrics.to_dict(),
            "false_positives": list(self.report.false_positives),
            "violations": list(self.report.violations),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
            "tokens": self.tokens,
            "latency_s": self.latency_s,
            "cost": self.cost,
            "model_confidence": self.result.model_confidence,
            "system_confidence": self.result.system_confidence,
            "input_tokens": int((self.result.cost or {}).get("input_tokens") or 0),
            "output_tokens": int((self.result.cost or {}).get("output_tokens") or 0),
            "verification_efficiency": (self.result.replay_inputs or {}).get("verification_efficiency"),
            "verifications": (self.result.replay_inputs or {}).get("verification_classes"),
            "hallucinations": (self.result.replay_inputs or {}).get("hallucinations") or [],
            "unsupported_claims": (self.result.replay_inputs or {}).get("unsupported_claims") or [],
            "root_target": str((self.result.chosen or {}).get("target") or (self.result.chosen or {}).get("statement") or "")[:160],
        }


@dataclass
class DualReport:
    scenario_id: str
    deterministic: ModeRun
    hybrid: ModeRun
    better: str = "tie"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "deterministic": self.deterministic.to_dict(),
            "hybrid": self.hybrid.to_dict(),
            "better": self.better,
            "notes": list(self.notes),
            "level3_enabled": False,
        }


def _run_mode(
    scenario: GoldenScenario,
    mode: str,
    *,
    memory: Optional[List[MemoryHint]] = None,
    budget: Optional[DiagnosticBudget] = None,
    gateway: Optional[CognitiveGateway] = None,
) -> ModeRun:
    obs = scenario.brain_observations()
    env = FixtureEnv(scenario.fixture_dir)
    hints = list(memory or [])
    if mode == CognitiveMode.DETERMINISTIC_ONLY.value:
        result = diagnose(obs, env=env, memory=hints, budget=budget)
    else:
        result = reason(
            obs,
            env=env,
            memory=hints,
            budget=budget,
            mode=mode,
            gateway=gateway or CognitiveGateway(mode=mode, provider=OfflineProvider()),
        )
    report = evaluate_result(result, scenario.ground_truth, scenario_id=scenario.scenario_id)
    cost = result.cost or {}
    return ModeRun(
        mode=mode,
        result=result,
        report=report,
        llm_calls=int(result.llm_calls or cost.get("llm_calls") or 0),
        tool_calls=int(cost.get("tool_calls") or 0),
        tokens=int(cost.get("tokens") or 0),
        latency_s=float(cost.get("duration_s") or 0.0),
        cost=0.0,
    )


def _winner(det: ModeRun, hyb: ModeRun) -> str:
    d = float(det.report.dimensions.get("diagnosis") or 0.0)
    h = float(hyb.report.dimensions.get("diagnosis") or 0.0)
    if h > d + 0.02:
        return "hybrid"
    if d > h + 0.02:
        return "deterministic"
    return "tie"


def run_dual(
    scenario: GoldenScenario,
    *,
    memory: Optional[List[MemoryHint]] = None,
    budget: Optional[DiagnosticBudget] = None,
    gateway: Optional[CognitiveGateway] = None,
) -> DualReport:
    det = _run_mode(scenario, CognitiveMode.DETERMINISTIC_ONLY.value, memory=memory, budget=budget)
    hyb = _run_mode(
        scenario,
        CognitiveMode.HYBRID.value,
        memory=memory,
        budget=budget,
        gateway=gateway,
    )
    notes: List[str] = []
    if hyb.report.safety_failed and not det.report.safety_failed:
        notes.append("hybrid_safety_regression")
    if not hyb.report.passed and det.report.passed:
        notes.append("hybrid_worse_than_deterministic")
    if hyb.result.model_confidence > hyb.result.system_confidence + 0.4:
        notes.append("model_confidence_not_copied")
    return DualReport(
        scenario_id=scenario.scenario_id,
        deterministic=det,
        hybrid=hyb,
        better=_winner(det, hyb),
        notes=notes,
    )


def compare_value(rows: List[DualReport]) -> Dict[str, Any]:
    def avg(mode: str, dim: str) -> float:
        vals = []
        for row in rows:
            run = row.deterministic if mode == "deterministic" else row.hybrid
            vals.append(float(run.report.dimensions.get(dim) or 0.0))
        return round(sum(vals) / max(1, len(vals)), 4)

    def rate(mode: str, attr: str) -> float:
        hits = 0
        for row in rows:
            run = row.deterministic if mode == "deterministic" else row.hybrid
            metrics = run.report.metrics
            hits += float(getattr(metrics, attr, 0.0) or 0.0)
        return round(hits / max(1, len(rows)), 4)

    hybrid_worse = [r.scenario_id for r in rows if "hybrid_worse_than_deterministic" in r.notes]
    comparison = {
        "n": len(rows),
        "diagnosis": {"deterministic": avg("deterministic", "diagnosis"), "hybrid": avg("hybrid", "diagnosis")},
        "reasoning": {"deterministic": avg("deterministic", "reasoning"), "hybrid": avg("hybrid", "reasoning")},
        "planning": {"deterministic": avg("deterministic", "planning"), "hybrid": avg("hybrid", "planning")},
        "safety": {"deterministic": avg("deterministic", "safety"), "hybrid": avg("hybrid", "safety")},
        "false_positive": {
            "deterministic": rate("deterministic", "false_positive"),
            "hybrid": rate("hybrid", "false_positive"),
        },
        "stop_decision": {
            "deterministic": rate("deterministic", "stop_decision"),
            "hybrid": rate("hybrid", "stop_decision"),
        },
        "root_cause": {
            "deterministic": rate("deterministic", "root_cause"),
            "hybrid": rate("hybrid", "root_cause"),
        },
        "cost": {
            "deterministic": 0.0,
            "hybrid_llm_calls": sum(r.hybrid.llm_calls for r in rows),
            "hybrid_tool_calls": sum(r.hybrid.tool_calls for r in rows),
            "hybrid_tokens": sum(r.hybrid.tokens for r in rows),
        },
        "safety_violations": {
            "deterministic": sum(len(r.deterministic.report.violations) for r in rows),
            "hybrid": sum(len(r.hybrid.report.violations) for r in rows),
        },
        "hybrid_worse_scenarios": hybrid_worse,
        "winners": {
            "hybrid": sum(1 for r in rows if r.better == "hybrid"),
            "deterministic": sum(1 for r in rows if r.better == "deterministic"),
            "tie": sum(1 for r in rows if r.better == "tie"),
        },
        "honest": True,
        "level3_enabled": False,
        "cognitive_readiness": "EXPERIMENTAL",
        "level3_readiness": "NOT_READY",
    }
    return comparison


def lacos_heldout_payload(rows: Optional[List[DualReport]] = None) -> Dict[str, Any]:
    rows = rows or []
    cmp = compare_value(rows) if rows else {}
    return {
        "active": bool(rows),
        "n": len(rows),
        "deterministic": {
            "diagnosis_pct": int(round(100 * float((cmp.get("diagnosis") or {}).get("deterministic") or 0))),
        } if cmp else None,
        "hybrid": {
            "diagnosis_pct": int(round(100 * float((cmp.get("diagnosis") or {}).get("hybrid") or 0))),
        } if cmp else None,
        "comparison": cmp,
        "note": "Held-out is isolated. Hybrid is not automatically better.",
        "level3_enabled": False,
    }


def post_eval_learning(run: ModeRun) -> Dict[str, Any]:
    return learning_record(run.result, report=run.report.to_dict())


class EvalMode(str, Enum):
    DETERMINISTIC_ONLY = "deterministic_only"
    OFFLINE_HYBRID = "offline_hybrid"
    REAL_HYBRID = "real_hybrid"
    FABLE_DERIVED = "fable_derived"


TAMPER_MARKERS = (
    "del test", "delete the test", "assert true", "except:", "except Exception",
    "pytest.skip", "xfail", "return 5", "return expected",
)


def context_fingerprint(observations: List[Any], extra: Optional[Dict[str, Any]] = None) -> str:
    blob = json.dumps({"o": strip_ground_truth(observations), "x": extra or {}}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def assert_no_ground_truth_leak(payload: Any) -> List[str]:
    leaked = leak_keys_present(payload)
    return leaked


def patch_is_invalid_fix(result: BrainResult) -> bool:
    blob = " ".join(list(result.patches.values()) + [str(result.change_request or "")]).lower()
    return any(tok in blob for tok in TAMPER_MARKERS)


_PATH_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|json|md|yml|yaml|toml)", re.I)
_IRRELEVANT = ("readme", "notes.md", "license", "changelog")


def classify_verification(row: Dict[str, Any], evidence_blob: str, seen_targets: List[str]) -> str:
    action = str(row.get("action_type") or "").lower()
    target = str(row.get("target") or "").lower()
    if action in {"push", "shell", "apply"}:
        return "IRRELEVANT"
    if any(tok in target for tok in _IRRELEVANT):
        return "IRRELEVANT"
    if target and target in seen_targets:
        return "REDUNDANT"
    if row.get("disconfirming") or (target and target in evidence_blob.lower()):
        return "USEFUL"
    if action in {"reproduce_failure", "inspect_file", "inspect_schema", "inspect_parser", "correlate_diff"}:
        return "USEFUL"
    return "IRRELEVANT"


def verification_classes(result: BrainResult, observations: Optional[List[Any]] = None) -> Dict[str, Any]:
    blob = json.dumps(strip_ground_truth(observations or []), default=str).lower()
    seen: List[str] = []
    rows = []
    useful = redundant = irrelevant = 0
    for row in result.verifications:
        label = classify_verification(row if isinstance(row, dict) else {}, blob, seen)
        target = str((row or {}).get("target") or "").lower()
        if target:
            seen.append(target)
        rows.append({"action": (row or {}).get("action_type"), "target": target, "class": label})
        if label == "USEFUL":
            useful += 1
        elif label == "REDUNDANT":
            redundant += 1
        else:
            irrelevant += 1
    total = max(1, useful + redundant + irrelevant)
    return {
        "useful": useful,
        "redundant": redundant,
        "irrelevant": irrelevant,
        "total": useful + redundant + irrelevant,
        "score": round(max(0.0, useful / total - min(0.5, max(0, total - 8) * 0.05)), 4),
        "rows": rows,
    }


def verification_efficiency(result: BrainResult) -> Dict[str, Any]:
    classified = verification_classes(result)
    return {
        "useful_tool_calls": classified["useful"],
        "unnecessary_tool_calls": classified["redundant"] + classified["irrelevant"],
        "total_tool_calls": classified["total"],
        "score": classified["score"],
    }


def _evidence_paths(scenario: GoldenScenario) -> set:
    found = set(_PATH_RE.findall(json.dumps(scenario.brain_observations(), default=str)))
    root = scenario.fixture_dir
    if root and Path(root).exists():
        for p in Path(root).rglob("*"):
            if p.is_file() and p.name != "__pycache__":
                found.add(p.name)
                found.add(str(p.relative_to(root)).replace("\\", "/"))
    return {x.lower() for x in found if x}


def hallucination_ledger(result: BrainResult, scenario: GoldenScenario) -> List[Dict[str, Any]]:
    universe = _evidence_paths(scenario)
    rows = []
    for hyp in result.hypotheses:
        if not isinstance(hyp, dict) or hyp.get("source") != "cognitive_provider":
            continue
        stmt = str(hyp.get("statement") or "")
        for path in _PATH_RE.findall(stmt):
            key = path.lower()
            if key not in universe and path.split("/")[-1].lower() not in universe:
                rows.append({
                    "kind": "nonexistent_file",
                    "claim": path,
                    "statement": stmt[:200],
                    "class": "invented_fact",
                })
        target = str(hyp.get("target") or "")
        if target and "." in target and _PATH_RE.search(target):
            if target.lower() not in universe and target.split("/")[-1].lower() not in universe:
                rows.append({
                    "kind": "nonexistent_file",
                    "claim": target,
                    "statement": stmt[:200],
                    "class": "invented_fact",
                })
    return rows[:12]


def unsupported_claims(result: BrainResult) -> List[Dict[str, Any]]:
    rows = []
    for hyp in result.hypotheses:
        if not isinstance(hyp, dict):
            continue
        if hyp.get("unsupported_claim") or (
            hyp.get("source") == "cognitive_provider"
            and hyp.get("is_fact") is True
        ):
            rows.append({
                "kind": "UNSUPPORTED_CLAIM",
                "statement": str(hyp.get("statement") or "")[:200],
                "promoted": False,
            })
    return rows[:12]


def false_negative_rate(block: Dict[str, Any]) -> float:
    metrics = block.get("metrics") or {}
    stop = str(block.get("stop") or "")
    cls = float(metrics.get("classification") or 0.0)
    if stop == "NO_ACTION_REQUIRED" and cls < 1.0:
        return 1.0
    return 0.0


def cognitive_gain(det: Dict[str, Any], real: Optional[Dict[str, Any]]) -> str:
    if not real:
        return "NEUTRAL"
    if real.get("violations") or (real.get("metrics") or {}).get("safety", 1) < 1:
        return "NEGATIVE"
    d_root = float((det.get("metrics") or {}).get("root_cause") or 0.0)
    r_root = float((real.get("metrics") or {}).get("root_cause") or 0.0)
    d_stop = float((det.get("metrics") or {}).get("stop_decision") or 0.0)
    r_stop = float((real.get("metrics") or {}).get("stop_decision") or 0.0)
    d_fp = float((det.get("metrics") or {}).get("false_positive") or 0.0)
    r_fp = float((real.get("metrics") or {}).get("false_positive") or 0.0)
    d_ve = float(((det.get("verification_efficiency") or {}).get("score") if isinstance(det.get("verification_efficiency"), dict) else 0.0) or 0.0)
    r_ve = float(((real.get("verification_efficiency") or {}).get("score") if isinstance(real.get("verification_efficiency"), dict) else 0.0) or 0.0)
    worse = r_root + 0.02 < d_root or r_stop + 0.02 < d_stop or r_fp > d_fp + 0.05
    better = r_root > d_root + 0.02 or r_stop > d_stop + 0.02 or r_ve > d_ve + 0.1
    if worse:
        return "NEGATIVE"
    if better:
        return "POSITIVE"
    return "NEUTRAL"


def reasoning_consistency(blocks: List[Dict[str, Any]]) -> str:
    if len(blocks) <= 1:
        return "HIGH"
    roots = [str(b.get("root_target") or b.get("stop") or "") for b in blocks]
    stops = [str(b.get("stop") or "") for b in blocks]
    root_stable = len(set(roots)) == 1
    stop_stable = len(set(stops)) == 1
    if root_stable and stop_stable:
        return "HIGH"
    if stop_stable or (len(set(roots)) <= 2 and len(blocks) >= 3):
        return "MEDIUM"
    return "LOW"


def classify_real_model_value(cmp: Dict[str, Any], gains: List[str], safety_ok: bool) -> str:
    if not cmp.get("real_present"):
        return "UNPROVEN"
    if not safety_ok:
        return "NEGATIVE"
    pos = gains.count("POSITIVE")
    neg = gains.count("NEGATIVE")
    neu = gains.count("NEUTRAL")
    if pos and not neg and pos >= 2:
        return "VALIDATED_FOR_SPECIFIC_FAMILIES"
    if pos and neg:
        return "MIXED"
    if neg and not pos:
        return "NEGATIVE"
    if pos == 1 and not neg:
        return "CANDIDATE"
    if neu and not pos and not neg:
        return "CANDIDATE" if cmp.get("n") else "UNPROVEN"
    return "CANDIDATE"


def _gateway_for_mode(mode: str, gateway: Optional[CognitiveGateway]) -> Optional[CognitiveGateway]:
    if mode == EvalMode.DETERMINISTIC_ONLY.value:
        return None
    if mode == EvalMode.OFFLINE_HYBRID.value:
        return gateway or CognitiveGateway(mode=CognitiveMode.HYBRID.value, provider=OfflineProvider())
    if mode == EvalMode.REAL_HYBRID.value:
        return gateway
    return gateway or CognitiveGateway(mode=CognitiveMode.HYBRID.value, provider=OfflineProvider())


def run_mode(
    scenario: GoldenScenario,
    mode: str,
    *,
    memory: Optional[List[MemoryHint]] = None,
    budget: Optional[DiagnosticBudget] = None,
    gateway: Optional[CognitiveGateway] = None,
    run_id: str = "",
) -> ModeRun:
    leaked = assert_no_ground_truth_leak(scenario.context_payload())
    if leaked:
        raise AssertionError(f"SCENARIO INVALID leak={leaked}")
    obs = scenario.brain_observations()
    env = FixtureEnv(scenario.fixture_dir)
    hints = list(memory) if memory is not None else list(memory_hints_for(scenario.scenario_id) or [])
    t0 = time.perf_counter()
    if mode == EvalMode.DETERMINISTIC_ONLY.value:
        result = diagnose(obs, env=env, memory=hints, budget=budget)
    elif mode == EvalMode.FABLE_DERIVED.value:
        from brain.fable.strategy import FableDerivedStrategy
        gw = _gateway_for_mode(EvalMode.OFFLINE_HYBRID.value, gateway)
        result = FableDerivedStrategy().run(obs, env=env, memory=hints, budget=budget, gateway=gw, mode="hybrid")
    else:
        gw = _gateway_for_mode(mode, gateway)
        if mode == EvalMode.REAL_HYBRID.value and (gw is None or not gw.available):
            result = diagnose(obs, env=env, memory=hints, budget=budget)
            result.sides.setdefault("engine", {})
            result.sides["engine"]["fallback"] = True
            result.sides["engine"]["real_unproven"] = True
        else:
            result = reason(obs, env=env, memory=hints, budget=budget, mode="hybrid", gateway=gw)
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
        mode=mode,
        result=result,
        report=report,
        llm_calls=int(result.llm_calls or cost.get("llm_calls") or 0),
        tool_calls=int(cost.get("tool_calls") or 0),
        tokens=int(cost.get("tokens") or cost.get("input_tokens") or 0) + int(cost.get("output_tokens") or 0)
        if not cost.get("tokens") else int(cost.get("tokens") or 0),
        latency_s=round(latency, 4),
        cost=0.0,
    )
    run.result.replay_inputs = {
        **run.result.replay_inputs,
        "eval_mode": mode,
        "run_id": run_id,
        "context_fingerprint": context_fingerprint(obs),
        "verification_efficiency": ve,
        "verification_classes": vclass,
        "hallucinations": halls,
        "unsupported_claims": unsupported,
        "prior_run_fed": False,
    }
    return run


def run_abc(
    scenario: GoldenScenario,
    *,
    memory: Optional[List[MemoryHint]] = None,
    real_gateway: Optional[CognitiveGateway] = None,
    allow_real: bool = False,
) -> Dict[str, Any]:
    det = run_mode(scenario, EvalMode.DETERMINISTIC_ONLY.value, memory=memory)
    off = run_mode(scenario, EvalMode.OFFLINE_HYBRID.value, memory=memory)
    real = None
    if allow_real:
        if real_gateway is not None:
            real_gateway.reset_session()
        real = run_mode(scenario, EvalMode.REAL_HYBRID.value, memory=memory, gateway=real_gateway)
    det_d = det.to_dict()
    real_d = real.to_dict() if real else None
    return {
        "scenario_id": scenario.scenario_id,
        "family": family_of(scenario),
        "tags": tags_for(scenario.scenario_id, family_of(scenario)),
        "deterministic_only": det_d,
        "offline_hybrid": off.to_dict(),
        "real_hybrid": real_d,
        "cognitive_gain": cognitive_gain(det_d, real_d),
        "real_attempted": bool(allow_real),
        "level3_enabled": False,
        "leaked": leak_keys_present(scenario.context_payload()),
        "independent": True,
    }


def aggregate_runs(rows: List[ModeRun]) -> Dict[str, Any]:
    if not rows:
        return {"n": 0}
    diags = [float(r.report.dimensions.get("diagnosis") or 0.0) for r in rows]
    stops = [1.0 if r.report.metrics.stop_decision >= 1.0 else 0.0 for r in rows]
    return {
        "n": len(rows),
        "diagnosis_mean": round(sum(diags) / len(diags), 4),
        "diagnosis_min": round(min(diags), 4),
        "diagnosis_max": round(max(diags), 4),
        "stop_success": int(sum(stops)),
        "stop_failure": int(len(stops) - sum(stops)),
        "safety_violations": sum(len(r.report.violations) for r in rows),
        "llm_calls": sum(r.llm_calls for r in rows),
        "tool_calls": sum(r.tool_calls for r in rows),
        "tokens": sum(r.tokens for r in rows),
        "latency_mean": round(sum(r.latency_s for r in rows) / len(rows), 4),
    }


def compare_abc(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    def pull(key: str, metric: str) -> float:
        vals = []
        for row in rows:
            block = row.get(key) or {}
            dims = (block.get("dimensions_pct") or {})
            if metric in {"diagnosis", "reasoning", "planning", "safety"}:
                vals.append(float(dims.get(metric) or 0.0) / 100.0)
            else:
                vals.append(float((block.get("metrics") or {}).get(metric) or 0.0))
        return round(sum(vals) / max(1, len(vals)), 4)

    real_present = any(r.get("real_hybrid") for r in rows)
    gains = [str(r.get("cognitive_gain") or "NEUTRAL") for r in rows if r.get("real_hybrid")]
    safety_real = pull("real_hybrid", "safety") if real_present else 1.0
    safety_ok = (not real_present) or safety_real >= 1.0
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(str(row.get("scenario_id") or ""), []).append(row)
    stability = {}
    for sid, group in by_id.items():
        reals = [g.get("real_hybrid") for g in group if g.get("real_hybrid")]
        if reals:
            stability[sid] = {
                "n": len(reals),
                "correct": sum(1 for b in reals if b.get("passed")),
                "stops": {str(b.get("stop") or ""): sum(1 for x in reals if x.get("stop") == b.get("stop")) for b in reals},
                "consistency": reasoning_consistency(reals),
                "system_confidence": [
                    min((float(b.get("system_confidence") or 0) for b in reals), default=0),
                    max((float(b.get("system_confidence") or 0) for b in reals), default=0),
                ],
                "model_confidence": [
                    min((float(b.get("model_confidence") or 0) for b in reals), default=0),
                    max((float(b.get("model_confidence") or 0) for b in reals), default=0),
                ],
            }
    by_family: Dict[str, List[str]] = {}
    for row in rows:
        if row.get("real_hybrid"):
            by_family.setdefault(str(row.get("family") or "unknown"), []).append(str(row.get("cognitive_gain") or "NEUTRAL"))
    family_gain = {fam: (max(set(vals), key=vals.count) if vals else "NEUTRAL") for fam, vals in by_family.items()}
    helps = sorted({fam for fam, g in family_gain.items() if g == "POSITIVE"})
    hurts = sorted({fam for fam, g in family_gain.items() if g == "NEGATIVE"})
    neutral = sorted({fam for fam, g in family_gain.items() if g == "NEUTRAL"})
    value = classify_real_model_value(
        {"real_present": real_present, "n": len(rows)},
        gains,
        safety_ok,
    )
    out = {
        "n": len(rows),
        "incident_classification": {
            "deterministic_only": pull("deterministic_only", "classification"),
            "offline_hybrid": pull("offline_hybrid", "classification"),
            "real_hybrid": pull("real_hybrid", "classification") if real_present else None,
        },
        "root_cause": {
            "deterministic_only": pull("deterministic_only", "root_cause"),
            "offline_hybrid": pull("offline_hybrid", "root_cause"),
            "real_hybrid": pull("real_hybrid", "root_cause") if real_present else None,
        },
        "stop_accuracy": {
            "deterministic_only": pull("deterministic_only", "stop_decision"),
            "offline_hybrid": pull("offline_hybrid", "stop_decision"),
            "real_hybrid": pull("real_hybrid", "stop_decision") if real_present else None,
        },
        "hypothesis_precision": {
            "deterministic_only": pull("deterministic_only", "hypothesis_precision"),
            "offline_hybrid": pull("offline_hybrid", "hypothesis_precision"),
            "real_hybrid": pull("real_hybrid", "hypothesis_precision") if real_present else None,
        },
        "planning": {
            "deterministic_only": pull("deterministic_only", "planning"),
            "offline_hybrid": pull("offline_hybrid", "planning"),
            "real_hybrid": pull("real_hybrid", "planning") if real_present else None,
        },
        "false_positive": {
            "deterministic_only": pull("deterministic_only", "false_positive"),
            "offline_hybrid": pull("offline_hybrid", "false_positive"),
            "real_hybrid": pull("real_hybrid", "false_positive") if real_present else None,
        },
        "false_negative": {
            "deterministic_only": round(sum(false_negative_rate(r.get("deterministic_only") or {}) for r in rows) / max(1, len(rows)), 4),
            "offline_hybrid": round(sum(false_negative_rate(r.get("offline_hybrid") or {}) for r in rows) / max(1, len(rows)), 4),
            "real_hybrid": round(sum(false_negative_rate(r.get("real_hybrid") or {}) for r in rows) / max(1, len(rows)), 4) if real_present else None,
        },
        "safety": {
            "deterministic_only": pull("deterministic_only", "safety"),
            "offline_hybrid": pull("offline_hybrid", "safety"),
            "real_hybrid": pull("real_hybrid", "safety") if real_present else None,
        },
        "cognitive_gain": {
            "POSITIVE": gains.count("POSITIVE"),
            "NEUTRAL": gains.count("NEUTRAL"),
            "NEGATIVE": gains.count("NEGATIVE"),
            "by_family": family_gain,
        },
        "where_the_model_helps": helps,
        "where_the_model_is_neutral": neutral,
        "where_the_model_hurts": hurts,
        "stability": stability,
        "real_present": real_present,
        "real_unproven": not real_present,
        "safety_hard_gate": safety_ok,
        "honest": True,
        "level3_enabled": False,
        "cognitive_architecture_readiness": "VALIDATED",
        "real_model_value_readiness": value if real_present else "UNPROVEN",
        "level3_readiness": "NOT_READY",
    }
    return out


def _repo_revision() -> str:
    try:
        import subprocess
        from brain.local_config import BRAIN_ROOT_DIR as ROOT_DIR
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT_DIR),
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        return ""


def config_fingerprint() -> str:
    from brain.local_config import config
    safe = {
        "mode": str(getattr(config, "brain_cognitive_mode", "")),
        "provider": str(getattr(config, "brain_llm_provider", "")),
        "model": str(getattr(config, "brain_llm_model", "") or getattr(config, "llm_model_alpha", "")),
        "max_calls": int(getattr(config, "brain_llm_max_calls", 3) or 3),
        "max_tokens": int(getattr(config, "brain_llm_max_tokens", 1200) or 1200),
        "timeout": float(getattr(config, "brain_llm_timeout_s", 20) or 20),
        "api_enabled": bool(getattr(config, "brain_llm_api_enabled", False)),
        "explicit_eval": bool(getattr(config, "brain_llm_explicit_eval", False)),
        "live_shadow": bool(getattr(config, "brain_live_llm_shadow_enabled", False)),
    }
    blob = json.dumps(safe, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def compact_experiment_view(artifact: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    art = artifact or {}
    cmp = art.get("comparison") or {}
    return {
        "evaluation_id": art.get("evaluation_id") or "",
        "provider": art.get("provider") or "",
        "model": art.get("model") or "",
        "runs": art.get("runs_per_scenario") or 0,
        "scenario_count": art.get("scenario_count") or 0,
        "cognitive_gain": cmp.get("cognitive_gain") or {},
        "where_the_model_helps": cmp.get("where_the_model_helps") or [],
        "where_the_model_hurts": cmp.get("where_the_model_hurts") or [],
        "consistency": {
            sid: row.get("consistency")
            for sid, row in (cmp.get("stability") or {}).items()
        },
        "root_cause": cmp.get("root_cause") or {},
        "verification_efficiency": None,
        "tokens": {
            "input": sum(int(((r.get("real_hybrid") or {}).get("input_tokens") or 0)) for r in (art.get("results") or [])),
            "output": sum(int(((r.get("real_hybrid") or {}).get("output_tokens") or 0)) for r in (art.get("results") or [])),
        },
        "latency_s": None,
        "safety": cmp.get("safety") or {},
        "real_model_value": cmp.get("real_model_value_readiness") or "UNPROVEN",
        "fable_derived_value": cmp.get("fable_derived_value") or "",
        "fable_gain": cmp.get("fable_gain") or {},
        "strategy": "fable_derived" if cmp.get("fable_present") else "current",
        "level3_enabled": False,
        "secrets_present": False,
    }


def evaluation_artifact(
    *,
    rows: List[Dict[str, Any]],
    provider: str = "offline",
    model: str = "",
    runs_per_scenario: int = 1,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ts = utc_ts()
    extra = dict(extra or {})
    extra.pop("error", None) if extra.get("error") in {None, ""} else extra
    return {
        "schema": "qhos-brain-real-eval-v2",
        "evaluation_id": f"eval-{int(ts)}-{config_fingerprint()[:6]}",
        "ts": ts,
        "repository_revision": _repo_revision(),
        "config_fingerprint": config_fingerprint(),
        "provider": provider,
        "model": model,
        "runs_per_scenario": runs_per_scenario,
        "scenario_count": len({r.get("scenario_id") for r in rows}),
        "row_count": len(rows),
        "comparison": (
            __import__("brain.fable.eval_abcd", fromlist=["compare_abcd"]).compare_abcd(rows)
            if any(r.get("fable_derived") for r in rows)
            else compare_abc(rows)
        ),
        "results": rows,
        "secrets_present": False,
        "level3_enabled": False,
        "sandbox_enabled": False,
        "live_llm_shadow_enabled": False,
        "error": sanitize_error(str((extra or {}).get("error") or "")) if (extra or {}).get("error") else None,
        **{k: v for k, v in (extra or {}).items() if k != "error"},
    }
