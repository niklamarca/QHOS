"""Health engine: consolidate existing signals with explicit evidence.

Does not invent a score. Formula is subtractive from 1.0 and documented.
Causality of any derived incident stays OBSERVED / SUSPECTED, never CONFIRMED.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

from .models import Confidence, Evidence, HealthTone, confidence_from_evidence


CRITICAL_MODULES = ("risk_engine", "orchestrator", "storage")
BOOT_PENDING_STATUSES = frozenset({
    "pending", "starting", "initializing", "init", "booting", "warmup",
})


def _tone(score: float, critical: bool) -> HealthTone:
    if critical:
        return HealthTone.CRITICAL
    if score >= 0.85:
        return HealthTone.OK
    if score >= 0.65:
        return HealthTone.WARNING
    if score >= 0.4:
        return HealthTone.DEGRADED
    return HealthTone.CRITICAL


def evaluate(observation: Mapping[str, Any]) -> Dict[str, Any]:
    evidence: List[Evidence] = []
    deductions: List[Tuple[str, float]] = []
    components: Dict[str, Dict[str, Any]] = {}

    registry = observation.get("registry") if isinstance(observation.get("registry"), dict) else {}
    ready = bool(observation.get("application_ready"))
    telemetry = observation.get("telemetry") or {}
    unknown = not registry or not ready or bool(telemetry.get("errors")) or any(
        name not in registry for name in CRITICAL_MODULES)
    critical_down = []
    not_ready = []
    degraded = []
    for name, info in registry.items():
        if not isinstance(info, dict):
            continue
        healthy = bool(info.get("healthy"))
        status = str(info.get("status") or "unknown")
        components[name] = {"healthy": healthy, "status": status}
        if healthy and status not in BOOT_PENDING_STATUSES:
            evidence.append(Evidence("module_healthy", f"registry.{name}", status, 0.3))
        if status in BOOT_PENDING_STATUSES:
            unknown = True
            if not ready:
                continue
            if name in CRITICAL_MODULES:
                not_ready.append(name)
                evidence.append(Evidence(
                    signal="critical_module_not_ready",
                    source=f"registry.{name}",
                    detail=status,
                    weight=2.0,
                ))
            else:
                degraded.append(name)
            continue
        if name in CRITICAL_MODULES and not healthy:
            critical_down.append(name)
            evidence.append(Evidence(
                signal="critical_module_unhealthy",
                source=f"registry.{name}",
                detail=status,
                weight=2.0,
            ))
        elif not healthy:
            degraded.append(name)
            evidence.append(Evidence(
                signal="module_unhealthy",
                source=f"registry.{name}",
                detail=status,
                weight=0.8,
            ))

    if critical_down or not_ready:
        deductions.append(("critical_modules", 0.45))
    deductions.append(("degraded_modules", min(0.08 * len(degraded), 0.32)))

    truth = observation.get("truth") if isinstance(observation.get("truth"), dict) else {}
    health = truth.get("health") if isinstance(truth.get("health"), dict) else {}
    research_integrity = str(health.get("research_integrity") or health.get("process") or "")
    if truth.get("snapshot_stale"):
        deductions.append(("stale_truth", 0.15))
        evidence.append(Evidence(
            signal="snapshot_stale",
            source="CURRENT_STATE",
            detail="dashboard/discord/agents may be reading a stale snapshot",
            weight=1.2,
        ))
    if research_integrity in {"DEGRADED", "BLOCKED"}:
        deductions.append(("research_integrity", 0.12 if research_integrity == "DEGRADED" else 0.25))
        evidence.append(Evidence(
            signal="research_integrity",
            source="CURRENT_STATE.health",
            detail=research_integrity,
            weight=1.0,
        ))

    resources = observation.get("resources") if isinstance(observation.get("resources"), dict) else {}
    ram = float(resources.get("ram_percent") or 0.0)
    cpu = float(resources.get("cpu_percent") or 0.0)
    if ram >= 90:
        deductions.append(("ram_critical", 0.2))
        evidence.append(Evidence("ram_percent", "psutil", f"{ram:.0f}%", 1.5))
    elif ram >= 80:
        deductions.append(("ram_high", 0.08))
        evidence.append(Evidence("ram_percent", "psutil", f"{ram:.0f}%", 0.6))
    if cpu >= 95:
        deductions.append(("cpu_hot", 0.08))
        evidence.append(Evidence("cpu_percent", "psutil", f"{cpu:.0f}%", 0.5))

    errors = observation.get("error_tail") if isinstance(observation.get("error_tail"), list) else []
    if errors:
        deductions.append(("error_log", 0.08))
        evidence.append(Evidence("error_tail", "logs/errors.log", f"{len(errors)} recent lines", 0.5))

    hpc = observation.get("hpc") if isinstance(observation.get("hpc"), dict) else {}
    kill = int(hpc.get("kill_level") or 0)
    if kill >= 2:
        deductions.append(("kill_switch", 0.35))
        evidence.append(Evidence("kill_level", "hpc_orchestrator", str(kill), 2.0))
        critical_down.append("kill_switch")
    elif kill == 1:
        deductions.append(("kill_l1", 0.1))
        evidence.append(Evidence("kill_level", "hpc_orchestrator", "1", 0.8))

    score = 1.0
    for _label, amount in deductions:
        score -= amount
    score = max(0.0, min(1.0, score))

    issues = []
    if unknown:
        issues.append({"component":"telemetry", "severity":"warning", "kind":"insufficient_evidence"})
        detail = "; ".join(str(error) for error in telemetry.get("errors", [])) or "Missing, failed or incomplete runtime observation"
        evidence.append(Evidence("telemetry_incomplete", "observer", detail, 1.0))
    for name in not_ready:
        issues.append({"component": name, "severity": "critical", "kind": "not_ready"})
    for name in critical_down:
        issues.append({"component": name, "severity": "critical", "kind": "unhealthy"})
    for name in degraded:
        issues.append({"component": name, "severity": "warning", "kind": "unhealthy"})

    confidence = confidence_from_evidence(evidence)
    tone = _tone(score, bool(critical_down or not_ready) or kill >= 2)
    if unknown and tone == HealthTone.OK:
        tone = HealthTone.DEGRADED
    return {
        "global_score": None if unknown else round(score, 4),
        "evidence_state": "insufficient" if unknown else "observed",
        "tone": tone.value,
        "critical_issues": [i for i in issues if i["severity"] == "critical"],
        "warnings": [i for i in issues if i["severity"] != "critical"],
        "components": components,
        "deductions": [{"reason": r, "amount": a} for r, a in deductions if a > 0],
        "confidence": confidence.to_dict(),
        "causality": "observed",
        "formula": "1.0 minus documented deductions; never claimed as a trading health score",
    }
