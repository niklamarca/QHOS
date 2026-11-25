"""Trader-native cognitive effort. Fable numbers are not architecture."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence

from brain.reasoner import ReasoningBudget


class EffortLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    XHIGH = "XHIGH"
    MAX = "MAX"


_ORDER = {
    EffortLevel.LOW: 0,
    EffortLevel.MEDIUM: 1,
    EffortLevel.HIGH: 2,
    EffortLevel.XHIGH: 3,
    EffortLevel.MAX: 4,
}


@dataclass(frozen=True)
class EffortBudget:
    rounds: int
    hypotheses: int
    context_files: int
    memory: int
    verifications: int
    tools: int
    agents: int
    committee: bool
    fresh_verifier: bool
    time_s: float
    tokens: int

    def to_reasoning_budget(self) -> ReasoningBudget:
        return ReasoningBudget(
            max_reasoning_rounds=self.rounds,
            max_llm_calls=1 if self.rounds <= 1 else min(3, self.rounds),
            max_tool_calls=self.tools,
            max_hypotheses=self.hypotheses,
            max_verifications=self.verifications,
            timeout_s=self.time_s,
            token_budget=self.tokens,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


BUDGETS = {
    EffortLevel.LOW: EffortBudget(1, 2, 2, 2, 1, 2, 0, False, False, 8.0, 400),
    EffortLevel.MEDIUM: EffortBudget(2, 3, 4, 4, 3, 4, 0, False, False, 20.0, 800),
    EffortLevel.HIGH: EffortBudget(3, 5, 6, 6, 5, 6, 0, True, True, 30.0, 1200),
    EffortLevel.XHIGH: EffortBudget(4, 6, 8, 8, 6, 8, 0, True, True, 45.0, 1600),
    EffortLevel.MAX: EffortBudget(5, 8, 10, 10, 8, 10, 0, True, True, 60.0, 2000),
}


@dataclass
class EffortSignals:
    severity: int = 0
    ambiguity: int = 0
    components: int = 0
    plausible_hypotheses: int = 0
    contradictory: bool = False
    unknown_pattern: bool = False
    memory_conflict: bool = False
    safety_impact: bool = False
    economic: bool = False
    confidence: float = 1.0
    trivial_deterministic: bool = False
    untrusted_text: bool = False
    multi_commit: bool = False
    deceptive: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def assess_signals(
    observations: Sequence[Mapping[str, Any]],
    *,
    memory: Optional[Sequence[Any]] = None,
    findings: Optional[Sequence[Mapping[str, Any]]] = None,
) -> EffortSignals:
    texts = []
    kinds = []
    paths = set()
    for row in observations or []:
        if not isinstance(row, Mapping):
            continue
        kinds.append(str(row.get("kind") or "").lower())
        blob = f"{row.get('summary') or ''} {row.get('body') or ''}"
        texts.append(blob.lower())
        if row.get("path"):
            paths.add(str(row["path"]))
    blob = "\n".join(texts)
    findings = list(findings or [])
    finding_kinds = {str(f.get("kind") or "") for f in findings if isinstance(f, Mapping)}
    trivial = bool(finding_kinds & {"wrong_operator", "timeout_zero", "nested_loop", "parse_none"})
    inj = any(
        tok in blob
        for tok in (
            "ignore policy", "ignore previous", "git push", "level 5",
            "sandbox_enabled=true", "disable risk", "root cause is",
        )
    )
    deceptive = any(
        tok in blob
        for tok in ("delete the test", "xfail", "pytest.skip", "assert true", "except:", "hardcoded")
    )
    commits = blob.count("commit") + blob.count("sha ")
    components = max(len(paths), blob.count(".py"), 1 if "test_failure" in kinds else 0)
    contradictory = "contradict" in blob or ("timeout" in blob and "wrong_operator" in str(finding_kinds))
    mem_conflict = bool(memory) and (contradictory or "stale" in blob or "superseded" in blob)
    safety = any(tok in blob for tok in ("execution/engine", "governance/", "risk", "push", "deploy"))
    unknown = bool(kinds) and not trivial and "test_failure" in kinds and components >= 2
    n_fail = sum(1 for k in kinds if k == "test_failure")
    severity = 80 if safety else (60 if n_fail >= 2 else (40 if n_fail else 10))
    ambiguity = 0
    if inj or contradictory or unknown:
        ambiguity = 3
    elif n_fail >= 2 or commits >= 2:
        ambiguity = 2
    elif not trivial:
        ambiguity = 1
    conf = 0.9 if trivial and not contradictory else (0.35 if ambiguity >= 2 else 0.6)
    return EffortSignals(
        severity=severity,
        ambiguity=ambiguity,
        components=max(components, n_fail),
        plausible_hypotheses=max(1, n_fail + (2 if contradictory else 0)),
        contradictory=contradictory,
        unknown_pattern=unknown,
        memory_conflict=mem_conflict,
        safety_impact=safety,
        economic=False,
        confidence=conf,
        trivial_deterministic=trivial and not contradictory and not inj,
        untrusted_text=inj,
        multi_commit=commits >= 2,
        deceptive=deceptive,
    )


def select_effort(
    signals: EffortSignals,
    *,
    model_recommendation: Optional[str] = None,
) -> EffortLevel:
    """Policy sets the ceiling. The model may recommend; it cannot raise the cap."""
    if signals.trivial_deterministic and not signals.safety_impact and not signals.untrusted_text:
        policy = EffortLevel.LOW
    elif signals.safety_impact and (signals.ambiguous() if hasattr(signals, "ambiguous") else signals.ambiguity >= 2):
        policy = EffortLevel.HIGH
    elif signals.deceptive or signals.untrusted_text or signals.memory_conflict:
        policy = EffortLevel.HIGH
    elif signals.multi_commit or signals.unknown_pattern or signals.contradictory:
        policy = EffortLevel.HIGH
    elif signals.components >= 3 or signals.ambiguity >= 2:
        policy = EffortLevel.HIGH
    elif signals.ambiguity >= 1 or signals.components >= 2:
        policy = EffortLevel.MEDIUM
    else:
        policy = EffortLevel.LOW
    if signals.safety_impact and policy == EffortLevel.LOW:
        policy = EffortLevel.MEDIUM
    # MAX is reserved for explicit human/eval override — policy never auto-selects it.
    rec = _parse_level(model_recommendation)
    if rec is None:
        return policy
    if _ORDER[rec] <= _ORDER[policy]:
        return rec
    return policy


def _parse_level(raw: Optional[str]) -> Optional[EffortLevel]:
    if not raw:
        return None
    key = str(raw).strip().upper()
    try:
        return EffortLevel(key)
    except ValueError:
        return None


class CognitiveEffortPolicy:
    """Deterministic ceiling. Never default MAX. Never enables Level 3."""

    def decide(
        self,
        observations: Sequence[Mapping[str, Any]],
        *,
        memory: Optional[Sequence[Any]] = None,
        findings: Optional[Sequence[Mapping[str, Any]]] = None,
        model_recommendation: Optional[str] = None,
    ) -> Dict[str, Any]:
        signals = assess_signals(observations, memory=memory, findings=findings)
        level = select_effort(signals, model_recommendation=model_recommendation)
        budget = BUDGETS[level]
        overkill = level in {EffortLevel.XHIGH, EffortLevel.MAX} and signals.trivial_deterministic
        underthink = level == EffortLevel.LOW and (
            signals.unknown_pattern or signals.contradictory or signals.deceptive
        )
        return {
            "level": level.value,
            "budget": budget.to_dict(),
            "signals": signals.to_dict(),
            "model_recommendation": model_recommendation,
            "policy_ceiling": level.value,
            "overkill": overkill,
            "underthink": underthink,
            "committee_eligible": budget.committee,
            "fresh_verifier": budget.fresh_verifier,
            "autonomy_unchanged": True,
            "level3_enabled": False,
        }


def effort_selection_accuracy(selected: str, expected: str) -> float:
    if str(selected).upper() == str(expected).upper():
        return 1.0
    s = _ORDER.get(_parse_level(selected) or EffortLevel.MEDIUM, 1)
    e = _ORDER.get(_parse_level(expected) or EffortLevel.MEDIUM, 1)
    if abs(s - e) == 1:
        return 0.5
    return 0.0
