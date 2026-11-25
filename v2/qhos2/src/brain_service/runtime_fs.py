"""Private runtime directories for the standalone service."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterable


PRIVATE_DIR_MODE = 0o750


def _prepare_one(path: Path) -> Path:
    target = Path(path)
    if target.is_symlink():
        raise RuntimeError(f"runtime directory may not be a symlink: {target.name}")
    target.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if not target.is_dir():
        raise RuntimeError(f"runtime path is not a directory: {target.name}")
    if os.name != "nt":
        details = target.stat()
        if details.st_uid != os.geteuid():
            raise PermissionError(f"runtime directory is not owned by uid {os.geteuid()}: {target.name}")
        current = stat.S_IMODE(details.st_mode)
        if current != PRIVATE_DIR_MODE:
            os.chmod(target, PRIVATE_DIR_MODE)
        if stat.S_IMODE(target.stat().st_mode) != PRIVATE_DIR_MODE:
            raise PermissionError(f"runtime directory mode must be 0750: {target.name}")
    return target


def prepare_runtime_dirs(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Create and verify only Brain-owned directories, never Trader paths."""
    return tuple(_prepare_one(Path(path)) for path in paths)


def permission_status(path: Path) -> dict:
    target = Path(path)
    try:
        details = target.stat()
        mode = stat.S_IMODE(details.st_mode)
        return {
            "exists": target.is_dir(),
            "symlink": target.is_symlink(),
            "mode": f"{mode:04o}" if os.name != "nt" else "platform_managed",
            "owner_uid": details.st_uid if hasattr(details, "st_uid") else None,
            "owner_matches_process": details.st_uid == os.geteuid() if os.name != "nt" else True,
            "private": not target.is_symlink() and (os.name == "nt" or mode == PRIVATE_DIR_MODE),
        }
    except OSError:
        return {"exists": False, "symlink": False, "mode": None, "owner_uid": None,
                "owner_matches_process": False, "private": False}
