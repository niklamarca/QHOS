"""Ephemeral cognitive workspace. Not production data. Not curated memory."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict

from brain.eval_paths import EVAL_DIRNAME, atomic_write_json
from brain.local_config import BRAIN_ROOT_DIR, config


SCRATCH_SUBDIR = "scratch"


def scratch_root() -> Path:
    return Path(config.brain_state_dir) / SCRATCH_SUBDIR


def scratch_dir(investigation_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", str(investigation_id or "")):
        raise ValueError("invalid_investigation_id")
    dest = scratch_root() / str(investigation_id or "unknown")
    if not dest.resolve().is_relative_to(scratch_root().resolve()):
        raise ValueError("scratch_outside_root")
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def write_scratch(investigation_id: str, name: str, payload: Dict[str, Any]) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}(?:\.json)?", name):
        raise ValueError("invalid_scratch_name")
    dest = scratch_dir(investigation_id) / name
    if dest.suffix != ".json":
        dest = dest.with_suffix(".json")
    return atomic_write_json(dest, payload)


def is_isolated(path: Path) -> bool:
    return Path(path).resolve().is_relative_to(scratch_root().resolve())
