"""Curated operational regression in a disposable, source-only mirror.

Adapted from the reviewed work/regression_runner.py harness. Python audit hooks
are NOT an OS sandbox. Only pinned, reviewed tests are admitted; no full suite,
production data, credentials, network, training, restart, or generated commands.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import site
import subprocess
import sys
import threading
import time
import uuid

from brain.local_logger import get_logger
from brain_service.supervisor import SingleInstanceLock, atomic_json_write
from brain_service.verification import EVIDENCE_SCHEMA, read_evidence, source_manifest, verify_regression
from brain_service.config import DEFAULT_TEST_INTERVAL_S, normalize_test_interval, restore_test_schedule, mark_test_attempt_started

log = get_logger("Brain.Regression")

# Inherited V1 profile (Windows launcher suite). Its files do not exist in the V2
# tree; selecting it makes prepare_mirror fail with reviewed_tests_missing
# (QHOS-AUD-314: kept until the owner decides to retire the legacy profiles).
WINDOWS_REVIEWED_TESTS = {
    "tests/test_queue_freshness_safety.py": "9afca91821f3c5dc89cce9e1edaa8b7470b1b55b919fd4b86be9c343cda31ed8",
    "tests/test_runtime_release.py": "b1c6b40f6056b8e6fcf07ab6fa908e5fca8937643e592ade38f20cd6a3a5a0a7",
    "tests/test_launcher_safety.py": "719fc1524b25f4cd113b7794fc9b994e38aaed7bd69a9bd90dc2f44ba8f77e58",
    "tests/test_gpu_stats_cache_regressions.py": "809e0eec37a3d2c0543cf41275ea307b5f209659ac165603c3e7c97cdb89debf",
    "tests/test_refresh_contracts.py": "80fe7076d32a4d077ee347e8f2972520ec63b6a22fd4ebb69e26ee9ef2ee06fb",
    "tests/test_crypto_schedule.py": "f53dcc94942dc4fbce97b23364fad94d6465ffd9c5c1463d9f0d477dc95d26cd",
    "tests/test_crypto_refresh.py": "990aedbf60862b71a6558c032cb40152e68ad0e36d09f633c5b61754c0589da0",
}

# Explicit deployment profiles: no auto-discovery, no silently missing tests.
# The Linux profile covers runtime contracts, not the Windows launcher suite.
REGRESSION_PROFILE = os.environ.get("BRAIN_REGRESSION_PROFILE", "qhos2-operational-v1")
LINUX_REVIEWED_TESTS = {
    "tests/test_linux_operational_safety.py": "b2b58809456dda8c1e42ad3acfdf99485b6bf4484843d7b24ec2312de281610a",
}


def reviewed_tests_for_profile(profile):
    """Pinned reviewed tests of a deployment profile; unknown profiles are refused."""
    if profile == "qhos2-operational-v1":
        return {"tests/test_runtime_contracts.py": "78bdfb201924c7ffdd3a27a1cb7f8ed1336aaa75db21b7ad07426108c78f6d87"}
    if profile == "linux-runtime-v1":
        return dict(LINUX_REVIEWED_TESTS)
    if profile == "windows-operational-v1":
        return dict(WINDOWS_REVIEWED_TESTS)
    raise ValueError("unknown_reviewed_regression_profile")

# An owned mirror without a finalization marker is a crash leftover. It is only
# removed after this grace, far above the worker self-deadline (QHOS-AUD-308).
ORPHAN_MIRROR_GRACE_S = 3600


REVIEWED_TESTS = reviewed_tests_for_profile(REGRESSION_PROFILE)


def prepare_mirror(target_root, destination):
    """Copy reviewed source only to a NEW external directory; return manifest.

The caller may prepare a patch candidate here, then use another RegressionRunner
whose target is that candidate and whose state is outside it. Never applies code.
"""
    root, mirror = Path(target_root).resolve(), Path(destination).resolve()
    if mirror == root or mirror.is_relative_to(root) or mirror.exists():
        raise ValueError("mirror_must_be_new_and_external")
    if any(not (root / name).is_file() for name in REVIEWED_TESTS):
        # A profile whose files are not in this tree is "missing", not "changed".
        raise ValueError("reviewed_tests_missing")
    manifest = source_manifest(root, REVIEWED_TESTS)
    hashes = {row["path"]: row["sha256"] for row in manifest["files"]}
    if any(hashes.get(name) != pinned for name, pinned in REVIEWED_TESTS.items()):
        raise ValueError("reviewed_tests_changed")
    mirror.mkdir(parents=True, exist_ok=False)
    for row in manifest["files"]:
        raw = (root / row["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("source_changed_while_copying")
        output = mirror / row["path"]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(raw)
    return manifest


class RegressionRunner:
    def __init__(self, target_root, state_dir, *, cadence_s=DEFAULT_TEST_INTERVAL_S, deadline_s=180):
        self.target_root = Path(target_root).resolve()
        self.state_dir = Path(state_dir).resolve()
        if self.state_dir == self.target_root or self.state_dir.is_relative_to(self.target_root):
            raise ValueError("regression_state_must_be_outside_trader")
        self.path = self.state_dir / "operational_regression.json"
        self.work = self.state_dir / "regression_runs"
        self.cadence_s = normalize_test_interval(cadence_s)
        self.deadline_s = min(300, max(10, int(deadline_s)))
        self._lock = threading.Lock()
        self._thread = None
        self._process = None
        self._stop = threading.Event()
        self._last_source_check = 0.0
        self.state = {"schema": "brain-operational-regression-state-v1", "scope": "operational",
                      "phase": "NOT_RUN", "last_run": None, "next_due": 0, "passed": 0,
                      "failed": 0, "skipped": 0, "collection_errors": 0, "verified": False,
                      "cadence_s": self.cadence_s, "deadline_s": self.deadline_s,
                      "summary": "Regressão operacional ainda não executada; não substitui auditoria ampla.",
                      "boundary": "python-audit-hooks-not-os-sandbox", "history": []}
        loaded = None
        # Instance attribute, injected only by snapshot(): it describes THIS load and
        # must never be persisted, or it would come back on the next valid reopen.
        self._previous_state_discarded = None
        try:
            saved = read_evidence(self.path)
            if saved.get("schema") != self.state["schema"]:
                self._previous_state_discarded = "schema_mismatch"
            elif saved.get("target_root") != str(self.target_root):
                self._previous_state_discarded = "target_root_changed"
            else:
                loaded = saved
                loaded.pop("previous_state_discarded", None)  # left by an earlier build of this fix
                self.state.update(loaded)
                if self.state["phase"] == "RUNNING":
                    self.state.update(phase="INTERRUPTED", verified=False, summary="Execução anterior interrompida; aprovação não comprovada.")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as exc:
            # Starting from zero is still the behaviour; it is no longer silent (QHOS-AUD-295).
            self._previous_state_discarded = f"{type(exc).__name__}: {str(exc)[:120]}"
        if self._previous_state_discarded:
            log.warning("operational_regression state discarded (%s); history restarts empty",
                        self._previous_state_discarded)
        self.state.update(deadline_s=self.deadline_s, target_root=str(self.target_root),
                          profile=REGRESSION_PROFILE, suite=list(REVIEWED_TESTS))
        migrated = restore_test_schedule(self.state, self.cadence_s,
                                        previous_cadence=loaded.get("cadence_s") if loaded is not None else self.cadence_s)
        if not isinstance(self.state.get("history"), list):
            self.state["history"] = []
        if self.state.get("phase") in {"PASSED", "PASSED_WITH_SKIPS", "FAILED"}:
            self._verify_saved()
        if loaded is not None and (migrated or loaded.get("phase") == "RUNNING"):
            try:
                self._persist()
            except OSError:
                self.state.update(phase="ERROR", verified=False, schedule_persistence_error=True,
                                  summary="Cadência corrigida em memória, mas o agendamento não pôde ser persistido.")

    def _verify_saved(self):
        try:
            run_id = self.state.get("run_id")
            if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
                raise ValueError("invalid_saved_identity")
            artifact = self.state_dir / "regression_evidence" / (run_id+".json")
            if artifact.is_symlink() or artifact.resolve().parent != artifact.parent:
                raise ValueError("artifact_redirected")
            evidence = read_evidence(artifact)
            digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
            if digest != self.state.get("evidence_sha256"):
                raise ValueError("artifact_hash_changed")
            expected = {"run_id": run_id, "source_hash": self.state.get("source_hash"),
                        "worker_pid": self.state.get("worker_pid"), "suite": list(REVIEWED_TESTS),
                        "started_at": self.state.get("last_run")}
            result = verify_regression(evidence, expected, current_hash=source_manifest(self.target_root, REVIEWED_TESTS)["sha256"],
                                       mirror_hash=expected["source_hash"], now=time.time())
            self.state.update(result)
            if not result["verified"]:
                self.state["summary"] = "Evidência anterior desatualizada ou não verificável; nova regressão necessária."
        except (OSError, ValueError, TypeError):
            self.state.update(phase="UNVERIFIED", verified=False, reason="saved_evidence_invalid",
                              summary="Artefato anterior ausente/alterado; aprovação não comprovada.")

    def snapshot(self):
        with self._lock:
            result = json.loads(json.dumps(self.state))
        result["stale"] = bool(result.get("last_run") and time.time() >= result.get("next_due", 0))
        if self._previous_state_discarded:
            result["previous_state_discarded"] = self._previous_state_discarded
        return result

    def tick(self):
        now = time.time()
        if now-self._last_source_check >= 60 and self.snapshot().get("phase") in {"PASSED", "PASSED_WITH_SKIPS", "FAILED"}:
            with self._lock:
                self._verify_saved()
            self._last_source_check = now
        return self.start_run() if time.time() >= self.snapshot().get("next_due", 0) else {"accepted": False, "reason": "not_due"}

    def start_run(self, scope="operational", *, explicit=False):
        if self._stop.is_set():
            return {"accepted": False, "reason": "stopped"}
        if scope != "operational":
            return {"accepted": False, "reason": "broad_requires_reviewed_suite"}
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"accepted": False, "reason": "already_running"}
            now = time.time()
            if now < self.state.get("next_due", 0):
                return {"accepted": False, "reason": "cooldown", "next_due": self.state["next_due"]}
            self.state.update(phase="RUNNING", verified=False, last_run=now, next_due=now+self.cadence_s,
                              coordinator_pid=os.getpid(), worker_pid=None, run_id=uuid.uuid4().hex,
                              passed=0, failed=0, skipped=0, collection_errors=0, worker_error=None,
                              source_hash=None, artifact=None, reason="started",
                              summary="Regressão operacional em mirror isolado por guardas Python; não sandbox de SO.")
            try:
                self._persist()
            except OSError:
                self.state.update(phase="ERROR", summary="Não foi possível persistir o início; testes não executados.")
                return {"accepted": False, "reason": "state_unwritable"}
            self._thread = threading.Thread(target=self._run, daemon=True, name="BrainOperationalRegression")
            try:
                self._thread.start()
            except (RuntimeError, OSError) as exc:
                self.state.update(phase="ERROR", verified=False, reason="thread_start_failed",
                                  summary="Regressão não iniciada: "+type(exc).__name__+". Cooldown preservado.")
                try:
                    self._persist()
                except OSError:
                    self.state["schedule_persistence_error"] = True
                return {"accepted": False, "reason": "thread_start_failed"}
        return {"accepted": True, "reason": "started", "scope": "operational"}

    def stop(self, timeout_s=5):
        """Stop only this runner's child; never inspect/kill any server PID."""
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(max(0, min(10, float(timeout_s))))
        return {"stopped": not (self._thread and self._thread.is_alive())}

    def _persist(self):
        atomic_json_write(self.path, self.state)

    @staticmethod
    def _lock_refusal(lease, *, deferred_reason, deferred_summary):
        """Contention defers 30 s; an unusable lock is an ERROR with its cause, not 'another diagnostic'."""
        if lease.failure_kind == "error":
            return {"phase": "ERROR", "verified": False, "reason": "diagnostic_lock_error: " + str(lease.last_error),
                    "summary": "Lock de diagnóstico inutilizável (" + str(lease.last_error) + "); regressão não executada."}
        return {"phase": "DEFERRED", "verified": False, "reason": deferred_reason,
                "next_due": time.time()+30, "summary": deferred_summary}

    def _reap(self, process):
        """Wait for our own killed child; a child stuck past the grace period must not take the lease with it."""
        try:
            process.wait(timeout=10)
            return True
        except subprocess.TimeoutExpired:
            with self._lock:
                self.state["worker_unreaped"] = True
            return False

    def _finish(self, result):
        with self._lock:
            self.state.update(result, completed_at=time.time())
            history_row = {k: self.state.get(k) for k in ("run_id", "phase", "last_run", "completed_at",
                           "source_hash", "worker_pid", "passed", "failed", "skipped", "collection_errors", "verified", "reason")}
            self.state["history"] = [history_row, *self.state.get("history", [])][:40]
            try:
                self._persist()
            except OSError:
                self.state.update(phase="ERROR", verified=False, summary="Resultado não persistido; aprovação não confirmada.")

    def _owned_mirror(self, path):
        """Return true only for a directly owned, canonical UUID temp directory."""
        try:
            run_id = path.name.removeprefix("run-")
            if (not re.fullmatch(r"[a-f0-9]{32}", run_id) or path.resolve() != self.work / path.name
                    or path.parent != self.work or path.is_symlink()
                    or (hasattr(Path, "is_junction") and path.is_junction())):
                return False
            return read_evidence(path / ".brain-regression-owned.json").get("run_id") == run_id
        except (OSError, ValueError, TypeError):
            return False

    def _unmarked_current_mirror(self, path):
        """The mirror this very run created but could not mark yet (prepare_mirror failed after mkdir)."""
        try:
            return (path.parent == self.work and path.name == "run-" + str(self.state.get("run_id"))
                    and not path.is_symlink() and path.resolve() == self.work / path.name
                    and path.is_dir() and not (path / ".brain-regression-owned.json").exists())
        except OSError:
            return False

    def _cleanup(self, mirror):
        """Discard reproducible copies; keep the latest three failed mirrors.

Only UUID mirrors with our marker are eligible. Evidence/history are separate.
Unknown directories and externally prepared repair candidates are untouched.
An owned mirror that was never finalized (crash) is removed once it is older
than ORPHAN_MIRROR_GRACE_S; nothing new is retained (QHOS-AUD-308).
"""
        try:
            if not self._owned_mirror(mirror):
                if self._unmarked_current_mirror(mirror):
                    shutil.rmtree(mirror)
                return
            failed = self.state["phase"] not in {"PASSED", "PASSED_WITH_SKIPS"}
            if not failed:
                shutil.rmtree(mirror)
            else:
                atomic_json_write(mirror / ".brain-regression-finalized.json",
                                  {"run_id": self.state["run_id"], "failed": True, "completed_at": time.time()})
            retained = []
            now = time.time()
            for path in self.work.iterdir():
                if not path.is_dir() or not self._owned_mirror(path):
                    continue
                try:
                    final = read_evidence(path / ".brain-regression-finalized.json")
                    if final.get("run_id") == path.name[4:] and final.get("failed") is True:
                        retained.append((float(final["completed_at"]), path))
                except FileNotFoundError:
                    # Owned but never finalized: a crash left it. Remove only well after
                    # any worker self-deadline (<= 300 s) could still be using it. A
                    # failure here must not skip the retention and evidence pruning below.
                    try:
                        if path != mirror and now - (path / ".brain-regression-owned.json").stat().st_mtime > ORPHAN_MIRROR_GRACE_S:
                            shutil.rmtree(path)
                    except OSError:
                        with self._lock:
                            self.state["retention_warning"] = "Orphan mirror cleanup incomplete; no other paths touched."
                    continue
                except (OSError, ValueError, TypeError, KeyError):
                    continue
            # These are only source-only, reproducible diagnostic copies. Keep
            # three failures and compact evidence even when more have occurred.
            retained.sort(reverse=True)
            used = 0
            for index, (_, path) in enumerate(retained):
                size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())
                if index >= 3 or used + size > 200 * 1024 * 1024:
                    shutil.rmtree(path)
                else:
                    used += size
            self._prune_evidence()
        except (OSError, ValueError, TypeError):
            # Cleanup trouble never authorizes touching a different path.
            with self._lock:
                self.state["retention_warning"] = "Temporary mirror cleanup incomplete; no other paths touched."

    def _prune_evidence(self):
        keep = {row.get("run_id") for row in self.state.get("history", [])}
        keep.add(self.state.get("run_id"))
        directory = self.state_dir / "regression_evidence"
        if not directory.is_dir() or directory.resolve() != directory:
            return
        for path in directory.glob("*.json"):
            run_id = path.stem.removesuffix("-error")
            if (not re.fullmatch(r"[a-f0-9]{32}", run_id) or run_id in keep or path.is_symlink()
                    or path.resolve().parent != directory):
                continue
            try:
                report = read_evidence(path)
                if report.get("run_id") == run_id and report.get("schema") in {EVIDENCE_SCHEMA, "brain-regression-worker-error-v1"}:
                    path.unlink()
            except (OSError, ValueError, TypeError):
                continue

    def _run(self):
        process = None
        mirror = None
        run_clock = time.monotonic()
        lease = SingleInstanceLock(self.state_dir / "diagnostic_execution.lock")
        with self._lock:
            # worker_unreaped describes the previous run only; never carry it into this one.
            self.state.pop("worker_unreaped", None)
        if not lease.acquire():
            self._finish(self._lock_refusal(lease, deferred_reason="diagnostic_running",
                                            deferred_summary="Outro diagnóstico está executando; regressão adiada."))
            return
        try:
            # A child keeps its own lock if the Brain coordinator is interrupted.
            child_lease = SingleInstanceLock(self.state_dir / "diagnostic_worker.lock")
            if not child_lease.acquire():
                self._finish(self._lock_refusal(child_lease, deferred_reason="previous_worker_running",
                                                deferred_summary="Worker anterior ainda ativo; sem execução sobreposta."))
                return
            child_lease.release()
            with self._lock:
                mark_test_attempt_started(self.state, self.cadence_s)
                self._persist()
            # Only this invocation owns/cleans its fresh mirror; candidate folders
            # made through prepare_mirror are never cleaned by that helper.
            if self.work.resolve() != self.work:
                raise ValueError("mirror_root_redirected")
            mirror = self.work / ("run-" + self.state["run_id"])
            manifest = prepare_mirror(self.target_root, mirror)
            (mirror / ".brain-regression-owned.json").write_text(json.dumps({"run_id": self.state["run_id"]}), encoding="utf-8")
            # A fixed config prevents discovery of a parent conftest/pytest options.
            (mirror / "pytest.ini").write_text("[pytest]\naddopts =\n", encoding="utf-8")
            expected = {"run_id": self.state["run_id"], "source_hash": manifest["sha256"],
                        "suite": list(REVIEWED_TESTS), "started_at": self.state["last_run"]}
            remaining = self.deadline_s-(time.monotonic()-run_clock)
            if self._stop.is_set() or remaining <= 0:
                self._finish({"phase": "INTERRUPTED" if self._stop.is_set() else "TIMEOUT", "verified": False,
                              "reason": "stopped_before_worker", "summary": "Regressão interrompida antes de executar os testes."})
                return
            (mirror / "request.json").write_text(json.dumps({**expected, "deadline_s": remaining}), encoding="utf-8")
            # Fixed worker and argv; no model output, shell, batch, server, or GUI.
            package_root = Path(__file__).resolve().parents[1]
            library_roots = [Path(p).resolve() for p in site.getsitepackages()
                             if isinstance(p, str) and Path(p).is_dir()]
            env = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC"}}
            env.update(PYTHONPATH=os.pathsep.join(map(str, [package_root, *library_roots])), PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                       BRAIN_REGRESSION_PROFILE=REGRESSION_PROFILE,
                       PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TEMP=str(mirror / "_temp"), TMP=str(mirror / "_temp"))
            # -S avoids sitecustomize/usercustomize/.pth startup execution. Only
            # explicit installed library roots above are provided for imports.
            process = subprocess.Popen([sys.executable, "-B", "-S", "-m", "brain_service.regression", "--worker", str(mirror)],
                                       cwd=package_root, env=env, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=0x08000000 if os.name == "nt" else 0)
            self._process = process
            if self._stop.is_set():
                process.kill()
            expected["worker_pid"] = process.pid
            with self._lock:
                self.state.update(source_hash=manifest["sha256"], source_files=len(manifest["files"]),
                                  worker_pid=process.pid, artifact=str(self.state_dir / "regression_evidence" / (self.state["run_id"] + ".json")))
                self._persist()
            try:
                returncode = process.wait(timeout=max(0.01, self.deadline_s-(time.monotonic()-run_clock)))
            except subprocess.TimeoutExpired:
                process.kill()  # Only the child created above; never a port/PID lookup.
                reaped = self._reap(process)
                self._finish({"phase": "TIMEOUT", "verified": False,
                              "reason": "deadline_exceeded" if reaped else "deadline_exceeded_worker_unreaped",
                              "summary": "Regressão excedeu o prazo; worker próprio encerrado, resultado não aprovado."
                              if reaped else "Regressão excedeu o prazo; worker próprio recebeu kill mas não terminou em 10 s."})
                return
            if self._stop.is_set():
                self._finish({"phase": "INTERRUPTED", "verified": False, "reason": "service_stopping",
                              "summary": "Worker próprio encerrado ao desligar o Brain; sem aprovação."})
                return
            if (mirror / "worker-error.json").is_file():
                diagnostic = read_evidence(mirror / "worker-error.json")
                diagnostic.update(schema="brain-regression-worker-error-v1", run_id=self.state["run_id"])
                atomic_json_write(self.state_dir / "regression_evidence" / (self.state["run_id"] + "-error.json"), diagnostic)
                self._finish({"phase": "ERROR", "verified": False, "reason": "worker_initialization_failed",
                              "worker_error": diagnostic, "summary": "Worker não concluiu a coleta; resultado não aprovado."})
                return
            evidence = read_evidence(mirror / "regression-results.json")
            atomic_json_write(Path(self.state["artifact"]), evidence)
            if evidence.get("exit_code") != returncode:
                raise ValueError("worker_exit_mismatch")
            result = verify_regression(evidence, expected,
                                      current_hash=source_manifest(self.target_root, REVIEWED_TESTS)["sha256"],
                                      mirror_hash=source_manifest(mirror, REVIEWED_TESTS)["sha256"], now=time.time())
            result["summary"] = (f"Regressão operacional {result['phase']}: {result['passed']} aprovados, "
                                 f"{result['failed']} falhas, {result['collection_errors']} erros de coleta, "
                                 f"{result['skipped']} ignorados. Não é a auditoria ampla; guardas Python não são sandbox de SO.")
            result["evidence_sha256"] = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
            self._finish(result)
        except Exception as exc:
            # Exception text and test reprs may contain secrets: persist only class.
            reason = str(exc) if type(exc) is ValueError and str(exc) in {"reviewed_tests_changed", "source_changed_while_copying", "reviewed_tests_missing"} else type(exc).__name__
            self._finish({"phase": "REVIEW_REQUIRED" if reason == "reviewed_tests_changed" else "STALE" if reason == "source_changed_while_copying" else "ERROR", "verified": False, "reason": reason,
                          "summary": "Regressão não verificada; falha de preparação, execução ou artefato ("+type(exc).__name__+")."})
        finally:
            # Nothing in this block may raise past lease.release(): a stuck child
            # would otherwise leave diagnostic_execution.lock held by a live process.
            try:
                if process is not None and process.poll() is None:
                    process.kill()
                    self._reap(process)
            except OSError:
                pass
            finally:
                self._process = None
                lease.release()
            if mirror is not None:
                self._cleanup(mirror)


def _worker(mirror):
    """Separate interpreter: reviewed pytest only, with bounded, redacted evidence."""
    import contextlib
    import io
    import socket
    mirror = Path(mirror).resolve()
    if mirror.parent.name != "regression_runs" or not mirror.name.startswith("run-"):
        raise ValueError("invalid_worker_mirror")
    request = read_evidence(mirror / "request.json")
    if request.get("suite") != list(REVIEWED_TESTS):
        raise ValueError("unreviewed_suite")
    child_lease = SingleInstanceLock(mirror.parent.parent / "diagnostic_worker.lock")
    if not child_lease.acquire():
        return 6
    # A hard self-deadline also applies if the parent dies before it can reap us.
    timer = threading.Timer(min(300, max(1, float(request.get("deadline_s", 180)))), lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    os.chdir(mirror)
    # A wheel puts this package and pytest in the same site-packages directory.
    # Keep that dependency root under -S, but deny the live application packages
    # below. A source checkout remains absent from the worker import path.
    package_root = Path(__file__).resolve().parents[1]
    installed_package = package_root.name in {"site-packages", "dist-packages"}
    sys.path[:] = [str(mirror), *[p for p in sys.path if p and
                                 (Path(p).resolve() != package_root or installed_package)]]
    application_names = {"qhos2", "brain", "brain_service", "voice_service"}
    live_application_roots = tuple((package_root / name).resolve() for name in application_names)
    sys.dont_write_bytecode = True
    os.environ.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1",
                      MPLCONFIGDIR=str(mirror / "_cache"), NUMBA_CACHE_DIR=str(mirror / "_cache"),
                      TEMP=str(mirror / "_temp"), TMP=str(mirror / "_temp"),
                      USERPROFILE=str(mirror / "_home"), HOME=str(mirror / "_home"))
    for name in ("_temp", "_home"):
        (mirror / name).mkdir(exist_ok=True)
    violations, records = [], []
    readable_roots = (mirror, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                      *[Path(p).resolve() for p in sys.path if Path(p).name in {"site-packages", "dist-packages"}])
    readable_worker_files = {Path(__file__).resolve(), Path(__file__).with_name("verification.py").resolve(),
                             Path(__file__).with_name("supervisor.py").resolve()}
    null_target = Path(os.devnull).resolve()
    local = threading.local()
    original_socketpair = socket.socketpair
    def private_socketpair(*args, **kwargs):
        local.socketpair = True
        try:
            return original_socketpair(*args, **kwargs)
        finally:
            local.socketpair = False
    socket.socketpair = private_socketpair
    def deny(event, filename=None):
        if len(violations) < 100:
            frame, frames = sys._getframe(1), []
            for _ in range(6):
                if frame is None:
                    break
                frames.append({"file": Path(frame.f_code.co_filename).name, "line": frame.f_lineno,
                               "function": frame.f_code.co_name})
                frame = frame.f_back
            violations.append({"event": event, "filename": filename, "frames": frames})
        raise RuntimeError("isolated_regression_side_effect_refused")
    def writable(path, dir_fd=None):
        if path is None or isinstance(path, int) or str(path).lower() in {"nul", os.devnull.lower()}:
            return
        raw = Path(os.fsdecode(path).removeprefix("\\\\?\\"))
        if not raw.is_absolute() and isinstance(dir_fd, int) and dir_fd >= 0 and os.name != "nt":
            try:
                raw = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / raw
            except OSError:
                deny("unresolved_write_dir_fd")
        actual = raw.resolve()
        if actual != null_target and not actual.is_relative_to(mirror):
            deny("external_write")
    def audit(event, args):
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "subprocess.Popen", "os.system", "os.startfile", "os.startfile/2", "os.fork", "os.posix_spawn"}:
            if event in {"socket.connect", "socket.bind"} and getattr(local, "socketpair", False) and args[1][0] in {"127.0.0.1", "::1"}:
                return
            deny(event)
        if event == "open":
            path, mode, flags = args
            if not isinstance(path, int) and str(path).lower() not in {"nul", os.devnull.lower()}:
                actual = Path(os.fsdecode(path).removeprefix("\\\\?\\")).resolve()
                if (actual not in readable_worker_files and not actual.is_relative_to(mirror)
                        and any(actual.is_relative_to(root) for root in live_application_roots)):
                    deny("live_application_read", actual.name)
                if actual != null_target and actual not in readable_worker_files and not any(actual.is_relative_to(root) for root in readable_roots):
                    deny("external_read", actual.name)
                # Reviewed tests may create fake .env/credentials fixtures inside
                # their mirror. No real credential is copied or readable outside.
                if not actual.is_relative_to(mirror) and any(p.lower().startswith(".env") or p.lower() in {".codex", ".aws", ".ssh", "credentials.json", "auth.json"} for p in actual.parts):
                    deny("credential_read")
            if (mode and any(c in mode for c in "wax+")) or (flags and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                writable(path)
        elif event in {"os.remove", "os.rmdir"}:
            writable(args[0], args[1] if len(args) > 1 else None)
        elif event in {"os.mkdir", "os.chmod"}:
            writable(args[0], args[2] if len(args) > 2 else None)
        elif event == "os.truncate":
            writable(args[0])
        elif event == "os.utime":
            writable(args[0], args[3] if len(args) > 3 else None)
        elif event in {"os.rename", "os.link"}:
            writable(args[0], args[2] if len(args) > 2 else None)
            writable(args[1], args[3] if len(args) > 3 else None)
        elif event == "os.symlink":
            writable(args[1], args[2] if len(args) > 2 else None)
    sys.addaudithook(audit)
    import ctypes
    if os.name == "nt":
        ctypes.windll.kernel32.SetThreadExecutionState = lambda flags: 1
    # Existing control-function references above remain local to this worker;
    # tests cannot inherit imported production application modules from startup.
    for name in list(sys.modules):
        if name.split(".", 1)[0] in application_names:
            del sys.modules[name]
    if REGRESSION_PROFILE == "qhos2-operational-v1":
        import importlib.util
        # Load only the mirrored QHOS2 package, never the installed live sources.
        spec = importlib.util.spec_from_file_location("qhos2", mirror / "__init__.py", submodule_search_locations=[str(mirror)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["qhos2"] = module
        spec.loader.exec_module(module)
    import pytest
    class Report:
        def pytest_runtest_logreport(self, report):
            records.append({"nodeid": report.nodeid, "phase": report.when, "outcome": report.outcome})
        def pytest_collectreport(self, report):
            if report.failed:
                records.append({"nodeid": report.nodeid, "phase": "collect", "outcome": "failed"})
    # Discard test output instead of collecting potentially sensitive traceback text.
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        code = pytest.main([*request["suite"], "-q", "--tb=no", "--continue-on-collection-errors",
                            "-p", "no:cacheprovider", "-c", str(mirror / "pytest.ini"),
                            "-o", "log_file="+str(mirror / "pytest.log"),
                            "--confcutdir", str(mirror), "--basetemp", str(mirror / "_pytest_temp")], plugins=[Report()])
    evidence = {**request, "schema": EVIDENCE_SCHEMA, "worker_pid": os.getpid(), "exit_code": int(code),
                "completed_at": time.time(), "records": records, "blocked_side_effects": violations,
                "boundary": "python-audit-hooks-not-os-sandbox"}
    atomic_json_write(mirror / "regression-results.json", evidence)
    timer.cancel()
    child_lease.release()
    return int(code)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Only a reviewed mirror worker is supported")
    try:
        worker_code = _worker(sys.argv[2])
    except Exception as exc:
        # Safe diagnostic locations, never traceback source, args, or exception text.
        frames, tb = [], exc.__traceback__
        while tb is not None:
            frames.append({"file": Path(tb.tb_frame.f_code.co_filename).name, "line": tb.tb_lineno,
                           "function": tb.tb_frame.f_code.co_name})
            tb = tb.tb_next
        atomic_json_write(Path(sys.argv[2]) / "worker-error.json", {"error": type(exc).__name__, "frames": frames[-8:]})
        worker_code = 7
    raise SystemExit(worker_code)
