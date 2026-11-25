"""Named cognitive strategies and the contract they share.

A strategy decides *how* an investigation reasons. It never decides what the
Brain is allowed to do: permissions, authority, trading and deployment are
properties of the autonomy policy, and no strategy can widen them. That
separation is the point of this module — a strategy is swappable precisely
because swapping it cannot change the blast radius.

Fable is EXPERIMENTAL and is imported lazily. Importing this registry must not
drag in ``brain.fable``, so that the experimental package stays removable.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

STRATEGY_DETERMINISTIC = "deterministic"
STRATEGY_CURRENT_HYBRID = "current_hybrid"
STRATEGY_REAL_HYBRID = "real_hybrid"
STRATEGY_FABLE_DERIVED = "fable_derived"

DEFAULT_RUNTIME_STRATEGY = STRATEGY_DETERMINISTIC

# Every strategy result must carry these keys, whatever produced it, so that
# evaluation and the UI can read any arm without special-casing.
RESULT_CONTRACT: Tuple[str, ...] = (
    "strategy",          # which arm produced this
    "hypotheses",        # ranked, each with evidence
    "confidence",        # numeric or an explicit absence marker
    "stop",              # StopDecision value
    "evidence",          # what was actually looked at
    "llm_calls",         # 0 for deterministic; never unreported
    "read_only",         # must be True at Level 2
)

# Invariants that hold for every strategy regardless of implementation.
STRATEGY_INVARIANTS: Tuple[str, ...] = (
    "may not change the autonomy level",
    "may not enable the sandbox or Level 3",
    "may not write production state",
    "may not submit or modify a trade",
    "may not call a paid provider without the provider gate",
    "must report its own llm_calls truthfully",
)

STRATEGIES: Dict[str, Dict[str, Any]] = {
    STRATEGY_DETERMINISTIC: {
        "status": "ACTIVE",
        "default": True,
        "paid": False,
        "requires_provider": False,
        "requires_fable": False,
        "detail": "Rule-based diagnosis. The only always-on arm.",
    },
    STRATEGY_CURRENT_HYBRID: {
        "status": "AVAILABLE_OPT_IN",
        "default": False,
        "paid": True,
        "requires_provider": True,
        "requires_fable": False,
        "detail": "Deterministic core plus a provider pass over the same evidence.",
    },
    STRATEGY_REAL_HYBRID: {
        "status": "AVAILABLE_OPT_IN",
        "default": False,
        "paid": True,
        "requires_provider": True,
        "requires_fable": False,
        "evidence_gated": True,
        "detail": (
            "Paid remote model on selected information-rich candidates. Value "
            "observed on 3 of 4 controlled cases; n is too small to promote."
        ),
    },
    STRATEGY_FABLE_DERIVED: {
        "status": "EXPERIMENTAL",
        "default": False,
        "paid": True,
        "requires_provider": True,
        "requires_fable": True,
        "detail": "FABLE-derived investigation loop. NO_MEASURED_VALUE to date.",
    },
}


def strategy_names() -> List[str]:
    return sorted(STRATEGIES)


def strategy_status(name: str) -> str:
    return str((STRATEGIES.get(name) or {}).get("status") or "UNKNOWN")


def default_strategy() -> str:
    return DEFAULT_RUNTIME_STRATEGY


def available_strategies(*, fable_available: Optional[bool] = None) -> List[str]:
    """Strategies that can actually be constructed in this deployment."""
    if fable_available is None:
        fable_available = _fable_available()
    return sorted(
        name for name, spec in STRATEGIES.items()
        if fable_available or not spec.get("requires_fable")
    )


def _fable_available() -> bool:
    try:
        import brain.fable.strategy  # noqa: F401
    except Exception:
        return False
    return True


def load_fable_strategy() -> Any:
    """Construct the experimental Fable strategy, or raise a clear error.

    Deliberately not imported at module scope: the deterministic core must load
    with ``brain.fable`` absent.
    """
    from brain.fable.strategy import FableDerivedStrategy

    return FableDerivedStrategy()


def result_is_contract_compliant(result: Any) -> Dict[str, Any]:
    """Whether a strategy result carries the shared contract fields."""
    row = result if isinstance(result, dict) else getattr(result, "__dict__", {}) or {}
    if hasattr(result, "to_dict"):
        try:
            row = result.to_dict()
        except Exception:
            pass
    missing = [k for k in RESULT_CONTRACT if k not in row]
    return {
        "compliant": not missing,
        "missing": missing,
        "read_only": bool(row.get("read_only", True)),
    }


def __getattr__(name: str) -> Any:
    # Preserve `from brain.strategies import FableDerivedStrategy` for existing
    # callers without importing Fable when the registry is merely inspected.
    if name == "FableDerivedStrategy":
        from brain.fable.strategy import FableDerivedStrategy

        return FableDerivedStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DEFAULT_RUNTIME_STRATEGY",
    "RESULT_CONTRACT",
    "STRATEGIES",
    "STRATEGY_CURRENT_HYBRID",
    "STRATEGY_DETERMINISTIC",
    "STRATEGY_FABLE_DERIVED",
    "STRATEGY_REAL_HYBRID",
    "STRATEGY_INVARIANTS",
    "available_strategies",
    "default_strategy",
    "load_fable_strategy",
    "result_is_contract_compliant",
    "strategy_names",
    "strategy_status",
]
