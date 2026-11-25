"""Bounded, structured action history for the standalone Linux service.

This is deliberately not a shell transcript or a journal reader.  It records
only actions owned by the Brain process and exposes a small, redacted schema to
the read-only UI.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import json
import os
import stat
import tempfile
import threading
import time
import math
from typing import Any, Optional

from brain.local_logger import get_logger

log = get_logger("Brain.LinuxActions")

SCHEMA = "qhos-linux-actions-v1"
MAX_ACTIONS = 120
MAX_FILE_BYTES = 128 * 1024
MAX_DETAIL_CHARS = 600


def _text(value: Any, limit: int = MAX_DETAIL_CHARS) -> str:
    from brain_service.log_feed import safe_message
    clean = " ".join(safe_message(value).replace("\x00", "").split())
    return clean[:limit]


class LinuxActionLog:
    """Persist a small allowlisted action history using atomic replacement."""

    def __init__(self, state_dir: Path, *, service: str = "qhos2-brain.service") -> None:
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "linux_actions.json"
        self.service = _text(service, 120) or "qhos2-brain.service"
        self._lock = threading.Lock()
        self._actions: deque[dict[str, Any]] = deque(self._load(), maxlen=MAX_ACTIONS)
        # The trail must never control the Brain lifecycle, but it must say when it
        # is memory-only (QHOS-AUD-278).
        self._persistence_error: Optional[str] = None
        self._persistence_failures = 0

    def _load(self) -> list[dict[str, Any]]:
        try:
            if not self.path.exists() or self.path.is_symlink():
                return []
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                return []
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            rows = payload.get("actions", []) if isinstance(payload, dict) else []
            if payload.get("schema") != SCHEMA or not isinstance(rows, list):
                return []
            return [self._sanitize_row(row) for row in rows[-MAX_ACTIONS:] if isinstance(row, dict)]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []

    def _sanitize_row(self, row: dict[str, Any]) -> dict[str, Any]:
        timestamp = row.get("timestamp")
        try:
            timestamp = float(timestamp)
            if not math.isfinite(timestamp):
                timestamp = 0.0
        except (TypeError, ValueError):
            timestamp = 0.0
        pid = row.get("pid")
        try:
            pid = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid = None
        return {
            "timestamp": timestamp,
            "action": _text(row.get("action"), 100) or "event",
            "outcome": _text(row.get("outcome"), 60) or "observed",
            "detail": _text(row.get("detail")),
            "pid": pid,
        }

    def _persist(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise OSError("refusing symlink action log")
        payload = json.dumps(
            {"schema": SCHEMA, "service": self.service, "actions": list(self._actions)},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > MAX_FILE_BYTES:
            raise OSError("action log exceeds bounded size")
        fd, temporary = tempfile.mkstemp(prefix=".linux_actions-", suffix=".tmp", dir=self.state_dir)
        try:
            try:
                os.fchmod(fd, 0o640)
            except (AttributeError, OSError):
                pass
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass

    def record(self, action: str, *, outcome: str = "observed", detail: str = "") -> dict[str, Any]:
        row = self._sanitize_row({
            "timestamp": time.time(),
            "action": action,
            "outcome": outcome,
            "detail": detail,
            "pid": os.getpid(),
        })
        with self._lock:
            self._actions.append(row)
            try:
                self._persist()
            except OSError as exc:
                # Logging must never control the Brain lifecycle; it must not lie either.
                self._persistence_failures += 1
                detail = f"{type(exc).__name__}: {exc.strerror or (exc.args[0] if exc.args else '')}"
                if self._persistence_error is None:
                    log.warning("linux action trail not persisted (history is memory-only until this clears): %s", detail)
                self._persistence_error = detail
            else:
                if self._persistence_error is not None:
                    log.info("linux action trail persistence recovered after %s failure(s)", self._persistence_failures)
                self._persistence_error = None
        return dict(row)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "service": self.service,
                "read_only": True,
                "source": "brain_process_structured_actions",
                "persisted": self._persistence_error is None,
                "persistence_error": self._persistence_error,
                "persistence_failures": self._persistence_failures,
                "actions": [dict(row) for row in self._actions],
            }
