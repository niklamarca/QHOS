"""Bounded, read-only software diagnostics with durable results."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from brain.local_logger import get_logger
from brain_service.supervisor import SingleInstanceLock, atomic_json_write, http_json_get

log = get_logger("Brain.Tester")
from brain_service.config import DEFAULT_TEST_INTERVAL_S, normalize_test_interval, restore_test_schedule, mark_test_attempt_started


PORTABLE_SOURCE_FILES = (
    "config.py",
    "eventos.py",
    "leitura.py",
    "operacao.py",
    "coletor.py",
)

# Constant reasons raised by this module's own checks. They carry no payload, so
# exposing them is safe; any other exception is still reduced to its class name
# (QHOS-AUD-276).
INTERNAL_CHECK_REASONS = frozenset({
    "payload_not_object", "busy_not_confirmed", "process_identity_not_confirmed",
    "module_registry_unavailable", "invalid_module_registry", "invalid_qhos2_registry",
    "qhos2_reader_not_confirmed", "qhos2_registry_missing",
})


class SoftwareTester:
    def __init__(self, target_root: Path, state_dir: Path, *, quant_url="http://127.0.0.1:8766", cadence_s=DEFAULT_TEST_INTERVAL_S,
                 clock=None):
        self.target_root = Path(target_root).resolve()
        self.path = Path(state_dir) / "tester_state.json"
        self.url = quant_url.rstrip("/")
        self.cadence_s = normalize_test_interval(cadence_s)
        self._clock = clock if clock is not None else time.time  # resolved here, so a patched time.time is honoured
        self._lock = threading.Lock()
        self._thread = None
        self._stop_requested = threading.Event()
        self.state = {"phase": "IDLE", "last_run": None, "passed": 0, "failed": 0,
                      "summary": "Nenhum diagnóstico executado.", "cases": [], "history": [],
                      "scope": "health_smoke", "cadence_s": self.cadence_s, "next_due": 0,
                      "deadline_s": 30, "verified": False}
        loaded = None
        # Describes this load only; injected by snapshot(), never persisted (QHOS-AUD-295).
        self._previous_state_discarded = None
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(saved, dict):
                raise ValueError("state_not_object")
            loaded = saved
            loaded.pop("previous_state_discarded", None)
            self.state.update(loaded)
            if self.state["phase"] == "RUNNING":
                self.state["phase"] = "INTERRUPTED"
                self.state["verified"] = False
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            # Starting from zero stays; it is no longer silent (QHOS-AUD-295).
            self._previous_state_discarded = f"{type(exc).__name__}: {str(exc)[:120]}"
            log.warning("tester_state discarded (%s); smoke history restarts empty", self._previous_state_discarded)
        self.state.update(scope="health_smoke")
        migrated = restore_test_schedule(self.state, self.cadence_s,
                                        previous_cadence=loaded.get("cadence_s") if loaded is not None else self.cadence_s,
                                        now=self._clock())
        if not isinstance(self.state.get("history"), list):
            self.state["history"] = []
        if self.state.get("phase") in {"PASSED", "FAILED"}:
            try:
                artifact = self.path.parent / "smoke_evidence.json"
                if artifact.is_symlink() or artifact.resolve().parent != self.path.parent.resolve():
                    raise ValueError("artifact_redirected")
                with artifact.open("rb") as handle:
                    raw = handle.read(65537)
                if len(raw) > 65536:
                    raise ValueError("evidence_too_large")
                evidence = json.loads(raw)
                digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
                if digest != self.state.get("evidence_sha256") or evidence.get("run_id") != self.state.get("run_id"):
                    raise ValueError("evidence_mismatch")
                allowed = set(PORTABLE_SOURCE_FILES)
                files = evidence.get("source_files", {})
                if not isinstance(files, dict) or not set(files).issubset(allowed):
                    raise ValueError("unrequested_source")
                if any(hashlib.sha256((self.target_root / rel).read_bytes()).hexdigest() != value for rel, value in files.items()):
                    self.state.update(phase="STALE", verified=False, summary="Fontes mudaram desde os checks; nova verificação necessária.")
                elif len(evidence.get("cases", [])) != 10:
                    raise ValueError("incomplete_cases")
                else:
                    failures = sum(c.get("status") != "passed" for c in evidence["cases"])
                    if not failures and len(files) != 5:
                        raise ValueError("missing_syntax_evidence")
                    self.state.update(phase="FAILED" if failures else "PASSED", failed=failures, passed=10-failures)
            except (OSError, ValueError, TypeError, AttributeError):
                self.state.update(phase="UNVERIFIED", verified=False, summary="Evidência anterior ausente/alterada; checks não comprovados.")
        if loaded is not None and (migrated or loaded.get("phase") == "RUNNING"):
            try:
                atomic_json_write(self.path, self.state)
            except OSError:
                self.state.update(phase="ERROR", verified=False, schedule_persistence_error=True,
                                  summary="Cadência corrigida em memória, mas o agendamento não pôde ser persistido.")

    def snapshot(self):
        with self._lock:
            result = json.loads(json.dumps(self.state))
        result["stale"] = bool(result.get("last_run") and self._clock() >= result.get("next_due", 0))
        # A PASSED older than its cadence is not a current approval (QHOS-AUD-282).
        result["display_phase"] = "PASSED_STALE" if result["stale"] and result.get("phase") == "PASSED" else result.get("phase")
        if self._previous_state_discarded:
            result["previous_state_discarded"] = self._previous_state_discarded
        return result

    def tick(self):
        if self._clock() < self.snapshot().get("next_due", 0):
            return {"accepted": False, "reason": "not_due"}
        return self.start_run()

    def start_run(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"accepted": False, "reason": "already_running"}
            now = self._clock()
            if now < self.state.get("next_due", 0):
                return {"accepted": False, "reason": "cooldown", "next_due": self.state["next_due"]}
            self.state["phase"] = "RUNNING"
            self.state.update(verified=False, next_due=now+self.cadence_s, run_id=uuid.uuid4().hex,
                              coordinator_pid=os.getpid(), last_run=now, passed=0, failed=0, cases=[])
            self.state["summary"] = "Verificando sintaxe, APIs, identidade e módulos."
            try:
                # Persist the attempt before starting, so a reboot cannot repeat
                # an in-flight smoke run before its configured cooldown.
                atomic_json_write(self.path, self.state)
            except OSError:
                self.state.update(phase="ERROR", verified=False,
                                  summary="Não foi possível persistir o início; checks não executados.")
                return {"accepted": False, "reason": "state_unwritable"}
            self._thread = threading.Thread(target=self._run, daemon=True, name="BrainTester")
            self._stop_requested.clear()
            try:
                self._thread.start()
            except (RuntimeError, OSError) as exc:
                self.state.update(phase="ERROR", verified=False, reason="thread_start_failed",
                                  summary="Checks não iniciados: "+type(exc).__name__+". Cooldown preservado.")
                try:
                    atomic_json_write(self.path, self.state)
                except OSError:
                    self.state["schedule_persistence_error"] = True
                return {"accepted": False, "reason": "thread_start_failed"}
        return {"accepted": True, "reason": "started"}

    def _run(self):
        lease = SingleInstanceLock(self.path.parent / "diagnostic_execution.lock")
        if not lease.acquire():
            with self._lock:
                if lease.failure_kind == "error":  # QHOS-AUD-266: unusable lock is not contention
                    self.state.update(phase="ERROR", verified=False, reason="diagnostic_lock_error: " + str(lease.last_error),
                                      summary="Lock de diagnóstico inutilizável (" + str(lease.last_error) + "); checks não executados.")
                else:
                    self.state.update(phase="DEFERRED", verified=False, next_due=self._clock()+30,
                                      summary="Outro diagnóstico executando; verificações rápidas adiadas.")
                try:
                    atomic_json_write(self.path, self.state)
                except OSError:
                    self.state.update(phase="ERROR", summary="Não foi possível persistir o adiamento.")
            return
        try:
            child_lease = SingleInstanceLock(self.path.parent / "diagnostic_worker.lock")
            if not child_lease.acquire():
                with self._lock:
                    self.state.update(phase="DEFERRED", verified=False, next_due=self._clock()+30,
                                      summary="Worker anterior de regressão ainda ativo; checks adiados.")
                    atomic_json_write(self.path, self.state)
                return
            child_lease.release()
            with self._lock:
                mark_test_attempt_started(self.state, self.cadence_s, now=self._clock())
                atomic_json_write(self.path, self.state)
            self._checks()
        except Exception as exc:
            with self._lock:
                self.state.update(phase="ERROR", verified=False, summary="Verificações interrompidas: "+type(exc).__name__)
                try:
                    atomic_json_write(self.path, self.state)
                except OSError:
                    pass
        finally:
            lease.release()

    def _checks(self):
        started = self._clock()
        clock_started = time.monotonic()
        cases = []
        observed = {"reader_state": None}
        def check(name, operation):
            try:
                if self._stop_requested.is_set():
                    raise InterruptedError("service_stopping")
                if time.monotonic()-clock_started > 30:
                    raise TimeoutError("deadline")
                detail = operation()
                cases.append({"name": name, "status": "passed", "detail": detail or "OK"})
            except Exception as exc:
                # Never persist exception reprs/configuration or full server payloads;
                # only this module's own constant reasons are named.
                reason = str(exc) if type(exc) is ValueError and str(exc) in INTERNAL_CHECK_REASONS else type(exc).__name__
                cases.append({"name": name, "status": "failed", "detail": reason})
        files = PORTABLE_SOURCE_FILES
        hashes = {}
        for relative in files:
            path = self.target_root / relative
            def syntax(p=path, rel=relative):
                raw = p.read_bytes()
                hashes[rel] = hashlib.sha256(raw).hexdigest()
                ast.parse(raw.decode("utf-8-sig"), filename=p.name)
                return "Sintaxe Python válida; código não executado"
            check("Sintaxe: " + relative, syntax)
        payloads = {}
        for route in ("/api/health", "/api/status", "/api/busy"):
            def endpoint(r=route):
                obj = http_json_get(self.url + r, 4)
                json.dumps(obj, allow_nan=False)
                if not isinstance(obj, dict):
                    raise ValueError("payload_not_object")
                if r == "/api/busy" and type(obj.get("busy")) is not bool:
                    raise ValueError("busy_not_confirmed")
                payloads[r] = obj
                return "HTTP JSON válido, sem NaN/Infinity"
            check("API: " + route, endpoint)
        def identity():
            h = payloads.get("/api/health", {})
            if ((h.get("process") or {}).get("state") != "PROCESS_ONLINE"
                    or (h.get("process") or {}).get("component") != "qhos2.coletor"):
                raise ValueError("process_identity_not_confirmed")
            return "Trader identificado e processo online"
        check("Identidade e disponibilidade", identity)
        def modules():
            rows = payloads.get("/api/status", {}).get("modules", {})
            if not rows:
                raise ValueError("module_registry_unavailable")
            if not isinstance(rows, dict):
                raise ValueError("invalid_module_registry")
            if "qhos2_collector" in rows:
                if rows["qhos2_collector"].get("state") != "ONLINE" or not isinstance(rows.get("qhos2_reader"), dict):
                    raise ValueError("invalid_qhos2_registry")
                if rows["qhos2_reader"].get("state") not in {"SEM_CAPTURA", "AGUARDANDO", "LENDO", "ACOMPANHANDO", "ENCERRADA"}:
                    raise ValueError("qhos2_reader_not_confirmed")
                observed["reader_state"] = rows["qhos2_reader"].get("state")
                return "Processo e leitor identificados; nao comprova livro valido, sessao ao vivo ou pesquisa aprovada"
            raise ValueError("qhos2_registry_missing")
        check("Coerência do registro de módulos", modules)
        failed = sum(c["status"] == "failed" for c in cases)
        changed = any(hashlib.sha256((self.target_root / rel).read_bytes()).hexdigest() != value for rel, value in hashes.items())
        deadline_exceeded = time.monotonic()-clock_started > 30
        evidence = {"schema": "brain-health-smoke-evidence-v1", "run_id": self.state["run_id"],
                    "source_hash": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
                    "source_files": hashes, "source_scope": "five_portable_syntax_checked_files_only",
                    "coordinator_pid": os.getpid(), "observed_server_pid": payloads.get("/api/health", {}).get("process", {}).get("pid"),
                    "passed": len(cases)-failed, "failed": failed, "cases": cases}
        result = {"last_run": started, "completed_at": self._clock(), "duration_s": round(self._clock()-started, 2),
                  "phase": "TIMEOUT" if deadline_exceeded else "STALE" if changed else "FAILED" if failed else "PASSED", "passed": len(cases)-failed,
                  "failed": failed, "cases": cases,
                  "verified": not changed and not deadline_exceeded and len(cases) == 10,
                  "source_hash": evidence["source_hash"], "source_scope": evidence["source_scope"],
                  "observed_server_pid": evidence["observed_server_pid"],
                  # The smoke proves process identity and a reader state, never market data (QHOS-AUD-287).
                  "reader_state": observed["reader_state"], "market_data_confirmed": False,
                  "evidence_sha256": hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest(),
                  "summary": f"{len(cases)-failed}/{len(cases)} verificações aprovadas; {failed} falha(s). Não é a suíte completa do Trader."}
        if changed or deadline_exceeded:
            result["summary"] = "Aprovação não confirmada: código mudou ou prazo excedido. Não é a suíte completa do Trader."
        with self._lock:
            history = [ {k:v for k,v in result.items() if k != "cases"}, *self.state.get("history", [])][:40]
            self.state.update(result, history=history)
            try:
                atomic_json_write(self.path.parent / "smoke_evidence.json", evidence)
                atomic_json_write(self.path, self.state)
            except OSError:
                self.state.update(phase="ERROR", verified=False, summary="Diagnóstico concluído, mas não foi possível persistir o histórico.")

    def stop(self, timeout_s=5):
        """Bounded cooperative shutdown; never waits indefinitely for diagnostics."""
        self._stop_requested.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(max(0.0, min(10.0, float(timeout_s))))
        return {"stopped": not (thread and thread.is_alive())}

    @staticmethod
    def _syntax(path):
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
        return "Sintaxe Python válida; código não executado"
