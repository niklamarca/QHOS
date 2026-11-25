"""Process-local events. No connection to Trader's Python EventBus is implied.

External observers can translate HTTP/log evidence into these events. Awaiting
publish delivers an event even without a separate bus worker in the service.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class EventType(str, Enum):
    DATA_UPDATED = "DATA_UPDATED"
    FEATURES_COMPUTED = "FEATURES_COMPUTED"
    REGIME_CHANGED = "REGIME_CHANGED"
    REGIME_PREDICTED = "REGIME_PREDICTED"
    STRATEGY_GENERATED = "STRATEGY_GENERATED"
    GENERATION_COMPLETE = "GENERATION_COMPLETE"
    BACKTEST_STARTED = "BACKTEST_STARTED"
    BACKTEST_COMPLETE = "BACKTEST_COMPLETE"
    BACKTEST_FAILED = "BACKTEST_FAILED"
    STRATEGY_VALIDATED = "STRATEGY_VALIDATED"
    STRATEGY_REJECTED = "STRATEGY_REJECTED"
    RISK_ALERT = "RISK_ALERT"
    RISK_LIMIT_BREACH = "RISK_LIMIT_BREACH"
    KILL_SWITCH_L1 = "KILL_SWITCH_L1"
    KILL_SWITCH_L2 = "KILL_SWITCH_L2"
    KILL_SWITCH_L3 = "KILL_SWITCH_L3"
    PORTFOLIO_UPDATED = "PORTFOLIO_UPDATED"
    WEIGHTS_REBALANCED = "WEIGHTS_REBALANCED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    TRADE_CLOSED = "TRADE_CLOSED"
    SYSTEM_STARTED = "SYSTEM_STARTED"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"
    MODULE_ERROR = "MODULE_ERROR"
    JOB_ENQUEUED = "JOB_ENQUEUED"
    JOB_COMPLETE = "JOB_COMPLETE"
    JOB_FAILED = "JOB_FAILED"
    METRICS_UPDATE = "METRICS_UPDATE"
    BRAIN_EVENT = "BRAIN_EVENT"


@dataclass
class Event:
    type: EventType
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: str(time.time_ns()))

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)


Handler = Callable[[Event], Any]

log = logging.getLogger("Brain.EventBus")


def _sanitize(exc: BaseException) -> str:
    from brain.redact import sanitize_error  # lazy: this module stays free of package imports at load
    return sanitize_error(exc)


def _handler_name(handler: Any) -> str:
    return str(getattr(handler, "__qualname__", None) or getattr(handler, "__name__", None) or repr(handler))[:120]


class EventBus:
    def __init__(self, maxsize: int = 10_000) -> None:
        self._subscribers: Dict[EventType, List[Handler]] = {}
        self._wildcard: List[Handler] = []
        self._history: deque[Event] = deque(maxlen=min(max(1, maxsize), 5000))
        # A handler that raises no longer disappears into `continue` (QHOS-AUD-290).
        self.handler_failures = 0

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        handlers = self._subscribers.setdefault(event_type, [])
        if handler not in handlers:
            handlers.append(handler)

    def subscribe_all(self, handler: Handler) -> None:
        if handler not in self._wildcard:
            self._wildcard.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler) -> None:
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        self._history.append(event)
        pending = []
        names = []
        for handler in [*self._subscribers.get(event.type, []), *self._wildcard]:
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    pending.append(result)
                    names.append(_handler_name(handler))
            except Exception as exc:
                self.handler_failures += 1
                log.warning("event handler %s failed on %s: %s: %s", _handler_name(handler),
                            event.type.value, type(exc).__name__, _sanitize(exc))
        if pending:
            results = await asyncio.gather(*pending, return_exceptions=True)
            for name, result in zip(names, results):
                if isinstance(result, BaseException):
                    self.handler_failures += 1
                    log.warning("async event handler %s failed on %s: %s: %s", name,
                                event.type.value, type(result).__name__, _sanitize(result))

    def publish_sync(self, event: Event) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self.publish(event))
        else:
            loop.create_task(self.publish(event))

    async def start(self) -> None:
        """Compatibility hook; this bus dispatches directly on publication."""

    async def stop(self) -> None:
        """Compatibility hook; no background worker needs to be stopped."""

    def get_history(self, event_type: Optional[EventType] = None, limit: int = 100) -> List[Event]:
        if limit <= 0:
            return []
        return [e for e in self._history if event_type is None or e.type == event_type][-limit:]

    @property
    def queue_size(self) -> int:
        return 0


event_bus = EventBus()
