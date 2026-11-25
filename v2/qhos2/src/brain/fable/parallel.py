"""Deterministic parallelism. The LLM does not define concurrency safety."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Mapping, Sequence


class ParallelState(str, Enum):
    CAN_PARALLELIZE = "CAN_PARALLELIZE"
    DEPENDENCY_WAIT = "DEPENDENCY_WAIT"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
    SERIAL_REQUIRED = "SERIAL_REQUIRED"


WRITE_ACTIONS = frozenset({
    "apply", "write", "patch", "shell", "push", "merge", "deploy", "edit",
})
READ_ACTIONS = frozenset({
    "inspect_file", "inspect_schema", "inspect_parser", "correlate_diff",
    "reproduce_failure", "read_log", "read_commit", "read_metric",
})


def classify_task(task: Mapping[str, Any], *, occupied: Sequence[str] = ()) -> str:
    action = str(task.get("action_type") or task.get("action") or "").lower()
    target = str(task.get("target") or "").lower()
    depends = [str(x).lower() for x in (task.get("depends_on") or [])]
    if action in WRITE_ACTIONS:
        return ParallelState.SERIAL_REQUIRED.value
    if depends:
        return ParallelState.DEPENDENCY_WAIT.value
    if target and target in {str(x).lower() for x in occupied}:
        return ParallelState.RESOURCE_CONFLICT.value
    if action in READ_ACTIONS or action.startswith("inspect") or action.startswith("read"):
        return ParallelState.CAN_PARALLELIZE.value
    return ParallelState.SERIAL_REQUIRED.value


def plan_parallel(tasks: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    occupied: List[str] = []
    rows = []
    for task in tasks:
        state = classify_task(task, occupied=occupied)
        target = str(task.get("target") or "")
        rows.append({**dict(task), "parallel_state": state})
        if target and state != ParallelState.CAN_PARALLELIZE.value:
            occupied.append(target.lower())
        elif target:
            occupied.append(target.lower())
    can = [r for r in rows if r["parallel_state"] == ParallelState.CAN_PARALLELIZE.value]
    return {
        "tasks": rows,
        "parallel_ready": can,
        "serial": [r for r in rows if r["parallel_state"] != ParallelState.CAN_PARALLELIZE.value],
        "safe": all(r["parallel_state"] != "undefined" for r in rows),
    }
