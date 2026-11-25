"""Goals, tasks and a deterministic DAG orchestrator.

No LLM. No trading. Apply/write tasks are refused by policy. This module only
coordinates Brain investigations; it never drives the observed qhos2 process
(the V1 HPC orchestrator is not part of V2).
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from .events import BrainEventKind, publish_brain_event
from .models import (
    Goal,
    Task,
    TaskKind,
    TaskStatus,
    TERMINAL_TASK,
    new_id,
    utc_ts,
)
from .policies import Budget, LoopGuard, PolicyDecision, ResourceLocks, route_agent


def fingerprint(goal_id: str, title: str, action: str) -> str:
    raw = f"{goal_id}|{title.strip().lower()}|{action.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class TaskOrchestrator:
    def __init__(self, budget: Optional[Budget] = None) -> None:
        self.budget = budget or Budget()
        self.goals: Dict[str, Goal] = {}
        self.tasks: Dict[str, Task] = {}
        self.locks = ResourceLocks()
        self.guard = LoopGuard(self.budget)
        self.metrics = {
            "goals_created": 0,
            "tasks_created": 0,
            "tasks_completed": 0,
            "tasks_failed": 0,
            "duplicates_suppressed": 0,
            "conflicts": 0,
            "approvals_pending": 0,
        }

    def load(self, blob: Dict[str, Any]) -> None:
        for row in blob.get("goals") or []:
            if isinstance(row, dict):
                goal = Goal.from_dict(row)
                self.goals[goal.goal_id] = goal
        for row in blob.get("tasks") or []:
            if isinstance(row, dict):
                task = Task.from_dict(row)
                self.tasks[task.task_id] = task
                if task.fingerprint:
                    self.guard.remember(task.fingerprint)

    def dump(self) -> Dict[str, Any]:
        return {
            "goals": [g.to_dict() for g in self.goals.values()],
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "metrics": dict(self.metrics),
        }

    def create_goal(
        self,
        title: str,
        reason: str,
        *,
        incident_id: str = "",
        correlation_id: str = "",
        priority: int = 50,
        risk_level: str = "low",
        origin: str = "system",
    ) -> Goal:
        for existing in self.goals.values():
            if existing.status == "open" and existing.title == title and existing.incident_id == incident_id:
                return existing
        goal = Goal(
            goal_id=new_id("goal"),
            title=title,
            reason=reason,
            priority=priority,
            risk_level=risk_level,
            incident_id=incident_id,
            origin=origin,
            correlation_id=correlation_id or new_id("cor"),
        )
        self.goals[goal.goal_id] = goal
        self.metrics["goals_created"] += 1
        publish_brain_event(
            BrainEventKind.GOAL_CREATED,
            payload={"goal_id": goal.goal_id, "title": title},
            correlation_id=goal.correlation_id,
        )
        return goal

    def create_task(
        self,
        goal_id: str,
        title: str,
        *,
        action: str = "observe",
        description: str = "",
        dependencies: Optional[List[str]] = None,
        parent_task: str = "",
        assigned_agent: str = "",
        capabilities: Optional[set] = None,
        resource: str = "",
        approval_required: bool = False,
        decision: Optional[PolicyDecision] = None,
        kind: str = "",
    ) -> Optional[Task]:
        goal = self.goals.get(goal_id)
        if goal is None:
            return None
        fp = fingerprint(goal_id, title, action)
        if self.guard.seen(fp):
            self.metrics["duplicates_suppressed"] += 1
            for existing in self.tasks.values():
                if existing.fingerprint == fp:
                    return existing
            return None
        parent = self.tasks.get(parent_task) if parent_task else None
        depth = (parent.depth + 1) if parent else 0
        blocked = self.guard.can_spawn(parent_task=parent_task, goal_id=goal_id, depth=depth)
        if blocked:
            return None
        running = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
        if running >= self.budget.max_concurrent_tasks and action not in {"observe", "health"}:
            pass  # still enqueue; scheduler respects the cap
        agent = assigned_agent or route_agent(capabilities or set())
        needs_approval = bool(approval_required or (decision and decision.approval_required))
        task = Task(
            task_id=new_id("task"),
            title=title,
            description=description,
            parent_goal=goal_id,
            parent_task=parent_task,
            status=TaskStatus.WAITING_APPROVAL if needs_approval else TaskStatus.PLANNED,
            priority=goal.priority,
            risk_level=goal.risk_level,
            assigned_agent=agent,
            action=action,
            dependencies=list(dependencies or []),
            fingerprint=fp,
            max_retries=self.budget.max_retries,
            approval_required=needs_approval,
            resource=resource,
            depth=depth,
            correlation_id=goal.correlation_id,
            kind=kind or TaskKind.ANALYSIS.value,
        )
        self.tasks[task.task_id] = task
        self.guard.remember(fp)
        self.guard.spawned(parent_task=parent_task, goal_id=goal_id)
        self.metrics["tasks_created"] += 1
        if needs_approval:
            self.metrics["approvals_pending"] += 1
            publish_brain_event(
                BrainEventKind.APPROVAL_REQUIRED,
                payload={"task_id": task.task_id, "action": action},
                correlation_id=task.correlation_id,
            )
        publish_brain_event(
            BrainEventKind.TASK_CREATED,
            payload={"task_id": task.task_id, "goal_id": goal_id, "action": action},
            correlation_id=task.correlation_id,
        )
        return task

    def _deps_done(self, task: Task) -> bool:
        for dep_id in task.dependencies:
            dep = self.tasks.get(dep_id)
            if dep is None or dep.status != TaskStatus.COMPLETED:
                return False
        return True

    def refresh_readiness(self) -> None:
        for task in self.tasks.values():
            if task.status in TERMINAL_TASK:
                continue
            if task.status == TaskStatus.WAITING_APPROVAL:
                continue
            if task.status == TaskStatus.RUNNING:
                continue
            if not self._deps_done(task):
                task.status = TaskStatus.BLOCKED
                continue
            if task.resource:
                holder = self.locks.holder(task.resource)
                if holder and holder != task.task_id:
                    task.status = TaskStatus.BLOCKED
                    self.metrics["conflicts"] += 1
                    continue
            task.status = TaskStatus.READY

    def schedule(self, *, now: Optional[float] = None) -> List[Task]:
        """Move READY tasks to RUNNING up to the concurrency cap. Deterministic."""
        self.refresh_readiness()
        running = [t for t in self.tasks.values() if t.status == TaskStatus.RUNNING]
        capacity = max(0, self.budget.max_concurrent_tasks - len(running))
        ready = sorted(
            (t for t in self.tasks.values() if t.status == TaskStatus.READY),
            key=lambda t: (t.priority, t.created_at, t.task_id),
        )
        started: List[Task] = []
        ts = now if now is not None else utc_ts()
        for task in ready[:capacity]:
            if task.resource and not self.locks.acquire(task.resource, task.task_id):
                task.status = TaskStatus.BLOCKED
                self.metrics["conflicts"] += 1
                continue
            task.status = TaskStatus.RUNNING
            task.started_at = ts
            started.append(task)
            publish_brain_event(
                BrainEventKind.TASK_STARTED,
                payload={"task_id": task.task_id, "agent": task.assigned_agent},
                correlation_id=task.correlation_id,
            )
        return started

    def complete(self, task_id: str, output: Optional[Dict[str, Any]] = None) -> Optional[Task]:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        task.status = TaskStatus.COMPLETED
        task.finished_at = utc_ts()
        if output:
            task.output = dict(output)
        self.locks.release(task.resource, task.task_id)
        self.metrics["tasks_completed"] += 1
        publish_brain_event(
            BrainEventKind.TASK_COMPLETED,
            payload={"task_id": task_id},
            correlation_id=task.correlation_id,
        )
        self._maybe_close_goal(task.parent_goal)
        return task

    def fail(self, task_id: str, error: str) -> Optional[Task]:
        task = self.tasks.get(task_id)
        if task is None:
            return None
        task.retry_count += 1
        task.output = {"error": error[:400]}
        self.locks.release(task.resource, task.task_id)
        if task.retry_count <= task.max_retries:
            task.status = TaskStatus.QUEUED
            task.started_at = None
            return task
        task.status = TaskStatus.FAILED
        task.finished_at = utc_ts()
        self.metrics["tasks_failed"] += 1
        publish_brain_event(
            BrainEventKind.TASK_FAILED,
            payload={"task_id": task_id, "error": error[:200]},
            correlation_id=task.correlation_id,
        )
        return task

    def approve(self, task_id: str) -> bool:
        task = self.tasks.get(task_id)
        if task is None or not task.approval_required:
            return False
        task.approved = True
        task.status = TaskStatus.PLANNED
        self.metrics["approvals_pending"] = max(0, self.metrics["approvals_pending"] - 1)
        return True

    def _maybe_close_goal(self, goal_id: str) -> None:
        goal = self.goals.get(goal_id)
        if goal is None:
            return
        related = [t for t in self.tasks.values() if t.parent_goal == goal_id]
        if related and all(t.status in TERMINAL_TASK for t in related):
            goal.status = "done"
            goal.updated_at = utc_ts()


class BrainOrchestrator(TaskOrchestrator):
    """Goal/task DAG. Distinct from HPCOrchestrator (CPU/GPU job kernel)."""

    name = "BrainOrchestrator"

    def request_sandbox(self, task_id: str, executor: Any, request: Any) -> Dict[str, Any]:
        """Explicit entry. Analysis/research/validation tasks cannot open a worktree."""
        task = self.tasks.get(task_id)
        if task is None:
            return {"ok": False, "reason": "unknown_task", "executed": False}
        if str(task.kind) != TaskKind.CODE_CHANGE.value:
            return {"ok": False, "reason": "task_not_code_change", "kind": task.kind, "executed": False}
        if getattr(request, "task_id", "") and request.task_id != task_id:
            return {"ok": False, "reason": "request_task_mismatch", "executed": False}
        if not hasattr(executor, "run_pipeline"):
            return {"ok": False, "reason": "executor_missing", "executed": False}
        return executor.run_pipeline(request)
