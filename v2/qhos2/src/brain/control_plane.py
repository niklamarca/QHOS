"""Laços control-plane payload. Visualization only — no authority.

This module builds an overlay + inspectable DAG with hard limits so the
frontend (brain_service/ui) never has to render thousands of nodes. There is
no prescribed V1 map (dashboard/_hybrid.html) in V2.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from . import LACOS_SCHEMA
from .cognitive import default_sides_view
from .evaluate import evaluation_view
from .models import TaskStatus, TERMINAL_TASK, utc_ts
from .policies import AGENT_CAPABILITIES

OVERLAY_NODE_IDS = ("brain", "brain_health", "brain_goals", "brain_memory")
MAX_OVERLAY_NODES = 8
MAX_DAG_NODES = 40
MAX_ACTIVITY = 20
MAX_INCIDENTS = 20
MAX_TASKS = 40
MAX_MEMORY = 12
STALE_AFTER_S = 180.0
FILTERS = (
    "all",
    "brain",
    "incidents",
    "goals",
    "tasks",
    "agents",
    "improvements",
    "completed",
    "active",
    "errors",
    "high_risk",
    "evaluation",
)
PANELS = {
    "health": True,
    "incidents": True,
    "goals": True,
    "tasks": True,
    "agents": True,
    "approvals": True,
    "memory": True,
    "activity": True,
    "improvements": True,
    "audit": True,
    "sandbox": True,
    "evaluation": True,
    "sides": True,
}


def heartbeat_state(
    *,
    running: bool,
    last_cycle_at: Optional[float],
    interval_s: float,
    health_tone: str,
    now: Optional[float] = None,
) -> str:
    """Brain liveness. Distinct from trader server_heartbeat.txt."""
    ts = now if now is not None else utc_ts()
    if not running:
        return "offline"
    if last_cycle_at is None:
        return "stale"
    age = ts - float(last_cycle_at)
    limit = max(float(interval_s) * 3.0, STALE_AFTER_S)
    if age > limit:
        return "stale"
    tone = str(health_tone or "").lower()
    if tone in {"critical", "degraded"}:
        return "degraded"
    return "running"


def detect_cycles(edges: Sequence[Mapping[str, str]]) -> List[str]:
    """Return one cycle path (ids) or empty list. DFS. Frontend-safe."""
    graph: Dict[str, List[str]] = {}
    for edge in edges:
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if src and tgt:
            graph.setdefault(src, []).append(tgt)
    visiting: Set[str] = set()
    seen: Set[str] = set()
    stack: List[str] = []

    def dfs(node: str) -> Optional[List[str]]:
        visiting.add(node)
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt in visiting:
                if nxt in stack:
                    return stack[stack.index(nxt):] + [nxt]
                return [node, nxt]
            if nxt not in seen:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        visiting.discard(node)
        seen.add(node)
        return None

    for node in list(graph):
        if node not in seen:
            cycle = dfs(node)
            if cycle:
                return cycle
    return []


def task_dag(
    tasks: Sequence[Mapping[str, Any]],
    *,
    goals: Sequence[Mapping[str, Any]] = (),
    incidents: Sequence[Mapping[str, Any]] = (),
    limit: int = MAX_DAG_NODES,
) -> Dict[str, Any]:
    """Compact DAG for the inspector. Invalid cycles do not crash the map."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    seen: Set[str] = set()

    def add_node(nid: str, kind: str, label: str, extra: Optional[Dict[str, Any]] = None) -> None:
        if nid in seen or len(nodes) >= limit:
            return
        seen.add(nid)
        row = {"id": nid, "kind": kind, "label": label}
        if extra:
            row.update(extra)
        nodes.append(row)

    for inc in list(incidents)[:8]:
        iid = str(inc.get("incident_id") or "")
        if not iid:
            continue
        add_node(iid, "incident", str(inc.get("title") or iid), {
            "status": inc.get("status"),
            "severity": inc.get("severity"),
        })
        gid = str(inc.get("goal_id") or "")
        if gid:
            edges.append({"source": iid, "target": gid, "verb": "spawned"})

    for goal in list(goals)[:12]:
        gid = str(goal.get("goal_id") or "")
        if not gid:
            continue
        add_node(gid, "goal", str(goal.get("title") or gid), {
            "status": goal.get("status"),
            "priority": goal.get("priority"),
            "origin": goal.get("origin"),
        })
        iid = str(goal.get("incident_id") or "")
        if iid:
            edges.append({"source": iid, "target": gid, "verb": "opened"})

    for task in list(tasks)[:limit]:
        tid = str(task.get("task_id") or "")
        if not tid:
            continue
        add_node(tid, "task", str(task.get("title") or tid), {
            "status": task.get("status"),
            "agent": task.get("assigned_agent"),
            "risk": task.get("risk_level"),
            "approval_required": bool(task.get("approval_required")),
            "retry_count": int(task.get("retry_count") or 0),
        })
        gid = str(task.get("parent_goal") or "")
        if gid:
            edges.append({"source": gid, "target": tid, "verb": "planned"})
        parent = str(task.get("parent_task") or "")
        if parent:
            edges.append({"source": parent, "target": tid, "verb": "parent"})
        for dep in task.get("dependencies") or []:
            if dep:
                edges.append({"source": str(dep), "target": tid, "verb": "blocks"})

    cycle = detect_cycles(edges)
    return {
        "ok": not cycle,
        "error": "cycle_detected" if cycle else "",
        "cycle": cycle,
        "nodes": nodes,
        "edges": edges,
        "truncated": len(tasks) > limit,
        "limit": limit,
    }


def live_agents(tasks: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Only agents that actually hold a task. No fictional roster."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        agent = str(task.get("assigned_agent") or "").strip()
        if not agent:
            continue
        row = by_id.setdefault(agent, {
            "id": agent,
            "name": agent,
            "capabilities": sorted(AGENT_CAPABILITIES.get(agent, set())),
            "status": "idle",
            "current_task": "",
            "assigned": 0,
            "completed": 0,
            "failed": 0,
        })
        row["assigned"] += 1
        status = str(task.get("status") or "")
        if status == TaskStatus.RUNNING.value:
            row["status"] = "busy"
            row["current_task"] = str(task.get("task_id") or "")
        elif status == TaskStatus.COMPLETED.value:
            row["completed"] += 1
        elif status == TaskStatus.FAILED.value:
            row["failed"] += 1
    return [by_id[k] for k in sorted(by_id)]


def approvals_from_tasks(tasks: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for task in tasks:
        if str(task.get("status") or "") != TaskStatus.WAITING_APPROVAL.value and not task.get("approval_required"):
            continue
        if str(task.get("status") or "") in {s.value for s in TERMINAL_TASK}:
            if not (task.get("approval_required") and not task.get("approved")):
                continue
        if str(task.get("status") or "") != TaskStatus.WAITING_APPROVAL.value:
            continue
        out.append({
            "approval_id": f"apr_{task.get('task_id')}",
            "action": task.get("action") or "unknown",
            "reason": task.get("title") or "",
            "risk": task.get("risk_level") or "low",
            "component": task.get("resource") or "",
            "task_id": task.get("task_id"),
            "status": "pending",
            "requested_by": "brain",
            "requested_at": task.get("created_at"),
            "evidence": [],
            "sandbox_result": "",
            "rollback_plan": "discard sandbox; no production revert",
            "correlation_id": task.get("correlation_id") or "",
            "applies_to_production": False,
            "applies_to_trading": False,
        })
    return out[:20]


def improvement_candidates(health: Mapping[str, Any], resources: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Opportunities that are not incidents. Never auto-applied."""
    out: List[Dict[str, Any]] = []
    ram = float(resources.get("ram_percent") or 0.0)
    cpu = float(resources.get("cpu_percent") or 0.0)
    if 70 <= ram < 90:
        out.append({
            "id": "imp_ram",
            "component": "runtime",
            "observation": f"RAM {ram:.0f}% of host",
            "hypothesis": "allocation pressure",
            "expected_improvement": "headroom before critical",
            "risk": "low",
            "status": "candidate",
            "auto_apply": False,
        })
    if cpu >= 80:
        out.append({
            "id": "imp_cpu",
            "component": "hpc_orchestrator",
            "observation": f"CPU {cpu:.0f}%",
            "hypothesis": "job queue saturation",
            "expected_improvement": "lower steal time",
            "risk": "low",
            "status": "candidate",
            "auto_apply": False,
        })
    warnings = health.get("warnings") if isinstance(health.get("warnings"), list) else []
    for item in warnings[:3]:
        if not isinstance(item, dict):
            continue
        comp = str(item.get("component") or "unknown")
        out.append({
            "id": f"imp_{comp}",
            "component": comp,
            "observation": str(item.get("kind") or "warning"),
            "hypothesis": "component degraded but not critical",
            "expected_improvement": "restore healthy heartbeat",
            "risk": "medium",
            "status": "candidate",
            "auto_apply": False,
        })
    return out[:6]


def activity_from_audit(audit: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in list(audit)[-MAX_ACTIVITY:]:
        if not isinstance(row, dict):
            continue
        items.append({
            "ts": row.get("ts") or row.get("requested_at"),
            "kind": row.get("kind") or row.get("event") or "EVENT",
            "subject": row.get("subject") or "",
            "correlation_id": row.get("correlation_id") or "",
            "audit_id": row.get("audit_id") or "",
        })
    return list(reversed(items))


def graph_relations(graph_blob: Mapping[str, Any]) -> List[Dict[str, str]]:
    rels = graph_blob.get("relations") if isinstance(graph_blob, dict) else None
    if not isinstance(rels, list):
        return []
    out = []
    for rel in rels[:40]:
        if isinstance(rel, dict) and rel.get("source") and rel.get("verb") and rel.get("target"):
            out.append({
                "source": str(rel["source"]),
                "verb": str(rel["verb"]),
                "target": str(rel["target"]),
            })
    return out


def audit_chains(snapshot: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Group incidents → goals → tasks → audit → memory by correlation_id."""
    buckets: Dict[str, Dict[str, Any]] = {}

    def bucket(cid: str) -> Dict[str, Any]:
        key = str(cid or "").strip() or "_none"
        row = buckets.setdefault(key, {
            "correlation_id": "" if key == "_none" else key,
            "incident_ids": [],
            "goal_ids": [],
            "task_ids": [],
            "audit_ids": [],
            "memory_ids": [],
            "steps": [],
        })
        return row

    def add_step(row: Dict[str, Any], kind: str, ident: str, title: str = "") -> None:
        if not ident:
            return
        row["steps"].append({"kind": kind, "id": ident, "title": title})

    for inc in snapshot.get("incidents") or []:
        if not isinstance(inc, dict):
            continue
        iid = str(inc.get("incident_id") or "")
        row = bucket(str(inc.get("correlation_id") or iid))
        if iid and iid not in row["incident_ids"]:
            row["incident_ids"].append(iid)
            add_step(row, "incident", iid, str(inc.get("title") or ""))
    for goal in snapshot.get("goals") or []:
        if not isinstance(goal, dict):
            continue
        gid = str(goal.get("goal_id") or "")
        row = bucket(str(goal.get("correlation_id") or goal.get("incident_id") or gid))
        if gid and gid not in row["goal_ids"]:
            row["goal_ids"].append(gid)
            add_step(row, "goal", gid, str(goal.get("title") or ""))
    for task in snapshot.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("task_id") or "")
        row = bucket(str(task.get("correlation_id") or task.get("parent_goal") or tid))
        if tid and tid not in row["task_ids"]:
            row["task_ids"].append(tid)
            add_step(row, "task", tid, str(task.get("title") or ""))
    for aud in snapshot.get("audit_tail") or []:
        if not isinstance(aud, dict):
            continue
        aid = str(aud.get("audit_id") or "")
        row = bucket(str(aud.get("correlation_id") or ""))
        if aid and aid not in row["audit_ids"]:
            row["audit_ids"].append(aid)
            add_step(row, "audit", aid, str(aud.get("kind") or ""))
    for mem in snapshot.get("memory_tail") or []:
        if not isinstance(mem, dict):
            continue
        mid = str(mem.get("memory_id") or "")
        row = bucket(str(mem.get("correlation_id") or ""))
        if mid and mid not in row["memory_ids"]:
            row["memory_ids"].append(mid)
            add_step(row, "memory", mid, str(mem.get("title") or ""))
    return [row for row in buckets.values() if row["correlation_id"]][:12]


def apply_filter(name: str, snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """Canonical Laços filter semantics. Frontend may apply the same locally."""
    key = str(name or "all").strip().lower().replace(" ", "_").replace("-", "_")
    incidents = [i for i in (snapshot.get("incidents") or []) if isinstance(i, dict)]
    goals = [g for g in (snapshot.get("goals") or []) if isinstance(g, dict)]
    tasks = [t for t in (snapshot.get("tasks") or []) if isinstance(t, dict)]
    agents = live_agents(tasks)
    improvements = snapshot.get("improvements") if isinstance(snapshot.get("improvements"), list) else []
    terminal = {s.value for s in TERMINAL_TASK}
    if key in {"all", "brain"}:
        return {"incidents": incidents, "goals": goals, "tasks": tasks, "agents": agents, "improvements": improvements}
    if key == "incidents":
        return {"incidents": incidents, "goals": [], "tasks": [], "agents": [], "improvements": []}
    if key == "goals":
        return {"incidents": [], "goals": goals, "tasks": [], "agents": [], "improvements": []}
    if key == "tasks":
        return {"incidents": [], "goals": [], "tasks": tasks, "agents": [], "improvements": []}
    if key == "agents":
        return {"incidents": [], "goals": [], "tasks": [], "agents": agents, "improvements": []}
    if key == "improvements":
        return {"incidents": [], "goals": [], "tasks": [], "agents": [], "improvements": improvements}
    if key == "completed":
        done_ok = {
            TaskStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.ROLLED_BACK.value,
        }
        done = [t for t in tasks if str(t.get("status") or "") in done_ok]
        return {"incidents": [], "goals": [], "tasks": done, "agents": [], "improvements": []}
    if key == "active":
        return {
            "incidents": [i for i in incidents if str(i.get("status") or "") in {"open", "investigating"}],
            "goals": [g for g in goals if str(g.get("status") or "") == "open"],
            "tasks": [t for t in tasks if str(t.get("status") or "") not in terminal],
            "agents": agents,
            "improvements": [],
        }
    if key == "errors":
        return {
            "incidents": [i for i in incidents if str(i.get("status") or "") in {"open", "investigating"}],
            "goals": [],
            "tasks": [t for t in tasks if str(t.get("status") or "") == TaskStatus.FAILED.value],
            "agents": [],
            "improvements": [],
        }
    if key == "high_risk":
        return {
            "incidents": [i for i in incidents if str(i.get("severity") or "") in {"critical", "high"}],
            "goals": [g for g in goals if str(g.get("risk_level") or "") in {"high", "critical"}],
            "tasks": [t for t in tasks if str(t.get("risk_level") or "") in {"high", "critical"}],
            "agents": [],
            "improvements": [],
        }
    return {"incidents": incidents, "goals": goals, "tasks": tasks, "agents": agents, "improvements": improvements}


def overlay_nodes(snapshot: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    tone = str(health.get("tone") or "unknown")
    hb = snapshot.get("heartbeat") if isinstance(snapshot.get("heartbeat"), dict) else {}
    brain_hb = str(hb.get("brain") or ("running" if snapshot.get("running") else "offline"))
    incidents = snapshot.get("incidents") if isinstance(snapshot.get("incidents"), list) else []
    goals = snapshot.get("goals") if isinstance(snapshot.get("goals"), list) else []
    tasks = snapshot.get("tasks") if isinstance(snapshot.get("tasks"), list) else []
    open_inc = sum(1 for i in incidents if str(i.get("status") or "") in {"open", "investigating"})
    active_goals = sum(1 for g in goals if str(g.get("status") or "") == "open")
    active_tasks = sum(
        1 for t in tasks
        if str(t.get("status") or "") not in {s.value for s in TERMINAL_TASK}
    )
    blocked = sum(1 for t in tasks if str(t.get("status") or "") == TaskStatus.BLOCKED.value)
    pending = sum(1 for t in tasks if str(t.get("status") or "") == TaskStatus.WAITING_APPROVAL.value)
    nodes = [
        {
            "id": "brain",
            "label": "Trader Brain",
            "kind": "intelligence",
            "lane": "brain",
            "authority": "observes",
            "can_execute": False,
            "can_approve": False,
            "tone": tone,
            "status": brain_hb,
            "summary": f"L{int((snapshot.get('autonomy') or {}).get('level') or 2)} shadow",
            "health_score": health.get("global_score"),
            "active_goals": active_goals,
            "active_tasks": active_tasks,
            "open_incidents": open_inc,
            "blocked_tasks": blocked,
            "pending_approvals": pending,
            "last_observation_at": snapshot.get("last_observation_at"),
        },
        {
            "id": "brain_health",
            "label": "Health",
            "kind": "health",
            "lane": "brain",
            "authority": "observes",
            "tone": tone,
            "status": tone,
            "summary": f"score {health.get('global_score', '—')}",
            "can_execute": False,
        },
        {
            "id": "brain_goals",
            "label": "Goals / tasks",
            "kind": "goals",
            "lane": "brain",
            "authority": "plans",
            "status": f"{active_goals} open",
            "summary": f"{active_tasks} tasks",
            "can_execute": False,
        },
        {
            "id": "brain_memory",
            "label": "Memory",
            "kind": "memory",
            "lane": "brain",
            "authority": "records",
            "status": "jsonl",
            "summary": "episodic + audit",
            "can_execute": False,
        },
    ][:MAX_OVERLAY_NODES]
    edges = [
        {"source": "brain", "target": "brain_health", "verb": "feeds", "kind": "brain"},
        {"source": "brain", "target": "brain_goals", "verb": "plans", "kind": "brain"},
        {"source": "brain", "target": "brain_memory", "verb": "records", "kind": "brain"},
        {"source": "brain", "target": "orchestrator", "verb": "observes", "kind": "brain"},
        {"source": "brain", "target": "nicolas", "verb": "advises", "kind": "brain"},
    ]
    return nodes, edges


def build_control_plane(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
    """Full Laços payload. Frontend must not invent entities from this."""
    health = snapshot.get("health") if isinstance(snapshot.get("health"), dict) else {}
    resources = snapshot.get("resources") if isinstance(snapshot.get("resources"), dict) else {}
    incidents = list(snapshot.get("incidents") or [])[:MAX_INCIDENTS]
    goals = list(snapshot.get("goals") or [])[:24]
    tasks = list(snapshot.get("tasks") or [])[:MAX_TASKS]
    audit = list(snapshot.get("audit_tail") or [])
    graph = snapshot.get("graph") if isinstance(snapshot.get("graph"), dict) else {}
    raw_rels = snapshot.get("relations")
    if not isinstance(raw_rels, list):
        raw_rels = graph.get("relations") if isinstance(graph.get("relations"), list) else []
    nodes, edges = overlay_nodes(snapshot)
    autonomy = snapshot.get("autonomy") if isinstance(snapshot.get("autonomy"), dict) else {}
    trading = snapshot.get("trading_autonomy") if isinstance(snapshot.get("trading_autonomy"), dict) else {}
    dag = task_dag(tasks, goals=goals, incidents=incidents)
    return {
        "schema": LACOS_SCHEMA,
        "host_view": "v-map",
        "host_label": "Mapa",
        "not_a_trading_path": True,
        "overlay_ready": True,
        "overlay": {
            "additive": True,
            "does_not_replace_prescribed_graph": True,
            "nodes": nodes,
            "edges": edges,
            "limits": {
                "max_overlay_nodes": MAX_OVERLAY_NODES,
                "max_dag_nodes": MAX_DAG_NODES,
                "activity_window": MAX_ACTIVITY,
            },
        },
        "heartbeat": snapshot.get("heartbeat") or {},
        "shadow": True,
        "brain_autonomy": {
            "ladder": "brain",
            "level": autonomy.get("level", 2),
            "level_name": autonomy.get("level_name") or "investigate",
            "shadow": True,
            "not_trading_autonomy": True,
        },
        "trading_autonomy": {
            "ladder": "trading",
            "mode": trading.get("mode") or trading.get("status") or "unknown",
            "not_brain_autonomy": True,
        },
        "hpc_orchestrator": "HPCOrchestrator",
        "brain_orchestrator": "BrainOrchestrator",
        "panels": dict(PANELS),
        "filters": list(FILTERS),
        "dag": dag,
        "agents": live_agents(tasks),
        "approvals": approvals_from_tasks(tasks),
        "improvements": improvement_candidates(health, resources),
        "activity": activity_from_audit(audit),
        "relations": graph_relations({"relations": raw_rels}),
        "chains": audit_chains({
            "incidents": incidents,
            "goals": goals,
            "tasks": tasks,
            "audit_tail": audit,
            "memory_tail": snapshot.get("memory_tail") or [],
        }),
        "proposals": list(snapshot.get("proposals") or [])[:8],
        "sandbox_runs": list((snapshot.get("sandbox") or {}).get("runs") or snapshot.get("sandbox_runs") or [])[:8],
        "learnings": list(snapshot.get("learnings") or [])[:8],
        "sandbox": snapshot.get("sandbox") or {"enabled": False, "status": "capable_not_enabled", "level3_enabled": False},
        "evaluation": evaluation_view(snapshot.get("evaluation") if isinstance(snapshot.get("evaluation"), dict) else None),
        "sides": snapshot.get("sides") if isinstance(snapshot.get("sides"), dict) else default_sides_view(),
        "cognitive": snapshot.get("cognitive") if isinstance(snapshot.get("cognitive"), dict) else (
            (snapshot.get("sides") or {}).get("engine") if isinstance(snapshot.get("sides"), dict) else {}
        ) or {},
        "heldout": snapshot.get("heldout") if isinstance(snapshot.get("heldout"), dict) else {},
        "live_shadow": snapshot.get("live_shadow") if isinstance(snapshot.get("live_shadow"), dict) else {},
        "provider_performance": snapshot.get("provider_performance") if isinstance(snapshot.get("provider_performance"), dict) else {},
        "provider_health": snapshot.get("provider_health") if isinstance(snapshot.get("provider_health"), dict) else {},
        "real_model_experiment": snapshot.get("real_model_experiment") if isinstance(snapshot.get("real_model_experiment"), dict) else {},
        "cognitive_strategy": snapshot.get("cognitive_strategy") if isinstance(snapshot.get("cognitive_strategy"), dict) else {},
        "live_intelligence": snapshot.get("live_intelligence") if isinstance(snapshot.get("live_intelligence"), dict) else {},
        "event_bus": snapshot.get("event_bus") or {
            "kind": "in-process",
            "limitation": "Brain and dashboard share main.py; not Redis/Kafka",
        },
    }
