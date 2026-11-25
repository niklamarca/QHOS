"""Reconciliação independente entre broker, risco e executor.

Um sistema de trading não pode declarar exposição zero se o broker conserva
posições. Esta camada é deliberadamente simples e fail-closed: divergência
material bloqueia novas ordens, preserva a evidência e deixa flatten direto
disponível ao kill switch.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.config import config
from core.logger import get_logger

log = get_logger("Reconciliation")


def _atomic_json(path: Path, payload: Dict[str, Any],
                 attempts: int = 6) -> Optional[Exception]:
    """Grava o snapshot de forma atômica, tolerando lock transitório do Windows.

    O Defender/indexador pode segurar o alvo por alguns milissegundos e fazer
    ``os.replace`` levantar ``PermissionError`` (WinError 5). O tmp usa nome
    único para dois escritores nunca disputarem o mesmo arquivo intermediário.

    Retorna a exceção final em vez de propagá-la: falhar em *persistir* não é o
    mesmo que falhar em *verificar*, e o veredito já foi calculado em memória.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                         dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                os.replace(temporary, path)
                return None
            except PermissionError as exc:      # lock transitório
                last_error = exc
                time.sleep(0.10 * (attempt + 1))
            except OSError as exc:              # disco cheio, path inválido
                last_error = exc
                break
        if isinstance(last_error, PermissionError):
            # O retry já falhou; insistir em código não resolve. A causa é
            # externa ao Python (indexador/antivírus abrindo o alvo sem
            # FILE_SHARE_DELETE). Registrar o comando de diagnóstico é o que
            # transforma um erro recorrente e anônimo em algo acionável.
            log.error(
                "os.replace bloqueado apos %d tentativas em %s. Causa provavel: "
                "Windows Search ou antivirus segurando o handle. Diagnostique com "
                "'handle64.exe -a %s' ou exclua a pasta do projeto da indexacao e "
                "da varredura em tempo real.",
                attempts, path.name, path,
            )
        return last_error
    finally:
        if os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


class ReconciliationGate:
    def __init__(self):
        root = Path(config.data_lake_dir) / "governance"
        root.mkdir(parents=True, exist_ok=True)
        self.latest_path = root / "reconciliation.json"
        self.history_path = root / "reconciliation.jsonl"
        self._lock = threading.Lock()
        self._last_fingerprint = ""

    @staticmethod
    def _broker_snapshot(broker: Any) -> Dict[str, Any]:
        positions = {str(k): float(v) for k, v in (getattr(broker, "positions", {}) or {}).items()
                     if abs(float(v)) > 1e-9}
        prices = {str(k): float(v) for k, v in (getattr(broker, "prices", {}) or {}).items()}
        cash = float(getattr(broker, "cash", 0.0) or 0.0)
        marked = sum(qty * prices.get(symbol, 0.0) for symbol, qty in positions.items())
        nav = cash + marked
        missing_prices = sorted(symbol for symbol in positions if prices.get(symbol, 0.0) <= 0)
        exposure = (sum(abs(qty * prices.get(symbol, 0.0)) for symbol, qty in positions.items()) / nav
                    if nav > 0 else 0.0)
        return {"positions": positions, "cash": cash, "nav": nav,
                "exposure": exposure, "missing_prices": missing_prices}

    def check(self, broker: Any, risk_engine: Any = None,
              signal_executor: Any = None, emit_audit: bool = True) -> Dict[str, Any]:
        broker_state = self._broker_snapshot(broker)
        risk_state = getattr(risk_engine, "state", None)
        risk_exposure = float(getattr(risk_state, "total_exposure", 0.0) or 0.0)
        active_strategies = len(getattr(signal_executor, "_active_strategies", []) or [])
        issues: list[Dict[str, str]] = []
        if broker_state["missing_prices"]:
            issues.append({"severity": "P0", "code": "unmarked_broker_position",
                           "detail": ",".join(broker_state["missing_prices"])})
        if broker_state["positions"] and broker_state["exposure"] > 1e-6 and risk_exposure <= 1e-6:
            issues.append({"severity": "P0", "code": "broker_risk_exposure_mismatch",
                           "detail": f"broker={broker_state['exposure']:.2%}; risk={risk_exposure:.2%}"})
        if broker_state["positions"] and active_strategies == 0:
            issues.append({"severity": "P1", "code": "orphan_broker_positions",
                           "detail": f"{len(broker_state['positions'])} posições sem estratégia ativa"})
        status = "BLOCKED" if any(i["severity"] == "P0" for i in issues) else ("WARN" if issues else "OK")
        result = {
            "ts": time.time(), "status": status, "issues": issues,
            "broker": broker_state,
            "risk": {"exposure": risk_exposure},
            "executor": {"active_strategies": active_strategies},
        }
        fingerprint = hashlib.sha256(json.dumps(
            {"status": status, "issues": issues, "broker": broker_state,
             "risk_exposure": risk_exposure, "active": active_strategies},
            sort_keys=True).encode("utf-8")).hexdigest()
        with self._lock:
            # O veredito acima veio de broker/risk em memória e já é válido.
            # Persistir é observabilidade: se o disco falhar, não se descarta a
            # verificação nem se bloqueia uma ordem legítima por isso.
            write_error = _atomic_json(self.latest_path, result)
            result["snapshot_persisted"] = write_error is None
            if write_error is not None:
                # ERROR (não WARNING): o dashboard/health passa a ler um
                # snapshot velho, então o status corrente precisa aparecer aqui.
                log.error(
                    f"Reconciliação {status} calculada, mas o snapshot não foi "
                    f"gravado ({type(write_error).__name__}: {write_error}); "
                    f"{self.latest_path.name} está DESATUALIZADO"
                )
            if fingerprint != self._last_fingerprint:
                self._last_fingerprint = fingerprint
                try:
                    with open(self.history_path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(result, ensure_ascii=False) + "\n")
                except OSError as exc:
                    # Mesmo raciocínio do snapshot: o histórico é registro, não
                    # verificação. Perder o append não pode derrubar o veredito.
                    log.error(f"Reconciliação: histórico não gravado ({exc})")
                if status != "OK":
                    log.error(f"RECONCILIACAO {status}: " +
                              "; ".join(i["code"] for i in issues))
                    if emit_audit:
                        try:
                            from governance.control import audit
                            audit.record("RECONCILIATION_" + status, result,
                                         actor="reconciliation")
                        except Exception:
                            pass
        return result

    def execution_allowed(self, broker: Any, risk_engine: Any = None,
                          signal_executor: Any = None) -> tuple[bool, str]:
        result = self.check(broker, risk_engine, signal_executor)
        if result["status"] == "BLOCKED":
            codes = ",".join(i["code"] for i in result["issues"])
            return False, f"reconciliation_blocked:{codes}"
        return True, "reconciled"

    def latest(self) -> Dict[str, Any]:
        """Último snapshot em disco, sempre com a idade explícita.

        Quem lê precisa poder distinguir "OK agora" de "OK da última vez que o
        disco deixou gravar". Sem ``age_seconds`` um snapshot velho se disfarça
        de carteira saudável.
        """
        try:
            snapshot = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "UNKNOWN", "age_seconds": None,
                    "issues": [{"severity": "P1", "code": "no_reconciliation_yet"}]}
        try:
            snapshot["age_seconds"] = max(0.0, time.time() - float(snapshot.get("ts", 0.0)))
        except (TypeError, ValueError):
            snapshot["age_seconds"] = None
        return snapshot


reconciliation = ReconciliationGate()
