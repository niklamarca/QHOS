"""DISCOVERY_POLICY_G1.1 — a proposal, off by default.

The frozen G1 matrix found the right root for 27 of 28 detectable faults and
opened an investigation for 22. The six-fault gap is not random: every one of
them is a candidate whose root fingerprint extracted nothing concrete, so the
``root_signature`` feature contributed exactly 0.0 of its 0.14 weight and the
candidate could not reach the 0.42 threshold on its first cycle.

That is a real modelling error rather than a threshold that happens to be too
high. ``root_signature`` is meant to reward *evidence quality*, and it currently
measures *message parseability*. A registry entry saying ``healthy=false`` and
an attestation saying ``running != deployed`` are the highest-quality evidence
in the system — the system of record asserting its own state directly — and both
score zero on it, because there is no exception name to extract from a status
word.

G1.1 makes three changes and nothing else. Each is tied to a specific matrix
failure and each is measured before and after:

1. ``authoritative_state``: a non-concrete root from a source that directly
   asserts a component's state earns half credit on ``root_signature``.
   Targets F02a, F08a, F08b.
2. dotted lowercase exception extraction, so ``socket.gaierror`` is recognised
   as an exception rather than falling back to a message skeleton.
   Targets F03b.
3. pending severity maturity: a state promoted on independent evidence in its
   first observed cycle is reported at ``medium`` rather than ``high`` until a
   second cycle confirms it. Targets the ``pending_duration_s = 0.0`` finding.

Nothing here changes the promotion threshold. Lowering 0.42 would have raised
the same six candidates without saying why any of them deserved it, and would
have raised everything else with them.

The policy is disabled unless a caller opts in via ``g11_active()``. G1 evidence
is never overwritten: the matrix is run under both and both artifacts are kept.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Mapping

POLICY_NAME = "DISCOVERY_POLICY_G1.1"
POLICY_REVISION = "g1.1-proposal"
STATUS = "PROPOSED_NOT_ACTIVE"

# Sources whose rows are a direct assertion about a component's state by the
# system of record, rather than a parsed human-readable message.
AUTHORITATIVE_SOURCES = frozenset({"registry", "attestation"})

# Credit given to an authoritative non-concrete root on the root_signature
# feature. Half, not full: the source is trustworthy but the row still carries
# no discriminator that could distinguish two different causes.
AUTHORITATIVE_ROOT_SIGNATURE_VALUE = 0.5

# Cycles a pending state must be observed in before an evidence-based promotion
# is reported at high severity.
MATURITY_MIN_CYCLES_FOR_HIGH = 2

# Dotted lowercase exception names: socket.gaierror, ssl.SSLError, and similar.
DOTTED_EXCEPTION_RE = re.compile(
    r"\b([a-z][a-z0-9_]{1,30}\.[A-Za-z][A-Za-z0-9_]{2,40}"
    r"(?:error|exception|timeout|failure|fault))\b",
    re.I,
)

_active = False


@contextmanager
def g11_active() -> Iterator[None]:
    """Enable the proposal for the duration of the block. Never global."""
    global _active
    prior = _active
    _active = True
    try:
        yield
    finally:
        _active = prior


def is_active() -> bool:
    return _active


# ---------------------------------------------------------------------------
# Hooks. Each returns the G1 value unchanged when the proposal is inactive.
# ---------------------------------------------------------------------------

def root_signature_value(row: Mapping[str, Any], g1_value: float) -> float:
    if not _active or g1_value:
        return g1_value
    if not row.get("authoritative_source"):
        return g1_value
    return AUTHORITATIVE_ROOT_SIGNATURE_VALUE


def extract_dotted_exception(text: str) -> str:
    if not _active:
        return ""
    match = DOTTED_EXCEPTION_RE.search(str(text or ""))
    return match.group(1) if match else ""


def pending_severity(state: Mapping[str, Any], g1_severity: str) -> str:
    """Hold severity at medium for an unconfirmed first-cycle promotion."""
    if not _active or g1_severity != "high":
        return g1_severity
    if int(state.get("cycles_pending") or 0) >= MATURITY_MIN_CYCLES_FOR_HIGH:
        return g1_severity
    if state.get("dependent_impact") or state.get("missed_sla"):
        return g1_severity
    return "medium"


def policy_config() -> Dict[str, Any]:
    from .policy_g1 import policy_config as g1_config

    return {
        "inherits": g1_config(),
        "authoritative_sources": sorted(AUTHORITATIVE_SOURCES),
        "authoritative_root_signature_value": AUTHORITATIVE_ROOT_SIGNATURE_VALUE,
        "maturity_min_cycles_for_high": MATURITY_MIN_CYCLES_FOR_HIGH,
        "dotted_exception_extraction": True,
        "promotion_threshold_changed": False,
        "score_weights_changed": False,
    }


def policy_bundle() -> Dict[str, Any]:
    raw = json.dumps(policy_config(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "policy": POLICY_NAME,
        "revision": POLICY_REVISION,
        "status": STATUS,
        "config_hash": hashlib.sha256(raw).hexdigest()[:16],
        "active_by_default": False,
        "changes": [
            "authoritative_state: half root_signature credit for registry/attestation "
            "rows that assert a component state directly",
            "dotted lowercase exception extraction (socket.gaierror)",
            "pending severity maturity: first-cycle evidence promotion reported medium",
        ],
        "does_not_change": [
            "promotion threshold (0.42)",
            "score weights",
            "whether independent evidence can override UNKNOWN_PENDING",
        ],
        "targets": {
            "F02a": "authoritative_state",
            "F08a": "authoritative_state",
            "F08b": "authoritative_state",
            "F03b": "dotted exception extraction",
            "pending_duration_zero": "severity maturity",
        },
    }
