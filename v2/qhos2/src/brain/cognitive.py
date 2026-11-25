"""Cognitive side of the Brain.

Thinks, interprets, hypothesizes, plans. Output is never authority, evidence,
or an instruction to the observed qhos2 process. (The V1 DNA committee,
`evolution.model_committee`, does not exist in V2.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


COGNITIVE_SCHEMA = "qhos-brain-cognitive-v1"
EPISTEMIC_STATUS = "HYPOTHESIS"
DNA_MARKERS = frozenset({
    "entry_rules",
    "strategy_dna",
    "rule_type",
    "signal_type",
    "stop_loss_pct",
    "take_profit_pct",
    "position_size",
    "target_assets",
})


@dataclass
class CognitiveDraft:
    """Untrusted thought. Control must ignore claimed_confidence as fact."""

    source: str
    interpretation: str = ""
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    plan: Dict[str, Any] = field(default_factory=dict)
    claimed_confidence: float = 0.0
    epistemic_status: str = EPISTEMIC_STATUS
    patches: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": COGNITIVE_SCHEMA,
            "source": self.source,
            "interpretation": self.interpretation,
            "hypotheses": list(self.hypotheses),
            "plan": dict(self.plan),
            "claimed_confidence": float(self.claimed_confidence),
            "epistemic_status": EPISTEMIC_STATUS,
            "authority": False,
            "is_evidence": False,
            "is_fact": False,
            "not_dna_committee": True,
            "patch_files": sorted(self.patches),
        }


def draft_from_diagnosis(
    *,
    interpretation: str,
    hypotheses: Sequence[Mapping[str, Any]],
    plan: Optional[Mapping[str, Any]] = None,
    claimed_confidence: float = 0.0,
    patches: Optional[Mapping[str, str]] = None,
    source: str = "heuristic",
) -> CognitiveDraft:
    return CognitiveDraft(
        source=source,
        interpretation=interpretation[:400],
        hypotheses=[dict(h) for h in hypotheses[:12]],
        plan=dict(plan or {}),
        claimed_confidence=float(claimed_confidence or 0.0),
        patches=dict(patches or {}),
    )


def parse_committee_packet(raw: Mapping[str, Any]) -> CognitiveDraft:
    """Accept a Brain cognitive packet. Reject DNA/strategy payloads."""
    if not isinstance(raw, Mapping):
        raise ValueError("invalid_packet")
    blob = {str(k).lower() for k in raw}
    if blob & DNA_MARKERS or any(isinstance(raw.get(k), (list, dict)) and k in DNA_MARKERS for k in raw):
        raise ValueError("dna_committee_payload_rejected")
    strategies = raw.get("strategies")
    if isinstance(strategies, list) and strategies:
        raise ValueError("dna_committee_payload_rejected")
    hyps = raw.get("hypotheses") or []
    if not isinstance(hyps, list):
        hyps = []
    cleaned: List[Dict[str, Any]] = []
    for row in hyps:
        if not isinstance(row, Mapping):
            continue
        if set(str(k).lower() for k in row) & DNA_MARKERS:
            raise ValueError("dna_committee_payload_rejected")
        cleaned.append({
            "statement": str(row.get("statement") or "")[:400],
            "claimed_confidence": float(row.get("claimed_confidence") or row.get("confidence") or 0.0),
            "target": str(row.get("target") or ""),
        })
    source = str(raw.get("source") or "committee").lower()
    if source not in {"committee", "llm", "heuristic"}:
        source = "committee"
    return CognitiveDraft(
        source=source,
        interpretation=str(raw.get("interpretation") or raw.get("plan_summary") or "")[:400],
        hypotheses=cleaned,
        plan=dict(raw.get("plan") or {}) if isinstance(raw.get("plan"), Mapping) else {},
        claimed_confidence=float(raw.get("claimed_confidence") or 0.0),
        patches=dict(raw.get("patches") or {}) if isinstance(raw.get("patches"), Mapping) else {},
    )


def default_engine_view() -> Dict[str, Any]:
    return {
        "mode": "deterministic_only",
        "provider": "null",
        "model": "",
        "current_investigation": None,
        "hypotheses": [],
        "evidence": [],
        "verification_requests": [],
        "system_confidence": 0.0,
        "model_confidence": 0.0,
        "llm_calls": 0,
        "tool_calls": 0,
        "budget": {"status": "ok"},
        "decision": None,
        "committee_used": False,
        "disagreement": False,
        "fallback": False,
        "authority": False,
        "level3_enabled": False,
    }


def default_heldout_view() -> Dict[str, Any]:
    return {
        "active": False,
        "deterministic": None,
        "hybrid": None,
        "comparison": None,
        "note": "Held-out evaluation is test-only. Runtime stays deterministic_only.",
        "level3_enabled": False,
    }


def default_sides_view() -> Dict[str, Any]:
    return {
        "invariant": "cognitive → evidence → control → orchestrator",
        "cognitive": {
            "role": "think_interpret_hypothesize_plan",
            "engines": ["heuristic"],
            "committee": "brain_cognitive_not_dna",
            "llm_in_runtime": False,
            "authority": False,
            "output": "HYPOTHESIS",
        },
        "engine": default_engine_view(),
        "heldout": default_heldout_view(),
        "evidence": {
            "membrane": True,
            "llm_output_is_not_evidence": True,
            "confidence_requires_new_evidence_id": True,
        },
        "control": {
            "authority": True,
            "policies": True,
            "permissions": True,
            "validation": True,
            "budgets": True,
            "safety_gates": True,
            "ignores_claimed_confidence": True,
        },
        "orchestrator": {
            "name": "BrainOrchestrator",
            "not_hpc_orchestrator": True,
            "receives": "admitted_intents_only",
        },
        "level3_enabled": False,
        "not_a_trading_path": True,
    }
