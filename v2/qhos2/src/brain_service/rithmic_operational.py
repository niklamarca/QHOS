"""Bounded operational extension; current observations never imply execution authority.

Pure module inherited from the V1 observation contract; in QHOS V2 this is the only copy
(QHOS-AUD-211), so no other consumer is kept in sync with it.
"""
from copy import deepcopy
from fractions import Fraction
import math
import re

from .feed_classification import evidence_from_operational, validate_declared

SCHEMA = "qhos.rithmic.operational.v1"
NODES = ("source", "capture", "normalize", "store", "reader", "consumers")
STATES = {"unknown", "configured", "connecting", "idle", "receiving", "processing", "recovering",
          "stale", "error", "disconnected", "recorded", "unavailable"}


def ns(value):
    if type(value) is not str or not value.isascii() or not value.isdecimal() or len(value) > 20:
        raise ValueError("invalid_operational_timestamp")
    return int(value)


def accept_operational(value):
    if type(value) is not dict or value.get("schema_version") != SCHEMA:
        raise ValueError("unsupported_operational_schema")
    if (value.get("source_kind") not in {"current_session", "fixture"}
            or value.get("provider") != "rithmic_protocol" or value.get("environment") != "Rithmic Test"
            or value.get("research_only") is not True or value.get("can_execute") is not False
            or value.get("book_valid") is not False or value.get("available_as_live") is not False):
        raise ValueError("operational_guard_violation")
    observed = ns(value["observed_ns"])
    def tree(item, depth=0, budget=[0]):
        budget[0] += 1
        if depth > 12 or budget[0] > 10000 or type(item) is str and len(item) > 2048:
            raise ValueError("operational_size_limit")
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str or len(key) > 100:
                    raise ValueError("invalid_operational_field")
                tree(child, depth+1, budget)
        elif type(item) is list:
            for child in item:
                tree(child, depth+1, budget)
    tree(value)
    for key in ("run_id", "session_id"):
        if type(value[key]) is not str or re.fullmatch(r"[A-Za-z0-9_:.-]{1,160}", value[key]) is None:
            raise ValueError("invalid_operational_identity")
    if type(value["freshness_ms"]) is not int or not 100 <= value["freshness_ms"] <= 12000:
        raise ValueError("invalid_operational_freshness")
    if type(value["capture_finished"]) is not bool:
        raise ValueError("invalid_operational_lifecycle")
    persistence = value["persistence"]
    for section, flag in (("connection", "connected"), ("subscription", "active")):
        if type(value[section][flag]) is not bool:
            raise ValueError("invalid_operational_connection")
        if value["capture_finished"] and value[section][flag]:
            raise ValueError("closed_operational_connection")
    if value["subscription"]["active"] and not value["connection"]["connected"]:
        raise ValueError("subscription_without_connection")
    if (persistence.get("state") not in {"WAITING", "RECORDING", "CLOSED", "ERROR"}
            or type(persistence.get("write_confirmed")) is not bool or persistence.get("verified") is not False):
        raise ValueError("invalid_operational_persistence")
    if value["quality"].get("available_as_live") is not False:
        raise ValueError("operational_guard_violation")
    session = value.get("session")
    if session is not None and (type(session) is not dict or any(type(session.get(k)) not in (str, type(None)) for k in ("system_name", "plant", "endpoint"))
                                or type(session.get("login_confirmed")) is not bool):
        raise ValueError("invalid_operational_session_evidence")
    # The classification is derived from the evidence the payload carries; a declared one must equal it.
    derived_classification = validate_declared(value.get("feed_classification"), evidence_from_operational(value))
    for price in [value["bbo"].get(k) for k in ("bid_price", "ask_price", "lean_price")] + [(value["trades"].get("last_trade") or {}).get("trade_price")]:
        if price is None:
            continue
        number = float.fromhex(price["binary64_hex"])
        if not math.isfinite(number) or price["decimal"] != repr(number):
            raise ValueError("operational_price_precision_mismatch")
        ticks, tick, alignment = price["ticks"], price["tick_size"], price["alignment"]
        if alignment not in {"ON_GRID", "OFF_GRID", "UNKNOWN_REFERENCE", "NOT_APPLICABLE"}:
            raise ValueError("invalid_operational_price_alignment")
        if tick is not None and (not math.isfinite(float(tick)) or float(tick) <= 0):
            raise ValueError("invalid_operational_tick")
        ratio = Fraction(price["decimal"]) / Fraction(tick) if tick is not None else None
        if alignment == "ON_GRID":
            if type(ticks) is not str or ratio is None or ratio != int(ticks):
                raise ValueError("invalid_operational_ticks")
        elif ticks is not None or alignment == "OFF_GRID" and (ratio is None or ratio.denominator == 1):
            raise ValueError("invalid_operational_ticks")
    nodes = value["nodes"]
    if type(nodes) is not dict or set(nodes) != set(NODES):
        raise ValueError("invalid_operational_nodes")
    for node in nodes.values():
        if type(node) is not dict or node.get("state") not in STATES:
            raise ValueError("invalid_operational_node")
        if type(node.get("count")) is not int or not 0 <= node["count"] <= 2 ** 53 - 1:
            raise ValueError("invalid_operational_count")
        if node.get("last_activity_ns") is not None and ns(node["last_activity_ns"]) > observed:
            raise ValueError("invalid_operational_clock")
        activity = node.get("activity")
        if activity is not None:
            start, end = ns(activity["start_ns"]), ns(activity["end_ns"])
            if (not 0 < end-start <= 5_000_000_000 or end > observed or type(activity["count"]) is not int
                    or not 0 <= activity["count"] <= node["count"]):
                raise ValueError("invalid_operational_window")
    if type(value["edges"]) is not list or len(value["edges"]) > 5:
        raise ValueError("invalid_operational_edges")
    allowed = set(zip(NODES, NODES[1:]))
    for edge in value["edges"]:
        if (edge.get("from"), edge.get("to")) not in allowed or edge.get("observed") is not True:
            raise ValueError("invalid_operational_edge")
    result = deepcopy(value)
    result["feed_classification"] = derived_classification
    return result


def age_operational(value, now_ns):
    result = accept_operational(value)
    age = (now_ns-ns(result["observed_ns"])) / 1_000_000
    stale = not math.isfinite(age) or age < 0 or age > result["freshness_ms"]
    if stale:
        for node in result["nodes"].values():
            node.update(state="stale", activity=None)
        result["edges"] = []
        result["quality"]["stale"] = True
        result["connection"].update(state="UNKNOWN", connected=False)
        result["subscription"].update(state="UNKNOWN", active=False)
    return result
