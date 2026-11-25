"""Isolated Brain evaluation artifact paths.

Benchmark output is not market data and must not live under data_lake/.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from brain.local_config import config
from qhos2.config import Settings


EVAL_DIRNAME = ".qhos-brain"
EVAL_SUBDIR = "evaluations"


def state_root() -> Path:
    return Path(getattr(config, "brain_state_dir", None) or (Settings.from_environ().data_root / "brain" / "state"))


def evaluation_dir() -> Path:
    return state_root() / EVAL_SUBDIR


def default_artifact_path(name: str = "latest.json") -> Path:
    return evaluation_dir() / name


def is_production_data_path(path: Path) -> bool:
    parts = {p.lower() for p in Path(path).resolve().parts}
    return "data_lake" in parts


def latest_artifact() -> Optional[Path]:
    folder = evaluation_dir()
    if not folder.exists():
        return None
    files = [p for p in folder.glob("*.json") if p.is_file()]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def load_latest_artifact() -> Optional[Dict[str, Any]]:
    path = latest_artifact()
    if path is None:
        return None
    try:
        import json
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(blob, dict):
        return None
    dumped = json.dumps(blob)
    if "sk-ant-" in dumped.lower() and "sk-ant-fake" not in dumped.lower():
        return None
    return blob


def atomic_write_json(path: Path, payload: Dict[str, Any]) -> Path:
    """Replace dest only after a complete write. Crash mid-write keeps the last checkpoint."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    from brain.memory import _atomic_write
    _atomic_write(dest, payload)
    return dest


def resolve_artifact_path(requested: str = "") -> Path:
    root = state_root().resolve()
    if requested:
        dest = Path(requested)
        if not dest.is_absolute():
            dest = state_root() / dest
    else:
        dest = default_artifact_path()
    if not dest.resolve().is_relative_to(root):
        raise ValueError("artifact_outside_brain_state")
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest
