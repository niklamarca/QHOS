"""Read-only runtime revision attestation.

A previous phase shipped new Brain code, confirmed the served HTML contained the
new panels, and still could not tell that the *running Python* predated the
change. The frontend and the interpreter are separate revisions and nothing
reported the difference.

This module records package source files at import time and detects subsequent
disk changes. This is startup provenance, not proof of every loaded bytecode
object or an external attestation:

* ``running`` is captured once at import — the revision this interpreter loaded;
* ``worktree`` is recomputed on demand — the revision on disk right now;
* they differ ⇒ ``stale_runtime``.

Informational only. Nothing here restarts, reloads or mutates anything, and no
secret, token, environment value or absolute path outside the repo is exposed.
"""
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ATTESTATION_SCHEMA = "qhos-runtime-attestation-v1"
ROUTE_SCHEMA_VERSION = "qhos-brain-v1"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BRAIN_ROOT = Path(__file__).resolve().parent

# Recomputing the worktree hash on every request would re-read the package for no
# reason; a few seconds of staleness in a staleness indicator is acceptable.
_WORKTREE_CACHE_TTL_S = 5.0
_worktree_cache: Optional[Tuple[float, str, int]] = None


def _iter_sources(root: Path) -> List[Path]:
    try:
        return sorted(
            p for p in root.rglob("*.py")
            if "__pycache__" not in p.parts and p.is_file()
        )
    except OSError:
        return []


def source_revision(root: Optional[Path] = None) -> Tuple[str, int]:
    """Content hash of the Brain package on disk, plus the file count.

    Independent of git, so it stays meaningful with uncommitted work — which is
    exactly the situation where a stale process is easiest to miss.
    """
    digest = hashlib.sha256()
    files = _iter_sources(root or _BRAIN_ROOT)
    count = 0
    for path in files:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        try:
            rel = path.relative_to(_REPO_ROOT).as_posix()
        except ValueError:
            rel = path.name
        digest.update(rel.encode("utf-8"))
        digest.update(hashlib.sha256(data).digest())
        count += 1
    return digest.hexdigest()[:16], count


def _git_dir(start: Path) -> Optional[Path]:
    """``.git`` of the checkout containing ``start``: the package root, its parent or
    grandparent (``<checkout>/qhos2/src``), never further up. A ``.git`` *file*
    (``gitdir: ...``, linked worktrees) is followed. QHOS-AUD-277."""
    for candidate in (start, start.parent, start.parent.parent):
        marker = candidate / ".git"
        try:
            if marker.is_dir():
                return marker
            if marker.is_file():
                text = marker.read_text(encoding="utf-8").strip()
                if text.startswith("gitdir:"):
                    target = Path(text.split(":", 1)[1].strip())
                    if not target.is_absolute():
                        target = candidate / target
                    if target.is_dir():
                        return target.resolve()
        except OSError:
            continue
    return None


def _resolve_ref(git_dir: Path, ref: str) -> str:
    bases = [git_dir]
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
        if common:
            bases.append((git_dir / common).resolve())
    except OSError:
        pass
    for base in bases:
        try:
            return (base / ref).read_text(encoding="utf-8").strip()[:12]
        except OSError:
            pass
        try:
            for line in (base / "packed-refs").read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:12]
        except OSError:
            pass
    return ""


def git_revision(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Short HEAD revision read straight from ``.git``. No subprocess.

    ``available`` is False with an ``error`` when there is no usable ``.git``
    (wheel install, missing ref); callers must not treat "" as a revision.
    """
    out: Dict[str, Any] = {"revision": "", "ref": "", "available": False, "error": None}
    git_dir = _git_dir(Path(repo_root) if repo_root is not None else _REPO_ROOT)
    if git_dir is None:
        out["error"] = "no_git_dir"
        return out
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError as exc:
        out["error"] = f"head_unreadable:{type(exc).__name__}"
        return out
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        out["ref"] = ref
        out["revision"] = _resolve_ref(git_dir, ref)
        out["available"] = bool(out["revision"])
        if not out["available"]:
            out["error"] = "ref_unresolved"
        return out
    out["revision"] = head[:12]
    out["ref"] = "detached"
    out["available"] = bool(head)
    if not head:
        out["error"] = "head_empty"
    return out


def _worktree_revision() -> Tuple[str, int]:
    global _worktree_cache
    now = time.time()
    if _worktree_cache and now - _worktree_cache[0] < _WORKTREE_CACHE_TTL_S:
        return _worktree_cache[1], _worktree_cache[2]
    rev, count = source_revision()
    _worktree_cache = (now, rev, count)
    return rev, count


def _process_start() -> float:
    try:
        import psutil  # type: ignore

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:
        # Import time is a lower bound on process start and needs no dependency.
        return _IMPORT_TIME


_IMPORT_TIME = time.time()
_IMPORT_SOURCE_REVISION, _IMPORT_SOURCE_FILES = source_revision()
_IMPORT_GIT = git_revision()


def running_revision() -> Dict[str, Any]:
    """Source identity at import time; does not attest later dynamic imports."""
    from .discovery.revision import policy_revision

    return {
        "source_revision": _IMPORT_SOURCE_REVISION,
        "source_files": _IMPORT_SOURCE_FILES,
        "git_revision": _IMPORT_GIT.get("revision") or "",
        "git_ref": _IMPORT_GIT.get("ref") or "",
        "git_available": bool(_IMPORT_GIT.get("available")),
        "git_error": _IMPORT_GIT.get("error"),
        "discovery_policy": policy_revision(),
        "route_schema_version": ROUTE_SCHEMA_VERSION,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "pid": os.getpid(),
        "process_start_ts": _process_start(),
        "loaded_at_ts": _IMPORT_TIME,
    }


def runtime_attestation() -> Dict[str, Any]:
    """Running vs worktree revision, with an explicit staleness verdict."""
    running = running_revision()
    worktree_rev, worktree_files = _worktree_revision()
    git_now = git_revision()

    drift: List[str] = []
    if worktree_rev != running["source_revision"]:
        drift.append("brain_source_changed_on_disk")
    # Two unavailable readings ("" == "") are not a match, and one unavailable
    # reading is not a move: compare only measured revisions.
    if (running["git_available"] and git_now.get("available")
            and (git_now.get("revision") or "") != running["git_revision"]):
        drift.append("git_head_moved")

    uptime = max(0.0, time.time() - float(running["process_start_ts"]))
    return {
        "schema": ATTESTATION_SCHEMA,
        "running_revision": running["source_revision"],
        "worktree_revision": worktree_rev,
        "stale_runtime": bool(drift),
        "drift": drift,
        "drift_reason": ", ".join(drift) or "running_matches_worktree",
        "running": running,
        "worktree": {
            "source_revision": worktree_rev,
            "source_files": worktree_files,
            "git_revision": git_now.get("revision") or "",
            "git_ref": git_now.get("ref") or "",
            "git_available": bool(git_now.get("available")),
        },
        "process_uptime_s": round(uptime, 1),
        "informational_only": True,
        "restart_control_exposed": False,
        "can_reload": False,
    }


def attestation_panel() -> Dict[str, Any]:
    """Compact rows for Laços. Read-only, no controls."""
    att = runtime_attestation()
    policy = att["running"]["discovery_policy"]
    return {
        "kind": "RUNTIME_REVISION",
        "running_revision": att["running_revision"],
        "worktree_revision": att["worktree_revision"],
        "stale_runtime": att["stale_runtime"],
        "drift_reason": att["drift_reason"],
        "git_revision": att["running"]["git_revision"],
        "discovery_policy": f"{policy['policy']} {policy['revision']}",
        "policy_config_hash": policy["config_hash"],
        "route_schema_version": att["running"]["route_schema_version"],
        "process_uptime_s": att["process_uptime_s"],
        "read_only": True,
    }
