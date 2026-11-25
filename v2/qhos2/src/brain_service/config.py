"""Process-local settings. Not SystemConfig — Quant must not own this port."""
from __future__ import annotations

import logging
import os
import math
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from brain_service import DEFAULT_PORT, DEFAULT_QUANT_URL
from brain_service.authority import ALLOWED_QUANT_GET_PATHS


DEFAULT_TEST_INTERVAL_S = 3600
MIN_TEST_INTERVAL_S = 900


def normalize_test_interval(value) -> int:
    """Hourly by default; keep explicit safe intervals without silently changing them."""
    try:
        interval = DEFAULT_TEST_INTERVAL_S if isinstance(value, bool) else int(value)
    except (TypeError, ValueError, OverflowError):
        interval = DEFAULT_TEST_INTERVAL_S
    return max(MIN_TEST_INTERVAL_S, interval)


def _valid_schedule_time(value) -> bool:
    try:
        return type(value) in {int, float} and math.isfinite(value) and 0 < value < 10**12
    except (OverflowError, TypeError, ValueError):
        return False


def mark_test_attempt_started(state: dict, cadence_s: int, *, now=None) -> None:
    """Call only after both execution locks are acquired; retries never call it."""
    now = time.time() if now is None else now
    state.update(schedule_anchor_version=1, cadence_anchor_at=now,
                 cadence_anchor_kind="attempt_started_after_locks",
                 next_due=now+normalize_test_interval(cadence_s))
    state.pop("schedule_recovery_started_at", None)
    state.pop("schedule_recovery_until", None)
    state.pop("schedule_recovery_reason", None)


def restore_test_schedule(state: dict, cadence_s: int, *, previous_cadence=None, now=None) -> bool:
    """Restore a durable anchor, not the bounded display history or reboot time.

DEFERRED does not mean a test ran. A known first-run deferral keeps its short
retry. Legacy deferrals with lost history instead wait one full interval from
the first recovery; that persisted safety horizon is NOT an execution record.
Interrupted attempts keep their own cooldown even if an older anchor exists.
"""
    now = time.time() if now is None else now
    cadence = normalize_test_interval(cadence_s)
    fields = ("cadence_s", "next_due", "schedule_anchor_version", "cadence_anchor_at",
              "cadence_anchor_kind", "schedule_recovery_started_at", "schedule_recovery_until")
    before = tuple(state.get(k) for k in fields)
    previous_cadence = previous_cadence if _valid_schedule_time(previous_cadence) else None
    changed = previous_cadence != cadence
    due = state.get("next_due")
    valid_due = _valid_schedule_time(due)
    anchor = state.get("cadence_anchor_at")
    anchor = anchor if _valid_schedule_time(anchor) else None
    known_first_run = (state.get("schedule_anchor_version") == 1
                       and "cadence_anchor_at" in state and state["cadence_anchor_at"] is None)
    phase, last_run = state.get("phase"), state.get("last_run")
    if phase == "DEFERRED":
        if anchor is None:
            history = state.get("history") if isinstance(state.get("history"), list) else []
            actual_runs = [row["last_run"] for row in history
                           if isinstance(row, dict) and row.get("phase") not in {"DEFERRED", "NOT_RUN", "IDLE"}
                           and _valid_schedule_time(row.get("last_run"))]
            if actual_runs:
                anchor = max(actual_runs)
                state["cadence_anchor_kind"] = "recovered_from_legacy_history"
        due = due if valid_due else now+30
        if anchor is not None:
            due = max(due, anchor+cadence)
        elif not known_first_run or _valid_schedule_time(state.get("schedule_recovery_until")):
            recovery_started = state.get("schedule_recovery_started_at")
            if not _valid_schedule_time(recovery_started):
                recovery_started = now
            horizon = recovery_started+cadence
            state.update(schedule_recovery_started_at=recovery_started, schedule_recovery_until=horizon,
                         schedule_recovery_reason="legacy_anchor_unknown_not_an_execution")
            due = max(due, horizon)
    else:
        # A persisted in-flight/failed start attempt has a separate cooldown;
        # don't relabel a pre-lock or failed thread-start as a real execution.
        attempt_anchor = last_run if _valid_schedule_time(last_run) else anchor
        if anchor is None and _valid_schedule_time(last_run) and phase not in {"RUNNING", "INTERRUPTED"}:
            if state.get("schedule_anchor_version") != 1:
                anchor = last_run
                state["cadence_anchor_kind"] = "legacy_last_run"
        if attempt_anchor is not None:
            due = attempt_anchor+cadence if changed or not valid_due else max(due, attempt_anchor+cadence)
        elif last_run is not None:
            # A malformed legacy timestamp is unknown evidence, not a first run.
            recovery_started = state.get("schedule_recovery_started_at")
            if not _valid_schedule_time(recovery_started):
                recovery_started = now
            horizon = recovery_started+cadence
            state.update(schedule_recovery_started_at=recovery_started, schedule_recovery_until=horizon,
                         schedule_recovery_reason="invalid_legacy_timestamp_not_an_execution")
            due = max(due if valid_due else 0, horizon)
        else:
            due = due if valid_due else 0
    state.update(cadence_s=cadence, next_due=due, schedule_anchor_version=1, cadence_anchor_at=anchor)
    if changed and (anchor is not None or phase == "DEFERRED" or _valid_schedule_time(last_run)):
        state["schedule_migration"] = {"from_interval_s": previous_cadence, "to_interval_s": cadence,
                                       "anchor_last_run": anchor, "migrated_at": now}
    return changed or before != tuple(state.get(k) for k in fields)


log = logging.getLogger("Brain.Config")

_BOOL_WORDS = {"1": True, "true": True, "yes": True, "on": True, "0": False, "false": False, "no": False, "off": False}


def _violation(record, key: str, reason: str, fallback, applied=None) -> None:
    """Record an environment value that was rejected or degraded (QHOS-AUD-273).

    Only the key, the reason and the value actually applied are kept; the raw
    text never is (it may be a mistyped secret). ``record`` is a list.append.
    """
    row = {"key": key, "reason": reason, "fallback": fallback, "applied": fallback if applied is None else applied}
    if record is not None:
        record(row)
    log.warning("configuration %s: %s; applied %r", key, reason, row["applied"])


def _env_bool(name: str, default: bool, record=None) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    word = str(raw).strip().lower()
    if word not in _BOOL_WORDS:
        # Same grammar as brain.config_contract flags: an unknown word keeps the
        # default instead of silently turning the feature off.
        _violation(record, name, "malformed_bool", default)
        return default
    return _BOOL_WORDS[word]


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or not str(value).strip() else str(value).strip()


def _env_int(name: str, default: int, record=None) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(raw)
    except ValueError:
        _violation(record, name, "malformed_int", default)
        return default


def _env_float(name: str, default: float, record=None) -> float:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        _violation(record, name, "malformed_float", default)
        return default
    if not math.isfinite(value):
        _violation(record, name, "non_finite_float", default)
        return default
    return value


def _positive_float(value: float, *, name: str, maximum: float = 300.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0 or result > maximum:
        raise ValueError(f"{name} must be > 0 and <= {maximum}")
    return result


def loopback_url(value: str, *, schemes: tuple[str, ...] = ("http", "https")) -> str:
    """Keep the process boundary local; nginx is the public-facing boundary."""
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme not in schemes or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Brain may connect only to a loopback Trader URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Trader URL must not contain credentials, query, or fragment")
    return str(value).rstrip("/")


def websocket_url(http_url: str) -> str:
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))


def public_origin(value: str) -> str:
    if not str(value or "").strip():
        return ""
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("BRAIN_PUBLIC_ORIGIN must be an http(s) origin")
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("BRAIN_PUBLIC_ORIGIN must not contain credentials, path, query, or fragment")
    return f"{parsed.scheme}://{parsed.netloc}"


def loopback_host(value: str) -> str:
    """The unauthenticated local panel has one fixed IPv4 loopback boundary."""
    if not isinstance(value, str) or value.strip().lower() not in {"127.0.0.1", "localhost"}:
        raise ValueError("Brain permits only 127.0.0.1/localhost; LAN/public binding requires authentication")
    return "127.0.0.1"


@dataclass(frozen=True)
class ServiceConfig:
    port: int = DEFAULT_PORT
    host: str = "127.0.0.1"
    quant_url: str = DEFAULT_QUANT_URL
    observe_timeout_s: float = 2.0
    quant_ws_url: str = ""
    quant_ws_enabled: bool = False
    ws_open_timeout_s: float = 3.0
    ws_receive_timeout_s: float = 15.0
    ws_max_message_bytes: int = 2_000_000
    cycle_interval_s: int = 60
    test_interval_s: int = DEFAULT_TEST_INTERVAL_S
    diagnostics_enabled: bool = True
    relight_enabled: bool = False
    auto_relight: bool = False
    relight_dry_run: bool = False
    state_path: str = ""
    runtime_path: str = ""
    target_path: str = ""
    deployment_context: str = "production"
    expected_revision: str = ""
    expected_config_hash: str = ""
    service_unit: str = "qhos2-brain.service"
    public_origin: str = ""
    voice_enabled: bool = False
    voice_url: str = "http://127.0.0.1:8092"
    voice_timeout_s: float = 45.0
    allowed_get_paths: tuple = ALLOWED_QUANT_GET_PATHS
    # Environment values that were rejected or degraded while loading (key, reason,
    # applied value; never the raw text). Empty when every value was absent or valid.
    configuration_violations: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "host", loopback_host(self.host))
        object.__setattr__(self, "quant_url", loopback_url(self.quant_url))
        object.__setattr__(self, "observe_timeout_s", _positive_float(
            self.observe_timeout_s, name="observe_timeout_s", maximum=30.0,
        ))
        object.__setattr__(self, "ws_open_timeout_s", _positive_float(
            self.ws_open_timeout_s, name="ws_open_timeout_s", maximum=30.0,
        ))
        object.__setattr__(self, "ws_receive_timeout_s", _positive_float(
            self.ws_receive_timeout_s, name="ws_receive_timeout_s", maximum=120.0,
        ))
        if not 1024 <= int(self.ws_max_message_bytes) <= 16_000_000:
            raise ValueError("ws_max_message_bytes must be between 1024 and 16000000")
        object.__setattr__(self, "ws_max_message_bytes", int(self.ws_max_message_bytes))
        ws_url = self.quant_ws_url or websocket_url(self.quant_url)
        object.__setattr__(self, "quant_ws_url", loopback_url(ws_url, schemes=("ws", "wss")))
        context = str(self.deployment_context or "production").strip().lower()
        if context not in {"dev", "development", "production"}:
            raise ValueError("deployment_context must be dev, development, or production")
        object.__setattr__(self, "deployment_context", "production" if context == "production" else "development")
        object.__setattr__(self, "public_origin", public_origin(self.public_origin))
        object.__setattr__(self, "voice_url", loopback_url(self.voice_url))
        object.__setattr__(self, "voice_timeout_s", _positive_float(self.voice_timeout_s, name="voice_timeout_s", maximum=120.0))
        object.__setattr__(self, "test_interval_s", normalize_test_interval(self.test_interval_s))
        if self.relight_enabled or self.auto_relight:
            raise ValueError("legacy_relight_not_supported_by_qhos2")

    @property
    def brain_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def target_root(self) -> Path:
        import qhos2
        if str(self.target_path or "").strip():
            root = Path(str(self.target_path).strip()).expanduser().resolve()
            if (root / "src" / "qhos2" / "coletor.py").is_file():
                root = root / "src" / "qhos2"
        else:
            root = Path(qhos2.__file__).resolve().parent
        if not all((root / name).is_file() for name in ("__init__.py", "coletor.py", "config.py")):
            raise ValueError("BRAIN_QUANT_ROOT must name the QHOS2 source package")
        return root

    @property
    def state_dir(self) -> Path:
        from qhos2.config import Settings, validar_data_root
        configured = str(self.state_path or "").strip()
        return validar_data_root(configured) if configured else Settings.from_environ().data_root / "brain"

    @property
    def brain_lake_dir(self) -> Path:
        return self.state_dir

    @property
    def runtime_dir(self) -> Path:
        from qhos2.config import validar_data_root
        configured = str(self.runtime_path or "").strip()
        return validar_data_root(configured) if configured else self.state_dir / "run"

    @property
    def quant_base(self) -> str:
        return self.quant_url.rstrip("/")


def load_config() -> ServiceConfig:
    violations: list = []
    record = violations.append
    port = _env_int("BRAIN_SERVICE_PORT", DEFAULT_PORT, record)
    if not 1 <= port <= 65535:
        # No documented domain beyond TCP itself: keep the value (the bind will
        # refuse it) but do not let it pass unremarked.
        _violation(record, "BRAIN_SERVICE_PORT", "out_of_range", DEFAULT_PORT, applied=port)
    cycle_raw = _env_int("BRAIN_SERVICE_CYCLE_S", 60, record)
    cycle = max(15, cycle_raw)
    if cycle != cycle_raw:
        _violation(record, "BRAIN_SERVICE_CYCLE_S", "below_minimum", 15, applied=cycle)
    interval_raw = _env_int("BRAIN_TEST_INTERVAL_S", DEFAULT_TEST_INTERVAL_S, record)
    interval = normalize_test_interval(interval_raw)
    if interval != interval_raw:
        _violation(record, "BRAIN_TEST_INTERVAL_S", "below_minimum", MIN_TEST_INTERVAL_S, applied=interval)
    return ServiceConfig(
        port=port,
        host=_env("BRAIN_SERVICE_HOST", "127.0.0.1"),
        quant_url=_env("BRAIN_QUANT_OBSERVE_URL", DEFAULT_QUANT_URL),
        observe_timeout_s=_env_float("BRAIN_QUANT_OBSERVE_TIMEOUT_S", 2.0, record),
        quant_ws_url=_env("BRAIN_QUANT_WS_URL", ""),
        quant_ws_enabled=_env_bool("BRAIN_QUANT_WS_ENABLED", False, record),
        ws_open_timeout_s=_env_float("BRAIN_QUANT_WS_OPEN_TIMEOUT_S", 3.0, record),
        ws_receive_timeout_s=_env_float("BRAIN_QUANT_WS_RECEIVE_TIMEOUT_S", 15.0, record),
        ws_max_message_bytes=_env_int("BRAIN_QUANT_WS_MAX_MESSAGE_BYTES", 2_000_000, record),
        cycle_interval_s=cycle,
        test_interval_s=interval,
        diagnostics_enabled=_env_bool("BRAIN_DIAGNOSTICS_ENABLED", True, record),
        relight_enabled=_env_bool("BRAIN_CAN_RELIGHT_QUANT", False, record),
        auto_relight=_env_bool("BRAIN_AUTO_RELIGHT_QUANT", False, record),
        relight_dry_run=_env_bool("BRAIN_RELIGHT_DRY_RUN", False, record),
        state_path=_env("BRAIN_STATE_DIR", ""),
        runtime_path=_env("BRAIN_RUNTIME_DIR", ""),
        target_path=_env("BRAIN_QUANT_ROOT", ""),
        deployment_context=_env("BRAIN_DEPLOYMENT_CONTEXT", "production"),
        expected_revision=_env("BRAIN_EXPECTED_REVISION", ""),
        expected_config_hash=_env("BRAIN_EXPECTED_CONFIG_HASH", ""),
        service_unit=_env("BRAIN_SYSTEMD_UNIT", "qhos2-brain.service"),
        public_origin=_env("BRAIN_PUBLIC_ORIGIN", ""),
        voice_enabled=_env_bool("BRAIN_VOICE_ENABLED", False, record),
        voice_url=_env("BRAIN_VOICE_URL", "http://127.0.0.1:8092"),
        voice_timeout_s=_env_float("BRAIN_VOICE_TIMEOUT_S", 45.0, record),
        configuration_violations=tuple(violations),
    )
