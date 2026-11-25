"""Out-of-process QHOS availability supervisor.

This module intentionally depends only on the Python standard library.  It can
therefore keep observing and recovering the server when the application
environment itself is partially broken.  It is an operational supervisor, not
an extension of Brain authority: it never edits source, changes trading state,
or bypasses the existing restart preflight.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import threading
import functools
import time
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Optional, Sequence


STATE_SCHEMA = "qhos-supervisor-state-v1"
AUDIT_SCHEMA = "qhos-supervisor-audit-v1"


class SupervisorPhase(str, Enum):
    HEALTHY = "HEALTHY"
    SUSPECT = "SUSPECT"
    RESTART_PENDING = "RESTART_PENDING"
    RESTARTING = "RESTARTING"
    VERIFYING = "VERIFYING"
    QUARANTINED = "QUARANTINED"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class SupervisorConfig:
    root: Path
    state_dir: Optional[Path] = None
    health_url: str = "http://127.0.0.1:8766/api/health"
    busy_url: str = "http://127.0.0.1:8766/api/busy"
    request_timeout_s: float = 2.0
    poll_interval_s: float = 30.0
    heartbeat_stale_s: float = 180.0
    failure_threshold: int = 3
    restart_cooldown_s: float = 300.0
    verify_timeout_s: float = 600.0
    restart_timeout_s: float = 900.0
    max_restarts_30m: int = 2
    max_restarts_6h: int = 3
    dry_run: bool = False

    @property
    def heartbeat_path(self) -> Path:
        return self.root / "data_lake" / "server_heartbeat.txt"

    @property
    def state_path(self) -> Path:
        return (self.state_dir or self.root / "state") / "supervisor_state.json"

    @property
    def lock_path(self) -> Path:
        return (self.state_dir or self.root / "state") / "qhos_watchdog.lock"

    @property
    def audit_path(self) -> Path:
        return (self.state_dir or self.root / "state") / "qhos_supervisor.jsonl"

    @property
    def restart_batch(self) -> Path:
        return self.root / "QHOS_RESTART.bat"


HttpGetter = Callable[[str, float], Mapping[str, Any]]
CommandRunner = Callable[[Sequence[str], Path, float], Any]
Clock = Callable[[], float]


def http_json_get(url: str, timeout_s: float) -> Mapping[str, Any]:
    """Fetch one local JSON object or raise a regular exception."""
    from brain_service.local_http import get_json
    return get_json(url, timeout_s)


def run_command(command: Sequence[str], cwd: Path, timeout_s: float) -> CommandResult:
    """Legacy restart is unavailable in V2; offline tests may inject a fake runner."""
    raise RuntimeError("legacy_relight_not_supported_by_qhos2")


def atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    """Durably replace a JSON state file without exposing a partial document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            # allow_nan=False: a NaN raises here instead of producing a file the
            # readers reject later as "start from zero" (QHOS-AUD-294).
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class SingleInstanceLock:
    """Lifetime OS file lock.

    ``acquire()`` returns False both when another holder has the lock
    (``failure_kind == "contention"``) and when the lock itself cannot be used
    (``failure_kind == "error"``: unwritable directory, disk full, a file where
    the directory should be...). Callers must not report the second case as
    "another diagnostic is running" (QHOS-AUD-266).
    """

    def __init__(self, path: Path, *, metadata: Optional[Mapping[str, Any]] = None):
        self.path = Path(path)
        self.metadata = dict(metadata or {})
        self._handle: Optional[Any] = None
        self.failure_kind: Optional[str] = None
        self.last_error: Optional[str] = None

    @staticmethod
    def _is_contention(exc: BaseException) -> bool:
        """Only the errno the OS uses for 'held elsewhere' on this platform."""
        import errno

        if os.name == "nt":
            return isinstance(exc, OSError) and exc.errno in {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK)}
        return isinstance(exc, BlockingIOError) or (
            isinstance(exc, OSError) and exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK})

    @staticmethod
    def _describe(exc: BaseException) -> str:
        """Type plus strerror; never the path (it may name a home directory)."""
        if isinstance(exc, OSError) and exc.strerror:
            return f"{type(exc).__name__}: {exc.strerror}"
        return f"{type(exc).__name__}: {str(exc)[:160]}" if str(exc) else type(exc).__name__

    @staticmethod
    def _lock_fd(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock_fd(handle: Any) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        handle: Optional[Any] = None
        self.failure_kind = None
        self.last_error = None
        locking = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b", buffering=0)
            if self.path.stat().st_size == 0:
                handle.write(b" ")
                handle.flush()
            locking = True
            self._lock_fd(handle)
            locking = False
            metadata = {
                "pid": os.getpid(),
                "acquired_at": time.time(),
                **self.metadata,
            }
            encoded = json.dumps(metadata, ensure_ascii=False).encode("utf-8")
            handle.seek(0)
            handle.truncate(0)
            handle.write(encoded or b" ")
            handle.flush()
            os.fsync(handle.fileno())
            self._handle = handle
            return True
        except Exception as exc:
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
            self._handle = None
            if locking and self._is_contention(exc):
                self.failure_kind = "contention"
                self.last_error = "lock_held_elsewhere"
            else:
                self.failure_kind = "error"
                self.last_error = self._describe(exc)
            return False

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            self._unlock_fd(handle)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            if self.failure_kind == "error":
                raise RuntimeError(f"qhos watchdog lock unusable: {self.last_error}")
            raise RuntimeError("qhos watchdog lock is already held")
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def _default_state() -> dict[str, Any]:
    return {
        "schema": STATE_SCHEMA,
        "phase": SupervisorPhase.HEALTHY.value,
        "consecutive_failures": 0,
        "restart_attempts": [],
        "last_probe_at": None,
        "last_success_at": None,
        "last_transition_at": None,
        "verify_started_at": None,
        "cooldown_until": 0.0,
        "last_reason": "initial",
        "last_error": None,
        "last_observation": {},
        "last_restart_returncode": None,
        "last_restart_output": None,
    }


def _finite_timestamp(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _redact(text: str, limit: int = 1200) -> str:
    """Keep useful command diagnostics without persisting likely credentials."""
    clean = str(text or "")[-limit:]
    clean = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+", r"\1=<redacted>", clean)
    clean = re.sub(r"https://discord(?:app)?\.com/api/webhooks/\S+", "<redacted-webhook>", clean)
    clean = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<redacted-key>", clean)
    return clean


def _serialized(method):
    """Serialize manual/automatic decisions across threads and processes."""
    @functools.wraps(method)
    def guarded(self, *args, **kwargs):
        if not self._mutex.acquire(blocking=False):
            return {**dict(self.state), "accepted": False, "request_reason": "recovery_in_progress"}
        lock = SingleInstanceLock(self.config.lock_path)
        try:
            if not lock.acquire():
                return {**dict(self.state), "accepted": False, "request_reason": "supervisor_lock_unavailable",
                        "lock_failure": lock.failure_kind, "lock_error": lock.last_error}
            self.state = self._load_state()
            return method(self, *args, **kwargs)
        finally:
            lock.release()
            self._mutex.release()
    return guarded


class QhosSupervisor:
    """Pure policy/state machine around injected probes and command execution."""

    def __init__(
        self,
        config: SupervisorConfig,
        *,
        http_get: HttpGetter = http_json_get,
        command_runner: CommandRunner = run_command,
        now: Clock = time.time,
    ) -> None:
        self._mutex = threading.Lock()
        self.config = config
        self.http_get = http_get
        self.command_runner = command_runner
        self.now = now
        self.state: MutableMapping[str, Any] = self._load_state()

    def _load_state(self) -> MutableMapping[str, Any]:
        base = _default_state()
        try:
            raw = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return base
        if not isinstance(raw, dict) or raw.get("schema") != STATE_SCHEMA:
            return base
        for key in base:
            if key in raw:
                base[key] = raw[key]
        if base.get("phase") not in {phase.value for phase in SupervisorPhase}:
            base["phase"] = SupervisorPhase.HEALTHY.value
        base["consecutive_failures"] = max(0, int(base.get("consecutive_failures") or 0))
        attempts = []
        for value in base.get("restart_attempts") or []:
            stamp = _finite_timestamp(value)
            if stamp is not None:
                attempts.append(stamp)
        base["restart_attempts"] = attempts
        return base

    def _persist(self) -> None:
        self.state["schema"] = STATE_SCHEMA
        atomic_json_write(self.config.state_path, dict(self.state))

    def _audit(self, event: str, **fields: Any) -> None:
        self.config.audit_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "schema": AUDIT_SCHEMA,
            "ts": self.now(),
            "event": str(event),
            "phase": self.state.get("phase"),
            **fields,
        }
        with open(self.config.audit_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _transition(self, phase: SupervisorPhase, reason: str) -> None:
        previous = str(self.state.get("phase") or "")
        self.state["phase"] = phase.value
        self.state["last_reason"] = str(reason)
        if previous != phase.value:
            self.state["last_transition_at"] = self.now()
            self._audit(
                "transition",
                previous=previous,
                current=phase.value,
                reason=str(reason),
            )

    def _heartbeat_age(self, now: float) -> tuple[Optional[float], Optional[str]]:
        path = self.config.heartbeat_path
        try:
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
            try:
                beat = float(raw)
            except (TypeError, ValueError):
                beat = path.stat().st_mtime
            if not math.isfinite(beat):
                raise ValueError("non-finite heartbeat")
            return max(0.0, now - beat), None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    def _fetch(self, url: str) -> tuple[Optional[Mapping[str, Any]], Optional[str]]:
        try:
            payload = self.http_get(url, self.config.request_timeout_s)
            if not isinstance(payload, Mapping):
                raise ValueError("endpoint did not return an object")
            return payload, None
        except Exception as exc:
            return None, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _is_qhos_health(payload: Optional[Mapping[str, Any]]) -> bool:
        if not isinstance(payload, Mapping):
            return False
        status = payload.get("status")
        process = payload.get("process")
        if not isinstance(status, Mapping) or "process" not in status:
            return False
        if not isinstance(process, Mapping):
            return False
        return "state" in process or "operational_means" in process

    @staticmethod
    def _operational_ok(payload: Optional[Mapping[str, Any]]) -> bool:
        if not QhosSupervisor._is_qhos_health(payload):
            return False
        status = payload.get("status") or {}
        process = payload.get("process") or {}
        return (
            str(status.get("process") or "").upper() == "OK"
            and str(process.get("state") or "").upper() == "PROCESS_ONLINE"
        )

    def observe(self) -> dict[str, Any]:
        now = self.now()
        health, health_error = self._fetch(self.config.health_url)
        busy, busy_error = self._fetch(self.config.busy_url)
        heartbeat_age, heartbeat_error = self._heartbeat_age(now)
        health_replied = health is not None
        identity_ok = self._is_qhos_health(health)
        status = health.get("status") if isinstance(health, Mapping) else {}
        if not isinstance(status, Mapping):
            status = {}
        busy_value: Optional[bool] = None
        busy_reasons: list[str] = []
        if isinstance(busy, Mapping) and isinstance(busy.get("busy"), bool):
            busy_value = bool(busy.get("busy"))
            raw_reasons = busy.get("reasons")
            if isinstance(raw_reasons, list):
                busy_reasons = [str(item) for item in raw_reasons if item][:8]
        return {
            "ts": now,
            "health_replied": health_replied,
            "qhos_identity": identity_ok,
            "operational_ok": self._operational_ok(health),
            "health_error": health_error,
            "busy": busy_value,
            "busy_reasons": busy_reasons,
            "busy_error": busy_error,
            "heartbeat_age_s": heartbeat_age,
            "heartbeat_error": heartbeat_error,
            # These fields are deliberately informational.  The supervisor
            # never interprets research/execution evidence as process failure.
            "scientific_status": {
                "data_integrity": status.get("data_integrity"),
                "research_integrity": status.get("research_integrity"),
                "execution_readiness": status.get("execution_readiness"),
            },
        }

    def _trim_attempts(self, now: float) -> list[float]:
        attempts = []
        for raw in self.state.get("restart_attempts") or []:
            stamp = _finite_timestamp(raw)
            if stamp is not None and now - stamp <= 6 * 3600:
                attempts.append(stamp)
        attempts.sort()
        self.state["restart_attempts"] = attempts
        return attempts

    def _budget(self, now: float) -> tuple[bool, bool, str]:
        attempts = self._trim_attempts(now)
        in_30m = sum(1 for stamp in attempts if now - stamp <= 30 * 60)
        in_6h = len(attempts)
        if in_6h >= self.config.max_restarts_6h:
            return False, True, "restart_budget_6h_exhausted"
        if in_30m >= self.config.max_restarts_30m:
            return False, False, "restart_budget_30m_cooldown"
        if now < float(self.state.get("cooldown_until") or 0.0):
            return False, False, "restart_cooldown"
        return True, False, "restart_budget_available"

    def restart_command(self) -> list[str]:
        if os.name == "nt":
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            return [comspec, "/d", "/c", str(self.config.restart_batch), "--auto"]
        # QHOS is a Windows application; this branch exists for policy tests
        # and fails safely on unsupported hosts.
        return [str(self.config.restart_batch), "--auto"]

    @staticmethod
    def _command_result(raw: Any) -> CommandResult:
        if isinstance(raw, CommandResult):
            return raw
        return CommandResult(
            returncode=int(getattr(raw, "returncode", 1)),
            stdout=str(getattr(raw, "stdout", "") or ""),
            stderr=str(getattr(raw, "stderr", "") or ""),
        )

    @_serialized
    def tick(self) -> dict[str, Any]:
        """Run one observation/decision cycle and return persisted state."""
        observation = self.observe()
        now = float(observation["ts"])
        self.state["last_probe_at"] = now
        self.state["last_observation"] = observation
        self.state["last_error"] = observation.get("health_error")

        if observation["health_replied"] and not observation["qhos_identity"]:
            self.state["consecutive_failures"] = int(self.state.get("consecutive_failures") or 0) + 1
            self._transition(SupervisorPhase.QUARANTINED, "collector_endpoint_is_not_qhos")
            self._persist()
            return dict(self.state)

        if observation["operational_ok"]:
            recovered = self.state.get("phase") != SupervisorPhase.HEALTHY.value
            self.state["consecutive_failures"] = 0
            self.state["last_success_at"] = now
            self.state["verify_started_at"] = None
            self.state["last_error"] = None
            self._transition(SupervisorPhase.HEALTHY, "qhos_process_online")
            self._persist()
            if recovered:
                self._audit("recovered", reason="health_endpoint_confirmed_online")
            return dict(self.state)

        failures = int(self.state.get("consecutive_failures") or 0) + 1
        self.state["consecutive_failures"] = failures
        heartbeat_age = observation.get("heartbeat_age_s")
        heartbeat_stale = heartbeat_age is None or float(heartbeat_age) >= self.config.heartbeat_stale_s

        if self.state.get("phase") == SupervisorPhase.VERIFYING.value:
            started = _finite_timestamp(self.state.get("verify_started_at")) or now
            if now - started < self.config.verify_timeout_s:
                self.state["last_reason"] = "waiting_for_post_restart_health"
                self._persist()
                return dict(self.state)

        if failures < self.config.failure_threshold or not heartbeat_stale:
            reason = "health_probe_failed" if heartbeat_stale else "health_failed_but_heartbeat_fresh"
            self._transition(SupervisorPhase.SUSPECT, reason)
            self._persist()
            return dict(self.state)

        if observation["health_replied"] and observation.get("busy") is None:
            self._transition(SupervisorPhase.RESTART_PENDING, "busy_status_unknown")
            self._persist()
            return {**dict(self.state), "accepted": False, "request_reason": "busy_status_unknown"}

        if observation.get("busy") is True:
            reasons = observation.get("busy_reasons") or ["busy"]
            self._transition(SupervisorPhase.RESTART_PENDING, "busy:" + ",".join(reasons))
            self._persist()
            return dict(self.state)

        allowed, hard_stop, budget_reason = self._budget(now)
        if not allowed:
            phase = SupervisorPhase.QUARANTINED if hard_stop else SupervisorPhase.RESTART_PENDING
            self._transition(phase, budget_reason)
            self._persist()
            return dict(self.state)

        self._transition(SupervisorPhase.RESTART_PENDING, "confirmed_process_failure")
        return self._invoke_restart(now, reason="confirmed_process_failure")

    @_serialized
    def request_restart(self, *, reason: str = "explicit_request") -> dict[str, Any]:
        """Operator/Brain-requested relight. Honors busy + budget. Never FORCE.

        Unlike tick(), this does not wait for the failure threshold. A healthy
        Quant may still be relit so new code can load; a busy Quant may not.
        """
        observation = self.observe()
        now = float(observation["ts"])
        self.state["last_probe_at"] = now
        self.state["last_observation"] = observation
        if observation["health_replied"] and not observation["qhos_identity"]:
            self._transition(SupervisorPhase.QUARANTINED, "collector_endpoint_is_not_qhos")
            self._persist()
            return {**dict(self.state), "accepted": False, "request_reason": "identity_mismatch"}
        if observation["health_replied"] and observation.get("busy") is None:
            self._transition(SupervisorPhase.RESTART_PENDING, "busy_status_unknown")
            self._persist()
            return {**dict(self.state), "accepted": False, "request_reason": "busy_status_unknown"}
        if observation.get("busy") is True:
            reasons = observation.get("busy_reasons") or ["busy"]
            self._transition(SupervisorPhase.RESTART_PENDING, "busy:" + ",".join(reasons))
            self._persist()
            out = dict(self.state)
            out["accepted"] = False
            out["request_reason"] = "quant_busy"
            return out
        allowed, hard_stop, budget_reason = self._budget(now)
        if not allowed:
            phase = SupervisorPhase.QUARANTINED if hard_stop else SupervisorPhase.RESTART_PENDING
            self._transition(phase, budget_reason)
            self._persist()
            out = dict(self.state)
            out["accepted"] = False
            out["request_reason"] = budget_reason
            return out
        result = self._invoke_restart(now, reason=reason)
        # A refused attempt must not inherit success from an earlier restart.
        # In particular, a missing entrypoint preserves the previous returncode
        # for diagnostics but explicitly reports that nothing was invoked.
        if result.get("accepted") is False:
            return result
        if result.get("last_reason") == "dry_run":
            result["accepted"] = False
            result["request_reason"] = "dry_run"
        elif result.get("last_reason") == "restart_preflight_busy":
            result["accepted"] = False
            result["request_reason"] = "quant_busy"
        elif result.get("last_restart_returncode") == 0:
            result["accepted"] = True
            result["request_reason"] = "invoked"
        else:
            result["accepted"] = False
            result["request_reason"] = str(result.get("last_reason") or reason)
        return result

    def _invoke_restart(self, now: float, *, reason: str) -> dict[str, Any]:
        if self.config.dry_run:
            self.state["cooldown_until"] = now + self.config.restart_cooldown_s
            self._audit("restart_suppressed", reason="dry_run", requested=reason)
            self._persist()
            out = dict(self.state)
            out["last_reason"] = "dry_run"
            return out

        if not self.config.restart_batch.is_file():
            self._transition(SupervisorPhase.QUARANTINED, "restart_batch_missing")
            self._persist()
            return {**dict(self.state), "accepted": False, "request_reason": "restart_batch_missing"}
        command = self.restart_command()
        self.state["restart_attempts"] = [*self._trim_attempts(now), now]
        self.state["cooldown_until"] = now + self.config.restart_cooldown_s
        self._transition(SupervisorPhase.RESTARTING, "invoking_guarded_restart")
        # Persist and audit before the external action.  If durable recording
        # fails, the exception prevents the restart rather than acting blind.
        self._persist()
        self._audit(
            "restart_invoked",
            requested=reason,
            command=[Path(part).name if index == 3 else part for index, part in enumerate(command)],
        )

        try:
            raw_result = self.command_runner(command, self.config.root, self.config.restart_timeout_s)
            result = self._command_result(raw_result)
        except Exception as exc:
            result = CommandResult(1, stderr=f"{type(exc).__name__}: {exc}")

        diagnostic = _redact("\n".join(part for part in (result.stdout, result.stderr) if part))
        self.state["last_restart_returncode"] = result.returncode
        self.state["last_restart_output"] = diagnostic
        if result.returncode == 0:
            self.state["consecutive_failures"] = 0
            # Give the newly launched process its full verification window;
            # the guarded command itself may take longer than that window.
            self.state["verify_started_at"] = self.now()
            self._transition(SupervisorPhase.VERIFYING, "restart_command_succeeded")
        elif result.returncode == 2:
            self._transition(SupervisorPhase.RESTART_PENDING, "restart_preflight_busy")
        else:
            _, exhausted, _ = self._budget(now)
            phase = SupervisorPhase.QUARANTINED if exhausted else SupervisorPhase.RESTART_PENDING
            self._transition(phase, f"restart_command_failed:{result.returncode}")
        self._audit(
            "restart_result",
            returncode=result.returncode,
            output=diagnostic,
            requested=reason,
        )
        self._persist()
        return dict(self.state)


__all__ = [
    "AUDIT_SCHEMA",
    "STATE_SCHEMA",
    "CommandResult",
    "QhosSupervisor",
    "SingleInstanceLock",
    "SupervisorConfig",
    "SupervisorPhase",
    "atomic_json_write",
    "http_json_get",
    "run_command",
]
