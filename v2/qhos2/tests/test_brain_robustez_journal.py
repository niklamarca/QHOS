"""Robustez do leitor do journal e da investigacao de logs (QHOS-AUD-269, 292, 304, 305, 306)."""
import json
import logging
import subprocess
import threading
import time
import types

import pytest

from brain_service.config import load_config


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL", "BRAIN_QUANT_LOGS"):
        monkeypatch.delenv(name, raising=False)


class FakeFeed:
    """Feed em memoria com o mesmo contrato de page() do RuntimeLogFeed."""

    def __init__(self, records):
        self.records = list(records)

    def page(self, after=0, limit=100):
        rows = [row for row in self.records if row["cursor"] > int(after)][: int(limit)]
        return {"schema": "qhos-log-stream-v1", "records": rows,
                "next_cursor": rows[-1]["cursor"] if rows else int(after)}


def _row(cursor, component, level="error"):
    tag = level.upper()
    return {"cursor": cursor, "timestamp": 1_700_000_000 + cursor, "level": level, "unit": "qhos2-coletor.service",
            "pid": "1", "message": f"2026-10-28 10:00:0{cursor % 10}.000 | {tag} | {component} | falhou ao gravar"}


def _error_row(cursor, component):
    return _row(cursor, component)


def _engine(tmp_path):
    from brain_service.investigation import InvestigationEngine
    return InvestigationEngine(load_config().target_root, tmp_path / "state")


# --- QHOS-AUD-269 -----------------------------------------------------------

@pytest.mark.parametrize("fail_on_call", [1, 2])
def test_observe_logs_does_not_skip_rows_on_search_failure(tmp_path, monkeypatch, fail_on_call):
    engine = _engine(tmp_path)
    feed = FakeFeed([_error_row(1, "alpha"), _error_row(2, "beta")])
    calls = {"n": 0}

    def flaky_search(_query, max_results=3):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise OSError(28, "No space left on device")
        return []

    monkeypatch.setattr(engine.knowledge, "search", flaky_search)
    with pytest.raises(OSError):
        engine.observe_logs(feed)
    # O cursor so avanca sobre linhas processadas: a que falhou volta no proximo lote.
    assert engine.log_cursor == fail_on_call - 1
    saved = json.loads(engine.incident_path.read_text()) if engine.incident_path.exists() else {"cursor": 0, "incidents": {}}
    assert saved["cursor"] == fail_on_call - 1 and len(saved["incidents"]) == fail_on_call - 1
    engine.observe_logs(feed)
    assert len(engine.automatic) == 2, sorted(v["component"] for v in engine.automatic.values())
    assert engine.log_cursor == 2
    assert all(v["occurrences"] == 1 for v in engine.automatic.values())


# --- QHOS-AUD-304 -----------------------------------------------------------

def test_info_only_batches_do_not_rewrite_investigations_file(tmp_path):
    engine = _engine(tmp_path)
    engine.observe_logs(FakeFeed([_row(1, "a", "info"), _row(2, "b", "info")]))
    assert engine.log_cursor == 2
    assert not engine.incident_path.exists(), "lote so de info nao deve regravar o arquivo (realimentacao do journal)"
    engine.observe_logs(FakeFeed([_row(3, "c", "error")]))
    assert json.loads(engine.incident_path.read_text())["cursor"] == 3
    first_mtime = engine.incident_path.stat().st_mtime_ns
    engine.observe_logs(FakeFeed([_row(4, "d", "info")]))
    assert engine.incident_path.stat().st_mtime_ns == first_mtime and engine.log_cursor == 4
    # Progresso so de cursor e persistido no maximo uma vez por janela, para limitar o reprocessamento apos reinicio.
    engine._last_persist_at = 0
    engine.observe_logs(FakeFeed([_row(5, "e", "info")]))
    assert json.loads(engine.incident_path.read_text())["cursor"] == 5


# --- QHOS-AUD-306 -----------------------------------------------------------

def test_corrupt_investigation_state_is_reported_not_silently_reset(tmp_path, caplog):
    state = tmp_path / "state"
    state.mkdir()
    (state / "runtime_investigations.json").write_text("{corrupt", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        engine = _engine(tmp_path)
    assert engine.log_cursor == 0
    assert [r for r in caplog.records if r.levelno >= logging.WARNING and "runtime_investigations" in r.getMessage()], \
        "cursor voltou a 0 sem nenhum registro"
    snap = engine.snapshot()
    assert str(snap.get("state_load_error") or "").startswith("JSONDecodeError"), snap.get("state_load_error")
    assert snap.get("cursor_origin") == "reset_after_load_error"


def test_investigation_state_origin_is_explicit_when_valid_or_absent(tmp_path):
    fresh = _engine(tmp_path)
    assert fresh.snapshot()["cursor_origin"] == "fresh" and fresh.snapshot()["state_load_error"] is None
    fresh.observe_logs(FakeFeed([_row(7, "x", "error")]))
    reopened = _engine(tmp_path)
    assert reopened.log_cursor == 7 and reopened.snapshot()["cursor_origin"] == "persisted"


# --- QHOS-AUD-292 / QHOS-AUD-305 ---------------------------------------------

def _journal_line(cursor, message, priority="6", stamp="1700000000000000"):
    return json.dumps({"__CURSOR": cursor, "MESSAGE": message, "PRIORITY": priority,
                       "__REALTIME_TIMESTAMP": stamp, "_SYSTEMD_USER_UNIT": "qhos2-coletor.service", "_PID": "7"}) + "\n"


class FakeJournal:
    """journalctl falso: stdout iteravel; opcionalmente ignora terminate() (so morre com kill())."""

    def __init__(self, lines, *, ignore_terminate=False, exits_after_stream=True, on_next=None):
        self.pid = 4242
        self._lines = list(lines)
        self.ignore_terminate = ignore_terminate
        self.exits_after_stream = exits_after_stream
        self.on_next = on_next
        self.returncode = None
        self.terminated = 0
        self.killed = 0
        self.stdout = self._stream()

    def _stream(self):
        for line in self._lines:
            yield line
        if self.on_next:
            self.on_next(self)
        if self.exits_after_stream:
            self.returncode = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        if not self.ignore_terminate:
            self.returncode = -15

    def kill(self):
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("journalctl", timeout)
        return self.returncode


class _QuickEvent(threading.Event):
    """stopped.wait(3) sem esperar 3 s de verdade."""

    def wait(self, timeout=None):
        return super().wait(0.01)


def _feed_with_fake_journal(tmp_path, monkeypatch, popen):
    from brain_service import log_feed
    monkeypatch.setattr(log_feed, "subprocess", types.SimpleNamespace(
        Popen=popen, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT, TimeoutExpired=subprocess.TimeoutExpired))
    feed = log_feed.RuntimeLogFeed(tmp_path)
    feed.stopped = _QuickEvent()
    return feed


def test_poisoned_journal_record_does_not_stall_the_feed(tmp_path, monkeypatch):
    lines = {"c1": _journal_line("c1", "ok antes"), "c2": _journal_line("c2", "envenenado", priority="emergência", stamp="x"),
             "c3": _journal_line("c3", "ok depois")}
    order = ["c1", "c2", "c3"]
    spawns = []
    holder = {}

    class Popen:
        def __init__(self, command, **_kwargs):
            spawns.append(list(command))
            after = next((arg.split("=", 1)[1] for arg in command if arg.startswith("--after-cursor=")), None)
            remaining = order[order.index(after) + 1:] if after in order else order
            if after == "c3" or len(spawns) >= 4:
                holder["feed"].stopped.set()
                remaining = []
            self.proc = FakeJournal([lines[c] for c in remaining])
            self.stdout, self.poll, self.terminate, self.kill, self.wait = (
                self.proc.stdout, self.proc.poll, self.proc.terminate, self.proc.kill, self.proc.wait)

    feed = _feed_with_fake_journal(tmp_path, monkeypatch, Popen)
    holder["feed"] = feed
    try:
        feed._run()
        messages = [row["message"] for row in feed.page(0, 10)["records"]]
        status = feed.status()
    finally:
        feed.close()
    assert "ok depois" in messages, f"o registro depois do envenenado nunca chegou; spawns={len(spawns)}"
    assert len(spawns) <= 2, "o journalctl foi reiniciado no mesmo cursor repetidamente"
    assert status["poisoned_records"] == 1 and "c2" in status["last_poisoned_cursor"]
    assert "ValueError" in status["last_poisoned_error"]


def test_journal_record_with_list_valued_fields_is_stored_not_poisoning(tmp_path, monkeypatch):
    """journalctl --output=json emite arrays em campos multivalorados; a lista chegava ao bind do sqlite
    (ProgrammingError), caia no except amplo e reiniciava o journalctl no mesmo cursor para sempre."""
    multi = json.dumps({"__CURSOR": "c1", "MESSAGE": "unidade dupla", "PRIORITY": "4", "__REALTIME_TIMESTAMP": "1700000000000000",
                        "_SYSTEMD_USER_UNIT": ["qhos2-coletor.service", "qhos2-brain.service"], "_PID": ["7", "8"]}) + "\n"
    spawns = []
    holder = {}

    def popen(command, **_kwargs):
        spawns.append(list(command))
        after = next((arg.split("=", 1)[1] for arg in command if arg.startswith("--after-cursor=")), None)
        if after == "c1" or len(spawns) >= 3:
            holder["feed"].stopped.set()
            return FakeJournal([])
        return FakeJournal([multi])

    feed = _feed_with_fake_journal(tmp_path, monkeypatch, popen)
    holder["feed"] = feed
    try:
        feed._run()
        records = feed.page(0, 10)["records"]
        status = feed.status()
    finally:
        feed.close()
    assert len(spawns) <= 2, f"journalctl reiniciado no mesmo cursor: {len(spawns)} spawns, error={status['error']}"
    assert records and records[0]["message"] == "unidade dupla" and records[0]["level"] == "warning"
    assert records[0]["unit"] == "qhos2-coletor.service,qhos2-brain.service" and records[0]["pid"] == "7,8"
    assert status["poisoned_records"] == 0


def test_journal_record_that_sqlite_cannot_bind_is_counted_as_poisoned(tmp_path, monkeypatch):
    """Backstop: um bind impossivel (sqlite3.InterfaceError/ProgrammingError) e envenenado, nao respawn eterno."""
    from brain_service import log_feed
    odd = json.dumps({"__CURSOR": "c1", "MESSAGE": "x", "PRIORITY": "6", "__REALTIME_TIMESTAMP": "1700000000000000"}) + "\n"
    spawns = []
    holder = {}

    def popen(command, **_kwargs):
        spawns.append(list(command))
        after = next((arg.split("=", 1)[1] for arg in command if arg.startswith("--after-cursor=")), None)
        if after == "c1" or len(spawns) >= 3:
            holder["feed"].stopped.set()
            return FakeJournal([])
        return FakeJournal([odd])

    feed = _feed_with_fake_journal(tmp_path, monkeypatch, popen)
    holder["feed"] = feed
    original = feed.append

    def unbindable(row, journal=True):
        import sqlite3
        raise sqlite3.InterfaceError("Error binding parameter 4: type 'object' is not supported")

    monkeypatch.setattr(feed, "append", unbindable)
    try:
        feed._run()
        status = feed.status()
    finally:
        feed.close()
    assert len(spawns) <= 2 and status["poisoned_records"] == 1 and "InterfaceError" in status["last_poisoned_error"]


def test_end_reader_counts_kill_failures(tmp_path, monkeypatch):
    from brain_service import log_feed

    class Stubborn(FakeJournal):
        def kill(self):
            raise OSError(1, "Operation not permitted")

    fake = Stubborn([], ignore_terminate=True, exits_after_stream=False)
    feed = _feed_with_fake_journal(tmp_path, monkeypatch, lambda *a, **k: fake)
    try:
        feed._end_reader(fake)
        status = feed.status()
    finally:
        feed.close()
    assert status["reader_end_failures"] == 1 and "PermissionError" in str(feed.error)


def test_journal_reader_escalates_to_kill_and_survives_wait_timeout(tmp_path, monkeypatch):
    holder = {}

    def stop_feed(_proc):
        holder["feed"].stopped.set()  # close() chegando enquanto o stdout ainda esta aberto

    fake = FakeJournal([_journal_line("c1", "ok")], ignore_terminate=True, exits_after_stream=False, on_next=stop_feed)
    feed = _feed_with_fake_journal(tmp_path, monkeypatch, lambda *a, **k: fake)
    holder["feed"] = feed
    try:
        try:
            feed._run()  # antes: TimeoutExpired escapava do finally e matava a thread do leitor
        except subprocess.TimeoutExpired:
            pytest.fail("TimeoutExpired escapou de _run: thread do journal morreria sem kill")
        stored = feed.page(0, 10)["records"]
    finally:
        feed.close()
    assert fake.terminated >= 1 and fake.killed == 1 and fake.returncode == -9
    assert feed.connected is False
    assert stored[0]["message"] == "ok"


def test_close_racing_with_spawn_terminates_the_new_reader(tmp_path, monkeypatch):
    holder = {}
    blocked = {"timed_out": False}

    class BlockingJournal(FakeJournal):
        """stdout que bloqueia (journalctl --follow sem linhas novas) ate terminate/kill."""

        def __init__(self):
            super().__init__([], ignore_terminate=False, exits_after_stream=False)
            self.ended = threading.Event()
            self.stdout = self._blocking()

        def _blocking(self):
            if not self.ended.wait(2.0):
                blocked["timed_out"] = True
            return
            yield  # pragma: no cover

        def terminate(self):
            super().terminate()
            self.ended.set()

        def kill(self):
            super().kill()
            self.ended.set()

    def popen(*_a, **_k):
        holder["feed"].stopped.set()  # close() entre o teste de stopped e o Popen
        holder["proc"] = BlockingJournal()
        return holder["proc"]

    feed = _feed_with_fake_journal(tmp_path, monkeypatch, popen)
    holder["feed"] = feed
    try:
        feed._run()
    finally:
        feed.close()
    assert holder["proc"].terminated >= 1 or holder["proc"].killed >= 1
    assert blocked["timed_out"] is False, "o leitor ficou preso no stdout de um journalctl orfao"
