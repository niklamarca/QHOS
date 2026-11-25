"""Internal API payloads for the Control Tower and the Laços (Mapa) view.

Served by brain_service.server (FastAPI + WebSocket). Does not add GraphQL,
SSE, or a second server. (The V1 dashboard/control_tower.py does not exist in V2.)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import LACOS_SCHEMA, SCHEMA
from .control_plane import build_control_plane, heartbeat_state
from .cognitive import default_sides_view
from .evaluate import default_evaluation_view
from .health import evaluate
from .perception import collect_observation
from .runtime import peek_brain
from .sandbox import SandboxExecutor

_offline_snap: Dict[str, Any] = {}
_offline_ts = 0.0
_OFFLINE_TTL = 15.0


def lacos_contract(snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Laços overlay + inspector payload. Visualization only."""
    return build_control_plane(snapshot or {})


def _offline_payload(observation: Dict[str, Any], health: Dict[str, Any]) -> Dict[str, Any]:
    import time
    now = time.time()
    snap = {
        "schema": SCHEMA,
        "available": True,
        "running": False,
        "shadow": True,
        "cycles": 0,
        "health": health,
        "resources": observation.get("resources") or {},
        "incidents": [],
        "goals": [],
        "tasks": [],
        "orchestrator": {"name": "BrainOrchestrator", "not_hpc_orchestrator": True},
        "git": observation.get("git") or {},
        "trading_autonomy": observation.get("trading_autonomy") or {},
        "autonomy": {"level": 2, "level_name": "investigate", "shadow": True, "can_apply": False, "can_trade": False},
        "heartbeat": {
            "brain": heartbeat_state(
                running=False,
                last_cycle_at=None,
                interval_s=60,
                health_tone=str((health or {}).get("tone") or ""),
                now=now,
            ),
            "trader": "separate",
        },
        "metrics": {"cycles_total": 0, "observations_total": 0},
        "sandbox": SandboxExecutor(enabled=False).status(),
        "event_bus": {
            "kind": "in-process",
            "limitation": "Brain and dashboard share main.py; not Redis/Kafka",
        },
        "not_a_trading_path": True,
        "supervision_only": True,
        "offline": True,
        "last_observation_at": None,
        "audit_tail": [],
        "memory_tail": [],
        "relations": [],
        "proposals": [],
        "evaluation": default_evaluation_view(),
        "sides": default_sides_view(),
    }
    snap["lacos"] = build_control_plane(snap)
    return snap


def public_snapshot() -> Dict[str, Any]:
    """Dashboard-safe snapshot. Never raises. Works with Brain OFF."""
    global _offline_snap, _offline_ts
    try:
        runtime = peek_brain()
        if runtime is not None:
            return runtime.snapshot()
        import time
        now = time.time()
        if _offline_snap and now - _offline_ts < _OFFLINE_TTL:
            return _offline_snap
        observation = collect_observation()
        health = evaluate(observation)
        snap = _offline_payload(observation, health)
        _offline_snap, _offline_ts = snap, now
        return snap
    except Exception as exc:
        return {
            "schema": SCHEMA,
            "available": False,
            "running": False,
            "shadow": True,
            "error": str(exc)[:200],
            "not_a_trading_path": True,
            "lacos": {
                "schema": LACOS_SCHEMA,
                "host_view": "v-map",
                "overlay": {"nodes": [], "edges": [], "additive": True},
            },
        }


def attach_brain_state(state: Dict[str, Any]) -> None:
    """Attach onto the WebSocket payload. Isolated: failure leaves state intact."""
    try:
        state["brain"] = public_snapshot()
    except Exception:
        state["brain"] = {
            "schema": SCHEMA,
            "available": False,
            "not_a_trading_path": True,
        }


def view_model(snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stable read-only projection for the frontend (see brain/view_model.py).

    This is the surface a UI should bind to. The raw snapshot remains available
    for tooling, but its shape is internal and may change with the engine.
    """
    from brain.view_model import build_view

    return build_view(snapshot if snapshot is not None else public_snapshot())


def health_only() -> Dict[str, Any]:
    snap = public_snapshot()
    return {
        "schema": SCHEMA,
        "health": snap.get("health") or {},
        "running": bool(snap.get("running")),
        "heartbeat": snap.get("heartbeat") or {},
        "shadow": True,
    }


def tasks_only() -> Dict[str, Any]:
    snap = public_snapshot()
    lacos = snap.get("lacos") if isinstance(snap.get("lacos"), dict) else {}
    return {
        "schema": SCHEMA,
        "goals": snap.get("goals") or [],
        "tasks": snap.get("tasks") or [],
        "dag": lacos.get("dag") or {},
        "orchestrator": snap.get("orchestrator") or {},
    }


def audit_only() -> Dict[str, Any]:
    snap = public_snapshot()
    return {"schema": SCHEMA, "audit": snap.get("audit_tail") or []}


def apply_shadow_feedback(fingerprint: str, action: str, note: str = "") -> Dict[str, Any]:
    runtime = peek_brain()
    if runtime is None:
        return {"ok": False, "reason": "brain_offline", "change_request": None, "sandbox": None}
    return runtime.record_shadow_feedback(fingerprint, action, note=note)
