"""Bounded LLM-OFF soak runner. Read-only. Never enables Level 3."""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Dict, List, Optional

from brain.attest import runtime_attestation
from brain.health import evaluate
from brain.models import utc_ts
from brain.perception import collect_observation
from brain.local_config import config

from .engine import DiscoveryEngine
from .revision import session_provenance
from .soak import SoakSession, prune_shadow, shadow_dir


def _get(host, path):
    """Bounded GET to a local observer; redirects cannot escape the loopback."""
    host = host or os.environ.get("BRAIN_QUANT_OBSERVE_URL", "http://127.0.0.1:8766")
    parsed = urlsplit(host)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("observer_url_must_be_local")
    if path not in {"/api/health", "/api/status"}:
        raise ValueError("observer_route_not_allowed")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(host.rstrip("/") + path, timeout=5) as response:
        raw = response.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("observer_response_too_large")
    return json.loads(raw)


def enrich_from_live_api(base: Dict[str, Any], *, host: Optional[str] = None) -> Dict[str, Any]:
    """Best-effort enrichment from a running Trader HTTP surface. Fail closed."""
    out = dict(base)
    try:
        status = _get(host, "/api/status")
    except Exception:
        return out
    if not isinstance(status, dict):
        return out
    modules = status.get("modules") if isinstance(status.get("modules"), dict) else {}
    if modules and not out.get("registry"):
        registry = {}
        for name, info in list(modules.items())[:24]:
            if isinstance(info, dict):
                registry[name] = {
                    "healthy": info.get("healthy") if type(info.get("healthy")) is bool else None,
                    "status": info.get("status") or ("ok" if info.get("healthy") else "unknown"),
                    # Lifecycle evidence: without these a pending status is a default.
                    "uptime": info.get("uptime"),
                    "error_count": info.get("error_count"),
                    "last_error": info.get("last_error"),
                }
            else:
                registry[name] = {"healthy": None, "status": "unknown"}
        out["registry"] = registry
    hpc = out.get("hpc") if isinstance(out.get("hpc"), dict) else {}
    out["hpc"] = {
        **hpc,
        "cycle": status.get("cycle") if status.get("cycle") is not None else hpc.get("cycle"),
        "kill_level": status.get("kill_level") if status.get("kill_level") is not None else hpc.get("kill_level"),
        "queue": status.get("queue") if isinstance(status.get("queue"), dict) else hpc.get("queue"),
    }
    res = status.get("resources") if isinstance(status.get("resources"), dict) else {}
    if res:
        out["resources"] = {**(out.get("resources") or {}), **res}
    return out


def trader_reachable(host: Optional[str] = None) -> Dict[str, Any]:
    try:
        health = _get(host, "/api/health")
        return {
            "reachable": True,
            "health_status": (health or {}).get("status") if isinstance(health, dict) else None,
            "brain_api": False,
            "brain_api_reason": "separate_brain_service",
        }
    except Exception as exc:
        return {"reachable": False, "reason": type(exc).__name__, "brain_api": False}


def run_bounded_soak(
    *,
    duration_s: float = 60.0,
    interval_s: float = 5.0,
    host: Optional[str] = None,
    lake: Optional[Path] = None,
    enrich_http: bool = True,
    allow_offline: bool = False,
) -> Dict[str, Any]:
    """One LLM-OFF soak. Does not flip permanent config. No Level 3. No sandbox.

    ``allow_offline`` keeps the soak running out-of-process when the Trader HTTP
    surface is down. Filesystem/log/git signals are still real; the HTTP registry
    is simply unavailable, and that degradation is recorded in the payload.
    """
    if (type(duration_s) not in (float, int) or not math.isfinite(duration_s) or not 0 < duration_s <= 3600
            or type(interval_s) not in (float, int) or not math.isfinite(interval_s) or not 0.1 <= interval_s <= 60):
        raise ValueError("invalid_soak_duration_or_interval")
    probe = trader_reachable(host)
    if not probe.get("reachable"):
        if not allow_offline:
            return {
                "REAL_SOAK": "BLOCKED",
                "reason": f"trader_unreachable:{probe.get('reason')}",
                "llm_enabled": False,
                "level3_enabled": False,
                "sandbox_enabled": False,
                "change_request": None,
                "writes": 0,
            }
        enrich_http = False

    engine = DiscoveryEngine()
    session = SoakSession(duration_s=float(duration_s))
    session.start()
    t0 = utc_ts()
    cycles = 0
    all_roots: Dict[str, Dict[str, Any]] = {}
    all_improvements: Dict[str, Dict[str, Any]] = {}
    stability: List[Dict[str, Any]] = []
    try:
        while True:
            now = utc_ts()
            if now - t0 >= float(duration_s):
                break
            raw = collect_observation()
            if enrich_http:
                raw = enrich_from_live_api(raw, host=host)
            health = evaluate(raw)
            intel = engine.ingest(raw, health=health, api_enabled=False, soak=True)
            intel["llm_called"] = False
            intel["change_request"] = None
            intel["sandbox"] = None
            session.record_cycle(intel, list(intel.get("observations") or []))
            cycles += 1
            for cand in intel.get("incidents") or []:
                all_roots[str(cand.get("root_id") or cand.get("fingerprint"))] = cand
            for imp in intel.get("improvements") or []:
                all_improvements[str(imp.get("fingerprint") or imp.get("candidate_id"))] = imp
            m = intel.get("metrics") or {}
            sm = intel.get("signal_metrics") or {}
            stability.append({
                "cycle": cycles,
                "t_offset_s": round(utc_ts() - t0, 3),
                "signals": int(m.get("signals") or 0),
                "candidates": int(m.get("candidate_n") or 0),
                "noise_suppressed": int(m.get("noise_suppressed") or 0),
                "duplicates": int(m.get("duplicates") or 0),
                "root_clusters": int(m.get("root_clusters") or 0),
                "root_collapsed": int(m.get("root_collapsed_occurrences") or 0),
                "pending_suppressed": int(m.get("pending_suppressed") or 0),
                "processing_latency_s": (sm.get("self_health") or {}).get("processing_latency_s"),
                "seen_keys": len(engine.seen),
                "root_keys": len(engine.roots),
                "queue_depth": (sm.get("self_health") or {}).get("queue_depth"),
            })
            if cycles % 3 == 0:
                session.checkpoint()
            remaining = float(duration_s) - (utc_ts() - t0)
            if remaining <= 0:
                break
            time.sleep(min(float(interval_s), max(0.1, remaining)))
        payload = session.close()
    except Exception as exc:
        payload = session.fail(type(exc).__name__)
        payload["REAL_SOAK"] = "FAILED"
        payload["error"] = type(exc).__name__
        return payload

    safety = session.safety_assertions()
    payload["REAL_SOAK"] = "EXECUTED"
    payload["cycles"] = cycles
    payload["probe"] = probe
    payload["source_availability"] = {
        "http_registry": bool(enrich_http and probe.get("reachable")),
        "filesystem_logs": True,
        "git": True,
        "degraded": not (enrich_http and probe.get("reachable")),
    }
    # Provenance so a session artifact stays scientifically attributable.
    payload["provenance"] = session_provenance({
        "session_id": session.session_id,
        "started_ts": t0,
        "ended_ts": utc_ts(),
        "duration_requested_s": float(duration_s),
        "duration_actual_s": round(utc_ts() - t0, 3),
        "interval_s": float(interval_s),
        "cycles": cycles,
        "runtime_attestation": runtime_attestation(),
        "source_availability": payload["source_availability"],
    })
    payload["engine_metrics"] = engine.last_intel.get("metrics") if engine.last_intel else {}
    payload["signal_metrics"] = (engine.last_intel or {}).get("signal_metrics") or {}
    payload["pending_model"] = (engine.last_intel or {}).get("pending_model") or {}
    payload["telemetry_ledger"] = (engine.last_intel or {}).get("telemetry_ledger") or {}
    payload["telemetry_recommendations"] = (engine.last_intel or {}).get("telemetry_recommendations") or []
    payload["root_candidates"] = [
        {
            "root_id": c.get("root_id"),
            "root_title": c.get("root_title"),
            "root_tier": c.get("root_tier"),
            "score": c.get("score"),
            "occurrence_count": c.get("occurrence_count"),
            "affected_instances": c.get("affected_instances"),
            "affected_components": c.get("affected_components"),
            # Without this a reviewed disposition cannot be attributed back to
            # the telemetry source that produced it, and per-source useful yield
            # stays permanently zero.
            "sources": c.get("sources"),
            "root_signature": c.get("root_signature"),
            "promotion_reason": c.get("promotion_reason"),
            "score_components": c.get("score_components"),
        }
        for c in all_roots.values()
    ]
    payload["candidates_all"] = list(all_roots.values())
    payload["improvements_all"] = list(all_improvements.values())
    payload["unique_root_candidates"] = len(all_roots)
    payload["unique_improvements"] = len(all_improvements)
    payload["stability"] = stability
    payload["cumulative_metrics"] = {
        "signals": sum(r["signals"] for r in stability),
        "noise_suppressed": sum(r["noise_suppressed"] for r in stability),
        "duplicates": sum(r["duplicates"] for r in stability),
        "root_collapsed": sum(r["root_collapsed"] for r in stability),
        "pending_suppressed": sum(r["pending_suppressed"] for r in stability),
        "candidate_rows": sum(r["candidates"] for r in stability),
    }
    payload["safety_ok"] = safety.get("ok") is True and safety.get("remote_llm_calls") == 0
    payload["path"] = str(shadow_dir() / f"{session.session_id}.json")
    payload["storage_bytes"] = Path(payload["path"]).stat().st_size if Path(payload["path"]).exists() else 0
    payload["trader_unaffected"] = True
    payload["brain_api_in_process"] = bool(probe.get("brain_api"))
    prune_shadow()
    return payload
