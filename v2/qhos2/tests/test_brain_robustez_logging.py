"""Robustez do Brain: erros do laço, do desligamento, do monitor e do bus chegam ao log com causa (QHOS-AUD-259, 268, 289, 290)."""
import asyncio
import io
import logging
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


# --- QHOS-AUD-259 -----------------------------------------------------------

def test_cycle_error_reaches_logging_with_cause(tmp_path, caplog):
    import brain.runtime as rt

    def observer():
        raise RuntimeError("disco cheio")

    runtime = rt.BrainRuntime(tmp_path / "brain", observer=observer)
    with caplog.at_level(logging.DEBUG, logger="Brain"):
        asyncio.run(runtime.cycle())
    found = _warnings(caplog, "disco cheio")
    assert found, "a falha do ciclo tem de sair em WARNING+ com a causa; registros: %r" % [
        (r.levelname, r.getMessage()) for r in caplog.records]
    # Onde falhou (cauda de quadros), sem exc_info: o texto do traceback repetiria a mensagem sem redacao.
    assert "observer" in found[0].getMessage() and "_cycle_body" in found[0].getMessage()
    audit = runtime.store.audit.tail(1)[0]
    assert audit["kind"] == "CYCLE_FAILED"
    assert audit["payload"]["error"] == "RuntimeError"
    assert "disco cheio" in audit["payload"]["detail"]
    assert "observer" in audit["payload"]["where"]


def test_cycle_error_detail_is_sanitized(tmp_path, caplog):
    import brain.runtime as rt

    def observer():
        raise RuntimeError("falha token=abc123segredo ao gravar")

    runtime = rt.BrainRuntime(tmp_path / "brain", observer=observer)
    with caplog.at_level(logging.DEBUG, logger="Brain"):
        asyncio.run(runtime.cycle())
    audit = runtime.store.audit.tail(1)[0]
    assert "abc123segredo" not in audit["payload"]["detail"]
    assert "abc123segredo" not in caplog.text


def test_main_installs_root_log_handler():
    from brain_service.__main__ import configure_logging
    root = logging.Logger("qhos-test-root")
    stream = io.StringIO()
    handler = configure_logging(stream=stream, root=root)
    try:
        assert handler is not None and handler in root.handlers
        assert root.level <= logging.INFO
        # Idempotente: a segunda chamada nao duplica o destino.
        assert configure_logging(stream=io.StringIO(), root=root) is handler
        assert len(root.handlers) == 1
        root.warning("ciclo falhou: %s", "disco cheio")
        assert "disco cheio" in stream.getvalue()
    finally:
        root.removeHandler(handler)


# --- QHOS-AUD-268 -----------------------------------------------------------

def _standalone(tmp_path):
    from brain_service.config import ServiceConfig
    from brain_service.process import StandaloneBrain
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False)
    return StandaloneBrain(cfg)


async def _one_monitor_iteration(brain, monkeypatch):
    """Uma iteracao do monitor: _monitor_once quando existe; na referencia, o laco _monitor com o sleep anulado."""
    once = getattr(brain, "_monitor_once", None)
    if once is not None:
        await once()
        return
    from brain_service import process as process_module

    async def stop_after_first(_seconds):
        brain.started = False

    monkeypatch.setattr(process_module.asyncio, "sleep", stop_after_first)
    try:
        brain.started = True
        await brain._monitor()
    finally:
        monkeypatch.undo()


def test_monitor_failure_is_logged_and_counted(tmp_path, caplog, monkeypatch):
    brain = _standalone(tmp_path)
    try:
        def failing():
            raise OSError("x: coletor respondeu lixo token=segredo123")

        brain.observer.collect = failing
        brain.started = True
        with caplog.at_level(logging.WARNING):
            asyncio.run(_one_monitor_iteration(brain, monkeypatch))
            asyncio.run(_one_monitor_iteration(brain, monkeypatch))
        # Comportamento primeiro: a causa tem de chegar ao journal e a trilha, nao so o nome da classe.
        found = _warnings(caplog, "x: coletor")
        assert found, "falha do monitor nao sai no log com a causa: %r" % [(r.levelname, r.getMessage()) for r in caplog.records]
        assert "segredo123" not in caplog.text
        linux = brain.linux_actions.snapshot()["actions"][-1]
        assert linux["action"] == "monitor_error" and "x: coletor" in linux["detail"], linux
        status = brain.monitor_status()
        assert status["consecutive_failures"] == 2
        assert status["failures_total"] == 2
        assert "OSError" in status["last_error"] and "x: coletor" in status["last_error"]
        assert "segredo123" not in status["last_error"]
        assert status["last_failure_at"] and status["last_success_at"] is None
        # Uma iteracao boa zera a sequencia, mas nao apaga a ultima falha nem o total.
        brain.observer.collect = lambda: {}
        asyncio.run(_one_monitor_iteration(brain, monkeypatch))
        status = brain.monitor_status()
        assert status["consecutive_failures"] == 0 and status["failures_total"] == 2
        assert status["last_error"] and status["last_success_at"]
        assert brain.assistant.state()["monitor"]["failures_total"] == 2
    finally:
        brain.started = False
        brain.log_feed.close()


# --- QHOS-AUD-289 -----------------------------------------------------------

def test_stop_persist_failure_is_logged(tmp_path, monkeypatch, caplog):
    import brain.runtime as rt
    runtime = rt.BrainRuntime(tmp_path / "brain", observer=lambda: dict(OBS))

    async def scenario():
        await runtime.start()

        def boom(_payload):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(runtime.store, "save_state", boom)
        await runtime.stop()

    with caplog.at_level(logging.WARNING, logger="Brain"):
        asyncio.run(scenario())
    assert runtime.running is False
    found = _warnings(caplog, "No space left on device")
    assert found, [(r.levelname, r.getMessage()) for r in caplog.records]
    kinds = [row["kind"] for row in runtime.store.audit.tail(5)]
    assert "PERSIST_FAILED" in kinds and kinds[-1] == "BRAIN_STOPPED"


def test_relate_intelligence_graph_failure_is_logged(tmp_path, monkeypatch, caplog):
    import brain.runtime as rt
    runtime = rt.BrainRuntime(tmp_path / "brain", observer=lambda: dict(OBS))

    def broken(*_a, **_k):
        raise RuntimeError("grafo corrompido")

    monkeypatch.setattr(runtime.store.graph, "relate", broken)
    with caplog.at_level(logging.WARNING, logger="Brain"):
        runtime._relate_intelligence({"knowledge_graph": [{"source": "a", "verb": "RELATED", "target": "b"}]})
    assert _warnings(caplog, "grafo corrompido")


# --- QHOS-AUD-290 -----------------------------------------------------------

def test_event_handler_failure_is_logged_and_other_handlers_still_run(caplog):
    from brain.local_events import Event, EventBus, EventType
    bus = EventBus()
    seen = []

    def explosive_handler(_event):
        raise ValueError("boom")

    bus.subscribe(EventType.BRAIN_EVENT, explosive_handler)
    bus.subscribe(EventType.BRAIN_EVENT, seen.append)
    with caplog.at_level(logging.WARNING):
        asyncio.run(bus.publish(Event(type=EventType.BRAIN_EVENT, source="teste")))
    assert len(seen) == 1
    found = [r for r in _warnings(caplog, "boom") if "explosive_handler" in r.getMessage()]
    assert found, [(r.levelname, r.getMessage()) for r in caplog.records]
    assert bus.handler_failures == 1


def test_event_bus_and_publisher_logs_are_sanitized(caplog, monkeypatch):
    """Revisao do lote: str(exc) cru nos WARNING do bus/publicador; a causa sai redigida."""
    from brain import events
    from brain.local_events import Event, EventBus, EventType
    bus = EventBus()

    def leaky_handler(_event):
        raise ValueError("falhou com token=segredo456")

    bus.subscribe(EventType.BRAIN_EVENT, leaky_handler)
    with caplog.at_level(logging.WARNING):
        asyncio.run(bus.publish(Event(type=EventType.BRAIN_EVENT, source="teste")))

    async def scenario():
        loop = asyncio.get_running_loop()
        original = loop.create_task

        def refuse(*_a, **_k):
            raise RuntimeError("agendador recusou token=segredo789")

        loop.create_task = refuse
        try:
            with caplog.at_level(logging.WARNING):
                events.publish_brain_event(events.BrainEventKind.CYCLE_STARTED, payload={"n": 1})
        finally:
            loop.create_task = original

    asyncio.run(scenario())
    assert _warnings(caplog, "leaky_handler") and _warnings(caplog, "agendador recusou")
    assert "segredo456" not in caplog.text and "segredo789" not in caplog.text


def test_log_investigation_error_is_logged_and_sanitized(tmp_path, caplog):
    brain = _standalone(tmp_path)
    try:
        def broken(_feed):
            raise OSError("knowledge_index: disco cheio token=segredo321")

        brain.investigation.observe_logs = broken

        async def scenario():
            brain.started = True
            task = asyncio.create_task(brain._investigate_logs())
            deadline = time.monotonic() + 5
            while not brain.linux_actions.snapshot()["actions"] and time.monotonic() < deadline:
                await asyncio.sleep(0)
            brain.started = False
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        with caplog.at_level(logging.WARNING):
            asyncio.run(scenario())
        rows = [row for row in brain.linux_actions.snapshot()["actions"] if row["action"] == "log_investigation_error"]
        # Trilha persistida so com o nome da classe (QHOS-AUD-313); a causa sanitizada vai so ao WARNING do journal.
        assert rows and rows[0]["detail"] == "OSError", rows
        assert _warnings(caplog, "disco cheio") and "segredo321" not in caplog.text
    finally:
        brain.started = False
        brain.log_feed.close()


def test_publish_without_running_loop_is_counted_not_silent():
    from brain import events
    before = dict(events.publish_stats)
    events.publish_brain_event(events.BrainEventKind.CYCLE_STARTED, payload={"n": 1})
    assert events.publish_stats["not_scheduled_no_loop"] == before.get("not_scheduled_no_loop", 0) + 1


def test_publish_scheduling_failure_is_logged_and_counted(monkeypatch, caplog):
    from brain import events

    async def scenario():
        loop = asyncio.get_running_loop()

        def refuse(*_a, **_k):
            raise MemoryError("sem memoria para a tarefa")

        original = loop.create_task
        loop.create_task = refuse  # restaurado antes de o laco encerrar (asyncio.run usa create_task ao fechar)
        try:
            with caplog.at_level(logging.WARNING):
                events.publish_brain_event(events.BrainEventKind.CYCLE_STARTED, payload={"n": 1})
        finally:
            loop.create_task = original

    before = dict(events.publish_stats)
    asyncio.run(scenario())
    assert events.publish_stats["failed"] == before.get("failed", 0) + 1
    assert _warnings(caplog, "MemoryError")
