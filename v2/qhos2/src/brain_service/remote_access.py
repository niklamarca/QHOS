"""Minimal, explicit data boundary for the optional remote conversation.

Never pass logs, paths, commands, positions, chat history or secrets to Discord.
The bridge only obtains operational facts and a separate read-only conversation.
"""
from __future__ import annotations

import re


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _enum(value, allowed):
    return value if isinstance(value, str) and value in allowed else "UNKNOWN"


def _count(value):
    return value if type(value) is int and 0 <= value <= 1_000_000_000 else None


def remote_snapshot(state):
    """An allowlist, not redaction of an arbitrary full snapshot."""
    state = _mapping(state)
    brain, trader = _mapping(state.get("brain")), _mapping(state.get("trader"))
    tester, audit = _mapping(state.get("tester")), _mapping(state.get("regression_audit"))
    watchdog = _mapping(state.get("watchdog"))
    connection = _enum(trader.get("connection_state"), {"ONLINE", "OFFLINE", "INDETERMINATE"})
    stale = trader.get("stale") is not False
    partial = trader.get("telemetry_partial") is not False
    if stale or partial:
        connection = "INDETERMINATE"
    rows = list(_mapping(trader.get("modules")).values())[:200]
    valid = [row for row in rows if isinstance(row, dict)]
    return {
        "brain": {"running": brain.get("running") if type(brain.get("running")) is bool else None},
        "trader": {
            "connection_state": connection, "stale": stale, "telemetry_partial": partial,
            "process": _enum(trader.get("process"), {"OK", "ERROR", "DEGRADED", "OFFLINE", "UNAVAILABLE"}),
            "data_integrity": _enum(trader.get("data_integrity"), {"OK", "DEGRADED", "BLOCKED", "UNAVAILABLE"}),
            "research_integrity": _enum(trader.get("research_integrity"), {"OK", "DEGRADED", "BLOCKED", "UNAVAILABLE"}),
            "execution_readiness": _enum(trader.get("execution_readiness"), {"READY", "BLOCKED", "UNAVAILABLE"}),
            "generation": _count(trader.get("generation")),
            "runtime_release_state": _enum(trader.get("runtime_release_state"), {"MATCH", "MISMATCH", "UNKNOWN"}),
            "modules_total": len(rows),
            "modules_healthy": sum(row.get("healthy") is True for row in valid),
        },
        "watchdog": {"phase": _enum(watchdog.get("phase"), {
            "HEALTHY", "SUSPECT", "RESTART_PENDING", "RESTARTING", "VERIFYING", "QUARANTINED"})},
        "tester": {"phase": _enum(tester.get("phase"), {"PASSED", "FAILED", "RUNNING", "NOT_RUN"}),
                   "passed": _count(tester.get("passed")), "failed": _count(tester.get("failed")),
                   "summary": "Verificações rápidas; não substituem a regressão ampla."},
        "regression_audit": {
            "available": audit.get("available") is True,
            "phase": _enum(audit.get("phase"), {"PASSED", "FAILED", "EMPTY", "NOT_RUN", "UNAVAILABLE"}),
            "stale": audit.get("stale") is not False,
            "passed": _count(audit.get("passed")), "failed": _count(audit.get("failed")),
            "collection_errors": _count(audit.get("collection_errors")),
        },
    }


def safe_text(value, limit=1800):
    """Defense in depth; the context itself already contains no free-form logs."""
    value = str(value or "")[:8000]
    value = re.sub(r"https?://\S+", "[link omitido]", value, flags=re.I)
    value = re.sub(r"(?i)(?:[A-Z]:[\\/]|\\\\)[^\n<>\"|]*", "[caminho local omitido]", value)
    value = re.sub(r"(?i)((?:api[_-]?key|token|password|secret|senha)\s*[=:]\s*)\S+", r"\1[omitido]", value)
    value = re.sub(r"[A-Za-z0-9_-]{40,}", "[identificador omitido]", value)
    # Suppress Discord markdown mentions even if a caller forgets allowed_mentions.
    value = value.replace("@", "＠")
    return value[:limit]


def status_text(snapshot):
    t, q, a = snapshot["trader"], snapshot["tester"], snapshot["regression_audit"]
    return (f"Brain: {'ativo' if snapshot['brain']['running'] else 'indeterminado'}. "
            f"Trader: {t['connection_state']}; código: {t['runtime_release_state']}.\n"
            f"Módulos saudáveis: {t['modules_healthy']}/{t['modules_total']}; geração: {t['generation']}.\n"
            f"Pesquisa: {t['research_integrity']}; execução: {t['execution_readiness']}.\n"
            f"Checks rápidos: {q['passed']} aprovados / {q['failed']} falhas.\n"
            f"Regressão: {a['passed']} aprovados / {a['failed']} falhas / {a['collection_errors']} erros de coleta; "
            f"{'desatualizada ou indisponível' if a['stale'] or not a['available'] else 'última auditoria disponível'}.\n"
            "Pesquisa ativa não comprova aprendizado. Este canal não executa ações.")
