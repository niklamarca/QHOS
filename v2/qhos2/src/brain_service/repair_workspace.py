"""Reviewable repair staging, never a production writer or command interpreter."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
import time
import uuid

from brain_service.supervisor import atomic_json_write


ALLOWED_TOP = frozenset({
    "core", "data", "features", "orchestrator", "dashboard", "monitoring", "tests",
})
DENIED = frozenset({"config.py", "secrets.py", "credentials.py", "conftest.py"})
V2_SOURCE_FILES = frozenset({"atraso.py", "avaliador.py", "captura.py", "catalogo.py", "coletor.py",
    "conector.py", "desequilibrio.py", "eventos.py", "gravador.py", "historico.py", "inspecao.py",
    "leitura.py", "livro.py", "mercado.py", "operacao.py", "observabilidade.py", "rprotocol.py",
    "sessao.py", "sintetico.py", "pesquisa.py", "aprendizado.py"})
SUFFIXES = frozenset({".py", ".js", ".css", ".html"})
SCHEMA = "brain-repair-workspace-v1"
MAX_FILES = 40
MAX_SOURCE_BYTES = 1_000_000
MAX_MANIFEST_BYTES = 64_000
REQUIRED_STEPS = (
    "reproduce_failure",
    "candidate_tests",
    "independent_review",
    "source_unchanged",
    "guarded_restart_if_needed",
)
_MANIFEST_KEYS = frozenset({
    "schema", "id", "target_root", "created_at", "source_hashes",
    "status", "auto_apply", "required",
})
_WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _absolute(value, reason: str) -> Path:
    try:
        return Path(value).absolute()
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError(reason) from exc


def _is_redirect(path: Path) -> bool:
    """Recognise symlinks, junctions and other Windows reparse points."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        if not path.exists():
            return False
        attrs = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError as exc:
        raise ValueError("path_metadata_unavailable") from exc


def _reject_redirected_ancestors(path: Path, *, stop: Path | None = None) -> None:
    """Check the lexical chain before any resolve can erase a redirect."""
    current = path
    while True:
        if _is_redirect(current):
            raise ValueError("linked_path_refused")
        if stop is not None and current == stop:
            return
        if current.parent == current:
            return
        current = current.parent


def _safe_existing_dir(value, reason: str) -> Path:
    lexical = _absolute(value, reason)
    _reject_redirected_ancestors(lexical)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ValueError(reason) from exc
    _reject_redirected_ancestors(resolved)
    if not resolved.is_dir():
        raise ValueError(reason)
    return resolved


def _relative_source(relative) -> Path:
    try:
        rel = Path(relative)
    except (TypeError, ValueError, OSError) as exc:
        raise ValueError("unsafe_relative_path") from exc
    folded_parts = tuple(part.casefold() for part in rel.parts)
    if (
        rel.is_absolute()
        or not rel.parts
        or any(part in {"", ".", ".."} or part.startswith(".") for part in folded_parts)
    ):
        raise ValueError("unsafe_relative_path")
    name = rel.name.casefold()
    stem = rel.stem.casefold()
    if (
        not (folded_parts[0] in ALLOWED_TOP or (len(folded_parts) == 1 and name in V2_SOURCE_FILES))
        or name in DENIED
        or "secret" in stem
        or "credential" in stem
        or rel.suffix.casefold() not in SUFFIXES
    ):
        raise ValueError("path_requires_separate_review")
    return rel


def source_path(root, relative):
    """Resolve one allowlisted regular file without traversing redirects."""
    root = _safe_existing_dir(root, "invalid_root")
    rel = _relative_source(relative)
    lexical = root / rel
    _reject_redirected_ancestors(lexical, stop=root)
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError("invalid_source") from exc
    _reject_redirected_ancestors(resolved, stop=root)
    try:
        metadata = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise ValueError("invalid_source") from exc
    if not resolved.is_file() or metadata.st_size < 0 or metadata.st_size > MAX_SOURCE_BYTES:
        raise ValueError("invalid_source")
    return resolved


def _signature(metadata) -> tuple:
    return (
        metadata.st_size,
        getattr(metadata, "st_mtime_ns", None),
        getattr(metadata, "st_ctime_ns", None),
        getattr(metadata, "st_ino", None),
        getattr(metadata, "st_dev", None),
    )


def _stable_read_file(path: Path, *, maximum: int, boundary: Path) -> bytes:
    """Bound a read and reject replacement or mutation during the read."""
    _reject_redirected_ancestors(path, stop=boundary)
    try:
        path.resolve(strict=True).relative_to(boundary)
        before = path.stat(follow_symlinks=False)
        if not path.is_file() or before.st_size < 0 or before.st_size > maximum:
            raise ValueError("file_size_refused")
        with path.open("rb") as handle:
            raw = handle.read(maximum + 1)
        after = path.stat(follow_symlinks=False)
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("file_read_failed") from exc
    _reject_redirected_ancestors(path, stop=boundary)
    if len(raw) > maximum:
        raise ValueError("file_size_refused")
    if _signature(before) != _signature(after) or after.st_size != len(raw):
        raise ValueError("source_changed_during_read")
    return raw


def _read_source(root, relative) -> tuple[Path, bytes, str]:
    root = _safe_existing_dir(root, "invalid_root")
    path = source_path(root, relative)
    raw = _stable_read_file(path, maximum=MAX_SOURCE_BYTES, boundary=root)
    if source_path(root, relative) != path:
        raise ValueError("source_changed_during_read")
    return path, raw, hashlib.sha256(raw).hexdigest()


def _workspace_base(state_dir, *, create: bool) -> Path:
    state_lexical = _absolute(state_dir, "invalid_state_dir")
    _reject_redirected_ancestors(state_lexical)
    if create:
        state_lexical.mkdir(parents=True, exist_ok=True)
    state = _safe_existing_dir(state_lexical, "invalid_state_dir")
    base_lexical = state / "repair_workspaces"
    _reject_redirected_ancestors(base_lexical, stop=state)
    if create:
        base_lexical.mkdir(exist_ok=True)
    base = _safe_existing_dir(base_lexical, "repair_workspace_root_unavailable")
    if base.parent != state or base.name.casefold() != "repair_workspaces":
        raise ValueError("repair_workspace_root_invalid")
    return base


def _safe_workspace(workspace, *, state_dir=None) -> Path:
    lexical = _absolute(workspace, "invalid_workspace")
    expected_base = _workspace_base(state_dir, create=False) if state_dir is not None else None
    if expected_base is not None:
        try:
            relative = lexical.relative_to(expected_base)
        except ValueError as exc:
            raise ValueError("workspace_outside_state") from exc
        if len(relative.parts) != 1:
            raise ValueError("workspace_outside_state")
    elif lexical.parent.name.casefold() != "repair_workspaces":
        raise ValueError("workspace_root_unverified")
    if not _WORKSPACE_ID_RE.fullmatch(lexical.name.casefold()):
        raise ValueError("invalid_workspace_id")
    _reject_redirected_ancestors(lexical, stop=expected_base)
    workspace_path = _safe_existing_dir(lexical, "invalid_workspace")
    if expected_base is not None and workspace_path.parent != expected_base:
        raise ValueError("workspace_outside_state")
    if workspace_path.name.casefold() != lexical.name.casefold():
        raise ValueError("workspace_identity_changed")
    return workspace_path


def _validate_manifest(manifest, *, root: Path, workspace: Path) -> dict:
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("invalid_manifest_shape")
    try:
        json.dumps(manifest, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid_manifest_values") from exc
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("id") != workspace.name
        or manifest.get("target_root") != str(root)
        or manifest.get("status") != "PREPARED"
        or manifest.get("auto_apply") is not False
        or manifest.get("required") != list(REQUIRED_STEPS)
    ):
        raise ValueError("invalid_manifest_identity")
    created_at = manifest.get("created_at")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(created_at)
        or created_at <= 0
        or created_at > time.time() + 60
    ):
        raise ValueError("invalid_manifest_timestamp")
    source_hashes = manifest.get("source_hashes")
    if not isinstance(source_hashes, dict) or not 1 <= len(source_hashes) <= MAX_FILES:
        raise ValueError("invalid_manifest_sources")
    normalised: dict[str, str] = {}
    seen: set[str] = set()
    for relative, digest in source_hashes.items():
        if not isinstance(relative, str) or "\\" in relative:
            raise ValueError("invalid_manifest_source_path")
        rel = _relative_source(relative)
        canonical = rel.as_posix()
        if canonical != relative:
            raise ValueError("invalid_manifest_source_path")
        key = canonical.casefold()
        if key in seen:
            raise ValueError("duplicate_manifest_source")
        seen.add(key)
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError("invalid_manifest_hash")
        normalised[canonical] = digest
    return {**manifest, "source_hashes": normalised}


def _read_manifest(root: Path, workspace: Path) -> dict:
    manifest_path = workspace / "manifest.json"
    raw = _stable_read_file(
        manifest_path,
        maximum=MAX_MANIFEST_BYTES,
        boundary=workspace,
    )
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("manifest_unreadable") from exc
    return _validate_manifest(manifest, root=root, workspace=workspace)


def prepare(target_root, state_dir, paths):
    """Copy only explicitly reviewed files; never environment, data or secrets."""
    root = _safe_existing_dir(target_root, "invalid_root")
    try:
        requested = list(paths)
    except TypeError as exc:
        raise ValueError("choose_1_to_40_files") from exc
    if not 1 <= len(requested) <= MAX_FILES:
        raise ValueError("choose_1_to_40_files")

    sources: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for relative in requested:
        path = source_path(root, relative)
        canonical = path.relative_to(root).as_posix()
        key = os.path.normcase(str(path)).casefold()
        if key not in seen:
            seen.add(key)
            sources.append((canonical, path))
    if not sources:
        raise ValueError("choose_1_to_40_files")

    base = _workspace_base(state_dir, create=True)
    workspace = base / uuid.uuid4().hex
    workspace.mkdir(exist_ok=False)
    workspace = _safe_workspace(workspace, state_dir=state_dir)
    candidate_root = workspace / "candidate"
    candidate_root.mkdir()
    candidate_root = _safe_existing_dir(candidate_root, "invalid_candidate_root")

    hashes = {}
    for relative, initial_path in sources:
        source, raw, digest = _read_source(root, relative)
        if source != initial_path:
            raise ValueError("source_changed_during_preparation")
        destination = candidate_root / Path(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _reject_redirected_ancestors(destination, stop=candidate_root)
        destination.write_bytes(raw)
        _copied, copy_raw, copy_digest = _read_source(candidate_root, relative)
        if copy_digest != digest or copy_raw != raw:
            raise ValueError("candidate_copy_mismatch")
        hashes[relative] = digest

    record = {
        "schema": SCHEMA,
        "id": workspace.name,
        "target_root": str(root),
        "created_at": time.time(),
        "source_hashes": hashes,
        "status": "PREPARED",
        "auto_apply": False,
        "required": list(REQUIRED_STEPS),
    }
    atomic_json_write(workspace / "manifest.json", record)
    # Re-read the durable manifest before exposing the workspace to a caller.
    _read_manifest(root, workspace)
    return {**record, "workspace": str(workspace)}


def inspect_workspace(target_root, workspace, state_dir=None):
    """Re-read current sources independently; tests are not deployment approval."""
    root = _safe_existing_dir(target_root, "invalid_root")
    workspace_path = _safe_workspace(workspace, state_dir=state_dir)
    manifest = _read_manifest(root, workspace_path)
    candidate_root = _safe_existing_dir(workspace_path / "candidate", "invalid_candidate_root")
    if candidate_root.parent != workspace_path:
        raise ValueError("invalid_candidate_root")

    changes = []
    for relative, original_hash in manifest["source_hashes"].items():
        _current_path, _current_raw, current_hash = _read_source(root, relative)
        _candidate_path, _candidate_raw, candidate_hash = _read_source(candidate_root, relative)
        changes.append({
            "path": relative,
            "source_unchanged": current_hash == original_hash,
            "changed": candidate_hash != original_hash,
            "before": original_hash,
            "current": current_hash,
            "candidate": candidate_hash,
        })
    return {
        "id": manifest["id"],
        "files": changes,
        "source_unchanged": bool(changes) and all(row["source_unchanged"] for row in changes),
        "changed_files": sum(row["changed"] for row in changes),
        "deployment_authorized": False,
        "workspace_boundary_verified": state_dir is not None,
        "note": "Aguardando testes e revisão independente; nada foi aplicado ao Trader.",
    }


def _atomic_overlay(path: Path, raw: bytes, *, boundary: Path) -> None:
    _reject_redirected_ancestors(path, stop=boundary)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(raw)
        _reject_redirected_ancestors(temporary, stop=boundary)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def build_candidate(target_root, workspace, destination, state_dir=None):
    """Build a complete source-only candidate with an explicit, hashed overlay."""
    from brain_service.regression import prepare_mirror

    root = _safe_existing_dir(target_root, "invalid_root")
    workspace_path = _safe_workspace(workspace, state_dir=state_dir)
    result = inspect_workspace(root, workspace_path, state_dir=state_dir)
    if not result["source_unchanged"]:
        raise ValueError("original_changed_rebase_required")

    # Every known refusal happens before prepare_mirror creates a directory.
    if any(
        Path(row["path"]).parts[0].casefold() == "tests" and row["changed"]
        for row in result["files"]
    ):
        raise ValueError("changed_tests_require_independent_suite_review")
    candidate_root = _safe_existing_dir(workspace_path / "candidate", "invalid_candidate_root")
    overlays: dict[str, tuple[bytes, str, str]] = {}
    for row in result["files"]:
        _current, _current_raw, current_hash = _read_source(root, row["path"])
        if current_hash != row["before"]:
            raise ValueError("original_changed_rebase_required")
        _candidate, raw, candidate_hash = _read_source(candidate_root, row["path"])
        if candidate_hash != row["candidate"]:
            raise ValueError("candidate_changed_during_preparation")
        overlays[row["path"]] = (raw, candidate_hash, current_hash)

    destination_path = _absolute(destination, "invalid_candidate_destination")
    _reject_redirected_ancestors(destination_path)
    manifest = prepare_mirror(root, destination_path)
    mirror_root = _safe_existing_dir(destination_path, "invalid_candidate_destination")

    for relative, (preflight_raw, preflight_hash, current_hash) in overlays.items():
        _candidate, raw, candidate_hash = _read_source(candidate_root, relative)
        if candidate_hash != preflight_hash or raw != preflight_raw:
            raise ValueError("candidate_changed_during_preparation")
        target, _mirror_raw, mirror_hash = _read_source(mirror_root, relative)
        if mirror_hash != current_hash:
            raise ValueError("mirror_source_mismatch")
        _atomic_overlay(target, raw, boundary=mirror_root)
        _written, written_raw, written_hash = _read_source(mirror_root, relative)
        if written_hash != candidate_hash or written_raw != raw:
            raise ValueError("candidate_overlay_mismatch")

    return {
        "candidate_root": str(mirror_root),
        "original_source_hash": manifest["sha256"],
        "changed_files": result["changed_files"],
        "production_changed": False,
        "next": "Run RegressionRunner(candidate_root, separate_state_dir), then independently review the diff.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "inspect", "candidate"])
    parser.add_argument("--path", action="append", default=[])
    parser.add_argument("--workspace")
    parser.add_argument("--destination")
    args = parser.parse_args()
    from brain_service.config import load_config

    cfg = load_config()
    if args.action == "prepare":
        result = prepare(cfg.target_root, cfg.state_dir, args.path)
    elif args.action == "candidate":
        result = build_candidate(
            cfg.target_root,
            args.workspace,
            args.destination,
            state_dir=cfg.state_dir,
        )
    else:
        result = inspect_workspace(
            cfg.target_root,
            args.workspace,
            state_dir=cfg.state_dir,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
