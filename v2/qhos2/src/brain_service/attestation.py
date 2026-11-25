"""Attest the code, safe configuration, and process actually serving Brain."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable

from brain.attest import git_revision


SCHEMA = "qhos-brain-service-attestation-v1"
_ROOT = Path(__file__).resolve().parent.parent


def _source_files(root: Path = _ROOT) -> Iterable[Path]:
    for package in ("brain", "brain_service"):
        folder = root / package
        if folder.is_dir():
            yield from sorted(
                path for path in folder.rglob("*.py")
                if "__pycache__" not in path.parts and path.is_file()
            )


def source_revision(root: Path = _ROOT) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for path in _source_files(root):
        try:
            data = path.read_bytes()
            relative = path.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(hashlib.sha256(data).digest())
        count += 1
    return digest.hexdigest()[:16], count


def safe_config(cfg: Any) -> Dict[str, Any]:
    state_dir = Path(getattr(cfg, "state_dir", _ROOT / ".qhos-brain"))
    runtime_dir = Path(getattr(cfg, "runtime_dir", state_dir / "run"))
    return {
        "host": str(getattr(cfg, "host", "127.0.0.1")),
        "port": int(getattr(cfg, "port", 8091)),
        "trader_http": str(getattr(cfg, "quant_base", "")),
        "trader_ws": str(getattr(cfg, "quant_ws_url", "")),
        "trader_ws_enabled": bool(getattr(cfg, "quant_ws_enabled", False)),
        "observe_timeout_s": float(getattr(cfg, "observe_timeout_s", 2.0)),
        "ws_open_timeout_s": float(getattr(cfg, "ws_open_timeout_s", 3.0)),
        "ws_receive_timeout_s": float(getattr(cfg, "ws_receive_timeout_s", 15.0)),
        "cycle_interval_s": int(getattr(cfg, "cycle_interval_s", 60)),
        "diagnostics_enabled": bool(getattr(cfg, "diagnostics_enabled", True)),
        "regression_profile": os.environ.get("BRAIN_REGRESSION_PROFILE", "qhos2-operational-v1"),
        "relight_enabled": bool(getattr(cfg, "relight_enabled", False)),
        "auto_relight": bool(getattr(cfg, "auto_relight", False)),
        "deployment_context": str(getattr(cfg, "deployment_context", "production")),
        "public_origin_configured": bool(getattr(cfg, "public_origin", "")),
        "voice_enabled": bool(getattr(cfg, "voice_enabled", False)),
        "voice_loopback_configured": bool(getattr(cfg, "voice_url", "")),
        "voice_timeout_s": float(getattr(cfg, "voice_timeout_s", 45.0)),
        "state_directory": state_dir.name,
        "runtime_directory": runtime_dir.name,
        "autonomy_level": 2,
        "shadow": True,
        "level3_enabled": False,
        "read_roots": {name: os.environ.get(name, "") for name in (
            "BRAIN_QUANT_LOGS", "BRAIN_QUANT_DATA_LAKE", "BRAIN_COMPUTER_C", "BRAIN_TRADER_WORKSPACE")},
    }


def config_revision(cfg: Any) -> str:
    raw = json.dumps(safe_config(cfg), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class RuntimeAttestor:
    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.loaded_at = time.time()
        self.running_source_revision, self.running_source_files = source_revision()
        self.running_git = git_revision()
        self.running_config = safe_config(cfg)
        self.running_config_revision = config_revision(cfg)

    def snapshot(self) -> Dict[str, Any]:
        worktree_revision, worktree_files = source_revision()
        git_now = git_revision()
        running_git_available = bool(self.running_git.get("available"))
        worktree_git_available = bool(git_now.get("available"))
        drift = []
        warnings = []
        if worktree_revision != self.running_source_revision:
            drift.append("brain_source_changed_on_disk")
        # Only measured revisions are compared; "" from an unavailable git is
        # neither a match nor a move (QHOS-AUD-277).
        if (running_git_available and worktree_git_available
                and (git_now.get("revision") or "") != (self.running_git.get("revision") or "")):
            drift.append("git_head_moved")
        if not running_git_available:
            warnings.append("git_revision_unavailable")
        expected = str(getattr(self.cfg, "expected_revision", "") or "")[:40]
        compared_against = ["source_revision"]
        running_candidates = {self.running_source_revision}
        if running_git_available:
            compared_against.append("git_revision")
            running_candidates.add(str(self.running_git.get("revision") or ""))
        if expected and not any(
            value and (value.startswith(expected) or expected.startswith(value))
            for value in running_candidates
        ):
            drift.append("running_revision_not_expected")
        expected_config = str(getattr(self.cfg, "expected_config_hash", "") or "")[:40]
        if expected_config and not self.running_config_revision.startswith(expected_config):
            drift.append("running_config_not_expected")
        resources: Dict[str, Any] = {}
        try:
            import psutil
        except ImportError:
            resources = {"error": "psutil_unavailable"}
        else:
            try:
                process = psutil.Process(os.getpid())
                resources = {
                    "rss_bytes": int(process.memory_info().rss),
                    "threads": int(process.num_threads()),
                    "open_files": len(process.open_files()),
                }
            except (OSError, ValueError, psutil.Error) as exc:
                resources = {"error": type(exc).__name__}
        return {
            "schema": SCHEMA,
            "loaded_code_state": "STALE" if drift else "MATCH",
            "stale_runtime": bool(drift),
            "drift": drift,
            "running": {
                "source_revision": self.running_source_revision,
                "source_files": self.running_source_files,
                "git_revision": self.running_git.get("revision") or "",
                "git_ref": self.running_git.get("ref") or "",
                "git_available": running_git_available,
                "git_error": self.running_git.get("error"),
                "config_revision": self.running_config_revision,
                "config": dict(self.running_config),
                "pid": os.getpid(),
                "uid": os.geteuid() if hasattr(os, "geteuid") else None,
                "gid": os.getegid() if hasattr(os, "getegid") else None,
                "python": platform.python_version(),
                "platform": sys.platform,
                "systemd_unit": str(getattr(self.cfg, "service_unit", "qhos2-brain.service")),
                "loaded_at": self.loaded_at,
                "resources": resources,
            },
            "worktree": {
                "source_revision": worktree_revision,
                "source_files": worktree_files,
                "git_revision": git_now.get("revision") or "",
                "git_ref": git_now.get("ref") or "",
                "git_available": worktree_git_available,
            },
            "expected": {
                "revision": expected or None,
                "config_revision": expected_config or None,
                "compared_against": compared_against,
            },
            "warnings": warnings,
            "process_uptime_s": round(max(0.0, time.time() - self.loaded_at), 3),
            "informational_only": True,
            "restart_control_exposed": False,
            "level3_enabled": False,
        }
