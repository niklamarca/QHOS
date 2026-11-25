"""Contrato proprio QHOS2: observacao de arquivo nao prova sessao ao vivo."""
from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping

SCHEMA = "qhos2.observation.v1"
_STATIC_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Exception class names the observer reports (`_observation_error`) when no response was received at all. A response
# that arrived but is unusable (HTTPError, JSONDecodeError, ValueError for NaN/redirect/size) is a schema problem.
TRANSPORT_ERRORS = frozenset({"URLError", "TimeoutError", "ConnectionRefusedError", "ConnectionResetError",
                              "ConnectionAbortedError", "BrokenPipeError", "RemoteDisconnected", "OSError"})


def unavailable(reason="qhos2_observation_missing"):
    return {"schema_version": SCHEMA, "available": False, "reason": reason, "environment": "unknown",
            "source_kind": "none", "capture": {}, "quality": {"book_valid": False, "stale": True,
            "available_as_live": False}, "permissions": {"can_execute": False, "research_only": True,
            "can_control_capture": False}}


def accept(value, *, now=None):
    now = time.time() if now is None else now
    if isinstance(value, Mapping) and value.get("_observation_error"):
        # No response at all (port closed, timeout, reset): a transport failure, not a verdict about the schema.
        # A response that arrived but could not be used stays a schema problem.
        return unavailable("unreachable" if str(value["_observation_error"]) in TRANSPORT_ERRORS else "qhos2_schema_invalid")
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA:
        return unavailable("qhos2_schema_invalid")
    if value.get("available") is False and "observed_at" not in value:
        # A refusal this acceptor already produced: keep its reason instead of relabelling it as a contract error.
        reason = value.get("reason")
        return unavailable(reason if isinstance(reason, str) and _STATIC_REASON.fullmatch(reason) else "qhos2_contract_invalid")
    try:
        if value.get("environment") not in {"Rithmic Paper Trading", "QHOS2 Sintetico", "unknown"}:
            raise ValueError
        if value.get("source_kind") not in {"historical_capture", "capture_file", "synthetic_fixture", "none"}:
            raise ValueError
        permissions = value["permissions"]
        if (permissions.get("can_execute") is not False or permissions.get("research_only") is not True
                or permissions.get("can_control_capture") is not False):
            raise ValueError
        at = value["observed_at"]
        if type(at) not in {int, float} or not math.isfinite(at) or at <= 0 or at > now + 1:
            raise ValueError
        age = max(0.0, now - at)
        cap, quality = value["capture"], value["quality"]
        if not isinstance(cap, Mapping) or not isinstance(quality, Mapping) or quality.get("available_as_live") is not False:
            raise ValueError
        result = unavailable()
        result.update(environment=value["environment"], source_kind=value["source_kind"],
                      available=age <= 90, reason="observed" if age <= 90 else "qhos2_telemetry_stale",
                      observed_at=at, age_seconds=round(age, 3))
        for key in ("id", "symbol", "exchange", "reader_state", "error_code"):
            text = cap.get(key)
            result["capture"][key] = text if isinstance(text, str) and re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", text) else None
        for key in ("frames", "bytes"):
            n = cap.get(key)
            result["capture"][key] = n if type(n) is int and n >= 0 else None
        result["capture"]["closed"] = cap.get("closed") is True
        ns = quality.get("last_frame_received_ns")
        ns = str(ns) if type(ns) is int else ns
        ns = ns if isinstance(ns, str) and re.fullmatch(r"[0-9]{1,20}", ns) else None
        result["quality"].update(book_valid=quality.get("book_valid") is True and age <= 90,
                                stale=age > 90, sequence_verified=quality.get("sequence_verified") is True,
                                recent_file_activity=quality.get("recent_file_activity") is True and age <= 90,
                                last_frame_received_ns=ns)
        return result
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return unavailable("qhos2_contract_invalid")
