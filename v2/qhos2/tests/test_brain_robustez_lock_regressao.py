"""Robustez do lock de diagnostico e da regressao operacional (QHOS-AUD-266, 267, 294, 295, 262, 314)."""
import hashlib
import json
import math
import shutil
import socket
import subprocess
import time
import types
from pathlib import Path

import pytest

from brain_service.config import load_config
from brain_service.supervisor import SingleInstanceLock, atomic_json_write


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL"):
        monkeypatch.delenv(name, raising=False)


# --- QHOS-AUD-266 -----------------------------------------------------------

def test_lock_io_error_is_distinguished_from_contention(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    lock = SingleInstanceLock(blocker / "diagnostic_execution.lock")
    assert lock.acquire() is False
    assert lock.failure_kind == "error", "erro de I/O no lock nao pode virar 'ocupado'"
    assert "NotADirectoryError" in str(lock.last_error) or "FileExistsError" in str(lock.last_error)


def test_lock_contention_is_reported_as_contention(tmp_path):
    first = SingleInstanceLock(tmp_path / "a.lock")
    second = SingleInstanceLock(tmp_path / "a.lock")
    assert first.acquire() is True and first.failure_kind is None
    try:
        assert second.acquire() is False
        assert second.failure_kind == "contention"
    finally:
        first.release()
    assert second.acquire() is True and second.failure_kind is None
    second.release()


def _runner(tmp_path, **kwargs):
    from brain_service.regression import RegressionRunner
    target = tmp_path / "target"
    target.mkdir(exist_ok=True)
    return RegressionRunner(target, tmp_path / "state", **kwargs)


def test_regression_lock_io_error_is_error_not_deferred(tmp_path):
    runner = _runner(tmp_path)
    (tmp_path / "state" / "diagnostic_execution.lock").mkdir(parents=True)  # um diretorio no lugar do lock
    runner.state.update(run_id="a" * 32, last_run=time.time())
    runner._run()
    snap = runner.snapshot()
    assert snap["phase"] == "ERROR", snap
    assert snap["reason"].startswith("diagnostic_lock_error"), snap["reason"]
    assert "Outro diagnóstico" not in snap["summary"]


# --- QHOS-AUD-267 -----------------------------------------------------------

class _UnreapableChild:
    """Filho que ignora SIGKILL por 10 s (I/O em estado D): wait() sempre expira."""
    instances = []

    def __init__(self, *_args, **_kwargs):
        self.pid = 4242
        self.killed = 0
        _UnreapableChild.instances.append(self)

    def poll(self):
        return None

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("worker", timeout)


def _fake_subprocess(popen):
    return types.SimpleNamespace(Popen=popen, TimeoutExpired=subprocess.TimeoutExpired, DEVNULL=subprocess.DEVNULL)


def test_regression_releases_lease_even_if_child_wait_times_out(tmp_path, monkeypatch):
    from brain_service import regression
    _UnreapableChild.instances.clear()
    monkeypatch.setattr(regression, "subprocess", _fake_subprocess(_UnreapableChild))

    def fake_prepare(_root, destination):
        Path(destination).mkdir(parents=True)
        return {"sha256": "0" * 64, "files": []}

    monkeypatch.setattr(regression, "prepare_mirror", fake_prepare)
    runner = _runner(tmp_path, deadline_s=10)
    runner.state.update(run_id="b" * 32, last_run=time.time())
    try:
        runner._run()
    except subprocess.TimeoutExpired:
        pytest.fail("TimeoutExpired escapou de _run: a thread morreria com o lock preso")
    lease = SingleInstanceLock(tmp_path / "state" / "diagnostic_execution.lock")
    assert lease.acquire() is True, f"lock ficou preso: {lease.failure_kind} {lease.last_error}"
    lease.release()
    snap = runner.snapshot()
    assert snap["phase"] in {"TIMEOUT", "ERROR"} and snap["verified"] is False
    assert snap.get("worker_unreaped") is True
    assert _UnreapableChild.instances and _UnreapableChild.instances[0].killed >= 1
    assert runner._process is None


# --- QHOS-AUD-294 -----------------------------------------------------------

def test_atomic_json_write_refuses_nan_and_keeps_previous_file(tmp_path):
    path = tmp_path / "state.json"
    atomic_json_write(path, {"ok": 1})
    with pytest.raises(ValueError):
        atomic_json_write(path, {"x": math.nan})
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": 1}
    assert not list(tmp_path.glob("state.json.*.tmp")), "temporario abandonado"


def test_atomic_json_write_keeps_its_byte_format(tmp_path):
    """Contrato de bytes dos chamadores (tester/regressao/supervisor): indent 2, sort_keys, newline final."""
    path = tmp_path / "state.json"
    atomic_json_write(path, {"b": 1, "a": [1, 2]})
    assert path.read_text(encoding="utf-8") == '{\n  "a": [\n    1,\n    2\n  ],\n  "b": 1\n}\n'


# --- QHOS-AUD-295 -----------------------------------------------------------

def test_unreadable_regression_state_is_reported_not_silently_reset(tmp_path, caplog):
    import logging
    state = tmp_path / "state"
    state.mkdir()
    (state / "operational_regression.json").write_text("{corrupt", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        runner = _runner(tmp_path)
    snap = runner.snapshot()
    assert snap["phase"] == "NOT_RUN" and snap["history"] == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING and "operational_regression" in r.getMessage()], \
        "estado ilegivel descartado em silencio (sem WARNING)"
    assert str(snap.get("previous_state_discarded") or "").startswith("JSONDecodeError"), snap.get("previous_state_discarded")


def test_discard_marker_is_not_persisted_and_clears_on_valid_reopen(tmp_path, caplog):
    """Revisao do lote: o campo entrava em self.state, era persistido e voltava colado no reinicio seguinte."""
    import logging
    state = tmp_path / "state"
    state.mkdir()
    (state / "operational_regression.json").write_text("{corrupt", encoding="utf-8")
    first = _runner(tmp_path)
    assert first.snapshot()["previous_state_discarded"]
    first._persist()  # a proxima gravacao nao pode carregar o marcador
    assert "previous_state_discarded" not in json.loads((state / "operational_regression.json").read_text(encoding="utf-8"))
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        second = _runner(tmp_path)
    assert second.snapshot().get("previous_state_discarded") is None
    assert not [r for r in caplog.records if "discarded" in r.getMessage()]
    # Mesmo com um estado antigo que ja trazia o campo persistido: ele e ignorado na carga.
    atomic_json_write(state / "operational_regression.json", dict(second.state, previous_state_discarded="JSONDecodeError: antigo"))
    third = _runner(tmp_path)
    assert third.snapshot().get("previous_state_discarded") is None
    assert "previous_state_discarded" not in third.state


def test_worker_unreaped_marker_is_cleared_at_the_start_of_a_run(tmp_path):
    runner = _runner(tmp_path)
    runner.state["worker_unreaped"] = True
    (tmp_path / "state" / "diagnostic_execution.lock").mkdir(parents=True)  # a corrida termina ja no lock
    runner.state.update(run_id="d" * 32, last_run=time.time())
    runner._run()
    assert "worker_unreaped" not in runner.snapshot()


def test_regression_state_from_another_target_root_is_reported(tmp_path):
    first = _runner(tmp_path)
    assert first.snapshot().get("previous_state_discarded") is None
    saved = dict(first.state, target_root=str(tmp_path / "other-venv" / "qhos2"), phase="PASSED", history=[{"run_id": "x"}])
    atomic_json_write(tmp_path / "state" / "operational_regression.json", saved)
    second = _runner(tmp_path)
    snap = second.snapshot()
    assert snap["phase"] == "NOT_RUN" and snap["history"] == []
    assert snap.get("previous_state_discarded") == "target_root_changed", "troca de target_root descartou o historico em silencio"


def test_unreadable_tester_state_is_reported_not_silently_reset(tmp_path, caplog):
    import logging
    from brain_service.tester import SoftwareTester
    state = tmp_path / "smoke"
    state.mkdir()
    (state / "tester_state.json").write_text("[]", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        smoke = SoftwareTester(load_config().target_root, state)
    assert smoke.snapshot()["phase"] == "IDLE"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING and "tester_state" in r.getMessage()], \
        "estado ilegivel do tester descartado em silencio"
    assert str(smoke.snapshot().get("previous_state_discarded") or "").startswith("ValueError")
    atomic_json_write(state / "tester_state.json", smoke.state)
    assert "previous_state_discarded" not in json.loads((state / "tester_state.json").read_text(encoding="utf-8"))
    reopened = SoftwareTester(load_config().target_root, state)
    assert reopened.snapshot().get("previous_state_discarded") is None


# --- QHOS-AUD-262 -----------------------------------------------------------

def _frozen_copy(tmp_path):
    frozen = tmp_path / "qhos2"
    shutil.copytree(load_config().target_root, frozen, ignore=shutil.ignore_patterns("__pycache__"))
    return frozen


def test_regressao_com_hash_alterado_vai_para_review_required_sem_subprocesso(tmp_path, monkeypatch):
    from brain_service import regression
    frozen = _frozen_copy(tmp_path)
    reviewed = next(iter(regression.REVIEWED_TESTS))
    with (frozen / reviewed).open("a", encoding="utf-8") as handle:
        handle.write("\n# alteracao nao revisada\n")

    def forbidden(*_a, **_k):
        pytest.fail("worker nao pode ser iniciado com teste revisado alterado")

    monkeypatch.setattr(regression, "subprocess", _fake_subprocess(forbidden))
    runner = regression.RegressionRunner(frozen, tmp_path / "state", deadline_s=30)
    assert runner.start_run()["accepted"]
    runner._thread.join(30)
    snap = runner.snapshot()
    assert snap["phase"] == "REVIEW_REQUIRED" and snap["reason"] == "reviewed_tests_changed"
    assert snap["verified"] is False
    assert json.loads((tmp_path / "state" / "operational_regression.json").read_text(encoding="utf-8"))["phase"] == "REVIEW_REQUIRED"


def _passed_state(runner, frozen, state_dir):
    """Estado + evidencia coerentes e verificaveis, como os que uma execucao PASSED deixa."""
    from brain_service.regression import REVIEWED_TESTS
    from brain_service.verification import EVIDENCE_SCHEMA, source_manifest
    run_id = "c" * 32
    started = time.time() - 30
    source_hash = source_manifest(frozen, REVIEWED_TESTS)["sha256"]
    suite = list(REVIEWED_TESTS)
    records = []
    for node in (f"{suite[0]}::test_a", f"{suite[0]}::test_b"):
        for phase in ("setup", "call", "teardown"):
            records.append({"nodeid": node, "phase": phase, "outcome": "passed"})
    evidence = {"schema": EVIDENCE_SCHEMA, "run_id": run_id, "source_hash": source_hash, "worker_pid": 4321,
                "suite": suite, "started_at": started, "deadline_s": 30, "completed_at": started + 5,
                "exit_code": 0, "records": records, "blocked_side_effects": [],
                "boundary": "python-audit-hooks-not-os-sandbox"}
    artifact_dir = state_dir / "regression_evidence"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    atomic_json_write(artifact_dir / (run_id + ".json"), evidence)
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, allow_nan=False).encode()).hexdigest()
    state = dict(runner.state, phase="PASSED", verified=True, run_id=run_id, last_run=started, completed_at=started + 5,
                 source_hash=source_hash, worker_pid=4321, evidence_sha256=digest, passed=2, failed=0,
                 artifact=str(artifact_dir / (run_id + ".json")), next_due=time.time() + 3600)
    atomic_json_write(state_dir / "operational_regression.json", state)
    return artifact_dir / (run_id + ".json")


def test_evidencia_adulterada_fica_unverified_e_fonte_mudado_fica_stale(tmp_path):
    from brain_service.regression import RegressionRunner
    frozen = _frozen_copy(tmp_path)
    state_dir = tmp_path / "state"
    seed = RegressionRunner(frozen, state_dir)
    artifact = _passed_state(seed, frozen, state_dir)
    reopened = RegressionRunner(frozen, state_dir)
    assert reopened.snapshot()["phase"] == "PASSED" and reopened.snapshot()["verified"] is True
    # Evidencia adulterada (um caso a mais) -> hash do artefato nao bate -> UNVERIFIED.
    tampered = json.loads(artifact.read_text(encoding="utf-8"))
    tampered["records"].append({"nodeid": tampered["suite"][0] + "::test_c", "phase": "call", "outcome": "passed"})
    atomic_json_write(artifact, tampered)
    snap = RegressionRunner(frozen, state_dir).snapshot()
    assert snap["phase"] == "UNVERIFIED" and snap["verified"] is False and snap["reason"] == "saved_evidence_invalid"
    # Fonte mudou depois de uma aprovacao integra -> STALE, nao PASSED.
    _passed_state(seed, frozen, state_dir)
    (frozen / "coletor.py").write_text((frozen / "coletor.py").read_text(encoding="utf-8") + "\n# mudou\n", encoding="utf-8")
    snap = RegressionRunner(frozen, state_dir).snapshot()
    assert snap["phase"] == "STALE" and snap["verified"] is False and snap["reason"] == "source_changed"


def _closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_tester_sem_coletor_falha_verificado_e_persiste(tmp_path):
    from brain_service.tester import SoftwareTester
    smoke = SoftwareTester(load_config().target_root, tmp_path / "smoke", quant_url=f"http://127.0.0.1:{_closed_port()}")
    smoke.state["run_id"] = "fixture"
    smoke._checks()
    snap = smoke.snapshot()
    assert snap["phase"] == "FAILED" and snap["failed"] == 5 and snap["passed"] == 5
    assert snap["verified"] is True, "falha confirmada com codigo integro e 10 casos e verificada"
    failed = {case["name"]: case["detail"] for case in snap["cases"] if case["status"] == "failed"}
    assert set(failed) == {"API: /api/health", "API: /api/status", "API: /api/busy",
                           "Identidade e disponibilidade", "Coerência do registro de módulos"}
    persisted = json.loads((tmp_path / "smoke" / "tester_state.json").read_text(encoding="utf-8"))
    assert persisted["phase"] == "FAILED" and persisted["run_id"] == "fixture"
    assert (tmp_path / "smoke" / "smoke_evidence.json").is_file()


# --- QHOS-AUD-314 -----------------------------------------------------------

def test_only_the_v2_profile_points_to_existing_reviewed_tests():
    from brain_service import regression
    root = load_config().target_root
    current = regression.reviewed_tests_for_profile("qhos2-operational-v1")
    assert current == regression.REVIEWED_TESTS and regression.REGRESSION_PROFILE == "qhos2-operational-v1"
    assert all((root / path).is_file() for path in current)
    for legacy in ("windows-operational-v1", "linux-runtime-v1"):
        paths = list(regression.reviewed_tests_for_profile(legacy))
        assert paths and not any((root / path).exists() for path in paths), legacy
    with pytest.raises(ValueError, match="unknown_reviewed_regression_profile"):
        regression.reviewed_tests_for_profile("qhos2-operational-v9")


def test_prepare_mirror_names_missing_reviewed_test(tmp_path, monkeypatch):
    from brain_service import regression
    target = tmp_path / "target"
    (target / "tests").mkdir(parents=True)
    (target / "coletor.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(regression, "REVIEWED_TESTS", {"tests/test_linux_operational_safety.py": "0" * 64})
    with pytest.raises(ValueError, match="reviewed_tests_missing"):
        regression.prepare_mirror(target, tmp_path / "mirror")
    assert not (tmp_path / "mirror").exists()
