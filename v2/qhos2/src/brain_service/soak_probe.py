"""Read-only Linux integration soak for a running Brain and Trader."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import math
import ssl
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse, urlunparse


SOAK_MIN_DURATION_S = 6 * 3600


def _json_get(base: str, path: str, timeout_s: float, context=None) -> Dict[str, Any]:
    url = base.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    from brain_service.local_http import open_local
    with open_local(request, timeout=timeout_s, context=context) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("local_http_body_too_large")
    payload = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise ValueError("payload_not_object")
    return payload


def _brain_ws_url(base: str) -> str:
    parsed = urlparse(base)
    return urlunparse(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/ws/brain", "", "", ""))


async def _probe_brain_ws(base: str, timeout_s: float, context=None) -> str:
    import websockets
    from brain_service.config import loopback_url
    loopback_url(base)

    options = {"ssl": context} if base.startswith("https://") and context else {}
    async with websockets.connect(
        _brain_ws_url(base), open_timeout=timeout_s, close_timeout=2,
        max_size=2_000_000, **options,
    ) as websocket:
        raw = await asyncio.wait_for(websocket.recv(), timeout=timeout_s)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("schema") != "qhos-brain-ws-v1":
        raise ValueError("brain_websocket_schema_invalid")
    return str(payload.get("schema"))


def _trader_pid(health: Dict[str, Any]) -> int | None:
    loaded = (health.get("runtime_release") or {}).get("loaded") or {}
    pid = loaded.get("pid") or (health.get("process") or {}).get("pid")
    return pid if type(pid) is int and pid > 0 else None


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


async def run_probe(args: argparse.Namespace) -> Dict[str, Any]:
    started_wall = time.time()
    started = time.monotonic()
    deadline = started + args.duration_s
    failures = []
    samples = []
    first_brain_pid = None
    first_trader_pid = None
    first_source = None
    first_config = None
    brain_ws_schema = None
    max_rss = 0
    max_threads = 0
    stream_messages = 0
    context = ssl.create_default_context(cafile=args.ca_file) if getattr(args, "ca_file", None) else None
    last_sample_at = started
    first_trader_release = None
    last_stream_count = 0
    last_stream_progress = started

    try:
        brain_ws_schema = await _probe_brain_ws(args.brain_url, args.timeout_s, context)
    except Exception as exc:
        failures.append({"at_s": 0.0, "check": "brain_websocket", "error": type(exc).__name__})

    while True:
        elapsed = time.monotonic() - started
        now = time.monotonic()
        if now - last_sample_at > max(30.0, args.interval_s * 3 + args.timeout_s * 2):
            failures.append({"at_s": round(elapsed, 3), "check": "sampling_gap"})
        last_sample_at = now
        try:
            health, ready, runtime, identity, trader = await asyncio.gather(
                asyncio.to_thread(_json_get, args.brain_url, "/health", args.timeout_s, context),
                asyncio.to_thread(_json_get, args.brain_url, "/ready", args.timeout_s, context),
                asyncio.to_thread(_json_get, args.brain_url, "/api/brain/runtime", args.timeout_s, context),
                asyncio.to_thread(_json_get, args.brain_url, "/api/brain/authority", args.timeout_s, context),
                asyncio.to_thread(_json_get, args.trader_url, "/api/health", args.timeout_s),
            )
            running = runtime.get("running") or {}
            trader_process = trader.get("process") or {}
            brain_pid = running.get("pid")
            trader_pid = _trader_pid(trader)
            trader_release = (trader.get("runtime_release") or {}).get("loaded") or {}
            trader_revision = (trader_release.get("fingerprint"), trader_release.get("started_at"))
            first_trader_release = trader_revision if first_trader_release is None else first_trader_release
            source = running.get("source_revision")
            config = running.get("config_revision")
            first_brain_pid = brain_pid if first_brain_pid is None else first_brain_pid
            first_trader_pid = trader_pid if first_trader_pid is None else first_trader_pid
            first_source = source if first_source is None else first_source
            first_config = config if first_config is None else first_config
            resources = running.get("resources") or {}
            max_rss = max(max_rss, int(resources.get("rss_bytes") or 0))
            max_threads = max(max_threads, int(resources.get("threads") or 0))
            event_stream = identity.get("trader_event_stream") or {}
            stream_messages = max(stream_messages, int(event_stream.get("messages_received") or 0))
            if stream_messages > last_stream_count:
                last_stream_progress = time.monotonic()
                last_stream_count = stream_messages

            checks = {
                "brain_running": health.get("service_running") is True,
                "brain_ready": ready.get("ready") is True,
                "trader_not_required_for_brain_readiness": (ready.get("trader") or {}).get("required_for_brain_readiness") is False,
                "trader_identity": trader_process.get("state") == "PROCESS_ONLINE",
                "brain_pid_stable": type(brain_pid) is int and brain_pid > 0 and brain_pid == first_brain_pid,
                "trader_pid_stable": trader_pid is not None and trader_pid == first_trader_pid,
                "trader_release_stable": trader_revision == first_trader_release,
                "trader_release_current": (trader.get("runtime_release") or {}).get("state", "MATCH") == "MATCH",
                "source_stable": bool(source) and source == first_source,
                "config_stable": bool(config) and config == first_config,
                "trader_stream_progress": time.monotonic() - last_stream_progress <= 120,
                "runtime_current": runtime.get("stale_runtime") is False,
                "level3_off": runtime.get("level3_enabled") is False,
                "relight_off": (identity.get("relight") or {}).get("enabled") is False,
            }
            failed = sorted(name for name, passed in checks.items() if not passed)
            if failed:
                failures.append({"at_s": round(elapsed, 3), "check": "contract", "failed": failed})
            samples.append({
                "at_s": round(elapsed, 3),
                "brain_pid": brain_pid,
                "trader_pid": trader_pid,
                "trader_reachable": bool(identity.get("quant_reachable")),
                "stream_messages": int(event_stream.get("messages_received") or 0),
                "rss_bytes": int(resources.get("rss_bytes") or 0),
                "threads": int(resources.get("threads") or 0),
            })
        except Exception as exc:
            failures.append({"at_s": round(elapsed, 3), "check": "sample", "error": type(exc).__name__})

        _atomic_write(Path(args.output), {
            "schema": "qhos-brain-linux-soak-v1", "status": "RUNNING",
            "completed_soak": False, "started_at": started_wall,
            "updated_at": time.time(), "requested_duration_s": args.duration_s,
            "duration_s": round(time.monotonic() - started, 3),
            "samples": len(samples), "failures": failures,
            "brain_pid": first_brain_pid, "trader_pid": first_trader_pid,
            "running_source_revision": first_source, "running_config_revision": first_config,
            "trader_stream_messages": stream_messages,
        })

        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(min(args.interval_s, max(0.0, deadline - time.monotonic())))

    elapsed = time.monotonic() - started
    try:
        await _probe_brain_ws(args.brain_url, args.timeout_s, context)
    except Exception as exc:
        failures.append({"at_s": round(elapsed, 3), "check": "brain_websocket_final", "error": type(exc).__name__})
    if stream_messages < 1:
        failures.append({"at_s": round(elapsed, 3), "check": "trader_event_stream", "error": "no_messages"})
    completed_soak = elapsed >= SOAK_MIN_DURATION_S
    status = "SOAK_PASSED" if completed_soak and not failures else "SMOKE_PASSED" if not failures else "FAILED"
    result = {
        "schema": "qhos-brain-linux-soak-v1",
        "status": status,
        "completed_soak": completed_soak,
        "minimum_soak_duration_s": SOAK_MIN_DURATION_S,
        "requested_duration_s": args.duration_s,
        "duration_s": round(elapsed, 3),
        "started_at": started_wall,
        "completed_at": time.time(),
        "samples": len(samples),
        "failures": failures,
        "brain_websocket_schema": brain_ws_schema,
        "trader_stream_messages": stream_messages,
        "brain_pid": first_brain_pid,
        "trader_pid": first_trader_pid,
        "trader_release": first_trader_release,
        "brain_url": args.brain_url,
        "trader_url": args.trader_url,
        "tls_ca_file": getattr(args, "ca_file", None),
        "running_source_revision": first_source,
        "running_config_revision": first_config,
        "max_rss_bytes": max_rss,
        "max_threads": max_threads,
        "limitations": [
            "process/runtime integration only; not research or execution validation",
            "resource samples are descriptive and do not prove absence of leaks",
        ],
    }
    _atomic_write(Path(args.output), result)
    return result


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QHOS Brain Linux soak probe")
    parser.add_argument("--brain-url", default="http://127.0.0.1:8091")
    parser.add_argument("--trader-url", default="http://127.0.0.1:8766")
    parser.add_argument("--duration-s", type=float, default=SOAK_MIN_DURATION_S)
    parser.add_argument("--interval-s", type=float, default=10.0)
    parser.add_argument("--timeout-s", type=float, default=5.0)
    parser.add_argument("--output", default=".qhos-brain/linux-soak.json")
    parser.add_argument("--ca-file", help="Explicit CA/certificate trust for HTTPS and WSS; hostname verification stays enabled")
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v <= 0 for v in (args.duration_s, args.interval_s, args.timeout_s)):
        parser.error("duration, interval, and timeout must be positive")
    return args


def main(argv: list[str] | None = None) -> int:
    result = asyncio.run(run_probe(_parse(argv)))
    print(json.dumps({key: result[key] for key in (
        "status", "completed_soak", "duration_s", "samples", "failures",
    )}, ensure_ascii=False, allow_nan=False))
    return 0 if result["status"] in {"SOAK_PASSED", "SMOKE_PASSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
