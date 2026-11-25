"""Robustez do runtime do Brain: trilha de acoes, snapshot concorrente, desligamento e memoria de ids (QHOS-AUD-278, 281, 298, 316)."""
import asyncio
import logging
import threading
import time

import pytest

OBS = {"registry": {}, "application_ready": False, "error_tail": [], "resources": {}, "git": {}, "hpc": {},
       "committee": {}, "truth": {}, "telemetry": {"errors": []}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL", "BRAIN_QUANT_LOGS"):
        monkeypatch.delenv(name, raising=False)
    import brain.runtime as rt
    monkeypatch.setattr(rt, "soak_enabled", lambda: False)
    monkeypatch.setattr(rt.config, "brain_state_dir", tmp_path / "eval-state")


def _warnings(caplog, needle):
    return [r for r in caplog.records if r.levelno >= logging.WARNING and needle in r.getMessage()]


# --- QHOS-AUD-278 -----------------------------------------------------------

def test_action_log_reports_persistence_failure(tmp_path, monkeypatch, caplog):
    from brain_service.linux_actions import LinuxActionLog
    log = LinuxActionLog(tmp_path / "state")
    log.record("service_started")
    snap = log.snapshot()
    assert snap.get("persisted", True) is True and snap.get("persistence_error") is None

    def read_only():
        raise OSError("ro")

    monkeypatch.setattr(log, "_persist", read_only)
    with caplog.at_level(logging.WARNING):
        log.record("monitor_error", outcome="error", detail="x")
        log.record("monitor_error", outcome="error", detail="y")
    snap = log.snapshot()
    assert [row["detail"] for row in snap["actions"][-2:]] == ["x", "y"], "a memoria continua, so o disco falhou"
    assert len(_warnings(caplog, "OSError: ro")) == 1, "falha de persistencia da trilha engolida (sem aviso na transicao)"
    assert snap.get("persisted", True) is False and snap.get("persistence_error") == "OSError: ro"
    assert snap.get("persistence_failures") == 2
    monkeypatch.undo()
    log.record("recovered")
    snap = log.snapshot()
    assert snap["persisted"] is True and snap["persistence_error"] is None and snap["persistence_failures"] == 2


def test_action_log_real_io_failure_is_visible(tmp_path):
    from brain_service.linux_actions import LinuxActionLog
    state = tmp_path / "state"
    state.mkdir()
    (state / "linux_actions.json").mkdir()  # um diretorio onde o arquivo deveria estar
    log = LinuxActionLog(state)
    log.record("service_started")
    snap = log.snapshot()
    assert snap["actions"][-1]["action"] == "service_started"
    assert snap.get("persisted", True) is False and snap.get("persistence_error"), "trilha nao gravada sem nenhum sinal no snapshot"


# --- QHOS-AUD-281 -----------------------------------------------------------

def _runtime(tmp_path):
    import brain.runtime as rt
    return rt.BrainRuntime(tmp_path / "brain", observer=lambda: dict(OBS))


def test_snapshot_is_safe_when_orchestrator_dicts_change_during_iteration(tmp_path):
    """Determinístico: o to_dict() de uma tarefa insere outra, como faria open_incident() no laco
    enquanto o pool de threads serializa o snapshot (RuntimeError: dictionary changed size)."""
    from brain.models import Goal, Task
    runtime = _runtime(tmp_path)
    tasks, goals = runtime.orchestrator.tasks, runtime.orchestrator.goals

    class TaskInsertingAnother(Task):
        def to_dict(self):
            tasks[f"t-{len(tasks)}"] = Task(task_id=f"t-{len(tasks)}", title="concorrente")
            return super().to_dict()

    class GoalInsertingAnother(Goal):
        def to_dict(self):
            goals[f"g-{len(goals)}"] = Goal(goal_id=f"g-{len(goals)}", title="concorrente", reason="r")
            return super().to_dict()

    tasks["t0"] = TaskInsertingAnother(task_id="t0", title="x")
    goals["g0"] = GoalInsertingAnother(goal_id="g0", title="x", reason="r")
    snap = runtime.snapshot(force=True)
    assert any(t["task_id"] == "t0" for t in snap["tasks"]) and any(g["goal_id"] == "g0" for g in snap["goals"])
    assert snap["available"] is True


def test_snapshot_is_safe_when_sandbox_dicts_change_during_iteration(tmp_path):
    runtime = _runtime(tmp_path)
    runs = runtime.sandbox.runs

    class RunInsertingAnother:
        def __init__(self, name):
            self.name = name

        @property
        def status(self):
            runs[f"{self.name}-again"] = type("Quiet", (), {"status": "ready_for_review", "to_dict": lambda s: {"id": s.name}})()
            runs[f"{self.name}-again"].name = f"{self.name}-again"
            return "ready_for_review"

        def to_dict(self):
            return {"id": self.name}

    runs["r0"] = RunInsertingAnother("r0")
    snap = runtime.snapshot(force=True)
    assert snap["metrics"]["sandbox_success_total"] >= 1
    assert any(r["id"] == "r0" for r in snap["sandbox"]["runs"])


# --- QHOS-AUD-316 -----------------------------------------------------------

def test_mark_processed_forgets_oldest_not_arbitrary(tmp_path):
    from brain.memory import BrainStore
    store = BrainStore(tmp_path / "brain")
    for index in range(20_001):
        assert store.mark_processed(f"ev-{index}") is True
    assert len(store.processed_ids) <= 10_001
    # Os 10 000 mais recentes continuam conhecidos (idempotencia preservada onde importa)...
    forgotten_recent = [i for i in range(10_001, 20_001) if store.mark_processed(f"ev-{i}")]
    assert forgotten_recent == []
    # ...e os mais antigos foram os descartados: um id antigo volta a ser aceito como novo.
    # Lacuna de contrato registrada na ficha: o docstring nao define o que acontece com ids esquecidos.
    assert store.mark_processed("ev-0") is True


# --- QHOS-AUD-298 -----------------------------------------------------------

def _standalone(tmp_path):
    from brain_service.config import ServiceConfig
    from brain_service.process import StandaloneBrain
    return StandaloneBrain(ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False))


def test_stop_waits_for_in_flight_monitor_work(tmp_path):
    brain = _standalone(tmp_path)
    entered = threading.Event()   # collect() comecou (esta em voo)
    stopping = threading.Event()  # stop() foi chamado
    gate = threading.Event()      # collect() pode terminar
    done = {"collect": False}

    def slow_collect():
        entered.set()
        gate.wait(5)
        done["collect"] = True
        return {}

    def release_when_stopping():
        stopping.wait(5)
        gate.set()

    brain.observer.collect = slow_collect
    threading.Thread(target=release_when_stopping, daemon=True).start()

    async def scenario():
        brain.started = True
        brain._monitor_task = asyncio.create_task(brain._monitor())
        assert await asyncio.to_thread(entered.wait, 5), "collect() nao chegou a entrar em voo"
        stopping.set()
        started = time.monotonic()
        await brain.stop()
        return done["collect"], time.monotonic() - started

    try:
        finished, elapsed = asyncio.run(scenario())
    finally:
        gate.set()
        brain.log_feed.close()
    assert finished is True, "stop() voltou com collect() ainda em voo"
    assert elapsed < 5
    assert brain.shutdown_status()["drained"] is True


def test_stop_gives_up_on_stuck_thread_but_says_so(tmp_path, monkeypatch, caplog):
    from brain_service import process as process_module
    brain = _standalone(tmp_path)
    entered = threading.Event()
    gate = threading.Event()

    def stuck_collect():
        entered.set()
        gate.wait(10)

    brain.observer.collect = stuck_collect
    monkeypatch.setattr(process_module, "SHUTDOWN_GRACE_S", 0.2)

    async def scenario():
        brain.started = True
        brain._monitor_task = asyncio.create_task(brain._monitor())
        assert await asyncio.to_thread(entered.wait, 5)
        with caplog.at_level(logging.WARNING):
            await brain.stop()
        status = brain.shutdown_status()  # antes de asyncio.run encerrar o executor (que espera a thread)
        gate.set()
        return status

    try:
        status = asyncio.run(scenario())
        assert status["drained"] is False and status["in_flight"] == 1
        assert _warnings(caplog, "in flight")
    finally:
        gate.set()
        brain.log_feed.close()


def test_investigate_logs_does_not_open_incidents_after_stop(tmp_path):
    brain = _standalone(tmp_path)
    opened = []
    brain.runtime.open_incident = lambda **kw: opened.append(kw)
    gate = threading.Event()

    def slow_observe(_feed):
        gate.wait(5)
        return [{"component": "c", "title": "t", "severity": "warning", "last_cursor": 1, "id": "i"}]

    brain.investigation.observe_logs = slow_observe

    async def scenario():
        brain.started = True
        brain._log_task = asyncio.create_task(brain._investigate_logs())
        # Sem temporizadores: espera a chamada entrar em voo, chama stop() e so entao libera o gate.
        deadline = time.monotonic() + 5
        while not brain._inflight and time.monotonic() < deadline:
            await asyncio.sleep(0)
        stop = asyncio.ensure_future(brain.stop())
        gate.set()
        await stop

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
        brain.log_feed.close()
    assert opened == [], "incidente aberto depois de runtime.stop()"
