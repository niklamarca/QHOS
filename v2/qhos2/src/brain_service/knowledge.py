"""Bounded, read-only retrieval over explicitly allowlisted Trader sources.

Source text is untrusted data.  This module never imports, executes, evaluates
or treats retrieved text as an instruction.  Every result carries a relative
path, a content hash and an exact line reference that can be revalidated.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from brain_service.supervisor import atomic_json_write


SCHEMA = "brain-local-knowledge-v1"
ALLOWED_SUFFIXES = frozenset({".py", ".md", ".js", ".css", ".html"})
DEFAULT_SOURCE_DIRS = (
    "core",
    "data",
    "orchestrator",
    "evolution",
    "validation",
    "tests",
    "dashboard",
    "governance",
    "monitoring",
    "backtest",
    "features",
    "regime",
    "strategies",
    "lab",
    "risk",
    "execution",
    "portfolio",
    "docs",
    "scripts",
    ".",
)
DENIED_COMPONENTS = frozenset({
    ".git",
    ".qhos-brain",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "archive",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "logs",
    "log",
    "state",
    "states",
    "data_lake",
})
DENIED_FILENAMES = frozenset({
    "config.py",
    "credentials.py",
    "credential.py",
    "secrets.py",
    "secret.py",
})

MAX_FILES = 600
MAX_DIRECTORIES = 512
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
MAX_LINE_CHARS = 800
MAX_SNIPPET_CHARS = 1200
MAX_QUERY_CHARS = 500
MAX_RESULTS = 20

_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STOPWORDS = frozenset({
    "aos", "aqui", "assim", "com", "como", "da", "das", "de", "do", "dos",
    "ela", "ele", "em", "esta", "este", "isso", "na", "nas", "no", "nos",
    "para", "pela", "pelo", "por", "que", "se", "sem", "sua", "um", "uma",
    "and", "are", "for", "from", "how", "into", "is", "of", "the", "this",
    "to", "with",
})
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|credential|private[_-]?key)\b"
    r"(\s*[:=]\s*).*$"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_ALIASES = {
    "aprendizado": {"learning", "learn", "evolucao", "evolution", "treino", "training", "pesquisa", "research", "growth"},
    "aprender": {"learning", "learn", "evolucao", "evolution", "treino", "training", "pesquisa", "research"},
    "servidor": {"server", "service", "uvicorn", "fastapi", "startup", "health", "processo", "process"},
    "server": {"servidor", "service", "uvicorn", "fastapi", "startup", "health", "processo", "process"},
    "teste": {"test", "tests", "pytest", "regressao", "regression", "validation", "validacao"},
    "testes": {"test", "tests", "pytest", "regressao", "regression", "validation", "validacao"},
    "fila": {"queue", "queues", "job", "jobs", "scheduler", "agendador", "orchestrator"},
    "filas": {"queue", "queues", "job", "jobs", "scheduler", "agendador", "orchestrator"},
    "queue": {"fila", "filas", "queues", "job", "jobs", "scheduler", "agendador", "orchestrator"},
    "queues": {"fila", "filas", "queue", "job", "jobs", "scheduler", "agendador", "orchestrator"},
}


def _fold(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return text.encode("ascii", "ignore").decode("ascii").lower()


def _tokens(value: object) -> set[str]:
    return {token for token in _TOKEN_RE.findall(_fold(value)) if token not in _STOPWORDS}


def _safe_snippet(lines: Iterable[str]) -> str:
    safe = []
    for raw_line in lines:
        line = raw_line.replace("\x00", "")[:MAX_LINE_CHARS]
        if "PRIVATE KEY-----" in line.upper():
            safe.append("[REDACTED PRIVATE KEY]")
            continue
        line = _SECRET_ASSIGN_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", line)
        safe.append(_BEARER_RE.sub("Bearer [REDACTED]", line))
    return "\n".join(safe).strip()[:MAX_SNIPPET_CHARS]


def _is_link_or_junction(path: Path) -> bool:
    """Reject every redirecting filesystem object, including NTFS junctions."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attrs = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except OSError:
        return True


def _denied_relative(relative: Path) -> bool:
    parts = [part.casefold() for part in relative.parts]
    if any(part in DENIED_COMPONENTS for part in parts):
        return True
    name = relative.name.casefold()
    if name == ".env" or name.startswith(".env."):
        return True
    if name in DENIED_FILENAMES:
        return True
    stem = relative.stem.casefold()
    return "credential" in stem or "secret" in stem


class SourceBoundary:
    """Resolve source paths without crossing the declared trust boundary."""

    def __init__(
        self,
        target_root: Path | str,
        source_dirs: Optional[Iterable[Path | str]] = None,
        *,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        raw_root = Path(target_root).absolute()
        if not raw_root.is_dir() or _is_link_or_junction(raw_root):
            raise ValueError("target_root_unavailable_or_redirected")
        self.target_root = raw_root.resolve(strict=True)
        self.max_file_bytes = max(1, min(int(max_file_bytes), MAX_FILE_BYTES))
        requested = tuple(source_dirs) if source_dirs is not None else DEFAULT_SOURCE_DIRS
        self.source_roots: list[Path] = []
        self.missing_source_dirs = 0
        seen: set[str] = set()
        for item in requested:
            candidate = Path(item)
            if candidate.is_absolute():
                lexical = candidate.absolute()
            else:
                lexical = (self.target_root / candidate).absolute()
            try:
                lexical.relative_to(self.target_root)
            except ValueError as exc:
                raise ValueError("source_dir_outside_target") from exc
            if _denied_relative(lexical.relative_to(self.target_root)):
                raise ValueError("source_dir_denied")
            if not lexical.exists():
                self.missing_source_dirs += 1
                continue
            if not lexical.is_dir() or self._contains_redirect(lexical):
                raise ValueError("source_dir_unavailable_or_redirected")
            resolved = lexical.resolve(strict=True)
            try:
                resolved.relative_to(self.target_root)
            except ValueError as exc:
                raise ValueError("source_dir_outside_target") from exc
            key = os.path.normcase(str(resolved))
            if key not in seen:
                seen.add(key)
                self.source_roots.append(resolved)

    def _contains_redirect(self, candidate: Path) -> bool:
        try:
            relative = candidate.relative_to(self.target_root)
        except ValueError:
            return True
        current = self.target_root
        for part in relative.parts:
            current = current / part
            if _is_link_or_junction(current):
                return True
        return False

    def _inside_source_roots(self, path: Path) -> bool:
        for source_root in self.source_roots:
            try:
                path.relative_to(source_root)
                return True
            except ValueError:
                continue
        return False

    def resolve_source(self, value: object) -> tuple[Optional[Path], str]:
        """Return a safe regular source file or a stable denial reason."""
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            return None, "invalid_path"
        supplied = Path(value)
        lexical = supplied.absolute() if supplied.is_absolute() else (self.target_root / supplied).absolute()
        try:
            relative = lexical.relative_to(self.target_root)
        except ValueError:
            return None, "outside_target"
        if _denied_relative(relative):
            return None, "denied_path"
        if lexical.suffix.casefold() not in ALLOWED_SUFFIXES:
            return None, "extension_denied"
        if self._contains_redirect(lexical):
            return None, "redirected_path"
        try:
            resolved = lexical.resolve(strict=True)
            resolved.relative_to(self.target_root)
        except (OSError, ValueError):
            return None, "unavailable_path"
        if not self._inside_source_roots(resolved):
            return None, "outside_source_dirs"
        if not resolved.is_file() or _is_link_or_junction(resolved):
            return None, "not_regular_file"
        return resolved, "ok"

    def read_source(self, value: object) -> tuple[Optional[dict[str, Any]], str]:
        path, reason = self.resolve_source(value)
        if path is None:
            return None, reason
        try:
            before = path.stat(follow_symlinks=False)
            size = before.st_size
            if size <= 0:
                return None, "empty_file"
            if size > self.max_file_bytes:
                return None, "file_too_large"
            with path.open("rb") as handle:
                raw = handle.read(self.max_file_bytes + 1)
            if len(raw) > self.max_file_bytes:
                return None, "file_too_large"
            text = raw.decode("utf-8-sig")
            after = path.stat(follow_symlinks=False)
            before_signature = (
                before.st_size,
                getattr(before, "st_mtime_ns", None),
                getattr(before, "st_ino", None),
            )
            after_signature = (
                after.st_size,
                getattr(after, "st_mtime_ns", None),
                getattr(after, "st_ino", None),
            )
            if before_signature != after_signature or after.st_size != len(raw):
                return None, "source_changed_during_read"
            if self._contains_redirect(path) or path.resolve(strict=True) != path:
                return None, "source_changed_during_read"
        except UnicodeError:
            return None, "invalid_utf8"
        except OSError:
            return None, "read_failed"
        relative = path.relative_to(self.target_root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        return {
            "path": relative,
            "raw": raw,
            "text": text,
            "bytes": len(raw),
            "sha256": digest,
            "version": f"sha256:{digest[:16]}",
        }, "ok"

    def verify_reference(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(reference, Mapping):
            return {"valid": False, "reason": "invalid_reference"}
        source, reason = self.read_source(reference.get("path"))
        if source is None:
            return {"valid": False, "reason": reason}
        expected_hash = str(reference.get("sha256") or "").casefold()
        if not _SHA256_RE.fullmatch(expected_hash):
            return {"valid": False, "reason": "invalid_sha256"}
        if source["sha256"] != expected_hash:
            return {"valid": False, "reason": "source_hash_changed", "path": source["path"]}
        expected_version = reference.get("version")
        if not isinstance(expected_version, str) or not expected_version:
            return {"valid": False, "reason": "invalid_version", "path": source["path"]}
        if expected_version != source["version"]:
            return {"valid": False, "reason": "source_version_changed", "path": source["path"]}
        start = reference.get("line_start")
        end = reference.get("line_end", start)
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            return {"valid": False, "reason": "invalid_line_range", "path": source["path"]}
        line_count = max(1, len(source["text"].splitlines()))
        if end > line_count:
            return {"valid": False, "reason": "line_out_of_range", "path": source["path"]}
        line_ref = f"{source['path']}:{start}" if start == end else f"{source['path']}:{start}-{end}"
        if not isinstance(reference.get("line_ref"), str) or not reference.get("line_ref"):
            return {"valid": False, "reason": "invalid_line_ref", "path": source["path"]}
        if reference.get("line_ref") != line_ref:
            return {"valid": False, "reason": "line_ref_mismatch", "path": source["path"]}
        return {
            "valid": True,
            "reason": "verified",
            "reference": {
                "path": source["path"],
                "line_start": start,
                "line_end": end,
                "line_ref": line_ref,
                "sha256": source["sha256"],
                "version": source["version"],
            },
        }


class KnowledgeIndex:
    """Small lexical index for verifiable, local source retrieval."""

    def __init__(
        self,
        target_root: Path | str,
        state_dir: Path | str,
        *,
        source_dirs: Optional[Iterable[Path | str]] = None,
        max_files: int = MAX_FILES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        clock=time.time,
    ) -> None:
        self.boundary = SourceBoundary(target_root, source_dirs)
        self.target_root = self.boundary.target_root
        self.state_dir = Path(state_dir).absolute()
        self.state_path = self.state_dir / "knowledge_index.json"
        self.max_files = max(1, min(int(max_files), MAX_FILES))
        self.max_total_bytes = max(1, min(int(max_total_bytes), MAX_TOTAL_BYTES))
        self._clock = clock
        self._documents: list[dict[str, Any]] = []
        self._snapshot: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self.refresh()

    def _iter_candidates(self):
        seen: set[str] = set()
        directories = 0
        for source_root in self.boundary.source_roots:
            for dirpath, dirnames, filenames in os.walk(source_root, topdown=True, followlinks=False):
                directories += 1
                if directories > MAX_DIRECTORIES:
                    return
                base = Path(dirpath)
                safe_dirs = []
                for name in sorted(dirnames, key=str.casefold):
                    child = base / name
                    try:
                        relative = child.relative_to(self.target_root)
                    except ValueError:
                        continue
                    if _denied_relative(relative) or _is_link_or_junction(child):
                        continue
                    safe_dirs.append(name)
                dirnames[:] = safe_dirs
                for name in sorted(filenames, key=str.casefold):
                    candidate = base / name
                    key = os.path.normcase(str(candidate))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield candidate

    def refresh(self) -> dict[str, Any]:
        """Rebuild the index. Reading and tokenizing the sources happens outside the lock; the lock only swaps the
        references, so snapshot() never waits for the disk (QHOS-AUD-284)."""
        documents, snapshot, manifest = self._build()
        with self._lock:
            self._documents = documents
            self._snapshot = snapshot
        with self._persist_lock:
            # Persist metadata and hashes only; never duplicate source bodies.
            atomic_json_write(self.state_path, {**snapshot, "files": manifest})
        return dict(snapshot)

    def _build(self) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        documents: list[dict[str, Any]] = []
        skipped: dict[str, int] = {}
        total_bytes = 0
        for candidate in self._iter_candidates():
            if len(documents) >= self.max_files:
                skipped["file_budget"] = skipped.get("file_budget", 0) + 1
                break
            source, reason = self.boundary.read_source(candidate)
            if source is None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            if total_bytes + source["bytes"] > self.max_total_bytes:
                skipped["byte_budget"] = skipped.get("byte_budget", 0) + 1
                continue
            total_bytes += source["bytes"]
            lines = source["text"].splitlines() or [""]
            source["lines"] = lines
            source["path_tokens"] = _tokens(source["path"])
            documents.append(source)

        manifest = [
            {
                "path": row["path"],
                "sha256": row["sha256"],
                "version": row["version"],
                "bytes": row["bytes"],
                "lines": len(row["lines"]),
            }
            for row in documents
        ]
        encoded = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        index_hash = hashlib.sha256(encoded).hexdigest()
        refreshed_at = float(self._clock())
        if not math.isfinite(refreshed_at) or refreshed_at <= 0:
            refreshed_at = time.time()
        snapshot = {
            "schema": SCHEMA,
            "available": bool(documents),
            "target_root": str(self.target_root),
            "source_dirs": [root.relative_to(self.target_root).as_posix() for root in self.boundary.source_roots],
            "indexed_files": len(documents),
            "indexed_lines": sum(len(row["lines"]) for row in documents),
            "indexed_bytes": total_bytes,
            "skipped": skipped,
            "missing_source_dirs": self.boundary.missing_source_dirs,
            "index_hash": index_hash,
            "version": f"{SCHEMA}:{index_hash[:16]}",
            "refreshed_at": refreshed_at,
            "bounds": {
                "max_files": self.max_files,
                "max_file_bytes": self.boundary.max_file_bytes,
                "max_total_bytes": self.max_total_bytes,
            },
            "source_text_is_authority": False,
        }
        return documents, snapshot, manifest

    def snapshot(self) -> dict[str, Any]:
        """The last completed index, without waiting for a rebuild in progress."""
        with self._lock:
            current = self._snapshot
        return json.loads(json.dumps(current))

    def validate_reference(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        return self.boundary.verify_reference(reference)

    def search(self, query: object, max_results: int = 5) -> list[dict[str, Any]]:
        query_text = str(query or "").strip()[:MAX_QUERY_CHARS]
        query_tokens = _tokens(query_text)
        if not query_tokens:
            return []
        try:
            limit = int(max_results)
        except (TypeError, ValueError, OverflowError):
            limit = 5
        limit = max(0, min(limit, MAX_RESULTS))
        if not limit:
            return []

        # Rebuild within hard budgets so returned hashes and line references
        # describe the current local source, not a stale persisted index.
        self.refresh()
        with self._lock:
            documents = self._documents       # a completed list, replaced wholesale and never mutated
        expanded = set(query_tokens)
        for token in query_tokens:
            expanded.update(_ALIASES.get(token, ()))
        folded_phrase = " ".join(_TOKEN_RE.findall(_fold(query_text)))
        candidates: list[tuple[float, str, int, dict[str, Any]]] = []
        for document in documents:
            path_hits = document["path_tokens"] & expanded
            best: Optional[tuple[float, int, int, str]] = None
            for offset, raw_line in enumerate(document["lines"], start=1):
                line = raw_line[:MAX_LINE_CHARS]
                line_tokens = _tokens(line)
                direct = query_tokens & line_tokens
                semantic = (expanded - query_tokens) & line_tokens
                if not direct and not semantic and not path_hits:
                    continue
                score = float(len(direct) * 8 + len(semantic) * 3 + len(path_hits) * 2)
                folded_line = " ".join(_TOKEN_RE.findall(_fold(line)))
                if folded_phrase and folded_phrase in folded_line:
                    score += 12.0
                score += min(len(direct), len(query_tokens)) / max(1, len(query_tokens))
                # Oito linhas de contexto (quatro de cada lado) continuam
                # presas ao mesmo hash e à referência exata do trecho.
                start = max(1, offset - 4)
                end = min(len(document["lines"]), offset + 4)
                snippet = _safe_snippet(document["lines"][start - 1:end])
                row = (score, start, end, snippet)
                if best is None or (row[0], -row[1]) > (best[0], -best[1]):
                    best = row
            if best is None:
                continue
            score, start, end, snippet = best
            line_ref = f"{document['path']}:{start}" if start == end else f"{document['path']}:{start}-{end}"
            hit = {
                "path": document["path"],
                "line_start": start,
                "line_end": end,
                "line_ref": line_ref,
                "snippet": snippet,
                "sha256": document["sha256"],
                "version": document["version"],
                "score": round(score, 3),
                "trust": "untrusted_source_text",
                "source_text_is_authority": False,
            }
            candidates.append((score, document["path"].casefold(), start, hit))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [item[3] for item in candidates[:limit]]


__all__ = [
    "ALLOWED_SUFFIXES",
    "DEFAULT_SOURCE_DIRS",
    "KnowledgeIndex",
    "SCHEMA",
    "SourceBoundary",
]
