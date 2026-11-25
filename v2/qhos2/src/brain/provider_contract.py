"""Provider output contract: status taxonomy and safe coercion.

The first paid Hybrid replay recorded two "successful" calls that were nothing of
the kind — output hit the token cap, JSON arrived truncated, validation failed,
and the run reported a fallback with no indication that the cause was our own
budget. Separately, a bare-string field was iterated into single characters.

Both were coercion problems dressed as provider problems. The rules here:

* every outcome gets an explicit status — no silent success;
* truncation is detected and never presented as valid reasoning;
* ambiguous shapes are coerced only where the intent is unambiguous, and every
  coercion is recorded as a warning rather than hidden.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

OUTPUT_STATUSES = (
    "VALID",
    "TRUNCATED",
    "SCHEMA_INVALID",
    "FALLBACK",
    "TIMEOUT",
    "CIRCUIT_OPEN",
    "PROVIDER_ERROR",
)

# A status that must never be counted as a real-model Hybrid result.
NON_REASONING_STATUSES = frozenset({
    "TRUNCATED", "SCHEMA_INVALID", "FALLBACK", "TIMEOUT", "CIRCUIT_OPEN", "PROVIDER_ERROR",
})

INFORMATION_GAIN_WORDS = ("VERY_HIGH", "HIGH", "MEDIUM", "LOW", "VERY_LOW", "NONE")

_GAIN_NUMERIC_RE = re.compile(r"^\s*(\d*\.?\d+)\s*$")


def detect_truncation(text: Any) -> Tuple[bool, str]:
    """Whether a raw response body was cut off mid-structure.

    Checked on the raw text rather than the parsed object, because a lenient
    parser may return a partial dict that looks structurally fine.
    """
    raw = text if isinstance(text, str) else ("" if text is None else str(text))
    body = raw.strip()
    if not body:
        return False, ""
    if not body.startswith(("{", "[")):
        return False, ""
    in_string = False
    escaped = False
    depth = 0
    for ch in body:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    if in_string:
        return True, "unterminated_string"
    if depth > 0:
        return True, f"unclosed_containers({depth})"
    if body.rstrip().endswith(","):
        return True, "trailing_comma"
    return False, ""


def coerce_confidence(value: Any) -> Tuple[float, str]:
    """Confidence in [0, 1]. Returns the value and a warning when adjusted.

    An unparseable confidence becomes 0.0, never a default that would let an
    unsupported hypothesis outrank a supported one.
    """
    if value is None:
        return 0.0, "confidence_missing"
    if isinstance(value, bool):
        return (1.0 if value else 0.0), "confidence_bool_coerced"
    if isinstance(value, (list, tuple, set)):
        items = [v for v in value if v is not None]
        if len(items) != 1:
            return 0.0, "confidence_not_scalar"
        return coerce_confidence(items[0])[0], "confidence_unwrapped_from_list"
    if isinstance(value, str):
        text = value.strip().rstrip("%")
        try:
            num = float(text)
        except ValueError:
            word = value.strip().upper()
            table = {"VERY_HIGH": 0.9, "HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.2, "VERY_LOW": 0.1}
            if word in table:
                return table[word], "confidence_word_mapped"
            return 0.0, "confidence_unparseable"
        if value.strip().endswith("%"):
            num = num / 100.0
        return max(0.0, min(1.0, num)), "confidence_from_string"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0, "confidence_unparseable"
    if num != num or num in (float("inf"), float("-inf")):
        return 0.0, "confidence_not_finite"
    if num < 0.0 or num > 1.0:
        return max(0.0, min(1.0, num)), "confidence_out_of_range"
    return num, ""


def coerce_information_gain(value: Any) -> Tuple[str, str]:
    """Normalize expected information gain to a word or a 0-1 numeric string."""
    if value is None:
        return "MEDIUM", "gain_missing"
    if isinstance(value, (list, tuple, set)):
        items = [v for v in value if v is not None]
        if len(items) == 1:
            gain, _ = coerce_information_gain(items[0])
            return gain, "gain_unwrapped_from_list"
        return "MEDIUM", "gain_not_scalar"
    if isinstance(value, bool):
        return ("HIGH" if value else "LOW"), "gain_bool_coerced"
    if isinstance(value, (int, float)):
        num = max(0.0, min(1.0, float(value)))
        return f"{num:.2f}", ""
    text = str(value).strip()
    match = _GAIN_NUMERIC_RE.match(text)
    if match:
        num = max(0.0, min(1.0, float(match.group(1))))
        return f"{num:.2f}", ""
    word = re.sub(r"[^A-Z_]+", "_", text.upper()).strip("_")
    for known in INFORMATION_GAIN_WORDS:
        if word.startswith(known):
            return known, ""
    return "MEDIUM", "gain_unrecognized"


def coerce_text(value: Any, *, limit: int, joiner: str = " ") -> Tuple[str, str]:
    """Text field that may arrive as a list or mapping."""
    if value is None:
        return "", ""
    if isinstance(value, str):
        return value.strip()[:limit], ""
    if isinstance(value, (list, tuple, set)):
        parts = [str(v).strip() for v in value if v is not None and str(v).strip()]
        return joiner.join(parts)[:limit], "text_joined_from_list"
    if isinstance(value, Mapping):
        parts = [f"{k}: {v}" for k, v in value.items()]
        return joiner.join(parts)[:limit], "text_flattened_from_mapping"
    return str(value)[:limit], "text_coerced_from_scalar"


def classify_output(
    *,
    raw_text: Any = None,
    parsed: Any = None,
    stop_reason: str = "",
    error: str = "",
    fallback: bool = False,
    circuit_open: bool = False,
    schema_error: str = "",
) -> Dict[str, Any]:
    """Single verdict for one provider call. Order is deliberate.

    Infrastructure outcomes are decided before schema ones: a timeout that also
    left unparseable text is a timeout, not a schema failure.
    """
    reasons: List[str] = []
    status = "VALID"

    truncated, trunc_reason = detect_truncation(raw_text)
    err = str(error or "").lower()

    if circuit_open:
        status, reasons = "CIRCUIT_OPEN", ["breaker_open_before_call"]
    elif "timeout" in err or "timed out" in err:
        status, reasons = "TIMEOUT", [str(error)[:120]]
    elif err:
        status, reasons = "PROVIDER_ERROR", [str(error)[:120]]
    elif str(stop_reason or "").lower() in {"max_tokens", "length"}:
        status, reasons = "TRUNCATED", [f"stop_reason={stop_reason}"]
        if trunc_reason:
            reasons.append(trunc_reason)
    elif truncated:
        status, reasons = "TRUNCATED", [trunc_reason or "incomplete_json"]
    elif schema_error:
        status, reasons = "SCHEMA_INVALID", [str(schema_error)[:120]]
    elif parsed is not None and not isinstance(parsed, (dict, list)):
        status, reasons = "SCHEMA_INVALID", ["not_an_object"]
    elif fallback:
        status, reasons = "FALLBACK", ["deterministic_fallback_used"]

    if status == "VALID" and fallback:
        # A fallback is never a successful real-model result, whatever else parsed.
        status, reasons = "FALLBACK", ["deterministic_fallback_used"]

    return {
        "output_status": status,
        "output_status_reasons": reasons,
        "truncated": status == "TRUNCATED",
        "is_real_model_reasoning": status == "VALID",
        "counts_as_hybrid_success": status == "VALID",
        "stop_reason": str(stop_reason or ""),
    }


def call_metrics(
    *,
    llm_called: bool,
    provider: str = "",
    model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_s: float = 0.0,
    output_status: str = "VALID",
    fallback: bool = False,
    breaker_state: str = "closed",
) -> Dict[str, Any]:
    """The full metric row every paid comparison must carry.

    A zero-token row that claims a successful call is unresolvable after the
    fact, so the fields that make it resolvable are mandatory here.
    """
    if output_status not in OUTPUT_STATUSES:
        output_status = "PROVIDER_ERROR"
    return {
        "llm_called": bool(llm_called),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "total_tokens": int(input_tokens or 0) + int(output_tokens or 0),
        "latency_s": round(float(latency_s or 0.0), 3),
        "output_status": output_status,
        "fallback": bool(fallback),
        "breaker_state": str(breaker_state or "closed"),
        "counts_as_hybrid_success": output_status == "VALID" and not fallback,
    }
