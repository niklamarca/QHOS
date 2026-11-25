"""Bounded observer projection of the versioned Trader Rithmic summary.

This module does not import Trader, read captures or open a market connection.
Data age uses the original local receive nanoseconds, never the HTTP/WS arrival.
Only recorded captures (historical or explicitly synthetic) are supported.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
import math
import re
import time
from typing import Any, Mapping
from .rithmic_operational import accept_operational, age_operational

SCHEMA = "qhos.rithmic.observation.v1"
FIELD = "rithmic_protocol_observation"
MAX_TELEMETRY_AGE_SECONDS = 90.0
STALE_DATA_SECONDS = 10.0


def _text(value: Any, maximum: int = 256) -> str | None:
    return value if isinstance(value, str) and len(value) <= maximum else None


def _count(value: Any) -> int | str | None:
    if value is None:
        return None
    if type(value) is int and 0 <= value <= (1 << 53) - 1:
        return value
    if type(value) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", value):
        return value
    raise ValueError("invalid_observation_count")


def _ns(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value) else None


def _decimal(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        return value if Decimal(value).is_finite() else None
    except InvalidOperation:
        return None


def _price(value: Any) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("invalid_price")
    decimal = _decimal(value.get("decimal"))
    binary = _text(value.get("binary64_hex"), 64)
    try:
        valid_binary = binary is not None and math.isfinite(float.fromhex(binary))
    except ValueError:
        valid_binary = False
    if decimal is None or not valid_binary:
        raise ValueError("invalid_price")
    ticks = value.get("ticks")
    tick_size = value.get("tick_size")
    if ticks is not None and (not isinstance(ticks, str) or re.fullmatch(r"-?[0-9]{1,800}", ticks) is None):
        raise ValueError("invalid_price_ticks")
    if tick_size is not None and _decimal(tick_size) is None:
        raise ValueError("invalid_tick_size")
    return {"decimal": decimal, "binary64_hex": binary, "ticks": ticks,
            "tick_size": tick_size, "alignment": _text(value.get("alignment"), 48)}


def _mapping(value: Mapping, key: str) -> Mapping:
    result = value.get(key, {})
    if not isinstance(result, Mapping):
        raise ValueError("invalid_summary_shape")
    return result


def _classification(value: Any) -> dict:
    """Validate reported taxonomy only; this observer cannot qualify a feed."""
    if not isinstance(value, Mapping) or set(value) != {"environment_kind", "modality", "production_market_data", "basis"}:
        raise ValueError("invalid_feed_classification")
    if value["environment_kind"] not in {"production", "vendor_test", "local", "unknown"}:
        raise ValueError("unsupported_feed_environment")
    if value["modality"] is not None and value["modality"] not in {"realtime", "delayed", "replay", "synthetic", "unproven", "vendor_test"}:
        raise ValueError("unsupported_feed_modality")
    if value["production_market_data"] is not False:
        raise ValueError("observation_may_not_assert_production_market_data")
    if not isinstance(value["basis"], str) or re.fullmatch(r"[A-Za-z0-9_:.-]{1,256}", value["basis"]) is None:
        raise ValueError("invalid_feed_basis")
    return dict(value)


def unavailable(reason: str = "observation_missing") -> dict:
    return {
        "schema_version": SCHEMA, "status": "UNAVAILABLE", "provider": "rithmic_protocol",
        "environment": "Rithmic Test", "source_kind": "none", "instrument": {}, "capture": {},
        "feed_classification": {"environment_kind": "unknown", "modality": None,
                                "production_market_data": False, "basis": "not_established"},
        "connection": {"state": "UNAVAILABLE", "connected": False, "login_confirmed": False},
        "subscription": {"state": "UNAVAILABLE", "active": False, "confirmed": False},
        "last_update": {"received_ns": None, "age_seconds": None,
                        "clock_source": "local_receive", "clock_anomaly": False},
        "persistence": {"state": "UNAVAILABLE", "captures": None, "events": None, "verified": False},
        "continuity": {"discontinuities": None, "reconnects": None, "recovery_observed": False, "verified": False},
        "bbo": {"condition": "UNAVAILABLE", "bid_price": None, "ask_price": None,
                "bid_size": None, "ask_size": None, "bid_orders": None, "ask_orders": None},
        "trades": {"incremental_count": None, "snapshot_count": None, "partial_count": None, "last_trade": None},
        "quality": {"stale": True, "blocked_reasons": [reason],
                    "available_for_observation": False, "available_as_live": False},
        "research_only": True, "can_execute": False, "book_valid": False,
    }


def accept_summary(value: Any) -> dict:
    """Copy known fields only; malformed/unknown contracts fail visibly closed."""
    if value is None:
        return unavailable()
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA:
        return unavailable("unsupported_observation_schema")
    try:
        if value.get("provider") != "rithmic_protocol" or value.get("environment") != "Rithmic Test":
            raise ValueError("unsupported_provider_environment")
        if value.get("source_kind") not in {"historical_capture", "synthetic_fixture", "none"}:
            raise ValueError("unsupported_observation_origin")
        if value.get("status") not in {"EMPTY", "RECORDED", "ERROR", "UNAVAILABLE"}:
            raise ValueError("unsupported_observation_status")
        sections = {key: _mapping(value, key) for key in (
            "instrument", "capture", "connection", "subscription", "last_update",
            "persistence", "continuity", "bbo", "trades", "quality")}
        connection, subscription, quality = (sections[key] for key in ("connection", "subscription", "quality"))
        if (value.get("research_only") is not True or value.get("can_execute") is not False
                or value.get("book_valid") is not False or connection.get("connected") is not False
                or subscription.get("active") is not False or quality.get("available_as_live") is not False):
            raise ValueError("observation_guard_violation")
        result = unavailable()
        result.update(status=value["status"], source_kind=value["source_kind"])
        if "feed_classification" in value:
            result["feed_classification"] = _classification(value["feed_classification"])
        result["instrument"] = {key: _text(sections["instrument"].get(key)) for key in ("symbol", "exchange")}
        result["capture"] = {key: _text(sections["capture"].get(key)) for key in ("capture_id", "session_id", "run_id")}
        result["connection"].update(state=_text(connection.get("state"), 48), login_confirmed=connection.get("login_confirmed") is True)
        result["subscription"].update(state=_text(subscription.get("state"), 48), confirmed=subscription.get("confirmed") is True)
        last = sections["last_update"]
        if last.get("clock_source") != "local_receive":
            raise ValueError("unsupported_receive_clock")
        received = _ns(last.get("received_ns"))
        if last.get("received_ns") is not None and received is None:
            raise ValueError("invalid_receive_timestamp")
        result["last_update"].update(received_ns=received, clock_anomaly=last.get("clock_anomaly") is True)
        persist = sections["persistence"]
        result["persistence"] = {"state": _text(persist.get("state"), 48), "captures": _count(persist.get("captures")),
                                 "events": _count(persist.get("events")), "verified": persist.get("verified") is True}
        continuity = sections["continuity"]
        result["continuity"].update({key: _count(continuity.get(key)) for key in ("discontinuities", "reconnects")})
        result["continuity"]["recovery_observed"] = continuity.get("recovery_observed") is True
        bbo = sections["bbo"]
        result["bbo"] = {"condition": _text(bbo.get("condition"), 48),
                         **{key: _price(bbo.get(key)) for key in ("bid_price", "ask_price")},
                         **{key: _count(bbo.get(key)) for key in ("bid_size", "ask_size", "bid_orders", "ask_orders")}}
        for key in ("bid_implicit_size", "ask_implicit_size"):
            if key in bbo:
                result["bbo"][key] = _count(bbo[key])
        for key in ("bid_time", "ask_time"):
            if key in bbo:
                result["bbo"][key] = _text(bbo[key])
        if "lean_price" in bbo:
            result["bbo"]["lean_price"] = _price(bbo["lean_price"])
        trades = sections["trades"]
        result["trades"].update({key: _count(trades.get(key)) for key in ("incremental_count", "snapshot_count", "partial_count")})
        trade = trades.get("last_trade")
        if trade is not None:
            if not isinstance(trade, Mapping):
                raise ValueError("invalid_last_trade")
            aggressor = trade.get("aggressor")
            if aggressor is not None and (type(aggressor) is not int or aggressor not in (1, 2)):
                raise ValueError("invalid_trade_aggressor")
            if type(trade.get("is_snapshot")) is not bool or _ns(trade.get("received_ns")) is None:
                raise ValueError("invalid_last_trade")
            result["trades"]["last_trade"] = {
                "trade_price": _price(trade.get("trade_price")), "trade_size": _count(trade.get("trade_size")),
                "aggressor": aggressor, "received_ns": trade["received_ns"], "is_snapshot": trade["is_snapshot"],
                **{key: _text(trade.get(key), 256) for key in ("exchange_order_id", "aggressor_exchange_order_id", "trade_time")},
                **{key: _count(trade.get(key)) for key in ("ssboe", "usecs", "source_ssboe", "source_usecs", "source_nsecs", "jop_ssboe", "jop_nsecs")},
            }
        reasons = quality.get("blocked_reasons", [])
        if not isinstance(reasons, list):
            raise ValueError("invalid_quality_reasons")
        result["quality"].update(stale=quality.get("stale") is not False,
                                blocked_reasons=[reason for reason in reasons[:128] if isinstance(reason, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", reason)],
                                available_for_observation=(quality.get("available_for_observation") is True
                                                           and value["status"] == "RECORDED"
                                                           and value["source_kind"] in {"historical_capture", "synthetic_fixture"}))
        if "operational" in value:
            result["operational"] = accept_operational(value["operational"])
        return result
    except (ValueError, TypeError, OverflowError, KeyError, RecursionError) as exc:
        result = unavailable(str(exc) if isinstance(exc, ValueError) else "invalid_summary_shape")
        result["status"] = "ERROR"
        return result


def project_summary(value: dict, *, observed_at: float | None = None, transport: str = "none",
                    transport_error: str | None = None, now_ns: int | None = None) -> dict:
    """Age a previously accepted summary without modifying the cached evidence."""
    result = deepcopy(value)
    now_ns = time.time_ns() if now_ns is None else now_ns
    if "operational" in result:
        result["operational"] = age_operational(result["operational"], now_ns)
    now = now_ns / 1_000_000_000
    observation_age = None if observed_at is None else max(0.0, now - observed_at)
    stale_telemetry = observation_age is None or observation_age > MAX_TELEMETRY_AGE_SECONDS or bool(transport_error)
    result["telemetry"] = {"transport": transport, "observed_at": observed_at,
                           "age_seconds": observation_age, "stale": stale_telemetry,
                           "error": transport_error}
    last = result["last_update"]
    received = last["received_ns"]
    age = None if received is None else (now_ns - int(received)) / 1_000_000_000
    last["age_seconds"] = age if age is not None and age >= 0 else None
    last["clock_anomaly"] = bool(last["clock_anomaly"] or (age is not None and age < 0))
    quality = result["quality"]
    quality["stale"] = bool(quality["stale"] or age is None or age > STALE_DATA_SECONDS or last["clock_anomaly"])
    reasons = list(quality["blocked_reasons"])
    if stale_telemetry:
        reasons.append("observer_telemetry_unavailable" if transport_error else "observer_telemetry_stale")
    if last["clock_anomaly"]:
        reasons.append("local_receive_clock_anomaly")
    quality["blocked_reasons"] = list(dict.fromkeys(reasons))
    quality["available_for_observation"] = bool(quality["available_for_observation"] and not stale_telemetry and not last["clock_anomaly"])
    return result
