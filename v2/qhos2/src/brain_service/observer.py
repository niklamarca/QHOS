"""Read-only observation of Quant from another process.

GET-only HTTP against an allowlist, plus the same filesystem surfaces
perception already uses. If Quant is down, this process stays up and the snapshot says so.
GET /api/busy is allowed so relight can honor the existing preflight.
No POST to Quant. No in-process registry import.
"""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import re
import time
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import urljoin, urlparse

from brain_service.authority import (
    ALLOWED_QUANT_GET_PATHS,
    FORBIDDEN_HTTP_METHODS,
    FORBIDDEN_QUANT_PATHS,
)
from brain_service.config import ServiceConfig, load_config
from brain_service.rithmic_observation import FIELD, accept_summary, project_summary
from brain_service.qhos2_observation import accept as accept_qhos2


def read_json_file(path: Path) -> tuple[Dict[str, Any], Optional[str]]:
    """(object, error). Absent is ({}, None); corrupt, oversized or refused is ({}, reason).

    Only file/format errors are absorbed; MemoryError and the like propagate
    (QHOS-AUD-288), so a dying process is not reported as an empty lake.
    """
    try:
        if path.is_symlink():
            return {}, "symlink_refused"
        if not path.is_file():
            return {}, None
        if path.stat().st_size > 4_000_000:
            return {}, "too_large"
        with path.open("rb") as handle:
            raw = handle.read(4_000_001)
        if len(raw) > 4_000_000:
            return {}, "too_large"
        blob = json.loads(raw)
        json.dumps(blob, allow_nan=False)
        if not isinstance(blob, dict):
            return {}, "not_an_object"
        return blob, None
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {str(exc)[:120]}"
    except RecursionError:
        # Nesting beyond the interpreter limit is a bad file, not a reason to abort collect().
        return {}, "RecursionError"


def _read_json(path: Path) -> Dict[str, Any]:
    return read_json_file(path)[0]


def _tail_text(path: Path, n: int = 20) -> List[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(max(0, path.stat().st_size - 16000))
            lines = handle.read(16000).decode("utf-8", errors="replace").splitlines()
        return [redact(ln)[:240] for ln in lines[-n:] if ln.strip()]
    except OSError:
        return []


def _git(root: Path, args: List[str]) -> Dict[str, Any]:
    """{"stdout", "available", "error"}: no git, a timeout or a non-repository is a
    measurement failure, never an empty (clean) result (QHOS-AUD-275)."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
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


def redact(value: str) -> str:
    value = re.sub(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s,]+", r"\1=<redacted>", str(value))
    value = re.sub(r"https://discord(?:app)?\.com/api/webhooks/\S+", "<redacted-webhook>", value)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "<redacted-key>", value)


def assert_get_only(method: str, path: str) -> None:
    if str(method or "").upper() in FORBIDDEN_HTTP_METHODS:
        raise PermissionError(f"brain_service forbids {method} to Quant")
    if str(path or "") in FORBIDDEN_QUANT_PATHS:
        raise PermissionError(f"brain_service forbids Quant path {path}")
    if str(path or "") not in ALLOWED_QUANT_GET_PATHS:
        raise PermissionError(f"brain_service will not GET unlisted Quant path {path}")


def get_json(base: str, path: str, *, timeout_s: float) -> Dict[str, Any]:
    assert_get_only("GET", path)
    url = urljoin(base if base.endswith("/") else base + "/", path.lstrip("/"))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise PermissionError("brain_service only speaks http(s) to Quant")
    request = urllib.request.Request(url, method="GET")
    try:
        from brain_service.local_http import open_local
        with open_local(request, timeout=timeout_s) as response:
            if int(getattr(response, "status", 200) or 200) >= 400:
                return {}
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ValueError("local_http_body_too_large")
            blob = json.loads(raw.decode("utf-8", errors="replace") or "{}")
            json.dumps(blob, allow_nan=False)
            return blob if isinstance(blob, dict) else {}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, http.client.HTTPException) as exc:
        # HTTPException (IncompleteRead, BadStatusLine, LineTooLong) is not an
        # OSError: a truncated collector reply must not abort the whole collect().
        return {"_observation_error": type(exc).__name__, "path": path}


class QuantObserver:
    """Collector injected into BrainRuntime. Never holds a Quant owner."""

    def __init__(self, cfg: Optional[ServiceConfig] = None, *, repo_root: Optional[Path] = None) -> None:
        self.cfg = cfg or load_config()
        self.repo_root = Path(repo_root) if repo_root is not None else self.cfg.target_root
        self.last_quant_reachable = False
        self.last_error = ""
        self.last = {}
        self.last_collected_at = None
        self._stream_lock = threading.Lock()
        self._stream_payload: Dict[str, Any] = {}
        self._stream_connected = False
        self._stream_messages = 0
        self._stream_last_at: Optional[float] = None
        self._stream_last_error = "not_started"
        self._stream_reconnects = 0
        self._rithmic_http_payload: Dict[str, Any] = {}
        self._rithmic_http_at: Optional[float] = None
        self._rithmic_http_error: Optional[str] = "not_started"

    @staticmethod
    def _stream_observations(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """Bounded, redacted event/log projection from the Trader WebSocket."""
        rows: List[Dict[str, Any]] = []
        for item in list(payload.get("recent_logs") or [])[-20:]:
            if not isinstance(item, Mapping):
                continue
            level = str(item.get("level") or "info").lower()
            if level not in {"warning", "error", "critical"}:
                continue
            rows.append({
                "source": "trader_websocket",
                "component": "trader_logs",
                "kind": "error" if level in {"error", "critical"} else "event",
                "severity": "high" if level == "critical" else "medium",
                "summary": redact(str(item.get("message") or ""))[:240],
                "timestamp": item.get("timestamp"),
            })
        for item in list(payload.get("events") or [])[-12:]:
            if not isinstance(item, Mapping):
                continue
            summary = str(item.get("summary") or item.get("message") or item.get("t") or "")
            if summary:
                rows.append({
                    "source": "trader_websocket",
                    "component": str(item.get("component") or "trader_events")[:80],
                    "kind": str(item.get("kind") or "event")[:40],
                    "severity": str(item.get("severity") or "low")[:16],
                    "summary": redact(summary)[:240],
                    "timestamp": item.get("timestamp") or item.get("ts"),
                })
        return rows[-24:]

    def _accept_stream_payload(self, payload: Mapping[str, Any]) -> None:
        if payload.get("snapshot_stale"):
            with self._stream_lock:
                self._stream_payload = {}
                self._stream_last_error = "snapshot_stale"
            return
        now = time.time()
        selected = {
            "sampled_market_quotes": dict(payload.get("sampled_market_quotes") or {}),
            FIELD: accept_summary(payload.get(FIELD)),
            "modules": dict(payload.get("modules") or {}) if isinstance(payload.get("modules"), Mapping) else {},
            "resources": dict(payload.get("resources") or {}) if isinstance(payload.get("resources"), Mapping) else {},
            "queue": dict(payload.get("queue") or {}) if isinstance(payload.get("queue"), Mapping) else {},
            "observations": self._stream_observations(payload),
        }
        with self._stream_lock:
            self._stream_payload = selected
            self._stream_messages += 1
            self._stream_last_at = now
            self._stream_last_error = ""

    def latest_quotes(self):
        with self._stream_lock:
            return dict(self._stream_payload.get("sampled_market_quotes") or {})

    def latest_rithmic_observation(self) -> Dict[str, Any]:
        """One observation contract through the existing HTTP/WS transports."""
        with self._stream_lock:
            candidates = []
            if self._rithmic_http_at is not None:
                candidates.append((self._rithmic_http_at, self._rithmic_http_payload,
                                   "http_status", self._rithmic_http_error))
            if self._stream_last_at is not None and FIELD in self._stream_payload:
                error = self._stream_last_error or (None if self._stream_connected else "stream_disconnected")
                candidates.append((self._stream_last_at, self._stream_payload[FIELD], "websocket", error))
            # Prefer a working transport, then its most recent observation.
            healthy = [item for item in candidates if not item[3] and time.time() - item[0] <= 90]
            selected = max(healthy or candidates, key=lambda item: item[0]) if candidates else None
        if selected is None:
            return project_summary(accept_summary(None), transport_error="observation_missing")
        observed_at, payload, transport, error = selected
        return project_summary(payload, observed_at=observed_at, transport=transport, transport_error=error)

    def stream_status(self) -> Dict[str, Any]:
        with self._stream_lock:
            age = None if self._stream_last_at is None else max(0.0, time.time() - self._stream_last_at)
            return {
                "enabled": bool(self.cfg.quant_ws_enabled),
                "connected": bool(self._stream_connected),
                "url": self.cfg.quant_ws_url,
                "messages_received": self._stream_messages,
                "last_message_at": self._stream_last_at,
                "last_message_age_s": round(age, 3) if age is not None else None,
                "reconnects": self._stream_reconnects,
                "last_error": self._stream_last_error or None,
                "max_message_bytes": self.cfg.ws_max_message_bytes,
            }

    async def stream_forever(self) -> None:
        """Consume Trader snapshots; disconnects never affect either process."""
        if not self.cfg.quant_ws_enabled:
            with self._stream_lock:
                self._stream_last_error = "disabled"
            return
        backoff = 0.5
        while True:
            try:
                import websockets

                async with websockets.connect(
                    self.cfg.quant_ws_url,
                    open_timeout=self.cfg.ws_open_timeout_s,
                    close_timeout=2.0,
                    ping_interval=20.0,
                    ping_timeout=10.0,
                    max_size=self.cfg.ws_max_message_bytes,
                ) as websocket:
                    with self._stream_lock:
                        self._stream_connected = True
                        self._stream_last_error = ""
                    backoff = 0.5
                    while True:
                        raw = await asyncio.wait_for(
                            websocket.recv(), timeout=self.cfg.ws_receive_timeout_s,
                        )
                        if not isinstance(raw, (str, bytes)):
                            raise ValueError("unsupported_websocket_frame")
                        if isinstance(raw, bytes):
                            raw = raw.decode("utf-8", errors="replace")
                        payload = json.loads(raw)
                        if not isinstance(payload, Mapping):
                            raise ValueError("websocket_payload_not_object")
                        self._accept_stream_payload(payload)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with self._stream_lock:
                    self._stream_connected = False
                    self._stream_reconnects += 1
                    self._stream_last_error = type(exc).__name__
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 2.0)
            finally:
                with self._stream_lock:
                    self._stream_connected = False

    def _paths(self) -> Dict[str, Path]:
        # Same locations perception uses, resolved from the repo rather than
        # from an in-process SystemConfig owner.
        from qhos2.config import Settings, validar_data_root
        default_lake = Settings.from_environ().data_root
        lake = validar_data_root(os.environ.get("BRAIN_QUANT_DATA_LAKE", str(default_lake)))
        logs = validar_data_root(os.environ.get("BRAIN_QUANT_LOGS", str(default_lake / "logs")))
        return {
            "truth": lake / "governance" / "CURRENT_STATE.json",
            "autonomy": lake / "autonomy.json",
            "committee_loop": lake / "committee_loop.json",
            "committee_state": lake / "orchestra_committee.json",
            "errors": logs / "errors.log",
        }

    def probe_quant(self) -> Dict[str, Any]:
        remote: Dict[str, Any] = {}
        reachable = False
        error = ""
        for path in self.cfg.allowed_get_paths:
            row = get_json(self.cfg.quant_base, path, timeout_s=self.cfg.observe_timeout_s)
            remote[path] = row
            if row.get("_observation_error"):
                error = (error + "; " if error else "") + path + ":" + row["_observation_error"]
            if path == "/api/health" and isinstance(row.get("process"), dict):
                reachable = (row["process"].get("state") == "PROCESS_ONLINE"
                             and row["process"].get("component") == "qhos2.coletor")
        self.last_quant_reachable = reachable
        self.last_error = error
        status = remote.get("/api/status") or {}
        with self._stream_lock:
            if FIELD in status and not status.get("_observation_error"):
                self._rithmic_http_payload = accept_summary(status.get(FIELD))
                self._rithmic_http_at = time.time()
                self._rithmic_http_error = None
            elif status.get("_observation_error"):
                self._rithmic_http_error = "http_observation_failed"
            else:
                self._rithmic_http_payload = accept_summary(None)
                self._rithmic_http_at = time.time()
                self._rithmic_http_error = "observation_missing"
        busy = remote.get("/api/busy") or {}
        health_process = (remote.get("/api/health") or {}).get("process")
        # Other valid responses prove partial telemetry, not process readiness.
        # Keep the readiness boolean health-only; restart policy is separate.
        partial = not reachable and (
            isinstance(status.get("modules"), dict)
            or isinstance(busy.get("busy"), bool)
            or (isinstance(health_process, dict) and bool(health_process.get("state")))
        )
        return {
            "reachable": reachable,
            "telemetry_partial": partial,
            "base": self.cfg.quant_base,
            "error": error or None,
            "health": remote.get("/api/health") or {},
            "status": status,
            "truth": remote.get("/api/truth") or {},
            "busy": busy,
            "qhos2_observation": accept_qhos2(remote.get("/api/qhos2/snapshot")),
        }

    def _own_resources(self) -> Dict[str, Any]:
        """This process, not Quant. A crash here must not be reported as theirs."""
        try:
            import psutil
        except ImportError:
            return {"subject": "brain_service", "error": "psutil_unavailable"}
        try:
            vm = psutil.virtual_memory()
            return {
                "cpu_percent": float(psutil.cpu_percent(interval=None)),
                "ram_percent": float(vm.percent),
                "ram_available_gb": round(vm.available / 1e9, 2),
                "subject": "brain_service",
            }
        except (OSError, ValueError, psutil.Error) as exc:
            return {"subject": "brain_service", "error": type(exc).__name__}

    def collect(self) -> Dict[str, Any]:
        paths = self._paths()
        quant = self.probe_quant()
        health = quant.get("health") or {}
        file_errors: Dict[str, str] = {}

        def lake_file(key: str) -> Dict[str, Any]:
            blob, error = read_json_file(paths[key])
            if error:
                file_errors[key] = error
            return blob

        truth = quant.get("truth") or lake_file("truth")
        autonomy = lake_file("autonomy")
        loop = lake_file("committee_loop")
        state = lake_file("committee_state")
        head = _git(self.repo_root, ["log", "-5", "--pretty=format:%h %s"])
        porcelain = _git(self.repo_root, ["status", "--porcelain"])
        dirty_lines = [ln.strip() for ln in porcelain["stdout"].splitlines() if ln.strip()]
        process = health.get("process") if isinstance(health.get("process"), Mapping) else {}
        with self._stream_lock:
            stream = dict(self._stream_payload)
        status_modules = (quant.get("status") or {}).get("modules") or {}
        registry = status_modules or stream.get("modules") or {}
        stream_queue = stream.get("queue") if isinstance(stream.get("queue"), Mapping) else {}
        queue_stats = stream_queue.get("stats") if isinstance(stream_queue.get("stats"), Mapping) else {}
        missing_paths = [str(path) for key,path in paths.items() if key in {"truth","errors"} and not path.is_file()]
        snapshot = {
            # file_errors is informational (corrupt/oversized lake files); it is kept
            # out of "errors" because health treats any entry there as missing evidence.
            "telemetry": {"errors": ([quant["error"]] if quant.get("error") else []) +
                          ["missing:"+path for path in missing_paths], "paths":{k:str(v) for k,v in paths.items()},
                          "file_errors": file_errors},
            "registry": registry,
            "application_ready": bool(quant["reachable"]),
            "truth": truth,
            "trading_autonomy": {
                "mode": autonomy.get("mode"),
                "reason": autonomy.get("reason"),
                "source": "disk" if autonomy else "absent",
            },
            "committee": {
                "loop_running": bool(loop.get("running")),
                "loop_wanted": bool(loop.get("wanted")) if "wanted" in loop else bool(loop.get("running")),
                "round": int(loop.get("round") or 0),
                "queued": len(state.get("queue") or []) if isinstance(state.get("queue"), list) else 0,
            },
            "resources": self._own_resources(),
            "git": {
                "available": bool(head["available"] and porcelain["available"]),
                "error": head["error"] or porcelain["error"],
                "recent_commits": [ln.strip() for ln in head["stdout"].splitlines() if ln.strip()][:5],
                "dirty_n": len(dirty_lines) if porcelain["available"] else None,
                "dirty_sample": dirty_lines[:8],
            },
            "error_tail": _tail_text(paths["errors"], 12),
            "observations": list(stream.get("observations") or []),
            "metrics": {
                "component": "trader_queue",
                "latency_ms": None,
                "queue_size": stream_queue.get("queue_size"),
                "active_jobs": stream_queue.get("active_jobs"),
                "total_failed": queue_stats.get("total_failed"),
            },
            "hpc": {},
            "remote": quant,
            "event_stream": self.stream_status(),
            FIELD: self.latest_rithmic_observation(),
            "qhos2_observation": quant["qhos2_observation"],
            "observed_at": time.time(),
            "quant_process": {
                "reachable": quant["reachable"],
                "telemetry_partial": quant["telemetry_partial"],
                "observe_url": self.cfg.quant_base,
                "pid": process.get("pid") if isinstance(process, Mapping) else None,
                "source": "http_get_allowlist",
                "runtime_release": health.get("runtime_release") if isinstance(health.get("runtime_release"), Mapping) else {},
                "restart_capability": bool(self.cfg.relight_enabled),
                "restart_mechanism": "guarded_legacy_relight" if self.cfg.relight_enabled else "disabled_level2_shadow",
            },
        }
        self.last = snapshot
        self.last_collected_at = snapshot["observed_at"]
        runtime_status = (health.get("status") or {})
        if runtime_status:
            snapshot["truth"] = {**snapshot["truth"], "health": runtime_status}
        return snapshot
