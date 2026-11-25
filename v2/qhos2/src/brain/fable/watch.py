"""Watcher lifecycle. Observation only. Failure cannot kill Trader."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class WatcherState(str, Enum):
    CREATED = "CREATED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    STOPPED = "STOPPED"


class Watcher:
    def __init__(self) -> None:
        self.state = WatcherState.CREATED
        self.authority = False
        self.prefer = "event_bus"

    def activate(self) -> None:
        self.state = WatcherState.ACTIVE

    def fail(self, *_a: Any, **_k: Any) -> None:
        self.state = WatcherState.FAILED

    def stop(self) -> None:
        self.state = WatcherState.STOPPED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "authority": False,
            "prefer": self.prefer,
            "polling": False,
            "can_kill_trader": False,
        }


def default_event_preference() -> Dict[str, Any]:
    return {
        "prefer": ["EventBus", "stream", "push"],
        "avoid": ["aggressive_polling"],
        "kind": "in-process",
        "authority": False,
    }
