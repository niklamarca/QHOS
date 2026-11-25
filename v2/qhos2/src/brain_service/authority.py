"""What this process is allowed to do. Linux Level-2 Shadow is read-only."""
from __future__ import annotations

from typing import Any, Dict, Tuple

CAN_READ: Tuple[str, ...] = (
    "Quant GET /api/health, /api/status, /api/truth, /api/busy",
    "data-lake JSON the Brain already reads (CURRENT_STATE, autonomy, committee)",
    "error log tail and git porcelain of the observed repo",
    "own memory, audit, view model and Discovery state",
    "complete runtime logs with resumable cursors and automatic investigations",
    "user-authorized filesystem roots through paginated read-only browsing; no automatic computer scan",
)
CAN_DO: Tuple[str, ...] = (
    "run Discovery on the collected snapshot",
    "serve the Level-2 view model on its own port",
    "restart *this* process without touching Quant",
)
CANNOT: Tuple[str, ...] = (
    "run QHOS_RESTART_FORCE.bat or skip the busy preflight",
    "POST/PUT/PATCH/DELETE application routes on the Quant server",
    "bind a QuantHPCSystem instance as owner",
    "enable Level 3 or the sandbox",
    "submit, modify or cancel an order",
    "change risk, OMS, kill-switch or autonomy",
    "call a paid model without the existing explicit gate",
    "apply, deploy, merge or push code",
)

# Methods the observer may not send to the Trader.
FORBIDDEN_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
FORBIDDEN_QUANT_PATHS = frozenset({
    "/api/restart",
    "/restart",
    "/api/apply",
    "/api/deploy",
    "/api/trade",
})
ALLOWED_QUANT_GET_PATHS = (
    "/api/health",
    "/api/status",
    "/api/truth",
    "/api/busy",
    "/api/qhos2/snapshot",
)


def authority_card(cfg: Any = None) -> Dict[str, Any]:
    relight = bool(getattr(cfg, "relight_enabled", False))
    can_do = list(CAN_DO)
    cannot = list(CANNOT)
    if relight:
        can_do.append("use the explicitly configured guarded Trader relight adapter")
    else:
        cannot.append("restart or control qhos-trader.service from the Level-2 Linux shadow")
    return {
        "process": "qhos-brain-service",
        "level": 2,
        "level_name": "INVESTIGATE",
        "liveness_ops": True,
        "recommendation_is_not_authorization": True,
        "can_read": list(CAN_READ),
        "can_do": can_do,
        "cannot": cannot,
        "allowed_quant_get_paths": list(ALLOWED_QUANT_GET_PATHS),
        "forbidden_quant_paths": sorted(FORBIDDEN_QUANT_PATHS),
        "forbidden_http_methods": sorted(FORBIDDEN_HTTP_METHODS),
        "quant_restart_from_brain": relight,
        "quant_restart_mechanism": "guarded_legacy_adapter" if relight else "disabled_level2_shadow",
        "quant_force_restart_from_brain": False,
    }
