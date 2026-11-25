"""VerificationRequest policy. The reasoner never gets a shell."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .diagnose import FixtureEnv
from .models import VerificationRequest, new_id
from .redact import redact_text
from .sandbox import authorize_command, classify_path


ALLOWED_ACTIONS = frozenset({
    "inspect_file",
    "inspect_source",
    "inspect_config",
    "inspect_schema",
    "inspect_parser",
    "inspect_log",
    "inspect_metric",
    "inspect_graph",
    "git_diff",
    "reproduce",
    "reproduce_failure",
    "run_targeted_test",
    "compare_latency",
    "correlate_diff",
    "check_protected",
    "memory_revalidate",
})

DENIED_ACTIONS = frozenset({
    "shell", "bash", "push", "merge", "deploy", "apply", "write",
    "order", "oms", "execution", "secret",
})


def authorize_verification(req: VerificationRequest) -> Dict[str, Any]:
    action = str(req.action_type or "").strip().lower()
    if action in DENIED_ACTIONS or action not in ALLOWED_ACTIONS:
        return {"allowed": False, "reason": "action_denied", "request": req.to_dict()}
    if classify_path(req.target) == "PROTECTED" and action in {"inspect_file", "inspect_source"}:
        return {"allowed": True, "reason": "read_protected_ok", "request": req.to_dict(), "write": False}
    if req.target and any(tok in req.target.lower() for tok in ("git push", "rm -rf", ".env")):
        return {"allowed": False, "reason": "target_denied", "request": req.to_dict()}
    if action == "run_targeted_test":
        auth = authorize_command("pytest")
        if not auth.get("allowed"):
            return {"allowed": False, "reason": "command_denied", "request": req.to_dict()}
    return {"allowed": True, "reason": "ok", "request": req.to_dict()}


def make_request(
    action_type: str,
    target: str = "",
    *,
    hypothesis_id: str = "",
    reason: str = "",
    disconfirming: bool = False,
    expected_information_gain: str = "MEDIUM",
) -> VerificationRequest:
    gain = str(expected_information_gain or "MEDIUM").upper()
    if gain not in {"LOW", "MEDIUM", "HIGH"}:
        gain = "MEDIUM"
    return VerificationRequest(
        request_id=new_id("vr"),
        hypothesis_id=hypothesis_id,
        action_type=action_type,
        target=target,
        reason=reason[:200],
        disconfirming=disconfirming,
        risk="low",
        expected_information_gain=gain,
    )


def execute_verification(req: VerificationRequest, env: Optional[FixtureEnv]) -> Dict[str, Any]:
    auth = authorize_verification(req)
    if not auth.get("allowed"):
        return {"ok": False, **auth, "evidence": []}
    env = env or FixtureEnv()
    action = req.action_type
    if action in {"inspect_file", "inspect_source", "inspect_config", "inspect_schema", "inspect_parser"}:
        text = env.read_text(req.target) if req.target else None
        if text is None and req.target:
            for rel in env.list_files():
                if rel.endswith(req.target) or req.target in rel:
                    text = env.read_text(rel)
                    req.target = rel
                    break
        return {
            "ok": True,
            "action": action,
            "target": req.target,
            "excerpt": redact_text((text or "")[:800]),
            "untrusted": True,
            "is_authority": False,
            "files": env.list_files()[:12],
        }
    if action in {"reproduce", "reproduce_failure", "run_targeted_test"}:
        return {"ok": True, "action": action, "result": env.reproduce()}
    if action == "check_protected":
        return {"ok": True, "action": action, "class": classify_path(req.target), "target": req.target}
    return {"ok": True, "action": action, "target": req.target, "note": "recorded"}
