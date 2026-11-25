"""Reasoner requests a capability. Policy selects the adapter. No generic shell."""
from __future__ import annotations

from typing import Any, Dict, Mapping


CAPABILITIES = {
    "inspect_file": "RepositoryAdapter",
    "inspect_schema": "RepositoryAdapter",
    "inspect_parser": "RepositoryAdapter",
    "correlate_diff": "GitAdapter",
    "read_commit": "GitAdapter",
    "reproduce_failure": "TestAdapter",
    "run_tests": "TestAdapter",
    "read_log": "ObservabilityAdapter",
    "read_metric": "ObservabilityAdapter",
    "graph_lookup": "GraphAdapter",
}


DENIED = frozenset({"shell", "bash", "push", "merge", "deploy", "apply", "write"})


def request_capability(action_type: str, target: str, *, reason: str = "") -> Dict[str, Any]:
    action = str(action_type or "").lower()
    if action in DENIED:
        return {
            "allowed": False,
            "reason": "generic_or_write_denied",
            "adapter": None,
            "action_type": action,
            "target": str(target)[:160],
        }
    adapter = CAPABILITIES.get(action, "RepositoryAdapter" if action.startswith("inspect") else None)
    if adapter is None:
        return {
            "allowed": False,
            "reason": "unknown_capability",
            "adapter": None,
            "action_type": action,
            "target": str(target)[:160],
        }
    return {
        "allowed": True,
        "reason": str(reason)[:160],
        "adapter": adapter,
        "action_type": action,
        "target": str(target)[:160],
        "selected_by": "policy",
    }


def adapter_for(request: Mapping[str, Any]) -> str:
    return str(request.get("adapter") or "")
