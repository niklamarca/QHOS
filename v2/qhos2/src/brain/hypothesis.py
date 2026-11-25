"""Evidence-first hypotheses. Confidence never rises because a model repeated itself."""
from __future__ import annotations

import hashlib
import math
from typing import Dict, Iterable, List, Optional, Tuple

from .models import Hypothesis, HypothesisStatus, DecisionTrace


def hypothesis_id(statement: str, target: str = "") -> str:
    raw = f"{statement.strip().lower()}|{target.strip().lower()}"
    return "hyp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _status_for(confidence: float, *, verified: bool, weakened: bool) -> str:
    if confidence < 0.15:
        return HypothesisStatus.REJECTED.value if weakened else HypothesisStatus.PROPOSED.value
    if weakened and confidence < 0.35:
        return HypothesisStatus.WEAKENED.value
    if verified and confidence >= 0.85:
        return HypothesisStatus.CONFIRMED.value
    if confidence >= 0.6:
        return HypothesisStatus.SUPPORTED.value
    if confidence >= 0.35:
        return HypothesisStatus.TESTING.value
    return HypothesisStatus.PROPOSED.value


class HypothesisEngine:
    """Structured hypotheses. Duplicate evidence ids do not change confidence."""

    def __init__(self) -> None:
        self.hypotheses: Dict[str, Hypothesis] = {}
        self._seen_evidence: Dict[str, set[str]] = {}
        self.traces: List[DecisionTrace] = []

    def propose(
        self,
        statement: str,
        *,
        target: str = "",
        kind: str = "code",
        next_verification: str = "inspect_source",
        prior: float = 0.2,
    ) -> Hypothesis:
        if type(prior) not in (int, float) or not 0 <= prior <= 1 or not math.isfinite(prior):
            raise ValueError("invalid_prior")
        hid = hypothesis_id(statement, target)
        existing = self.hypotheses.get(hid)
        if existing is not None:
            return existing
        hyp = Hypothesis(
            hypothesis_id=hid,
            statement=statement,
            confidence=max(0.0, min(0.35, float(prior))),
            status=HypothesisStatus.PROPOSED.value,
            next_verification=next_verification,
            target=target,
            kind=kind,
        )
        self.hypotheses[hid] = hyp
        self._seen_evidence[hid] = set()
        self.traces.append(
            DecisionTrace(
                what="hypothesis_proposed",
                why="observation_pattern",
                hypothesis_id=hid,
                action="propose",
                result=statement,
                confidence=hyp.confidence,
            )
        )
        return hyp

    def add_evidence(
        self,
        hypothesis_id: str,
        evidence_id: str,
        *,
        polarity: str,
        text: str,
        weight: float,
        verified: bool = False,
    ) -> Tuple[Hypothesis, bool]:
        """Return (hypothesis, changed). Same evidence_id is a no-op."""
        hyp = self.hypotheses[hypothesis_id]
        seen = self._seen_evidence.setdefault(hypothesis_id, set())
        if polarity not in {"for", "against"} or not isinstance(evidence_id, str) or not evidence_id:
            raise ValueError("invalid_evidence")
        if type(weight) not in (int, float) or not 0 <= weight <= 1 or not math.isfinite(weight):
            raise ValueError("invalid_evidence_weight")
        token = evidence_id
        if token in seen:
            return hyp, False
        seen.add(token)
        delta = abs(float(weight))
        if polarity == "against":
            hyp.evidence_against.append(f"{evidence_id}: {text}"[:240])
            hyp.confidence = max(0.0, hyp.confidence - delta)
            weakened = True
        else:
            hyp.evidence_for.append(f"{evidence_id}: {text}"[:240])
            hyp.confidence = min(1.0, hyp.confidence + delta)
            weakened = False
        hyp.status = _status_for(
            hyp.confidence,
            verified=verified and polarity != "against",
            weakened=weakened or bool(hyp.evidence_against),
        )
        self.traces.append(
            DecisionTrace(
                what="evidence_applied",
                why="new_evidence",
                evidence=[evidence_id, text[:160]],
                hypothesis_id=hypothesis_id,
                action=f"evidence_{polarity}",
                result=hyp.status,
                confidence=hyp.confidence,
            )
        )
        return hyp, True

    def ranked(self) -> List[Hypothesis]:
        return sorted(
            self.hypotheses.values(),
            key=lambda h: (-h.confidence, h.hypothesis_id),
        )

    def best(self) -> Optional[Hypothesis]:
        ranked = [h for h in self.ranked() if h.status != HypothesisStatus.REJECTED.value]
        return ranked[0] if ranked else None

    def confirmed(self) -> List[Hypothesis]:
        return [h for h in self.hypotheses.values() if h.status == HypothesisStatus.CONFIRMED.value]

    def dump(self) -> List[Dict[str, object]]:
        return [h.to_dict() for h in self.ranked()]


def forbidden_confirmed(hypotheses: Iterable[Hypothesis], forbidden: Iterable[str]) -> List[str]:
    needles = [str(x).lower() for x in forbidden if x]
    hits: List[str] = []
    for hyp in hypotheses:
        if hyp.status != HypothesisStatus.CONFIRMED.value:
            continue
        blob = f"{hyp.statement} {hyp.target} {hyp.kind}".lower()
        for needle in needles:
            if needle and needle in blob:
                hits.append(hyp.hypothesis_id)
                break
    return hits
