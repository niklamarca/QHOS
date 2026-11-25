"""Brain event kinds. Published onto the standalone process EventBus.

The local bus does not import or subscribe to Trader's Python process.
A single EventType.BRAIN_EVENT carries a `kind` in the payload so the enum
does not explode as the model grows.

Limitation: the default publisher is in-process. A future backend can
implement EventPublisher without changing callers.
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Dict, Optional

from brain.local_events import Event, EventType, event_bus
from brain.local_logger import get_logger

from .models import new_id, utc_ts
from .redact import sanitize_error

log = get_logger("Brain.Events")

# Visible accounting for a publisher that must not raise into its caller
# (QHOS-AUD-290): how many events were scheduled, dropped for lack of a running
# loop (synchronous callers), or failed to schedule.
publish_stats: Dict[str, int] = {"scheduled": 0, "not_scheduled_no_loop": 0, "failed": 0}


class BrainEventKind(str, Enum):
    CYCLE_STARTED = "CYCLE_STARTED"
    CYCLE_FINISHED = "CYCLE_FINISHED"
    ERROR_DETECTED = "ERROR_DETECTED"
    ANOMALY_DETECTED = "ANOMALY_DETECTED"
    SERVICE_DEGRADED = "SERVICE_DEGRADED"
    INCIDENT_CREATED = "INCIDENT_CREATED"
    GOAL_CREATED = "GOAL_CREATED"
    PLAN_CREATED = "PLAN_CREATED"
    TASK_CREATED = "TASK_CREATED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    LEARNING_CREATED = "LEARNING_CREATED"
    SHADOW_DECISION = "SHADOW_DECISION"
    SANDBOX_CREATED = "SANDBOX_CREATED"
    BASELINE_CAPTURED = "BASELINE_CAPTURED"
    WORKTREE_CREATED = "WORKTREE_CREATED"
    CHANGE_APPLIED = "CHANGE_APPLIED"
    VALIDATION_STARTED = "VALIDATION_STARTED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    CHANGE_PROPOSAL_CREATED = "CHANGE_PROPOSAL_CREATED"
    CHANGE_READY_FOR_REVIEW = "CHANGE_READY_FOR_REVIEW"
    CHANGE_REJECTED = "CHANGE_REJECTED"
    SANDBOX_CLEANED = "SANDBOX_CLEANED"


def publish_brain_event(
    kind: BrainEventKind | str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    source: str = "brain",
    correlation_id: str = "",
    event_id: str = "",
) -> Event:
    """Publish onto the process EventBus. Never raises into the caller."""
    kind_value = kind.value if isinstance(kind, BrainEventKind) else str(kind)
    body = dict(payload or {})
    body["kind"] = kind_value
    body["correlation_id"] = correlation_id or body.get("correlation_id") or new_id("cor")
    event = Event(
        type=EventType.BRAIN_EVENT,
        source=source,
        payload=body,
        timestamp=utc_ts(),
        event_id=event_id or new_id("bev"),
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Synchronous caller outside the service loop: the event is returned but not delivered.
        publish_stats["not_scheduled_no_loop"] += 1
        return event
    delivery = event_bus.publish(event)
    try:
        loop.create_task(delivery)
        publish_stats["scheduled"] += 1
    except Exception as exc:
        delivery.close()
        publish_stats["failed"] += 1
        log.warning("brain event %s not scheduled: %s: %s", kind_value, type(exc).__name__, sanitize_error(exc))
    return event


class EventPublisher:
    """Replaceable sink. Today: in-process EventBus. Not Redis/Kafka."""

    limitation = "abstract"

    def emit(
        self,
        kind: BrainEventKind | str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "brain",
        correlation_id: str = "",
        event_id: str = "",
    ) -> Event:
        raise NotImplementedError


class InProcessBusPublisher(EventPublisher):
    """Brain process EventBus. Cannot cross process boundaries."""

    limitation = "in-process; external Trader evidence arrives through the observer"

    def emit(
        self,
        kind: BrainEventKind | str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        source: str = "brain",
        correlation_id: str = "",
        event_id: str = "",
    ) -> Event:
        return publish_brain_event(
            kind,
            payload=payload,
            source=source,
            correlation_id=correlation_id,
            event_id=event_id,
        )


DEFAULT_PUBLISHER: EventPublisher = InProcessBusPublisher()
