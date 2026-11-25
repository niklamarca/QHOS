"""Canonical reference for every Brain configuration flag.

One table, one meaning per flag, and a safe-default audit that runs as a test
rather than living in a document that drifts.

``authority`` is the field that matters most: it says whether flipping the flag
changes what the system is *allowed* to do, as opposed to how much of it it
does. Anything marked ``authority=True`` is a deliberate human decision.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "qhos-brain-config-v1"

# name -> spec. `safe_default` is the value a production deployment should have;
# where it differs from `default` the reason is stated.
FLAGS: Dict[str, Dict[str, Any]] = {
    # --- lifecycle ---------------------------------------------------------
    "brain_enabled": {
        "type": "bool", "default": True, "safe_default": True,
        "purpose": "Start the Brain observation loop at boot.",
        "authority": True, "paid": False, "experimental": False,
        "note": "The subsystem is on by default, but every capability inside it is off.",
    },
    "brain_autonomy_level": {
        "type": "int", "default": 2, "safe_default": 2,
        "purpose": "Autonomy ladder. 0 observe, 1 advise, 2 investigate (read-only).",
        "authority": True, "paid": False, "experimental": False,
        "note": (
            "Frozen Architecture v1 ceiling is Level 2 / INVESTIGATE. "
            "Only non-bool integers 0, 1, and 2 are directly valid. "
            "Requested values above 2 are clamped to 2 and recorded as "
            "ARCHITECTURE_V1_LEVEL2_CEILING. Negative integers enforce 0 and "
            "record invalid_autonomy_level. Strings, floats, bools, null, "
            "mappings, and sequences are invalid and enforce Level 2 with a "
            "violation projected on brain.configuration_violation. "
            "Level 3 / Sandbox is never produced."
        ),
    },
    "brain_cycle_interval_s": {
        "type": "int", "default": 60, "safe_default": 60,
        "purpose": "Seconds between observation cycles.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_shadow_mode": {
        "type": "bool", "default": True, "safe_default": True,
        "purpose": "Historically intended to toggle shadow mode.",
        "authority": False, "paid": False, "experimental": False,
        "status": "NON_AUTHORITATIVE",
        "note": (
            "Not read. runtime.py constructs AutonomyPolicy(shadow=True) with the "
            "literal, so shadow mode cannot be turned off by configuration. "
            "Retained because removing it would suggest shadow mode became "
            "optional. See MD-09."
        ),
    },
    # --- executive budgets -------------------------------------------------
    "brain_max_concurrent_tasks": {
        "type": "int", "default": 8, "safe_default": 8,
        "purpose": "Executive task concurrency ceiling.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_max_retries": {
        "type": "int", "default": 2, "safe_default": 2,
        "purpose": "Task retry ceiling.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_max_tasks_per_goal": {
        "type": "int", "default": 32, "safe_default": 32,
        "purpose": "Task fan-out ceiling per goal.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_max_depth": {
        "type": "int", "default": 6, "safe_default": 6,
        "purpose": "Goal decomposition depth ceiling.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_graph_ephemeral_max": {
        "type": "int", "default": 48, "safe_default": 48,
        "purpose": "Ephemeral knowledge-graph node ceiling.",
        "authority": False, "paid": False, "experimental": False,
    },
    # --- cognition ---------------------------------------------------------
    "brain_cognitive_mode": {
        "type": "str", "default": "deterministic_only", "safe_default": "deterministic_only",
        "purpose": "Reasoning path. deterministic_only blocks the LLM branch entirely.",
        "authority": True, "paid": False, "experimental": False,
        "allowed": ["deterministic_only", "hybrid", "llm"],
    },
    "brain_cognitive_max_llm_calls": {
        "type": "int", "default": 3, "safe_default": 3,
        "purpose": "Runtime reasoner per-investigation call budget.",
        "authority": False, "paid": True, "experimental": False,
        "overlaps": "brain_llm_max_calls (eval CLI path). Same default, different consumer.",
    },
    "brain_cognitive_max_rounds": {
        "type": "int", "default": 4, "safe_default": 4,
        "purpose": "Reasoner round budget.",
        "authority": False, "paid": True, "experimental": False,
    },
    "brain_cognitive_max_tokens": {
        "type": "int", "default": 1200, "safe_default": 1200,
        "purpose": "Reasoner context token budget (what is assembled).",
        "authority": False, "paid": True, "experimental": False,
        "overlaps": "brain_llm_max_tokens is the provider request cap (what is sent).",
    },
    "brain_cognitive_context_files": {
        "type": "int", "default": 6, "safe_default": 6,
        "purpose": "Max files in a provider context packet.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_cognitive_context_lines": {
        "type": "int", "default": 80, "safe_default": 80,
        "purpose": "Max lines per file in a provider context packet.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_cognitive_context_logs": {
        "type": "int", "default": 20, "safe_default": 20,
        "purpose": "Max log rows in a provider context packet.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_cognitive_max_memory": {
        "type": "int", "default": 6, "safe_default": 6,
        "purpose": "Max memory records in a provider context packet.",
        "authority": False, "paid": False, "experimental": False,
    },
    # --- paid provider -----------------------------------------------------
    "brain_llm_api_enabled": {
        "type": "bool", "default": False, "safe_default": False,
        "purpose": "Master gate for paid Brain provider calls.",
        "authority": True, "paid": True, "experimental": False,
        "note": "Necessary but not sufficient; the mode and a per-run gate also apply.",
    },
    "brain_llm_explicit_eval": {
        "type": "bool", "default": False, "safe_default": False,
        "purpose": "Second gate required by brain.eval_cli for paid evaluation runs.",
        "authority": True, "paid": True, "experimental": False,
    },
    "brain_paid_replay_enabled": {
        "type": "bool", "default": False, "safe_default": False,
        "purpose": "Outermost gate for paid hybrid replay.",
        "authority": True, "paid": True, "experimental": False,
        "note": "Was declared but unread until the consolidation phase wired it.",
    },
    "brain_llm_provider": {
        "type": "str", "default": "anthropic", "safe_default": "anthropic",
        "purpose": "Provider selection. Only anthropic is implemented.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_llm_model": {
        "type": "str", "default": "", "safe_default": "",
        "purpose": "Model override; empty falls back to llm_model_alpha.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_llm_max_calls": {
        "type": "int", "default": 3, "safe_default": 3,
        "purpose": "Eval CLI per-run call budget.",
        "authority": False, "paid": True, "experimental": False,
        "overlaps": "brain_cognitive_max_llm_calls (runtime reasoner path).",
    },
    "brain_llm_max_tokens": {
        "type": "int", "default": 1200, "safe_default": 1200,
        "purpose": "Provider request max_tokens.",
        "authority": False, "paid": True, "experimental": False,
        "note": (
            "1200 truncated every response during the paid fault-generalization "
            "replay; that run passed 4000 explicitly. The default is unchanged "
            "because a low cap fails safe: it costs a wasted call, not a wrong "
            "conclusion, and TRUNCATED is an explicit output status."
        ),
    },
    "brain_llm_timeout_s": {
        "type": "float", "default": 20.0, "safe_default": 20.0,
        "purpose": "Provider HTTP timeout.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_live_llm_shadow_enabled": {
        "type": "bool", "default": False, "safe_default": False,
        "purpose": "Declares intent to allow live shadow LLM use.",
        "authority": False, "paid": True, "experimental": True,
        "status": "TELEMETRY_ONLY",
        "note": "Surfaced in snapshots; the observation cycle never calls a model regardless.",
    },
    # --- soak --------------------------------------------------------------
    "brain_live_shadow_soak_enabled": {
        "type": "bool", "default": False, "safe_default": False,
        "purpose": "Create a bounded read-only soak session at boot.",
        "authority": True, "paid": False, "experimental": False,
    },
    "brain_soak_max_duration_s": {
        "type": "int", "default": 60, "safe_default": 60,
        "purpose": "Soak session wall-clock ceiling.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_soak_max_sessions": {
        "type": "int", "default": 8, "safe_default": 8,
        "purpose": "Retained soak artifacts.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_soak_max_events": {
        "type": "int", "default": 4000, "safe_default": 4000,
        "purpose": "Observation ceiling per soak session.",
        "authority": False, "paid": False, "experimental": False,
    },
    "brain_soak_max_age_days": {
        "type": "int", "default": 7, "safe_default": 7,
        "purpose": "Soak artifact retention age.",
        "authority": False, "paid": False, "experimental": False,
    },
    # --- sandbox (capability exists, is not reachable at Level 2) ----------
    "brain_sandbox_max_commands": {
        "type": "int", "default": 12, "safe_default": 12,
        "purpose": "Sandbox command ceiling.",
        "authority": False, "paid": False, "experimental": True,
        "note": "Bounds a capability that SANDBOX_ENABLED=False makes unreachable.",
    },
    "brain_sandbox_max_duration_s": {
        "type": "int", "default": 180, "safe_default": 180,
        "purpose": "Sandbox wall-clock ceiling.",
        "authority": False, "paid": False, "experimental": True,
    },
    "brain_sandbox_max_retries": {
        "type": "int", "default": 1, "safe_default": 1,
        "purpose": "Sandbox retry ceiling.",
        "authority": False, "paid": False, "experimental": True,
    },
    "brain_sandbox_max_files_changed": {
        "type": "int", "default": 8, "safe_default": 8,
        "purpose": "Sandbox changed-file ceiling.",
        "authority": False, "paid": False, "experimental": True,
    },
    "brain_sandbox_max_diff_bytes": {
        "type": "int", "default": 100_000, "safe_default": 100_000,
        "purpose": "Sandbox diff size ceiling.",
        "authority": False, "paid": False, "experimental": True,
    },
    "brain_sandbox_command_timeout_s": {
        "type": "int", "default": 45, "safe_default": 45,
        "purpose": "Sandbox per-command timeout.",
        "authority": False, "paid": False, "experimental": True,
    },
}

# Not configuration: hardcoded because being flippable would itself be a risk.
HARDCODED: Dict[str, Dict[str, Any]] = {
    "SANDBOX_ENABLED": {
        "value": False, "where": "brain/sandbox.py",
        "why": "Level 3 requires a source change and a review, not an env var.",
    },
    "AutonomyPolicy(shadow=...)": {
        "value": True, "where": "brain/runtime.py",
        "why": "Shadow mode is not disableable by configuration. See MD-09.",
    },
}


def flags() -> Dict[str, Dict[str, Any]]:
    return {k: dict(v) for k, v in FLAGS.items()}


def authority_flags() -> List[str]:
    return sorted(k for k, v in FLAGS.items() if v.get("authority"))


def paid_flags() -> List[str]:
    return sorted(k for k, v in FLAGS.items() if v.get("paid"))


def non_authoritative_flags() -> List[str]:
    """Declared flags that do not actually control behaviour. Honesty surface."""
    return sorted(
        k for k, v in FLAGS.items()
        if v.get("status") in {"NON_AUTHORITATIVE", "TELEMETRY_ONLY"}
    )


def safe_default_audit(cfg: Optional[Any] = None) -> Dict[str, Any]:
    """Check the live config against the declared safe production defaults."""
    if cfg is None:
        from brain.local_config import config as cfg  # type: ignore

    unsafe: List[Dict[str, Any]] = []
    undeclared: List[str] = []
    for name, spec in FLAGS.items():
        if not hasattr(cfg, name):
            undeclared.append(name)
            continue
        actual = getattr(cfg, name)
        if actual != spec["safe_default"]:
            unsafe.append({
                "flag": name,
                "actual": actual,
                "safe_default": spec["safe_default"],
                "authority": bool(spec.get("authority")),
                "paid": bool(spec.get("paid")),
            })

    # The invariants a Level-2 Shadow RC must satisfy no matter what the flag
    # table says, expressed against live values rather than documentation.
    invariants = {
        "deterministic_mode": getattr(cfg, "brain_cognitive_mode", "") == "deterministic_only",
        "paid_calls_off": getattr(cfg, "brain_llm_api_enabled", True) is False,
        "explicit_eval_off": getattr(cfg, "brain_llm_explicit_eval", True) is False,
        "paid_replay_off": getattr(cfg, "brain_paid_replay_enabled", True) is False,
        "live_llm_shadow_off": getattr(cfg, "brain_live_llm_shadow_enabled", True) is False,
        "soak_off": getattr(cfg, "brain_live_shadow_soak_enabled", True) is False,
        "autonomy_at_most_2": _autonomy_at_most_2(getattr(cfg, "brain_autonomy_level", 99)),
        "sandbox_off": _sandbox_off(),
        "fable_not_default": _default_strategy() != "fable_derived",
    }
    return {
        "schema": SCHEMA,
        "flags_declared": len(FLAGS),
        "unsafe": unsafe,
        "undeclared_in_config": undeclared,
        "invariants": invariants,
        "ok": not unsafe and not undeclared and all(invariants.values()),
    }


def _autonomy_at_most_2(raw: Any) -> bool:
    try:
        from brain.policies import resolve_autonomy_level

        enforced, violation = resolve_autonomy_level(raw)
    except Exception:
        return False
    return (
        violation is None
        and isinstance(enforced, int)
        and not isinstance(enforced, bool)
        and enforced <= 2
    )


def _sandbox_off() -> bool:
    try:
        from brain.sandbox import SANDBOX_ENABLED

        return SANDBOX_ENABLED is False
    except Exception:
        return True


def _default_strategy() -> str:
    try:
        from brain.architecture import release_manifest

        return str(release_manifest().get("default_strategy") or "")
    except Exception:
        return "deterministic"
