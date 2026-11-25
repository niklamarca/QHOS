"""Read-only filesystem adapters over the observed qhos2 package
(brain.local_config.config.root_dir: QHOS2_BRAIN_CODE_ROOT, else ../qhos2 next to this package).

Live registry/HPC state is supplied by the service's injected HTTP observer,
never by importing the managed application into the Brain process.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List

from brain.local_config import config
from brain.local_logger import get_logger

log = get_logger("Brain.Perception")


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        import json
        blob = json.loads(path.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        # Absent or corrupt file only; MemoryError and friends propagate (QHOS-AUD-288).
        return {}


def _tail_text(path: Path, n: int = 20) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return [ln[:240] for ln in lines[-n:] if ln.strip()]
    except OSError:
        return []


def _git(args: List[str]) -> Dict[str, Any]:
    """{"stdout", "available", "error"}: no git or a non-repository is not "clean" (QHOS-AUD-275)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(config.root_dir),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"stdout": "", "available": False, "error": type(exc).__name__}
    if proc.returncode != 0:
        return {"stdout": "", "available": False, "error": f"git_exit_{proc.returncode}"}
    return {"stdout": (proc.stdout or "")[:4000], "available": True, "error": None}


def observe_registry() -> Dict[str, Any]:
    """Unavailable without an injected live observer; no invented module state."""
    return {}


def observe_truth() -> Dict[str, Any]:
    path = Path(config.quant_data_lake_dir) / "governance" / "CURRENT_STATE.json"
    return _read_json(path)


def observe_trading_autonomy() -> Dict[str, Any]:
    """Trading ladder, not Brain autonomy. Read-only."""
    path = Path(config.quant_data_lake_dir) / "autonomy.json"
    blob = _read_json(path)
    if blob:
        return {"mode": blob.get("mode"), "reason": blob.get("reason"), "source": "disk"}
    return {}


def observe_committee() -> Dict[str, Any]:
    lake = Path(config.quant_data_lake_dir)
    loop = _read_json(lake / "committee_loop.json")
    state = _read_json(lake / "orchestra_committee.json")
    return {
        "loop_running": bool(loop.get("running")),
        "loop_wanted": bool(loop.get("wanted")) if "wanted" in loop else bool(loop.get("running")),
        "round": int(loop.get("round") or 0),
        "queued": len(state.get("queue") or []) if isinstance(state.get("queue"), list) else 0,
    }


def observe_resources() -> Dict[str, Any]:
    try:
        import psutil
    except ImportError:
        return {"error": "psutil_unavailable"}
    try:
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": float(psutil.cpu_percent(interval=None)),
            "ram_percent": float(vm.percent),
            "ram_available_gb": round(vm.available / 1e9, 2),
        }
    except (OSError, ValueError, psutil.Error) as exc:
        return {"error": type(exc).__name__}


def observe_git() -> Dict[str, Any]:
    head = _git(["log", "-5", "--pretty=format:%h %s"])
    porcelain = _git(["status", "--porcelain"])
    commits = [ln.strip() for ln in head["stdout"].splitlines() if ln.strip()][:5]
    dirty = [ln.strip() for ln in porcelain["stdout"].splitlines() if ln.strip()][:30]
    return {"available": bool(head["available"] and porcelain["available"]),
            "error": head["error"] or porcelain["error"],
            "recent_commits": commits,
            "dirty_n": len(dirty) if porcelain["available"] else None,
            "dirty_sample": dirty[:8]}


def observe_errors() -> List[str]:
    return _tail_text(Path(config.quant_logs_dir) / "errors.log", 12)


def observe_hpc() -> Dict[str, Any]:
    """Live HPC metrics require the service's HTTP observer."""
    return {}


def collect_observation() -> Dict[str, Any]:
    """One cheap, read-only world snapshot. Safe to call from the dashboard."""
    return {
        "registry": observe_registry(),
        "application_ready": False,
        "truth": observe_truth(),
        "trading_autonomy": observe_trading_autonomy(),
        "committee": observe_committee(),
        "resources": observe_resources(),
        "git": observe_git(),
        "error_tail": observe_errors(),
        "hpc": observe_hpc(),
    }
