"""Grounded operational assistant. No generated text may execute a command."""
from __future__ import annotations

import time
import json
import math
import unicodedata
import threading
import os
import stat
import re
from collections import deque
from pathlib import Path
from brain_service.codex_chat import CodexChat
from brain_service.remote_access import safe_text
from brain_service.qhos2_observation import accept as accept_qhos2, unavailable as unavailable_qhos2
from brain_service.rithmic_observation import FIELD, accept_summary, project_summary


REGRESSION_AUDIT_SCHEMA = "brain-regression-audit-v1"
REGRESSION_AUDIT_MAX_BYTES = 64 * 1024
REGRESSION_AUDIT_STALE_SECONDS = 24 * 3600
REPAIR_HISTORY_MAX_BYTES = 24000
REPAIR_HISTORY_MAX_ROWS = 15


def conversation_mode(text):
    """Personal by default; only explicit operational topics retrieve code."""
    normalized = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()
    if re.search(r"\b(?:pesquis\w*|busqu\w*|procur\w*)\b.*\b(?:internet|web|online|sites?)\b", normalized):
        return "personal"
    if re.search(r"\b(?:trader|servidor|watchdog|telemetria|modulos?|regressao|auditoria|tester)\b", normalized):
        return "operational"
    if re.search(r'\b(?:linux|cpu|ram|disco|systemd)\b', normalized) and re.search(r'\b(?:verifi\w*|audit\w*|funcion\w*|erro\w*|falha\w*|estado|status|consumo|uso|diagnost\w*)\b', normalized):
        return 'operational'
    if re.fullmatch(r"\s*(?:qual |como esta |qual e )?(?:o |a )?(?:status|estado|qualidade|diagnostico)(?: atual| do sistema)?[?!.\s]*", normalized):
        return "operational"
    return "personal"


def _explicit_intent(text):
    """Only a direct imperative is a command; mentions/questions are context."""
    if "?" in text or re.search(r"\b(?:nao|nunca|jamais)\b", text):
        return None
    prefix = r"^(?:por favor[\s,]+)?"
    verb = r"(?:execute|executar|rode|rodar|inicie|iniciar)\s+"
    if re.match(prefix + verb + r"(?:(?:a|uma)\s+)?regressao\b", text):
        return "regression"
    if re.match(prefix + verb + r"(?:(?:os|as|o|a)\s+)?(?:testes?|verificacoes(?:\s+rapidas)?)\b", text):
        return "tester"
    if re.match(prefix + r"(?:reinicie|reiniciar|religue|religar|restart)\b", text):
        return "confirm_restart"
    return None


def _history_path_identity(path):
    """lstat the entire lexical path: resolving first would hide redirects."""
    identities = []
    for current in reversed((path, *path.parents)):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("linked_history_path")
        if current == path:
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("history_not_regular")
        elif not stat.S_ISDIR(info.st_mode):
            raise ValueError("history_ancestor_not_directory")
        identities.append((info.st_dev, info.st_ino))
    return identities


def read_repair_history(cfg):
    """Bounded, redacted UI projection; no raw log record enters the API."""
    path = Path(cfg.state_dir).absolute() / "repair_history.jsonl"
    try:
        identity = _history_path_identity(path)
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino) != identity[-1] or _history_path_identity(path) != identity:
                return []
            offset = max(0, opened.st_size - REPAIR_HISTORY_MAX_BYTES)
            handle.seek(offset)
            raw = handle.read(min(opened.st_size, REPAIR_HISTORY_MAX_BYTES))
            if offset:
                raw = raw.partition(b"\n")[2]  # Never parse a partial first line.
    except (OSError, ValueError):
        return []
    repairs = []
    for line in raw.decode("utf-8", errors="replace").splitlines()[-REPAIR_HISTORY_MAX_ROWS:]:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        message = row.get("message", row.get("summary", ""))
        if not isinstance(message, str) or not message.strip():
            continue
        kind = row.get("type", "repair")
        timestamp = row.get("timestamp")
        try:
            valid_time = type(timestamp) in (int, float) and math.isfinite(timestamp) and 0 < timestamp <= time.time() + 60
        except (ValueError, OverflowError):
            valid_time = False
        repairs.append({"timestamp": timestamp if valid_time else None,
                        "type": safe_text(kind if isinstance(kind, str) else "repair", 80),
                        "message": safe_text(message, 1200)})
    return repairs


def read_regression_audit(cfg, *, now=None):
    """Read a bounded external test report as data; never run its contents."""
    now = time.time() if now is None else now
    missing = {"schema": REGRESSION_AUDIT_SCHEMA, "available": False,
               "phase": "UNAVAILABLE", "generated_at": None, "age_seconds": None,
               "stale": True, "stale_after_seconds": REGRESSION_AUDIT_STALE_SECONDS,
               "passed": None, "failed": None, "collection_errors": None,
               "summary": "Auditoria ampla indisponível; verificações rápidas não substituem a regressão.",
               "findings": []}
    try:
        with (cfg.state_dir / "regression_audit.json").open("rb") as handle:
            raw = handle.read(REGRESSION_AUDIT_MAX_BYTES + 1)
        if len(raw) > REGRESSION_AUDIT_MAX_BYTES:
            return {**missing, "reason": "report_too_large"}
        report = json.loads(raw.decode("utf-8-sig"))
    except FileNotFoundError:
        return {**missing, "phase": "NOT_RUN", "reason": "report_missing"}
    except (OSError, UnicodeError, ValueError):
        return {**missing, "reason": "report_unreadable"}
    if not isinstance(report, dict) or report.get("schema") != REGRESSION_AUDIT_SCHEMA:
        return {**missing, "reason": "schema_mismatch"}
    if report.get("target_root") != str(cfg.target_root):
        return {**missing, "reason": "target_mismatch"}
    generated = report.get("generated_at")
    try:
        valid_timestamp = (not isinstance(generated, bool) and isinstance(generated, (int, float))
                           and math.isfinite(generated) and 0 < generated <= now + 60)
    except (OverflowError, ValueError):
        valid_timestamp = False
    if not valid_timestamp:
        return {**missing, "reason": "invalid_timestamp"}
    for key in ("passed", "failed", "collection_errors"):
        if type(report.get(key)) is not int or report[key] < 0:
            return {**missing, "reason": "invalid_counts"}
    if not isinstance(report.get("summary"), str) or not isinstance(report.get("findings"), list):
        return {**missing, "reason": "invalid_report"}
    findings = []
    for row in report["findings"][:10]:
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("severity", "title", "detail")):
            continue
        findings.append({"severity": row["severity"][:30], "title": row["title"][:200],
                         "detail": row["detail"][:1500]})
    age = max(0, now - generated)
    stale = age >= REGRESSION_AUDIT_STALE_SECONDS
    phase = "FAILED" if report["failed"] or report["collection_errors"] else "PASSED" if report["passed"] else "EMPTY"
    return {"schema": REGRESSION_AUDIT_SCHEMA, "available": True, "target_root": str(cfg.target_root),
            "phase": phase, "generated_at": generated, "age_seconds": round(age, 1),
            "stale": stale, "stale_after_seconds": REGRESSION_AUDIT_STALE_SECONDS,
            "passed": report["passed"], "failed": report["failed"], "collection_errors": report["collection_errors"],
            "summary": report["summary"][:1500], "findings": findings,
            "findings_truncated": len(report["findings"]) > 10}


def regression_summary(report):
    if not report.get("available"):
        return "Auditoria ampla indisponível; os checks rápidos não comprovam a qualidade completa."
    if report.get("phase") == "EMPTY":
        return "A auditoria ampla não contém testes executados e não comprova aprovação."
    age = int(report["age_seconds"] // 60)
    freshness = "desatualizada" if report["stale"] else "última leitura"
    return (f"Auditoria ampla ({freshness}, há {age} min): {report['passed']} aprovados, "
            f"{report['failed']} falhas e {report['collection_errors']} erros de coleta. {report['summary']}")


class OperationalAssistant:
    def __init__(self, process):
        self.process = process
        self.activity = deque(maxlen=80)
        self.model = CodexChat(process.cfg.state_dir)
        self._remote_models = {}
        self._remote_models_lock = threading.Lock()

    def remote_chat(self, message, user_id):
        """Separate memory and an allowlisted context; no action dispatch."""
        from brain_service.remote_access import remote_snapshot, safe_text, status_text

        user_id = str(user_id)
        if not user_id.isascii() or not user_id.isdigit() or not 15 <= len(user_id) <= 22:
            return {"ok": False, "reply": "Identidade remota inválida."}
        text = safe_text(message, 2500).strip()
        if not text:
            return {"ok": False, "reply": "Envie uma pergunta em texto."}
        # Bounded, per-user memory. Never reuse the local owner's conversation.
        with self._remote_models_lock:
            if user_id not in self._remote_models:
                if len(self._remote_models) >= 8:
                    return {"ok": False, "reply": "Limite de conversas remotas atingido."}
                self._remote_models[user_id] = CodexChat(self.process.cfg.state_dir / "discord" / user_id)
            model = self._remote_models[user_id]
        snapshot = remote_snapshot(self.state())
        answer = model.answer(text, snapshot)
        return {"ok": True, "reply": safe_text(answer or status_text(snapshot)),
                "source": "model" if answer else "local-fallback", "read_only": True}

    def record(self, kind, message):
        self.activity.appendleft({"timestamp": time.time(), "type": kind, "message": message})

    def state(self):
        p = self.process
        snap = p.snapshot()
        observed = p.observer.last
        remote = observed.get("remote", {})
        health = remote.get("health", {})
        status = health.get("status", {})
        details = remote.get("status", {})
        hb = health.get("process", {})
        stale = time.time() - (observed.get("observed_at") or 0) > 90
        reachable = p.observer.last_quant_reachable and not stale
        partial = bool(remote.get("telemetry_partial") or (observed.get("telemetry") or {}).get("errors")) and not stale
        connection_state = "INDETERMINATE" if stale or partial else "ONLINE" if reachable else "OFFLINE"
        runtime_release = health.get("runtime_release")
        repairs = read_repair_history(p.cfg)
        quotes = dict(getattr(p.observer, "latest_quotes", lambda: {})() or (observed.get("truth") or {}).get("sampled_market_quotes") or {})
        if time.time() - float(quotes.get("published_at") or 0) > 10:
            quotes["state"] = "STALE"
        quotes["can_execute"] = False
        rithmic = getattr(p.observer, "latest_rithmic_observation", None)
        rithmic = rithmic() if callable(rithmic) else project_summary(accept_summary(None))
        return {
            "brain": {"running": p.started, "cycles": snap.get("cycles", 0),
                      "heartbeat": snap.get("heartbeat", {}),
                      "health_score": snap.get("health", {}).get("global_score"),
                      "health_tone": snap.get("health", {}).get("tone", "unknown")},
            "trader": {"sampled_market_quotes": quotes, FIELD: rithmic, "reachable": reachable,
                       "qhos2_observation": accept_qhos2(observed["qhos2_observation"])
                       if "qhos2_observation" in observed else unavailable_qhos2(),
                       "telemetry_errors": (observed.get("telemetry") or {}).get("errors", []),
                       "telemetry_partial": partial,
                       "connection_state": connection_state,
                       "stale": stale,
                       "process": status.get("process", "UNAVAILABLE"),
                       "data_integrity": status.get("data_integrity", "UNAVAILABLE"),
                       "research_integrity": status.get("research_integrity", "UNAVAILABLE"),
                       "execution_readiness": status.get("execution_readiness", "UNAVAILABLE"),
                       "heartbeat_age_seconds": hb.get("heartbeat_age_seconds"),
                       "generation": details.get("generation"),
                       "busy": remote.get("busy", {}).get("busy"),
                       "modules": observed.get("registry", {}),
                       "loaded_code_state": hb.get("loaded_code_state"),
                       "runtime_release_state": runtime_release.get("state") if isinstance(runtime_release, dict) else None,
                       "observed_at": observed.get("observed_at")},
            "watchdog": p.relight.status(), "tester": p.tester.snapshot(),
            "test_schedule": {"interval_seconds": getattr(p.cfg, "test_interval_s", 3600),
                              "next_smoke_at": getattr(p, "next_smoke_at", None)},
            "monitor": p.monitor_status() if callable(getattr(p, "monitor_status", None)) else {},
            "regression": p.regression.snapshot() if hasattr(p, "regression") else {"phase": "NOT_CONFIGURED"},
            "cognition": p.investigation.snapshot() if hasattr(p, "investigation") else {"mode": "NOT_CONFIGURED"},
            "regression_audit": read_regression_audit(p.cfg),
            "incidents": snap.get("incidents", []), "activity": list(self.activity) + list(reversed(repairs)),
            "intelligence": snap.get("live_intelligence", {}),
            "target_root": str(p.cfg.target_root),
            "assistant": {"mode": "codex-with-local-fallback", "generative_model": self.model.status == "connected",
                          "model_status": self.model.status,
                          "model_error": self.model.last_error, "model_error_detail": self.model.last_error_detail,
                          "model_busy": self.model.busy, "configuration_violations": list(self.model.configuration_violations),
                          "model_requested": self.model.model, "reasoning_effort": self.model.effort,
                          "model_last_usage": self.model.last_usage,
                          "mission": "Auditar o servidor, investigar bugs, buscar soluções com fontes e preservar estratégias e resultados.",
                          "research_permission": "Pesquisar assuntos solicitados e consultar fontes e arquivos autorizados; informar conexões ausentes.",
                          "automatic_repair": {"requested": True, "auto_apply": False,
                                               "active_stages": ["detecção", "investigação", "registro de evidências"],
                                               "pending_stages": ["reprodução isolada", "patch testado", "revisão", "aplicação protegida", "validação após aplicação"]},
                          "code_repair": "Codex scheduled review, separately audited"},
        }

    def chat(self, message):
        text = str(message or "").strip()[:4000]
        if not text:
            return {"reply": "O que você gostaria de fazer?", "source": "personal-assistant"}
        normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
        intent = _explicit_intent(normalized)
        mode = conversation_mode(text)
        process = getattr(self, 'process', None)
        file_evidence = process.files.question(text) if hasattr(process,'files') else None
        if file_evidence is not None:
            mode = 'operational'
        if intent == "confirm_restart" and mode != "operational":
            intent = None
        self.record("question", text[:180])
        if mode == "personal" and not intent:
            generated = self.model.answer(text, {"conversation_mode": "personal"})
            if generated:
                self.record("answer", generated[:220])
                return {"reply": generated, "source": "jarvis-web" if getattr(self.model, "last_web_searches", []) else "jarvis-personal",
                        "conversation_mode": "personal", "investigation": None,
                        "web_searches": getattr(self.model, "last_web_searches", [])}
            return {"reply": "O modelo de conversa pessoal está indisponível neste momento. "
                             "Não consegui responder ao seu pedido nem realizar uma pesquisa. Tente novamente em alguns minutos.",
                    "source": "personal-unavailable", "conversation_mode": "personal", "investigation": None}
        s = self.state()
        if re.search(r'\b(?:linux|cpu|ram|disco|systemd|servidor)\b', normalized):
            from brain_service.linux_audit import collect_linux_audit
            s['linux'] = collect_linux_audit()
        t, tester = s["trader"], s["tester"]
        if intent == "regression":
            runner = getattr(self.process, "regression", None)
            result = runner.start_run(explicit=True) if runner else {"accepted": False, "reason": "not_configured"}
            return {"reply": "Regressão operacional isolada solicitada; acompanhe a aba Testes. A auditoria ampla continua separada." if result.get("accepted") else "Regressão não iniciada: " + str(result.get("reason")),
                    "action": "regression", "result": result}
        if intent == "tester":
            result = self.process.tester.start_run()
            reply = "Verificações rápidas solicitadas; são um diagnóstico operacional, não a suíte completa. " + regression_summary(s["regression_audit"]) if result.get("accepted") else "O tester já está executando ou não pôde aceitar a solicitação: " + str(result.get("reason"))
            return {"reply": reply, "action": "tester", "result": result}
        if intent == "confirm_restart":
            return {"reply": "O reinício passa pelo botão Religar Trader e pela confirmação do painel. Ele respeita geração em andamento, limite de tentativas e intervalo de segurança. Estado atual: " + str(s["watchdog"].get("phase")), "action": "confirm_restart"}
        packet = None
        if hasattr(self.process, "investigation"):
            try:
                packet = self.process.investigation.prepare(text, s)
                packet['files'] = file_evidence
                packet['read_roots'] = self.process.files.snapshot() if hasattr(self.process,'files') else None
                packet['automatic_incidents'] = self.process.investigation.snapshot().get('automatic_incidents',[])[-5:]
                s["investigation"] = packet
            except (OSError, ValueError, TypeError) as exc:
                self.record("retrieval_unavailable", type(exc).__name__)
                s["investigation"] = {"available": False, "reason": "source_evidence_unavailable"}
        if s.get('linux') and packet is not None:
            packet.setdefault('evidence', []).append({'id': 'L1', 'kind': 'linux_audit', **s['linux']})
        generated = self.model.answer(text, s)
        if generated:
            self.record("answer", generated[:220])
            return {"reply": generated, "source": "codex-subscription", "observed_at": t.get("observed_at"),
                    "investigation": packet}
        if s.get('linux'):
            audit = s['linux']
            return {'reply': 'Consultei o Linux que hospeda o Brain [L1]. ' +
                    ('Serviços: ' + '; '.join(f"{unit}: {row.get('ActiveState', row.get('reason', 'indeterminado'))}" for unit, row in audit.get('services', {}).items()) if audit.get('available') else 'A leitura Linux não está disponível neste host.') +
                    '\nEsta consulta não comprova ausência de bugs. ' + str(audit.get('limits', '')),
                    'source': 'linux-audit', 'linux': audit, 'investigation': packet}
        if any(x in normalized for x in ("erro", "falha", "problema", "corrij", "corrig")):
            modules = [name for name, row in t["modules"].items() if isinstance(row, dict) and (row.get("last_error") or row.get("status") == "error")]
            reply = (f"Falhas de módulos reportadas: {', '.join(modules) or 'nenhuma na última observação'}. "
                     f"Verificações rápidas: {tester.get('summary', 'ainda sem execução')}. "
                     f"{regression_summary(s['regression_audit'])} "
                     "Pesquisa degradada ou execução bloqueada não significam queda do servidor. "
                     "As correções de código são feitas pela revisão agendada do Codex, com testes; o watchdog cuida de recuperar o processo.")
        elif any(x in normalized for x in ("teste", "tester", "qualidade", "auditoria", "regressao")):
            reply = (f"Verificações rápidas em {tester.get('phase', 'IDLE')}: {tester.get('summary', 'sem resultado ainda')}. "
                     f"{regression_summary(s['regression_audit'])} "
                     "Diga 'executar testes' para solicitar outra rodada de checks rápidos; isso não executa a regressão ampla.")
        elif any(x in normalized for x in ("intelig", "cerebro", "capaz", "assistente", "jarvis", "ajuda", "ola")):
            reply = ("Sou o Brain, sua assessora pessoal e operacional local. Observo o Trader, correlaciono incidentes, mantenho memória de investigação e aciono testes. "
                     "O modelo conversacional está indisponível agora; estou usando o modo local fundamentado em telemetria. "
                     "A revisão inteligente de código acontece na automação do Codex. Você pode perguntar sobre status, módulos, erros, testes ou reinício.")
        elif "modul" in normalized:
            reply = "Módulos observados: " + "; ".join(f"{name}: {row.get('status', 'desconhecido')}" for name, row in t["modules"].items() if isinstance(row, dict))
        else:
            connectivity = "indeterminado (telemetria parcial ou desatualizada; health não confirma estado atual)" if t['connection_state'] == 'INDETERMINATE' else 'acessível' if t['reachable'] else 'indisponível'
            reply = (f"Trader {connectivity}; processo {t['process']}; dados {t['data_integrity']}; "
                     f"pesquisa {t['research_integrity']}; execução {t['execution_readiness']}. Geração: {t['generation'] if t['generation'] is not None else 'não disponível'}. "
                     f"Watchdog: {s['watchdog'].get('phase', 'não disponível')}. "
                     "Posso detalhar módulos, falhas, testes e recuperação do servidor.")
        self.record("answer", reply[:220])
        if packet and len(packet["evidence"]) > 1:
            reply += "\nFontes locais consultadas: " + "; ".join(f"[{h['id']}] {h.get('line_ref', h.get('path'))}" for h in packet["evidence"] if h["kind"] == "source")
        return {"reply": reply, "source": "live-telemetry", "observed_at": t.get("observed_at"), "investigation": packet}
