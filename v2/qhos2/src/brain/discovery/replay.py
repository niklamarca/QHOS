"""Replay sanitized soak streams. Deterministic first. Hybrid only behind an explicit paid gate."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from brain.invoke import should_invoke_llm
from brain.models import new_id, utc_ts
from brain.redact import redact_mapping

from .engine import DiscoveryEngine
from .review import strip_review_labels


REPLAY_STATUSES = ("CREATED", "RUNNING", "PARTIAL", "COMPLETE", "FAILED")


def replay_observations(
    observations: List[Mapping[str, Any]],
    *,
    engine: Optional[DiscoveryEngine] = None,
) -> Dict[str, Any]:
    eng = engine or DiscoveryEngine()
    raw = {"observations": [dict(o) for o in observations]}
    intel = eng.ingest(raw)
    out = intel if isinstance(intel, dict) else {}
    out["replay"] = True
    out["mode"] = "deterministic"
    out["writes"] = 0
    out["change_request"] = None
    out["sandbox"] = None
    out["llm_called"] = False
    return out


def _sanitize_for_reasoner(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Same evidence for all strategies. Strip review labels. No ground truth."""
    cleaned = strip_review_labels(row)
    cleaned.pop("ground_truth", None)
    cleaned.pop("expected", None)
    cleaned.pop("answer", None)
    return redact_mapping(cleaned)


def replay_deterministic(observations: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    out = replay_observations([dict(o) for o in observations])
    out["status"] = "COMPLETE"
    out["replay_id"] = new_id("rpl")
    out["discovery"] = {
        "noticed": bool(out.get("incidents") or out.get("improvements")),
        "candidate_n": len(out.get("incidents") or []),
        "improvement_n": len(out.get("improvements") or []),
    }
    out["reasoning"] = {
        "strategy": "deterministic",
        "hypotheses": [],
        "unsupported_claims": 0,
        "llm_calls": 0,
        "tokens": 0,
    }
    out["label_leakage"] = False
    out["same_evidence"] = True
    out["fable_included"] = False
    return out


def select_hybrid_candidates(
    incidents: Sequence[Mapping[str, Any]],
    *,
    api_enabled: bool = False,
    mode: str = "hybrid",
) -> List[Dict[str, Any]]:
    """Deterministic narrowing first. Never send every observation to a model."""
    selected = []
    for row in incidents:
        if not isinstance(row, Mapping):
            continue
        decision = should_invoke_llm(
            [{"kind": str(row.get("signal_class") or "error").lower(), "summary": row.get("summary") or "", "severity": row.get("severity")}],
            api_enabled=api_enabled,
            mode=mode,
            known_baseline=bool(row.get("pre_existing")),
            dismissed=str(row.get("disposition") or "").upper() in {"DISMISSED", "NOT_USEFUL"},
        )
        if decision.get("invoke"):
            selected.append({**dict(row), "invoke_reason": decision.get("reason")})
    return selected


def paid_replay_gate(
    *,
    explicit: bool,
    accept_cost: bool,
    candidate_ids: Sequence[str],
    max_calls: int,
    max_tokens: int,
    config_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    """Every condition that must hold before a paid replay may call a provider.

    ``config_enabled`` defaults to ``brain_paid_replay_enabled``. That flag was
    declared and documented as a safety control but read nowhere, so a reader of
    .env.example was being told about a gate that did not exist. It is now the
    outermost condition: a deployment can refuse paid replay regardless of what
    flags a script passes.
    """
    if config_enabled is None:
        try:
            from brain.local_config import config

            config_enabled = bool(getattr(config, "brain_paid_replay_enabled", False))
        except Exception:
            config_enabled = False
    if not config_enabled:
        return {"allowed": False, "reason": "paid_replay_disabled_by_config", "invoke": False}
    if not explicit:
        return {"allowed": False, "reason": "explicit_flag_required", "invoke": False}
    if not accept_cost:
        return {"allowed": False, "reason": "cost_not_accepted", "invoke": False}
    if not candidate_ids:
        return {"allowed": False, "reason": "empty_candidate_list", "invoke": False}
    if max_calls <= 0 or max_tokens <= 0:
        return {"allowed": False, "reason": "budget_zero", "invoke": False}
    return {
        "allowed": True,
        "reason": "gated",
        "invoke": True,
        "max_calls": max_calls,
        "max_tokens": max_tokens,
        "candidates": list(candidate_ids)[: max_calls],
        "requires_close": True,
        "requires_checkpoint": True,
        "timeout_required": True,
        "fable_auto": False,
    }


def replay_hybrid_shadow(
    observations: Sequence[Mapping[str, Any]],
    *,
    explicit: bool = False,
    accept_cost: bool = False,
    max_calls: int = 0,
    max_tokens: int = 0,
    provider: Any = None,
) -> Dict[str, Any]:
    """Selective Real Hybrid replay. Paid path requires gate. CI stays free."""
    det = replay_deterministic(observations)
    incidents = list(det.get("incidents") or [])
    # Hide review labels before any cognitive path.
    safe_incidents = [_sanitize_for_reasoner(r) for r in incidents]
    selected = select_hybrid_candidates(safe_incidents, api_enabled=False, mode="deterministic_only")
    gate = paid_replay_gate(
        explicit=explicit,
        accept_cost=accept_cost,
        candidate_ids=[str(r.get("candidate_id") or r.get("fingerprint") or "") for r in selected],
        max_calls=max_calls,
        max_tokens=max_tokens,
    )
    rows = []
    status = "COMPLETE"
    if gate.get("allowed") and provider is not None:
        status = "RUNNING"
        for cand in selected[: max_calls or 0]:
            ctx = {
                "observations": [_sanitize_for_reasoner(o) for o in observations[:20]],
                "candidate": _sanitize_for_reasoner(cand),
                "instructions": "read_only_investigation",
            }
            # Provider is injected by explicit paid harness only.
            try:
                packet = provider.generate_hypotheses(ctx) if hasattr(provider, "generate_hypotheses") else {}
            except Exception as exc:
                packet = {"error": type(exc).__name__}
                status = "PARTIAL"
            rows.append({
                "candidate_id": cand.get("candidate_id"),
                "packet": packet,
                "strategy": "real_hybrid",
                "checkpoint_at": utc_ts(),
            })
        status = "COMPLETE" if status != "PARTIAL" else "PARTIAL"
    elif not gate.get("allowed"):
        # Still return deterministic narrowing + gate denial. No paid call.
        rows = []
        status = "COMPLETE"
    return {
        "schema": "qhos-brain-replay-v1",
        "replay": True,
        "mode": "real_hybrid",
        "status": status,
        "deterministic": det.get("discovery"),
        "selected_candidates": [strip_review_labels(r) for r in selected],
        "gate": gate,
        "hybrid_rows": rows,
        "llm_called": bool(rows) and gate.get("allowed") is True,
        "writes": 0,
        "change_request": None,
        "sandbox": None,
        "label_leakage": False,
        "same_evidence": True,
        "fable_included": False,
        "fable_status": "EXPERIMENTAL_ONLY_NOT_RUN",
        "discovery_vs_reasoning": {
            "discovery": det.get("discovery"),
            "reasoning": {
                "attempted": bool(rows),
                "rows": len(rows),
                "note": "separated_from_discovery",
            },
        },
        "level3_enabled": False,
    }


def compare_same_evidence(det: Mapping[str, Any], hybrid: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "same_evidence": bool(det.get("same_evidence") and hybrid.get("same_evidence")),
        "label_leakage": bool(det.get("label_leakage") or hybrid.get("label_leakage")),
        "discovery_separated": True,
        "fable_included": False,
        "writes": 0,
    }
