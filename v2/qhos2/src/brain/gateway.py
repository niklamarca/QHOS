"""Cognitive gateway. Brain domain never imports a vendor SDK here.

Vendor clients are constructed in brain.local_llm_client and reached only via adapters.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Protocol

from .models import CognitiveMode
from .policies import ProviderHealthCircuitBreaker, SessionCircuitBreaker
from .provider_contract import (
    coerce_confidence,
    coerce_information_gain,
    coerce_text,
)
from .redact import prepare_provider_context, sanitize_error


REASONER_REQUIRED = (
    "incident_assessment",
    "hypotheses",
    "recommended_verifications",
    "stop_recommendation",
)

# Fields the contract understands. Anything else is reported and dropped.
_KNOWN_REASONER_KEYS = frozenset(REASONER_REQUIRED) | {
    "risk_notes", "confidence", "model_confidence", "notes", "summary",
}

INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all instructions",
    "ignore safety policy",
    "run git push",
    "disable risk",
    "you are now",
    "system prompt",
    "sandbox_enabled=true",
    "set sandbox_enabled",
    "you are now level 5",
    "delete failing tests",
    "use api key printed",
    "root cause has already been confirmed",
    "update governance/control.py",
)


def _string_list(value: Any, *, item_max: int, limit: int) -> List[str]:
    """Coerce to a list of strings.

    A bare string must become one item. Iterating it would split the text into
    individual characters, which is how ``evidence_needed`` arrived as
    ``["O", "u", "t", ...]`` in the first paid Hybrid replay.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        text = text.strip()
        return [text[:item_max]] if text else []
    if isinstance(value, Mapping):
        value = list(value.values())
    if not isinstance(value, (list, tuple, set)):
        return [str(value)[:item_max]]
    return [str(x)[:item_max] for x in value if x is not None and str(x).strip()][:limit]


def validate_reasoner_output(raw: Any) -> Dict[str, Any]:
    """Structured contract. Free-form prose is rejected.

    Ambiguous shapes are coerced only where intent is unambiguous, and every
    coercion is reported in ``schema_warnings`` rather than hidden.
    """
    if not isinstance(raw, dict):
        raise ValueError("invalid_reasoner_output")
    missing = [k for k in REASONER_REQUIRED if k not in raw]
    if missing:
        raise ValueError("missing_fields:" + ",".join(missing))
    hyps = raw.get("hypotheses")
    if isinstance(hyps, dict):
        # A single hypothesis returned bare is unambiguous; wrap it.
        hyps = [hyps]
    if not isinstance(hyps, list):
        raise ValueError("hypotheses_not_list")
    warnings: List[str] = []
    cleaned_hyps: List[Dict[str, Any]] = []
    for row in hyps[:8]:
        if isinstance(row, str) and row.strip():
            row = {"statement": row}
            warnings.append("hypothesis_string_wrapped")
        if not isinstance(row, dict):
            warnings.append("hypothesis_dropped_not_object")
            continue
        statement, warn = coerce_text(row.get("statement"), limit=400)
        if warn:
            warnings.append(f"statement:{warn}")
        if not statement.strip():
            warnings.append("hypothesis_dropped_no_statement")
            continue
        reason, warn = coerce_text(row.get("reason"), limit=400)
        if warn:
            warnings.append(f"reason:{warn}")
        confidence, warn = coerce_confidence(
            row.get("confidence") if row.get("confidence") is not None else row.get("model_confidence")
        )
        if warn:
            warnings.append(f"confidence:{warn}")
        cleaned_hyps.append({
            "statement": statement,
            "reason": reason,
            "evidence_needed": _string_list(row.get("evidence_needed"), item_max=200, limit=6),
            "model_confidence": confidence,
            "target": str(row.get("target") or "")[:120],
        })
    stop = str(raw.get("stop_recommendation") or "NEEDS_MORE_EVIDENCE").strip().upper()
    allowed_stop = {
        "ROOT_CAUSE_CONFIRMED", "LIKELY_CAUSE", "NEEDS_MORE_EVIDENCE",
        "NO_ACTION_REQUIRED", "PRE_EXISTING_FAILURE", "UNSAFE_TO_PROCEED",
        "BUDGET_EXHAUSTED",
    }
    if stop not in allowed_stop:
        stop = "NEEDS_MORE_EVIDENCE"
    raw_verifs = raw.get("recommended_verifications")
    if isinstance(raw_verifs, (str, dict)):
        raw_verifs = [raw_verifs]
        warnings.append("verifications_wrapped_from_scalar")
    verifs = []
    for row in raw_verifs or []:
        if isinstance(row, str) and row.strip():
            verifs.append({
                "action_type": "inspect_file",
                "target": row.strip()[:160],
                "reason": "model_request",
                "disconfirming": False,
                "expected_information_gain": "MEDIUM",
            })
        elif isinstance(row, dict) and (row.get("action_type") or row.get("target")):
            target, warn = coerce_text(row.get("target"), limit=160)
            if warn:
                warnings.append(f"target:{warn}")
            gain, warn = coerce_information_gain(
                row.get("expected_information_gain")
                if row.get("expected_information_gain") is not None
                else row.get("information_gain")
            )
            if warn:
                warnings.append(f"information_gain:{warn}")
            raw_gain, _ = coerce_text(
                row.get("expected_information_gain")
                if row.get("expected_information_gain") is not None
                else row.get("information_gain"),
                limit=120,
            )
            verifs.append({
                "action_type": str(row.get("action_type") or "inspect_file")[:40],
                "target": target,
                "reason": str(row.get("reason") or "")[:200],
                "disconfirming": bool(row.get("disconfirming")),
                # Normalized for ranking; the model's own wording is kept beside
                # it so narrowing the vocabulary does not destroy its rationale.
                "expected_information_gain": gain,
                "expected_information_gain_text": raw_gain,
            })
    assessment, warn = coerce_text(raw.get("incident_assessment"), limit=400)
    if warn:
        warnings.append(f"incident_assessment:{warn}")
    extra = sorted(set(raw.keys()) - _KNOWN_REASONER_KEYS)
    if extra:
        # Unknown keys are ignored, not merged: an unrecognised field must never
        # reach a consumer that might treat it as authority or evidence.
        warnings.append("ignored_extra_fields:" + ",".join(extra[:6]))
    return {
        "incident_assessment": assessment,
        "hypotheses": cleaned_hyps,
        "recommended_verifications": verifs[:8],
        "risk_notes": _string_list(raw.get("risk_notes"), item_max=200, limit=6),
        "stop_recommendation": stop,
        "epistemic_status": "HYPOTHESIS",
        "authority": False,
        "is_evidence": False,
        "schema_warnings": warnings[:12],
        "ignored_extra_fields": extra[:6],
    }


@dataclass
class ProviderCall:
    method: str
    ok: bool
    latency_s: float = 0.0
    tokens: int = 0
    error: str = ""
    provider: str = ""
    model: str = ""


class CognitiveProvider(Protocol):
    name: str
    model: str

    def available(self) -> bool: ...
    def analyze(self, context: Dict[str, Any]) -> Dict[str, Any]: ...
    def generate_hypotheses(self, context: Dict[str, Any]) -> Dict[str, Any]: ...
    def evaluate_evidence(self, context: Dict[str, Any]) -> Dict[str, Any]: ...
    def propose_verification(self, context: Dict[str, Any]) -> Dict[str, Any]: ...
    def propose_plan(self, context: Dict[str, Any]) -> Dict[str, Any]: ...
    def summarize_decision(self, context: Dict[str, Any]) -> Dict[str, Any]: ...


class NullProvider:
    name = "null"
    model = ""

    def available(self) -> bool:
        return False

    def analyze(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def generate_hypotheses(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def evaluate_evidence(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def propose_verification(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def propose_plan(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def summarize_decision(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {}


@dataclass
class CognitiveGateway:
    mode: str = CognitiveMode.DETERMINISTIC_ONLY.value
    provider: Optional[CognitiveProvider] = None
    max_llm_calls: int = 3
    max_retries: int = 1
    timeout_s: float = 20.0
    calls: List[ProviderCall] = field(default_factory=list)
    performance: List[Dict[str, Any]] = field(default_factory=list)
    breaker: SessionCircuitBreaker = field(default_factory=lambda: SessionCircuitBreaker(fail_limit=3, cooldown_s=120.0))
    provider_health: ProviderHealthCircuitBreaker = field(
        default_factory=lambda: ProviderHealthCircuitBreaker(fail_limit=5, cooldown_s=300.0)
    )
    last_success_at: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def __post_init__(self) -> None:
        if self.provider is None:
            self.provider = NullProvider()
        mode = str(self.mode or "").lower()
        if mode not in {m.value for m in CognitiveMode}:
            self.mode = CognitiveMode.DETERMINISTIC_ONLY.value

    def reset_session(self) -> None:
        """Drop per-run call state AND the session breaker.

        Independent evaluation repeats must not share budget or session-open
        state. Provider-health breaker is intentionally left intact.
        """
        self.calls = []
        self.performance = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.breaker.reset()

    def record_provider_health_failure(self, now: float | None = None) -> None:
        """Trip live provider-health breaker. Never called by reset_session()."""
        self.provider_health.record_failure(float(now if now is not None else time.time()))

    def record_provider_health_success(self) -> None:
        self.provider_health.record_success()

    def close(self) -> None:
        closer = getattr(self.provider, "close", None)
        if callable(closer):
            closer()

    @property
    def available(self) -> bool:
        if self.mode == CognitiveMode.DETERMINISTIC_ONLY.value:
            return False
        try:
            return bool(self.provider and self.provider.available())
        except Exception:
            return False

    def _invoke(self, method: str, context: Dict[str, Any]) -> Dict[str, Any]:
        if not self.available:
            return {}
        now = time.time()
        if self.provider_health.is_open(now) or self.breaker.is_open(now):
            reason = "provider_health_open" if self.provider_health.is_open(now) else "circuit_open"
            self.calls.append(ProviderCall(
                method=method, ok=False, error=reason,
                provider=getattr(self.provider, "name", ""),
            ))
            return {}
        if len(self.calls) >= self.max_llm_calls:
            self.calls.append(ProviderCall(
                method=method, ok=False, error="budget_llm_calls",
                provider=getattr(self.provider, "name", ""),
            ))
            return {}
        fn = getattr(self.provider, method, None)
        if fn is None:
            return {}
        last_err = "unavailable"
        for _attempt in range(self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                safe_ctx, notes = prepare_provider_context(context if isinstance(context, dict) else {})
                raw = fn(safe_ctx)
                latency = time.perf_counter() - t0
                if latency > self.timeout_s:
                    raise TimeoutError("provider_timeout")
                packet = validate_reasoner_output(raw)
                if notes:
                    packet["risk_notes"] = list(packet.get("risk_notes") or []) + notes[:4]
                rec = ProviderCall(
                    method=method, ok=True, latency_s=latency,
                    provider=getattr(self.provider, "name", ""),
                    model=getattr(self.provider, "model", ""),
                    tokens=int(getattr(self.provider, "last_tokens", 0) or 0),
                )
                self.calls.append(rec)
                self.last_success_at = now
                self.breaker.record_success()
                self.input_tokens += int(getattr(self.provider, "last_input_tokens", 0) or 0)
                self.output_tokens += int(getattr(self.provider, "last_output_tokens", 0) or 0)
                self.performance.append({
                    "task_type": method,
                    "provider": rec.provider,
                    "model": rec.model,
                    "correctness": None,
                    "latency_s": round(latency, 4),
                    "cost": None,
                    "confidence": None,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                })
                return packet
            except Exception as exc:
                last_err = sanitize_error(f"{type(exc).__name__}:{exc}")
                self.breaker.record_failure(now)
                self.input_tokens += int(getattr(self.provider, "last_input_tokens", 0) or 0)
                self.output_tokens += int(getattr(self.provider, "last_output_tokens", 0) or 0)
                self.calls.append(ProviderCall(
                    method=method, ok=False,
                    latency_s=time.perf_counter() - t0,
                    error=last_err[:80],
                    provider=getattr(self.provider, "name", ""),
                    tokens=int(getattr(self.provider, "last_tokens", 0) or 0),
                ))
        return {}

    def generate_hypotheses(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke("generate_hypotheses", context)

    def evaluate_evidence(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke("evaluate_evidence", context)

    def propose_verification(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return self._invoke("propose_verification", context)

    def status(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "provider": getattr(self.provider, "name", "null"),
            "model": getattr(self.provider, "model", ""),
            "available": self.available,
            "llm_calls": len(self.calls),
            "llm_ok": sum(1 for c in self.calls if c.ok),
            "not_dna_committee": True,
            "level3_enabled": False,
            "circuit_open": self.breaker.is_open(time.time()) or self.provider_health.is_open(time.time()),
            "session_breaker": self.breaker.snapshot(),
            "provider_health_breaker": self.provider_health.snapshot(),
            "last_success_at": self.last_success_at or None,
            "invalid_schema_rate": round(
                sum(1 for c in self.calls if not c.ok and "invalid" in (c.error or "").lower())
                / max(1, len(self.calls)),
                4,
            ),
            "timeout_rate": round(
                sum(1 for c in self.calls if "timeout" in (c.error or "").lower())
                / max(1, len(self.calls)),
                4,
            ),
            "health": "degraded" if self.breaker.is_open(time.time()) else ("available" if self.available else "unavailable"),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "sandbox_enabled": False,
        }
