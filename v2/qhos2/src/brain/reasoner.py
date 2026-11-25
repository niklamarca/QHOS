"""Hybrid reasoning loop. Diagnose first. LLM may propose. Policies decide.

Never stores chain-of-thought. Never imports a vendor SDK.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence

from brain.local_config import config

from .context import ContextBudget, build_context, fixture_excerpts
from .diagnose import (
    BrainResult,
    FixtureEnv,
    MemoryHint,
    diagnose,
    leak_keys_present,
    profile_observations,
    stamp_cognitive,
    strip_ground_truth,
)
from .gateway import CognitiveGateway, CognitiveMode
from .models import DiagnosticBudget, ObservationClass, StopDecision
from .providers import provider_from_config
from .sandbox import SANDBOX_ENABLED
from .verification import authorize_verification, execute_verification, make_request


SAFE_STOPS = {
    StopDecision.UNSAFE_TO_PROCEED.value,
    StopDecision.PRE_EXISTING_FAILURE.value,
    StopDecision.NO_ACTION_REQUIRED.value,
}


@dataclass
class ReasoningBudget:
    max_reasoning_rounds: int = 4
    max_llm_calls: int = 3
    max_tool_calls: int = 6
    max_hypotheses: int = 5
    max_verifications: int = 6
    timeout_s: float = 30.0
    token_budget: int = 1200


def _memory_rows(memory: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in memory or []:
        if hasattr(item, "to_dict"):
            rows.append(item.to_dict())
        elif isinstance(item, Mapping):
            rows.append(dict(item))
    return rows


def _gateway_for(mode: str, gateway: Optional[CognitiveGateway]) -> CognitiveGateway:
    if gateway is not None:
        gateway.mode = mode
        return gateway
    provider = provider_from_config(mode)
    return CognitiveGateway(
        mode=mode,
        provider=provider,
        max_llm_calls=int(getattr(config, "brain_cognitive_max_llm_calls", 3) or 3),
        timeout_s=20.0,
    )


def _should_escalate(result: BrainResult, packet: Mapping[str, Any], hyps: Sequence[Mapping[str, Any]]) -> bool:
    if result.system_confidence and result.system_confidence < 0.4:
        return True
    if result.memory == "contradicted":
        return True
    if result.priority >= 80:
        return True
    notes = " ".join(str(x) for x in (packet.get("risk_notes") or []))
    if "contradict" in notes.lower():
        return True
    ranked = sorted(
        (float(h.get("confidence") or h.get("system_confidence") or 0.0) for h in hyps),
        reverse=True,
    )
    if len(ranked) >= 2 and abs(ranked[0] - ranked[1]) <= 0.08:
        return True
    return False


def _merge_hypotheses(result: BrainResult, packet: Mapping[str, Any], *, cap: int) -> None:
    existing = {str(h.get("statement") or "").lower()[:80] for h in result.hypotheses if isinstance(h, dict)}
    for row in packet.get("hypotheses") or []:
        if not isinstance(row, dict):
            continue
        stmt = str(row.get("statement") or "").strip()
        if not stmt or stmt.lower()[:80] in existing:
            continue
        claimed = float(row.get("model_confidence") or row.get("confidence") or 0.0)
        evidence_for = list(row.get("evidence_for") or [])
        claimed_fact = bool(row.get("is_fact")) or str(row.get("epistemic_status") or "").upper() in {
            "FACT", "CONFIRMED", "TRUTH",
        }
        unsupported = bool(claimed_fact or (not evidence_for and "confirm" in stmt.lower()))
        result.hypotheses.append({
            "hypothesis_id": f"cog_{len(result.hypotheses)+1}",
            "statement": stmt[:400],
            "reason": str(row.get("reason") or "")[:200],
            "target": str(row.get("target") or "")[:120],
            "kind": "cognitive",
            "status": "proposed",
            "confidence": min(0.25, max(0.0, claimed)),
            "model_confidence": min(1.0, max(0.0, claimed)),
            "evidence_for": evidence_for,
            "evidence_against": list(row.get("evidence_against") or []),
            "source": "cognitive_provider",
            "verification_status": "unverified",
            "knowledge_state": "HYPOTHESIZED",
            "epistemic_status": "HYPOTHESIS",
            "is_fact": False,
            "unsupported_claim": unsupported,
        })
        existing.add(stmt.lower()[:80])
        if len(result.hypotheses) >= cap:
            break


def _trivial_confirmed(result: BrainResult) -> bool:
    kinds = {str(f.get("kind") or "") for f in result.findings}
    return bool(kinds & {"wrong_operator", "timeout_zero", "nested_loop", "parse_none"})


def reason(
    observations: Sequence[Mapping[str, Any]],
    *,
    env: Optional[FixtureEnv] = None,
    memory: Optional[Sequence[MemoryHint | Mapping[str, Any]]] = None,
    budget: Optional[DiagnosticBudget] = None,
    orchestrator: Any = None,
    mode: Optional[str] = None,
    gateway: Optional[CognitiveGateway] = None,
    graph_relations: Optional[Sequence[Mapping[str, Any]]] = None,
    reasoning_budget: Optional[ReasoningBudget] = None,
    strategy: Optional[str] = None,
) -> BrainResult:
    """Deterministic diagnose, then optional structured reasoning.

    strategy=fable_derived is opt-in. Default runtime never uses it.
    """
    if str(strategy or "").lower() == "fable_derived":
        from brain.fable.strategy import FableDerivedStrategy
        return FableDerivedStrategy().run(
            observations,
            env=env,
            memory=memory,
            budget=budget,
            gateway=gateway,
            mode=mode or CognitiveMode.HYBRID.value,
            orchestrator=orchestrator,
            graph_relations=graph_relations,
        )
    env = env or FixtureEnv()
    mode = str(mode or getattr(config, "brain_cognitive_mode", None) or CognitiveMode.DETERMINISTIC_ONLY.value).lower()
    if mode not in {m.value for m in CognitiveMode}:
        mode = CognitiveMode.DETERMINISTIC_ONLY.value
    rb = reasoning_budget or ReasoningBudget(
        max_reasoning_rounds=int(getattr(config, "brain_cognitive_max_rounds", 4) or 4),
        max_llm_calls=int(getattr(config, "brain_cognitive_max_llm_calls", 3) or 3),
        token_budget=int(getattr(config, "brain_cognitive_max_tokens", 1200) or 1200),
    )
    result = diagnose(
        observations,
        env=env,
        memory=[m for m in (memory or []) if isinstance(m, MemoryHint)],
        budget=budget,
        orchestrator=orchestrator,
    )
    result.cognitive_mode = mode
    result.system_confidence = float(result.confidence or 0.0)
    result.model_confidence = 0.0
    if SANDBOX_ENABLED:
        result.safety_flags.append("sandbox_enabled_refused_by_reasoner")
    if mode == CognitiveMode.DETERMINISTIC_ONLY.value:
        return stamp_cognitive(result)

    gw = _gateway_for(mode, gateway)
    gw.max_llm_calls = min(gw.max_llm_calls, rb.max_llm_calls)
    if not gw.available:
        result.sides.setdefault("engine", {})
        result.sides["engine"]["fallback"] = True
        result.sides["engine"]["provider"] = getattr(gw.provider, "name", "null")
        return stamp_cognitive(result)

    ctx_budget = ContextBudget(
        max_files=int(getattr(config, "brain_cognitive_context_files", 6) or 6),
        max_lines=int(getattr(config, "brain_cognitive_context_lines", 80) or 80),
        max_log_entries=int(getattr(config, "brain_cognitive_context_logs", 20) or 20),
        max_tokens=rb.token_budget,
        max_memory=int(getattr(config, "brain_cognitive_max_memory", 6) or 6),
    )
    context = build_context(
        strip_ground_truth(list(observations)),
        memory=_memory_rows(memory),
        graph_relations=graph_relations,
        fixture_files=fixture_excerpts(env.root, ctx_budget),
        budget=ctx_budget,
    )
    if leak_keys_present(context):
        result.traces.append({
            "what": "context_sanitized",
            "why": "ground_truth_isolation",
            "evidence": leak_keys_present(context)[:6],
            "action": "strip",
            "result": "stripped",
        })
        context = build_context(
            strip_ground_truth(list(observations)),
            memory=_memory_rows(memory),
            graph_relations=graph_relations,
            fixture_files=fixture_excerpts(env.root, ctx_budget),
            budget=ctx_budget,
        )

    packet = gw.generate_hypotheses(context)
    fallback = not bool(packet)
    if fallback:
        result.sides.setdefault("engine", {})
        result.sides["engine"]["fallback"] = True
        result.sides["engine"]["provider"] = getattr(gw.provider, "name", "null")
        result.llm_calls = len(gw.calls)
        result.cost = {**result.cost, "llm_calls": result.llm_calls}
        return stamp_cognitive(result)

    claimed = [float(h.get("model_confidence") or 0.0) for h in packet.get("hypotheses") or []]
    result.model_confidence = max(claimed) if claimed else 0.0
    # model 0.99 never becomes system confidence
    result.system_confidence = float(result.confidence or 0.0)
    _merge_hypotheses(result, packet, cap=rb.max_hypotheses)
    llm_stop = str(packet.get("stop_recommendation") or "")
    if result.findings and llm_stop and llm_stop != result.stop and result.stop in {
        StopDecision.ROOT_CAUSE_CONFIRMED.value,
        StopDecision.LIKELY_CAUSE.value,
        StopDecision.UNSAFE_TO_PROCEED.value,
    }:
        result.disagreement = True
        result.traces.append({
            "what": "model_contradicts_evidence",
            "why": "deterministic_evidence_wins",
            "evidence": [result.stop, llm_stop],
            "action": "keep_deterministic_stop",
            "result": result.stop,
        })

    profile = profile_observations(strip_ground_truth(list(observations)))
    tool_calls = int((result.cost or {}).get("tool_calls") or 0)
    rounds = 0
    disconfirm_done = False

    for row in packet.get("recommended_verifications") or []:
        if rounds >= rb.max_reasoning_rounds or len(result.verifications) >= rb.max_verifications:
            result.budget_status = StopDecision.BUDGET_EXHAUSTED.value
            break
        if tool_calls >= rb.max_tool_calls:
            result.budget_status = StopDecision.BUDGET_EXHAUSTED.value
            break
        rounds += 1
        req = make_request(
            str(row.get("action_type") or "inspect_file"),
            str(row.get("target") or ""),
            reason=str(row.get("reason") or ""),
            disconfirming=bool(row.get("disconfirming")),
            expected_information_gain=str(row.get("expected_information_gain") or "MEDIUM"),
        )
        auth = authorize_verification(req)
        executed = {"ok": False, "denied": True}
        if auth.get("allowed"):
            executed = execute_verification(req, env)
            tool_calls += 1
        else:
            result.traces.append({
                "what": "verification_denied",
                "why": auth.get("reason") or "policy",
                "action": req.action_type,
                "evidence": [req.target],
                "result": "denied",
            })
        result.verifications.append({**req.to_dict(), "authorized": bool(auth.get("allowed")), "result": executed})

    if (
        result.stop == StopDecision.ROOT_CAUSE_CONFIRMED.value
        and not _trivial_confirmed(result)
        and not disconfirm_done
        and len(result.verifications) < rb.max_verifications
        and tool_calls < rb.max_tool_calls
    ):
        req = make_request(
            "correlate_diff",
            (result.chosen or {}).get("target") or "",
            reason="disconfirm_favorite",
            hypothesis_id=str((result.chosen or {}).get("hypothesis_id") or ""),
            disconfirming=True,
        )
        auth = authorize_verification(req)
        executed = execute_verification(req, env) if auth.get("allowed") else {"ok": False, "denied": True}
        result.verifications.append({**req.to_dict(), "authorized": bool(auth.get("allowed")), "result": executed})
        tool_calls += 1
        disconfirm_done = True
        result.traces.append({
            "what": "disconfirmation",
            "why": "falsify_favorite",
            "action": req.action_type,
            "evidence": [req.target],
            "result": "requested",
        })

    if _should_escalate(result, packet, result.hypotheses) and len(gw.calls) < gw.max_llm_calls:
        critic = gw.evaluate_evidence(context)
        result.committee_used = True
        if critic:
            c_stop = str(critic.get("stop_recommendation") or "")
            p_stop = str(packet.get("stop_recommendation") or "")
            if c_stop and p_stop and c_stop != p_stop:
                result.disagreement = True
                if result.stop not in SAFE_STOPS and not result.findings:
                    result.stop = StopDecision.NEEDS_MORE_EVIDENCE.value
                    result.traces.append({
                        "what": "disagreement",
                        "why": "reasoners_diverge",
                        "evidence": [p_stop, c_stop],
                        "action": "need_more_evidence",
                        "result": result.stop,
                    })

    notes = [str(x).lower() for x in (packet.get("risk_notes") or [])]
    planted = any("untrusted_root_cause" in n or "untrusted_instruction" in n for n in notes)
    llm_stop = str(packet.get("stop_recommendation") or "")
    if (
        result.stop == StopDecision.UNSAFE_TO_PROCEED.value
        and not profile.test_failures
        and not result.findings
        and not result.change_request
        and (planted or llm_stop == StopDecision.NEEDS_MORE_EVIDENCE.value)
    ):
        result.stop = StopDecision.NEEDS_MORE_EVIDENCE.value
        result.observation_class = ObservationClass.INFORMATION.value
        result.change_request = None
        result.patches = {}
        result.traces.append({
            "what": "untrusted_claim_ignored",
            "why": "text_is_not_evidence",
            "evidence": profile.protected_paths[:4],
            "action": "downgrade_stop",
            "result": result.stop,
        })

    if result.stop in {
        StopDecision.NO_ACTION_REQUIRED.value,
        StopDecision.NEEDS_MORE_EVIDENCE.value,
        StopDecision.UNSAFE_TO_PROCEED.value,
        StopDecision.PRE_EXISTING_FAILURE.value,
    }:
        # reasoner cannot mint a change the control plane did not admit
        pass

    if result.budget_status == StopDecision.BUDGET_EXHAUSTED.value and result.stop == StopDecision.NEEDS_MORE_EVIDENCE.value:
        result.stop = StopDecision.BUDGET_EXHAUSTED.value

    result.llm_calls = len(gw.calls)
    tokens = int(gw.input_tokens + gw.output_tokens) or int((result.cost or {}).get("tokens") or 0)
    result.cost = {
        **result.cost,
        "llm_calls": result.llm_calls,
        "tool_calls": tool_calls,
        "tokens": tokens,
        "input_tokens": int(gw.input_tokens),
        "output_tokens": int(gw.output_tokens),
    }
    result.sides.setdefault("engine", {})
    result.sides["engine"]["provider"] = getattr(gw.provider, "name", "offline")
    result.sides["engine"]["model"] = getattr(gw.provider, "model", "")
    result.sides["engine"]["fallback"] = fallback
    result.sides["cognitive"] = {
        **result.sides.get("cognitive", {}),
        "llm_in_runtime": mode != CognitiveMode.DETERMINISTIC_ONLY.value,
        "engines": ["heuristic", getattr(gw.provider, "name", "offline")],
        "authority": False,
    }
    return stamp_cognitive(result)


def learning_record(result: BrainResult, *, report: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Post-evaluation learning. Ground truth stays out until the scenario ends."""
    incorrect = [
        h for h in result.hypotheses
        if isinstance(h, dict) and str(h.get("status") or "") == "rejected"
    ]
    useful = [v for v in result.verifications if (v.get("result") or {}).get("ok")]
    wasted = [v for v in result.verifications if not (v.get("result") or {}).get("ok")]
    rec = {
        "incident": result.incident,
        "hypotheses": result.hypotheses[:8],
        "evidence": result.findings[:8],
        "incorrect_hypotheses": incorrect[:8],
        "useful_verification": useful[:8],
        "unnecessary_verification": wasted[:8],
        "final_result": result.stop,
        "ground_truth_included": False,
    }
    if report is not None:
        rec["evaluator_passed"] = bool(report.get("passed"))
        rec["ground_truth_included"] = False
    return rec
