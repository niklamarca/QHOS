"""Legacy guarded Quant relight adapter.

The Linux Level-2 Shadow profile never constructs or calls this adapter unless
it is explicitly enabled. It remains for the old Windows access profile only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from brain_service.config import ServiceConfig, load_config
from brain_service.supervisor import QhosSupervisor, SupervisorConfig

CONFIRM_TOKEN = "RELIGHT_QUANT"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


class RelightController:
    def __init__(
        self,
        cfg: Optional[ServiceConfig] = None,
        *,
        supervisor: Optional[QhosSupervisor] = None,
        dry_run: bool = False,
    ) -> None:
        self.cfg = cfg or load_config()
        self.supervisor = supervisor
        if self.supervisor is None and self.cfg.relight_enabled:
            self.supervisor = QhosSupervisor(
                SupervisorConfig(
                    root=self.cfg.target_root,
                    state_dir=self.cfg.state_dir,
                    health_url=f"{self.cfg.quant_base}/api/health",
                    busy_url=f"{self.cfg.quant_base}/api/busy",
                    request_timeout_s=self.cfg.observe_timeout_s,
                    dry_run=bool(dry_run or self.cfg.relight_dry_run),
                )
            )
        self.last: Dict[str, Any] = {}

    def status(self) -> Dict[str, Any]:
        state = dict(self.supervisor.state) if self.supervisor is not None else {}
        return {
            "enabled": bool(self.cfg.relight_enabled),
            "auto": bool(self.cfg.auto_relight),
            "dry_run": bool(self.supervisor.config.dry_run) if self.supervisor is not None else False,
            "mechanism": "guarded_legacy_adapter" if self.cfg.relight_enabled else "disabled_level2_shadow",
            "force_allowed": False,
            "level3": False,
            "phase": state.get("phase"),
            "last_reason": state.get("last_reason"),
            "consecutive_failures": state.get("consecutive_failures"),
            "cooldown_until": state.get("cooldown_until"),
            "last_restart_returncode": state.get("last_restart_returncode"),
            "confirm_token": CONFIRM_TOKEN,
        }

    def consider(self, *, quant_reachable: bool = False) -> Dict[str, Any]:
        """Observe healthy cycles too, resetting failures and verifying recovery."""
        if not self.cfg.relight_enabled or not self.cfg.auto_relight:
            return {"ok": False, "reason": "auto_relight_off", "acted": False}
        if self.supervisor is None:
            return {"ok": False, "reason": "relight_adapter_unavailable", "acted": False}
        previous_attempts = len(self.supervisor.state.get("restart_attempts") or [])
        state = self.supervisor.tick()
        self.last = state
        acted = len(state.get("restart_attempts") or []) > previous_attempts
        return {"ok": True, "reason": state.get("last_reason"), "acted": acted, "state": state}

    def request(self, *, confirm: str, reason: str = "explicit_brain_request") -> Dict[str, Any]:
        if not self.cfg.relight_enabled:
            return {"ok": False, "reason": "relight_disabled", "accepted": False}
        if str(confirm or "") != CONFIRM_TOKEN:
            return {
                "ok": False,
                "reason": "confirm_required",
                "accepted": False,
                "expected": CONFIRM_TOKEN,
            }
        if self.supervisor is None:
            return {"ok": False, "reason": "relight_adapter_unavailable", "accepted": False}
        state = self.supervisor.request_restart(reason=reason)
        self.last = state
        accepted = bool(state.get("accepted"))
        return {
            "ok": accepted,
            "reason": state.get("request_reason") or state.get("last_reason"),
            "accepted": accepted,
            "state": {
                "phase": state.get("phase"),
                "last_reason": state.get("last_reason"),
                "last_restart_returncode": state.get("last_restart_returncode"),
            },
        }
