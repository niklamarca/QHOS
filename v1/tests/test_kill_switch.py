"""KillSwitch L1-L3: what each level does, that levels only go up until a reset, and that a failing broker cannot
leave the switch half-triggered. Ends with RiskEngine + KillSwitch wired together."""
import asyncio

import pytest

from core.config import config
from core.events import EventType
from risk.kill_switch import KillSwitch
from risk.risk_engine import RiskEngine


class RecordingBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)

    @property
    def types(self):
        return [e.type for e in self.events]


class RecordingExecution:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def reduce_exposure(self, target_pct):
        self.calls.append(("reduce_exposure", target_pct))
        if self.fail:
            raise RuntimeError("broker offline")

    async def flatten_all(self):
        self.calls.append(("flatten_all",))
        if self.fail:
            raise RuntimeError("broker offline")


class RecordingOrchestrator:
    def __init__(self):
        self.resets = 0

    def reset_kill_switch(self):
        self.resets += 1


@pytest.fixture
def bus():
    return RecordingBus()


@pytest.fixture
def execution():
    return RecordingExecution()


@pytest.fixture
def orchestrator():
    return RecordingOrchestrator()


@pytest.fixture
def ks(bus, execution, orchestrator):
    switch = KillSwitch()
    switch.bus = bus            # keep events out of the process-wide bus
    switch.inject_dependencies(execution, orchestrator)
    return switch


def run(coro):
    return asyncio.run(coro)


def test_starts_inactive(ks):
    assert ks.level == 0
    assert ks.is_active is False
    assert ks.status["description"].startswith("Normal")


def test_level1_stops_new_risk_but_does_not_touch_positions(ks, bus, execution):
    run(ks.trigger_level1("operator"))
    assert ks.level == 1
    assert bus.types == [EventType.KILL_SWITCH_L1]
    assert execution.calls == []


def test_level2_halves_exposure(ks, bus, execution):
    run(ks.trigger_level2("risk_engine"))
    assert ks.level == 2
    assert bus.types == [EventType.KILL_SWITCH_L2]
    assert execution.calls == [("reduce_exposure", 0.5)]


def test_level3_flattens_everything(ks, bus, execution):
    run(ks.trigger_level3("risk_engine"))
    assert ks.level == 3
    assert ks.status["description"] == "L3 — FLATTEN TOTAL"
    assert bus.types == [EventType.KILL_SWITCH_L3]
    assert execution.calls == [("flatten_all",)]


@pytest.mark.parametrize("first, then", [
    ("trigger_level3", "trigger_level1"),
    ("trigger_level3", "trigger_level2"),
    ("trigger_level2", "trigger_level1"),
])
def test_a_lower_level_never_downgrades_an_active_higher_level(ks, bus, first, then):
    run(getattr(ks, first)("risk_engine"))
    level_before, events_before = ks.level, len(bus.events)
    run(getattr(ks, then)("operator"))
    assert ks.level == level_before
    assert len(bus.events) == events_before


@pytest.mark.parametrize("trigger", ["trigger_level1", "trigger_level2", "trigger_level3"])
def test_repeating_the_same_level_is_idempotent(ks, bus, execution, trigger):
    run(getattr(ks, trigger)("risk_engine"))
    run(getattr(ks, trigger)("risk_engine"))
    assert len(bus.events) == 1
    assert len(execution.calls) <= 1


def test_escalation_from_l1_to_l3_runs_each_level_once(ks, bus, execution):
    run(ks.trigger_level1("a"))
    run(ks.trigger_level2("b"))
    run(ks.trigger_level3("c"))
    assert bus.types == [EventType.KILL_SWITCH_L1, EventType.KILL_SWITCH_L2, EventType.KILL_SWITCH_L3]
    assert execution.calls == [("reduce_exposure", 0.5), ("flatten_all",)]


@pytest.mark.parametrize("trigger, level", [("trigger_level2", 2), ("trigger_level3", 3)])
def test_broker_failure_does_not_raise_and_the_level_still_holds(bus, orchestrator, trigger, level):
    switch = KillSwitch()
    switch.bus = bus
    switch.inject_dependencies(RecordingExecution(fail=True), orchestrator)
    run(getattr(switch, trigger)("risk_engine"))      # must not raise
    assert switch.level == level


def test_level3_without_an_execution_engine_still_sets_the_level(bus):
    switch = KillSwitch()
    switch.bus = bus
    run(switch.trigger_level3("risk_engine"))
    assert switch.level == 3


def test_reset_returns_to_normal_and_tells_the_orchestrator(ks, orchestrator):
    run(ks.trigger_level3("risk_engine"))
    run(ks.reset("operator"))
    assert ks.level == 0
    assert ks.is_active is False
    assert orchestrator.resets == 1
    assert ks.status["trigger_history"][-1]["action"] == "reset"


def test_after_reset_the_switch_can_fire_again(ks, execution):
    run(ks.trigger_level3("risk_engine"))
    run(ks.reset("operator"))
    run(ks.trigger_level3("risk_engine"))
    assert execution.calls == [("flatten_all",), ("flatten_all",)]


def test_status_keeps_only_the_last_ten_history_entries(ks):
    for _ in range(8):
        run(ks.trigger_level1("a"))
        run(ks.reset("b"))
    history = ks.status["trigger_history"]
    assert len(history) == 10
    assert history[-1]["action"] == "reset"


def test_risk_engine_and_kill_switch_together_flatten_and_block_new_orders(ks, execution, monkeypatch):
    """A 20% drawdown with a 10% limit is severity 3: the portfolio is flattened and the pre-trade gate closes."""
    monkeypatch.setattr(config, "max_portfolio_drawdown", 0.10)
    risk = RiskEngine(storage=None)
    risk.inject_kill_switch(ks)
    risk.update_nav(100_000.0)
    risk.update_nav(80_000.0)
    risk.reset_daily()

    run(risk.check_all())

    assert ks.level == 3
    assert execution.calls == [("flatten_all",)]
    assert risk.pre_trade_check("ES", 0.05, direction=1) == (False, "kill_switch_L3_active")
