"""
QUANT HPC OS — Governança: Autonomia, Auditoria e Journal
=========================================================
Implementa as camadas de CONTROLE que a spec do Market OS exige e que faltavam:

1) AUTONOMIA  — MANUAL / COPILOT / SUPERVISED / AUTONOMOUS.
   O ExecutionEngine consulta isto ANTES de qualquer ordem. Em MANUAL e COPILOT
   nada é enviado automaticamente (COPILOT registra a proposta p/ o humano decidir).

2) AUDIT TRAIL — append-only, reconstrói QUALQUER decisão: qual bot/estratégia,
   qual estado de mercado, qual sinal, quais regras de risco, quem autorizou,
   qual execução, qual resultado.

3) JOURNAL — entrada automática por operação com o contexto completo.

Arquivos (JSONL append-only, à prova de restart):
  data_lake/audit/audit_YYYY-MM-DD.jsonl
  data_lake/journal/journal.jsonl
"""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, Optional

from core.config import config
from core.logger import get_logger

log = get_logger("Governance")

MANUAL, COPILOT, SUPERVISED, AUTONOMOUS = "MANUAL", "COPILOT", "SUPERVISED", "AUTONOMOUS"
_MODES = (MANUAL, COPILOT, SUPERVISED, AUTONOMOUS)


class AutonomyController:
    """Autoridade sobre O QUE o sistema pode fazer sozinho."""

    def __init__(self):
        self.path = os.path.join(str(config.data_lake_dir), "autonomy.json")
        self.mode = SUPERVISED          # default seguro p/ paper
        self.reason = "default"
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                d = json.load(open(self.path))
                if d.get("mode") in _MODES:
                    self.mode = d["mode"]
                    self.reason = d.get("reason", "")
        except Exception as e:
            log.warning(f"autonomy: estado corrompido: {e}")

    def set_mode(self, mode: str, reason: str = "manual") -> bool:
        if mode not in _MODES:
            return False
        self.mode = mode
        self.reason = reason
        try:
            tmp = self.path + ".tmp"
            json.dump({"mode": mode, "reason": reason, "ts": time.time()}, open(tmp, "w"))
            os.replace(tmp, self.path)
        except Exception:
            pass
        log.warning(f"AUTONOMIA -> {mode} ({reason})")
        audit.record("AUTONOMY_CHANGED", {"mode": mode, "reason": reason})
        return True

    def can_auto_execute(self) -> bool:
        """Só SUPERVISED e AUTONOMOUS podem mandar ordem sem humano."""
        return self.mode in (SUPERVISED, AUTONOMOUS)

    def status(self) -> Dict[str, Any]:
        return {"mode": self.mode, "reason": self.reason,
                "auto": self.can_auto_execute()}


class AuditTrail:
    """Trilha append-only. Nada é sobrescrito; tudo é reconstruível."""

    def __init__(self):
        self.dir = os.path.join(str(config.data_lake_dir), "audit")
        os.makedirs(self.dir, exist_ok=True)
        self._lock = threading.Lock()
        self._seq = 0

    def _file(self) -> str:
        return os.path.join(self.dir, f"audit_{datetime.now().strftime('%Y-%m-%d')}.jsonl")

    def record(self, event: str, payload: Optional[Dict] = None,
               actor: str = "system", subject: str = "") -> None:
        try:
            with self._lock:
                self._seq += 1
                rec = {"seq": self._seq, "ts": time.time(),
                       "iso": datetime.now().isoformat(timespec="seconds"),
                       "event": event, "actor": actor, "subject": subject,
                       "payload": payload or {}}
                with open(self._file(), "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            log.debug(f"audit falhou: {e}")

    def tail(self, n: int = 50) -> list:
        try:
            with open(self._file(), "r", encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            return [json.loads(x) for x in lines]
        except Exception:
            return []


class Journal:
    """Entrada automática por operação, com o contexto que existia no momento."""

    def __init__(self):
        self.dir = os.path.join(str(config.data_lake_dir), "journal")
        os.makedirs(self.dir, exist_ok=True)
        self.path = os.path.join(self.dir, "journal.jsonl")
        self._lock = threading.Lock()

    def entry(self, kind: str, ctx: Dict[str, Any], note: str = "") -> None:
        try:
            with self._lock:
                rec = {"ts": time.time(), "iso": datetime.now().isoformat(timespec="seconds"),
                       "kind": kind, "note": note, "ctx": ctx}
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            log.debug(f"journal falhou: {e}")

    def tail(self, n: int = 40) -> list:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-n:]
            return [json.loads(x) for x in lines]
        except Exception:
            return []


# singletons
autonomy = AutonomyController()
audit = AuditTrail()
journal = Journal()
