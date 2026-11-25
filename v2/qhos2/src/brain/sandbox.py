"""Isolated sandbox: Level 3 CAPABLE, not Level 3 ENABLED.

Production Brain stays on Level 2 + shadow. Real worktrees exist only when
SandboxExecutor is constructed with explicit_dev=True (tests / developer
harness). Apply, merge, push and deploy stay hard-blocked.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from brain.local_config import config

from .events import BrainEventKind, publish_brain_event
from .models import (
    ChangeProposal,
    ChangeRequest,
    IN_FLIGHT_SANDBOX,
    LearningRecord,
    ProposalStatus,
    SandboxRun,
    SandboxStatus,
    TaskKind,
    ValidationResult,
    can_transition,
    new_id,
    utc_ts,
)

SANDBOX_ENABLED = False
SANDBOX_CAPABLE = True

PROTECTED_PREFIXES: tuple[str, ...] = (
    "execution/",
    "risk/",
    "governance/control.py",
    "governance/promotion.py",
    ".env",
    "secrets",
    "credentials",
    "data_lake/",
    "oms/",
)

PROTECTED_NAMES: frozenset[str] = frozenset({
    ".env",
    "secrets.json",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "private.key",
})

ALLOWED_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("pytest",),
    ("python", "-m", "pytest"),
    ("py", "-3.13", "-m", "pytest"),
    ("python", "-m", "py_compile"),
    ("git", "status"),
    ("git", "diff"),
    ("git", "log"),
    ("git", "rev-parse"),
    ("git", "show"),
    ("ruff",),
)

DENIED_TOKENS: frozenset[str] = frozenset({
    "rm",
    "del",
    "reset",
    "--hard",
    "push",
    "drop",
    "detach",
    "rebase",
    "checkout",
    "clean",
    "mkfs",
    "shutdown",
    "kill",
    "sudo",
    "publish",
    "twine",
    "deploy",
})

SHELL_META = set(";&|`$<>\n\r")
SAFE_PYTEST_FLAGS = {
    "-q", "-x", "-vv", "-v",
    "--tb=short", "--tb=no", "--tb=line",
    "--collect-only",
}

DEFAULT_BUDGET = {
    "max_commands": 12,
    "max_duration_s": 180,
    "max_retries": 1,
    "max_files_changed": 8,
    "max_diff_bytes": 100_000,
    "command_timeout_s": 45,
}


def classify_failures(baseline: Iterable[str], after: Iterable[str]) -> Dict[str, List[str]]:
    """Compare pytest nodeids. Known baseline red is not a new regression."""
    before = set(str(x) for x in baseline)
    later = set(str(x) for x in after)
    return {
        "pre_existing": sorted(before & later),
        "new": sorted(later - before),
        "fixed": sorted(before - later),
        "pre_existing_failure": sorted(before & later),
        "new_failure": sorted(later - before),
        "fixed_failure": sorted(before - later),
        "unchanged_failure": sorted(before & later),
    }


def compare_baseline(baseline: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    """Separate PRE_EXISTING / NEW / FIXED / UNCHANGED / NEW_PASS / REGRESSION."""
    fails = classify_failures(baseline.get("failing") or [], after.get("failing") or [])
    pass_b = set(str(x) for x in (baseline.get("passed") or []))
    pass_a = set(str(x) for x in (after.get("passed") or []))
    col_b = set(str(x) for x in (baseline.get("collection_errors") or []))
    col_a = set(str(x) for x in (after.get("collection_errors") or []))
    new_pass = sorted(pass_a - pass_b)
    new_collect = sorted(col_a - col_b)
    regressions = sorted(set(fails["new"]) | set(new_collect))
    return {
        "tests": fails,
        "new_failure_count": len(fails["new"]),
        "fixed_failure_count": len(fails["fixed"]),
        "pre_existing_count": len(fails["pre_existing"]),
        "unchanged_failure": fails["unchanged_failure"],
        "new_pass": new_pass,
        "collection_errors": {
            "pre_existing": sorted(col_b & col_a),
            "new": new_collect,
            "fixed": sorted(col_b - col_a),
        },
        "regressions": regressions,
        "latency": {"baseline": baseline.get("latency"), "after": after.get("latency")},
        "cpu": {"baseline": baseline.get("cpu"), "after": after.get("cpu")},
        "memory": {"baseline": baseline.get("memory"), "after": after.get("memory")},
        "verdict": (
            "regression" if regressions
            else ("improved" if fails["fixed"] or new_pass else "unchanged")
        ),
    }


def apply_transition(run: SandboxRun, nxt: str) -> Dict[str, Any]:
    if not can_transition(run.status, nxt):
        return {
            "ok": False,
            "reason": "invalid_transition",
            "from": run.status,
            "to": nxt,
        }
    run.status = nxt
    run.updated_at = utc_ts()
    return {"ok": True, "status": nxt}


def _tokens(command: Sequence[str] | str) -> List[str]:
    if isinstance(command, str):
        if any(ch in command for ch in SHELL_META):
            return ["__shell_meta__"]
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = command.split()
    else:
        parts = [str(x) for x in command]
    return [p.strip() for p in parts if p]


def authorize_command(command: Sequence[str] | str) -> Dict[str, Any]:
    tokens = _tokens(command)
    if not tokens:
        return {"allowed": False, "reason": "empty_command"}
    if tokens == ["__shell_meta__"] or any(any(ch in t for ch in SHELL_META) for t in tokens):
        return {"allowed": False, "reason": "shell_metacharacters"}
    lowered = [t.lower() for t in tokens]
    for denied in DENIED_TOKENS:
        if denied in lowered:
            return {"allowed": False, "reason": f"denied_token:{denied}"}
    for prefix in ALLOWED_COMMANDS:
        want = [p.lower() for p in prefix]
        if lowered[:len(want)] == want:
            extra = tokens[len(prefix):]
            extra_auth = _authorize_extra(prefix, extra)
            if not extra_auth.get("allowed"):
                return extra_auth
            return {"allowed": True, "reason": "allowlist", "prefix": list(prefix), "argv": tokens}
    return {"allowed": False, "reason": "not_on_allowlist"}


def _authorize_extra(prefix: Sequence[str], extra: Sequence[str]) -> Dict[str, Any]:
    joined = " ".join(p.lower() for p in prefix)
    for arg in extra:
        low = arg.lower()
        if low.startswith("-c") or low in {"-c", "--command"}:
            return {"allowed": False, "reason": "denied_flag"}
        if joined.endswith("pytest"):
            if arg.startswith("-") and arg not in SAFE_PYTEST_FLAGS and not arg.startswith("--tb="):
                if arg in {"-k"} or arg.startswith("-k"):
                    continue
                return {"allowed": False, "reason": f"denied_pytest_flag:{arg}"}
            if not arg.startswith("-"):
                rel = normalize_rel(arg)
                if rel is None:
                    return {"allowed": False, "reason": "pytest_path_blocked"}
        if prefix[:1] == ("git",) and arg.startswith("--upload"):
            return {"allowed": False, "reason": "denied_git_flag"}
    return {"allowed": True, "reason": "ok"}


def _bind_python(argv: Sequence[str]) -> List[str]:
    """Run allowlisted python/pytest via the current interpreter."""
    tokens = [str(a) for a in argv]
    low = [t.lower() for t in tokens]
    if low[:1] == ["pytest"]:
        return [sys.executable, "-m", "pytest", *tokens[1:]]
    if low[:1] == ["python"]:
        return [sys.executable, *tokens[1:]]
    if low[:3] == ["py", "-3.13", "-m"]:
        return [sys.executable, "-m", *tokens[3:]]
    if low[:1] == ["ruff"]:
        return tokens
    return tokens


def normalize_rel(rel: str) -> Optional[str]:
    """Fail closed: None means BLOCK. No absolute, no .., no NUL."""
    raw = str(rel or "").replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    if raw.startswith("/") or raw.startswith("~"):
        return None
    if len(raw) >= 2 and raw[1] == ":":
        return None
    if raw.startswith("//") or raw.startswith("\\\\"):
        return None
    parts: List[str] = []
    for piece in raw.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            return None
        parts.append(piece)
    if not parts:
        return None
    return "/".join(parts)


def resolve_under(root: Path, rel: str) -> Optional[Path]:
    norm = normalize_rel(rel)
    if norm is None:
        return None
    try:
        base = root.resolve()
        candidate = (root / Path(*norm.split("/"))).resolve()
        candidate.relative_to(base)
    except (OSError, ValueError):
        return None
    try:
        if candidate.exists() and candidate.is_symlink():
            real = candidate.resolve()
            real.relative_to(base)
            return None if real != candidate.resolve() and not str(real).startswith(str(base)) else real
    except (OSError, ValueError):
        return None
    return candidate


def classify_path(rel: str) -> str:
    """READ_ONLY | SANDBOX_WRITABLE | PROTECTED"""
    path = normalize_rel(rel)
    if path is None:
        return "PROTECTED"
    lowered = path.lower()
    name = Path(lowered).name
    if name in PROTECTED_NAMES or name.endswith(".pem") or name.endswith(".key"):
        return "PROTECTED"
    for prefix in PROTECTED_PREFIXES:
        pref = prefix.lower().rstrip("/")
        if lowered == pref or lowered.startswith(prefix.lower()) or lowered.startswith(pref + "/"):
            return "PROTECTED"
    if lowered.startswith("brain/") or lowered.startswith("tests/"):
        return "SANDBOX_WRITABLE"
    if lowered.startswith("dashboard/_hybrid.html"):
        return "SANDBOX_WRITABLE"
    return "READ_ONLY"


def authorize_path(
    rel: str,
    *,
    write: bool = False,
    allowed_paths: Optional[Sequence[str]] = None,
    allow_sandbox_write: bool = False,
) -> Dict[str, Any]:
    norm = normalize_rel(rel)
    if norm is None:
        return {"allowed": False, "reason": "unresolved_path", "class": "PROTECTED"}
    kind = classify_path(norm)
    if kind == "PROTECTED":
        return {"allowed": False, "reason": "protected_path", "class": kind, "path": norm}
    if write:
        if not allow_sandbox_write and not SANDBOX_ENABLED:
            return {"allowed": False, "reason": "sandbox_disabled", "class": kind, "path": norm}
        scope = [normalize_rel(p) or "" for p in (allowed_paths or [])]
        scope = [p for p in scope if p]
        if not scope:
            return {"allowed": False, "reason": "deny_by_default", "class": kind, "path": norm}
        if not any(norm == p or norm.startswith(p.rstrip("/") + "/") for p in scope):
            return {"allowed": False, "reason": "out_of_scope", "class": kind, "path": norm}
        if kind == "READ_ONLY":
            return {"allowed": False, "reason": "read_only_path", "class": kind, "path": norm}
    return {"allowed": True, "reason": "ok", "class": kind, "path": norm}


def request_fingerprint(request: ChangeRequest) -> str:
    raw = "|".join([
        request.request_id,
        request.goal_id,
        request.task_id,
        ",".join(sorted(request.patches)),
        ",".join(sorted(request.target_files)),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_pytest_output(stdout: str, stderr: str) -> Dict[str, List[str]]:
    text = (stdout or "") + "\n" + (stderr or "")
    failing: List[str] = []
    errors: List[str] = []
    passed: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED "):
            failing.append(stripped.split()[1])
        elif stripped.startswith("ERROR "):
            errors.append(stripped.split()[1])
        elif stripped.startswith("PASSED "):
            passed.append(stripped.split()[1])
    return {"failing": failing, "collection_errors": errors, "passed": passed}


def confidence_from_validation(result: ValidationResult, *, files_ok: bool) -> float:
    """Observable evidence only. Never 1.0. Weak evidence stays low."""
    score = 0.0
    evidence_n = 0
    if files_ok:
        score += 0.2
        evidence_n += 1
    if result.commands_executed:
        score += 0.15
        evidence_n += 1
    if not result.new_failures and not result.regressions:
        score += 0.3
        evidence_n += 1
    if result.current_passed:
        score += 0.15
        evidence_n += 1
    if result.changed_files:
        score += 0.1
        evidence_n += 1
    if evidence_n < 2:
        return round(min(score, 0.35), 4)
    return round(min(0.92, score), 4)


def default_sandbox_parent() -> Path:
    root = Path(config.root_dir)
    return root.parent / f".qhos-brain-sandboxes-{root.name}"


@dataclass
class SandboxBudget:
    max_commands: int = 12
    max_duration_s: int = 180
    max_retries: int = 1
    max_files_changed: int = 8
    max_diff_bytes: int = 100_000
    command_timeout_s: int = 45

    @classmethod
    def from_config(cls) -> "SandboxBudget":
        return cls(
            max_commands=int(getattr(config, "brain_sandbox_max_commands", 12) or 12),
            max_duration_s=int(getattr(config, "brain_sandbox_max_duration_s", 180) or 180),
            max_retries=int(getattr(config, "brain_sandbox_max_retries", 1) or 1),
            max_files_changed=int(getattr(config, "brain_sandbox_max_files_changed", 8) or 8),
            max_diff_bytes=int(getattr(config, "brain_sandbox_max_diff_bytes", 100_000) or 100_000),
            command_timeout_s=int(getattr(config, "brain_sandbox_command_timeout_s", 45) or 45),
        )


class WorktreeStrategy:
    """Isolated git worktree. Never merge, never push.

    Default create() stays disabled so production callers cannot open a tree.
    """

    def __init__(self, git_root: Optional[Path] = None, sandbox_parent: Optional[Path] = None) -> None:
        self.git_root = Path(git_root) if git_root is not None else Path(config.root_dir)
        self.sandbox_parent = Path(sandbox_parent) if sandbox_parent is not None else default_sandbox_parent()

    def create(self, base_commit: str = "", *, run_id: str = "", execute: bool = False) -> Dict[str, Any]:
        if not execute:
            return {
                "ok": False,
                "reason": "sandbox_disabled",
                "worktree": "",
                "branch": "",
                "base_commit": base_commit,
                "merge": False,
                "push": False,
                "deploy": False,
            }
        run_id = run_id or new_id("sbx")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id):
            return {"ok": False, "reason": "invalid_run_id", "worktree": "", "merge": False, "push": False}
        parent = self.sandbox_parent.resolve()
        parent.mkdir(parents=True, exist_ok=True)
        dest = (parent / run_id).resolve()
        try:
            dest.relative_to(parent)
        except ValueError:
            return {"ok": False, "reason": "sandbox_parent_escape", "worktree": "", "merge": False, "push": False}
        if dest.exists():
            return {
                "ok": True,
                "reason": "idempotent",
                "worktree": str(dest),
                "branch": f"brain/sbx/{run_id}",
                "base_commit": base_commit,
                "merge": False,
                "push": False,
            }
        commit = base_commit or "HEAD"
        branch = f"brain/sbx/{run_id}"
        proc = self._git(["worktree", "add", "-B", branch, str(dest), commit], cwd=self.git_root)
        if proc.returncode != 0:
            return {
                "ok": False,
                "reason": "worktree_add_failed",
                "detail": (proc.stderr or proc.stdout)[:400],
                "worktree": "",
                "merge": False,
                "push": False,
            }
        return {
            "ok": True,
            "reason": "created",
            "worktree": str(dest),
            "branch": branch,
            "base_commit": commit,
            "merge": False,
            "push": False,
            "deploy": False,
        }

    def remove(self, worktree: str, *, branch: str = "") -> Dict[str, Any]:
        if not worktree:
            return {"ok": False, "reason": "empty_path"}
        try:
            path = Path(worktree).resolve()
            parent = self.sandbox_parent.resolve()
            path.relative_to(parent)
        except (OSError, ValueError):
            return {"ok": False, "reason": "cleanup_path_not_sandbox"}
        proc = self._git(["worktree", "remove", "--force", str(path)], cwd=self.git_root, timeout=30)
        if path.exists():
            try:
                shutil.rmtree(path)
            except OSError as exc:
                return {"ok": False, "reason": "cleanup_failed", "detail": str(exc)[:200]}
        if branch.startswith("brain/sbx/"):
            self._git(["branch", "-D", branch], cwd=self.git_root, timeout=15)
        return {"ok": True, "reason": "cleaned", "worktree": str(path)}

    def _git(self, args: Sequence[str], *, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        blocked = {"push", "--hard", "clean", "reset"}
        if any(str(a).lower() in blocked for a in args):
            return subprocess.CompletedProcess(args=list(args), returncode=99, stdout="", stderr="blocked_internal_git")
        try:
            return subprocess.run(
                ["git", *[str(a) for a in args]],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args=list(args), returncode=124, stdout="", stderr="timeout")
        except OSError as exc:
            return subprocess.CompletedProcess(args=list(args), returncode=1, stdout="", stderr=str(exc)[:200])


class SandboxExecutor:
    """Isolated experiment runner.

    enabled follows SANDBOX_ENABLED (always False this phase).
    explicit_dev unlocks worktrees for tests/developers only.
    """

    def __init__(
        self,
        *,
        enabled: bool = SANDBOX_ENABLED,
        explicit_dev: bool = False,
        root: Optional[Path] = None,
        git_root: Optional[Path] = None,
        sandbox_parent: Optional[Path] = None,
        state_dir: Optional[Path] = None,
        budget: Optional[SandboxBudget] = None,
    ) -> None:
        self.enabled = bool(enabled) and bool(SANDBOX_ENABLED)
        self.capable = SANDBOX_CAPABLE
        self.explicit_dev = bool(explicit_dev)
        self.root = Path(root) if root is not None else Path(config.root_dir)
        self.git_root = Path(git_root) if git_root is not None else self.root
        self.sandbox_parent = Path(sandbox_parent) if sandbox_parent is not None else default_sandbox_parent()
        self.state_dir = Path(state_dir) if state_dir is not None else Path(config.data_lake_dir) / "brain" / "sandbox"
        self.budget = budget or SandboxBudget.from_config()
        self.runs: Dict[str, SandboxRun] = {}
        self.proposals: Dict[str, ChangeProposal] = {}
        self.requests: Dict[str, ChangeRequest] = {}
        self.learnings: Dict[str, LearningRecord] = {}
        self.worktrees = WorktreeStrategy(git_root=self.git_root, sandbox_parent=self.sandbox_parent)
        self._started_at: Dict[str, float] = {}
        self._load_state()

    def _may_execute(self) -> bool:
        return bool(self.explicit_dev)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": False,
            "capable": True,
            "explicit_dev": self.explicit_dev,
            "hard_flag": SANDBOX_ENABLED,
            "status": "capable_not_enabled" if SANDBOX_CAPABLE else "foundation_only",
            "level3_enabled": False,
            "auto_merge": False,
            "auto_push": False,
            "auto_deploy": False,
            "protected_prefixes": list(PROTECTED_PREFIXES),
            "allowed_command_prefixes": [" ".join(p) for p in ALLOWED_COMMANDS],
            "runs_total": len(self.runs),
            "proposals_total": len(self.proposals),
            "budget": {
                "max_commands": self.budget.max_commands,
                "max_duration_s": self.budget.max_duration_s,
                "max_files_changed": self.budget.max_files_changed,
                "max_diff_bytes": self.budget.max_diff_bytes,
                "command_timeout_s": self.budget.command_timeout_s,
            },
        }

    def _state_path(self) -> Path:
        return self.state_dir / "runs.json"

    def _load_state(self) -> None:
        path = self._state_path()
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(blob, dict):
            return
        for row in blob.get("runs") or []:
            if not isinstance(row, dict):
                continue
            run = SandboxRun.from_dict(row)
            if run.status in {s.value for s in IN_FLIGHT_SANDBOX}:
                run.status = SandboxStatus.INTERRUPTED.value
                run.fail_reason = "crash_recovery_interrupted"
                run.validation = "INTERRUPTED"
            self.runs[run.run_id] = run
        for row in blob.get("proposals") or []:
            if isinstance(row, dict) and row.get("proposal_id"):
                self.proposals[str(row["proposal_id"])] = ChangeProposal(
                    proposal_id=str(row["proposal_id"]),
                    goal_id=str(row.get("goal_id") or ""),
                    sandbox_run_id=str(row.get("sandbox_run_id") or ""),
                    title=str(row.get("title") or ""),
                    status=str(row.get("status") or ProposalStatus.DRAFT.value),
                    correlation_id=str(row.get("correlation_id") or ""),
                    human_decision=str(row.get("human_decision") or ""),
                )

    def _persist(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "runs": [r.to_dict() for r in self.runs.values()],
                "proposals": [p.to_dict() for p in self.proposals.values()],
            }
            tmp = self._state_path().with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, self._state_path())
        except OSError:
            pass

    def _emit(self, kind: BrainEventKind, run: SandboxRun, extra: Optional[Dict[str, Any]] = None) -> str:
        audit_id = new_id("aud")
        publish_brain_event(
            kind,
            payload={"run_id": run.run_id, "audit_id": audit_id, **(extra or {})},
            correlation_id=run.correlation_id,
        )
        return audit_id

    def create_run(
        self,
        *,
        goal_id: str = "",
        task_id: str = "",
        base_commit: str = "",
        request_id: str = "",
        correlation_id: str = "",
        fingerprint: str = "",
    ) -> SandboxRun:
        if fingerprint:
            for existing in self.runs.values():
                if existing.fingerprint == fingerprint and existing.status not in {
                    SandboxStatus.CLEANED.value,
                    SandboxStatus.EXPIRED.value,
                }:
                    return existing
        if not self._may_execute():
            run = SandboxRun(
                run_id=new_id("sbx"),
                goal_id=goal_id,
                task_id=task_id,
                request_id=request_id,
                base_commit=base_commit,
                status=SandboxStatus.DISABLED.value,
                validation="SANDBOX_NOT_ENABLED",
                correlation_id=correlation_id or new_id("cor"),
                fingerprint=fingerprint,
            )
            self.runs[run.run_id] = run
            self._persist()
            return run
        run = SandboxRun(
            run_id=new_id("sbx"),
            goal_id=goal_id,
            task_id=task_id,
            request_id=request_id,
            base_commit=base_commit,
            status=SandboxStatus.CREATED.value,
            correlation_id=correlation_id or new_id("cor"),
            fingerprint=fingerprint,
        )
        self.runs[run.run_id] = run
        self._started_at[run.run_id] = time.time()
        self._emit(BrainEventKind.SANDBOX_CREATED, run)
        self._persist()
        return run

    def simulate(self, request: ChangeRequest) -> Dict[str, Any]:
        """Shadow: policy check only. No worktree, no writes."""
        blocked = []
        for rel in list(request.target_files) + list(request.patches):
            auth = authorize_path(rel, write=True, allowed_paths=request.allowed_paths, allow_sandbox_write=True)
            if not auth.get("allowed"):
                blocked.append({"path": rel, "reason": auth.get("reason")})
        return {
            "ok": not blocked,
            "simulated": True,
            "executed": False,
            "blocked": blocked,
            "status": "shadow_only",
            "request": request.to_dict(),
        }

    def execute(self, run_id: str, command: Sequence[str] | str) -> Dict[str, Any]:
        auth = authorize_command(command)
        if not auth.get("allowed"):
            return {"ok": False, **auth, "executed": False}
        if not self._may_execute():
            return {"ok": False, "reason": "sandbox_disabled", "executed": False}
        run = self.runs.get(run_id)
        if run is None:
            return {"ok": False, "reason": "unknown_run", "executed": False}
        if run.status in {SandboxStatus.CLEANED.value, SandboxStatus.DISABLED.value, SandboxStatus.INTERRUPTED.value}:
            return {"ok": False, "reason": f"run_{run.status}", "executed": False}
        if len(run.commands_executed) >= self.budget.max_commands:
            return {"ok": False, "reason": "max_commands", "executed": False}
        elapsed = time.time() - self._started_at.get(run_id, time.time())
        if elapsed > self.budget.max_duration_s:
            return {"ok": False, "reason": "max_duration", "executed": False}
        cwd = Path(run.worktree) if run.worktree else self.git_root
        if run.worktree:
            try:
                Path(run.worktree).resolve().relative_to(self.sandbox_parent.resolve())
            except (OSError, ValueError):
                return {"ok": False, "reason": "worktree_not_sandbox", "executed": False}
        argv = auth.get("argv") or _tokens(command)
        argv = _bind_python(argv)
        started = time.time()
        try:
            proc = subprocess.run(
                [str(a) for a in argv],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=self.budget.command_timeout_s,
                check=False,
                shell=False,
            )
            duration = time.time() - started
            run.commands_executed.append(" ".join(str(a) for a in argv))
            parsed = parse_pytest_output(proc.stdout or "", proc.stderr or "")
            result = {
                "ok": proc.returncode == 0,
                "executed": True,
                "exit_code": proc.returncode,
                "duration_s": round(duration, 4),
                "stdout": (proc.stdout or "")[:4000],
                "stderr": (proc.stderr or "")[:4000],
                "correlation_id": run.correlation_id,
                "tests": parsed,
            }
            self._persist()
            return result
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "executed": False,
                "reason": "timeout",
                "duration_s": self.budget.command_timeout_s,
                "correlation_id": run.correlation_id,
            }

    def create_worktree(self, run_id: str) -> Dict[str, Any]:
        run = self.runs.get(run_id)
        base = run.base_commit if run is not None else ""
        if not self._may_execute() or run is None:
            result = self.worktrees.create(base)
            result["run_id"] = run_id
            return result
        if run.worktree and Path(run.worktree).exists():
            return {"ok": True, "reason": "idempotent", "worktree": run.worktree, "run_id": run_id, "merge": False, "push": False}
        moved = apply_transition(run, SandboxStatus.WORKTREE_CREATED.value)
        if not moved.get("ok") and run.status != SandboxStatus.BASELINE_READY.value:
            if run.status == SandboxStatus.CREATED.value:
                apply_transition(run, SandboxStatus.BASELINE_RUNNING.value)
                apply_transition(run, SandboxStatus.BASELINE_READY.value)
                moved = apply_transition(run, SandboxStatus.WORKTREE_CREATED.value)
            if not moved.get("ok") and run.status != SandboxStatus.WORKTREE_CREATED.value:
                result = {"ok": False, **moved, "worktree": ""}
                result["run_id"] = run_id
                return result
        result = self.worktrees.create(run.base_commit or "HEAD", run_id=run.run_id, execute=True)
        result["run_id"] = run_id
        if result.get("ok"):
            run.worktree = str(result.get("worktree") or "")
            run.branch = str(result.get("branch") or "")
            if not run.base_commit:
                head = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(self.git_root),
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                run.base_commit = (head.stdout or "").strip()
            apply_transition(run, SandboxStatus.WORKTREE_CREATED.value)
            self._emit(BrainEventKind.WORKTREE_CREATED, run)
            self._persist()
        else:
            apply_transition(run, SandboxStatus.FAILED.value)
            run.fail_reason = str(result.get("reason") or "worktree_failed")
            self._persist()
        return result

    def capture_baseline(self, run: SandboxRun, *, tests: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        if not self._may_execute():
            return {"ok": False, "reason": "sandbox_disabled"}
        if run.status == SandboxStatus.CREATED.value:
            apply_transition(run, SandboxStatus.BASELINE_RUNNING.value)
        elif run.status == SandboxStatus.WORKTREE_CREATED.value:
            pass
        else:
            moved = apply_transition(run, SandboxStatus.BASELINE_RUNNING.value)
            if not moved.get("ok") and run.status not in {
                SandboxStatus.BASELINE_RUNNING.value,
                SandboxStatus.BASELINE_READY.value,
                SandboxStatus.WORKTREE_CREATED.value,
            }:
                return {"ok": False, **moved}
        cwd = Path(run.worktree) if run.worktree else self.git_root
        info: Dict[str, Any] = {"ts": utc_ts(), "python": sys.version.split()[0]}
        for args, key in (
            (["git", "rev-parse", "HEAD"], "commit"),
            (["git", "status", "--porcelain"], "status"),
            (["git", "branch", "--show-current"], "branch"),
        ):
            try:
                proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=10, check=False)
                info[key] = (proc.stdout or "").strip()[:2000]
            except (OSError, subprocess.TimeoutExpired):
                info[key] = ""
        if tests is not None:
            info["failing"] = list(tests.get("failing") or [])
            info["passed"] = list(tests.get("passed") or [])
            info["collection_errors"] = list(tests.get("collection_errors") or [])
        run.baseline = info
        run.base_commit = run.base_commit or str(info.get("commit") or "")
        if run.status == SandboxStatus.BASELINE_RUNNING.value:
            apply_transition(run, SandboxStatus.BASELINE_READY.value)
        self._emit(BrainEventKind.BASELINE_CAPTURED, run)
        self._persist()
        return {"ok": True, "baseline": info}

    def apply_changes(self, run: SandboxRun, request: ChangeRequest) -> Dict[str, Any]:
        if not self._may_execute():
            return {"ok": False, "reason": "sandbox_disabled"}
        if run.changes_applied:
            return {"ok": True, "reason": "idempotent", "files": list(run.files_changed)}
        if not run.worktree:
            return {"ok": False, "reason": "no_worktree"}
        if run.status == SandboxStatus.WORKTREE_CREATED.value:
            apply_transition(run, SandboxStatus.CHANGES_PENDING.value)
        moved = apply_transition(run, SandboxStatus.CHANGES_APPLIED.value)
        if not moved.get("ok") and run.status != SandboxStatus.CHANGES_PENDING.value:
            return {"ok": False, **moved}
        if run.status == SandboxStatus.CHANGES_PENDING.value:
            apply_transition(run, SandboxStatus.CHANGES_APPLIED.value)
        root = Path(run.worktree)
        written: List[str] = []
        patches = dict(request.patches)
        if not patches:
            apply_transition(run, SandboxStatus.FAILED.value)
            run.fail_reason = "no_patches"
            return {"ok": False, "reason": "no_patches"}
        if len(patches) > self.budget.max_files_changed:
            apply_transition(run, SandboxStatus.FAILED.value)
            run.fail_reason = "max_files_changed"
            return {"ok": False, "reason": "max_files_changed"}
        for rel, content in patches.items():
            auth = authorize_path(
                rel,
                write=True,
                allowed_paths=request.allowed_paths or request.target_files,
                allow_sandbox_write=True,
            )
            if not auth.get("allowed"):
                apply_transition(run, SandboxStatus.FAILED.value)
                run.fail_reason = str(auth.get("reason"))
                self._persist()
                return {"ok": False, **auth}
            dest = resolve_under(root, str(auth.get("path")))
            if dest is None:
                apply_transition(run, SandboxStatus.FAILED.value)
                run.fail_reason = "unresolved_path"
                return {"ok": False, "reason": "unresolved_path", "path": rel}
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
            written.append(str(auth.get("path")))
        run.files_changed = written
        run.changes_applied = True
        self._emit(BrainEventKind.CHANGE_APPLIED, run, {"files": written})
        self._persist()
        return {"ok": True, "files": written}

    def inspect_diff(self, run: SandboxRun) -> Dict[str, Any]:
        if not run.worktree:
            return {"ok": False, "reason": "no_worktree"}
        cwd = Path(run.worktree)
        stat = self.execute(run.run_id, ["git", "diff", "--stat"])
        full = self.execute(run.run_id, ["git", "diff"])
        status = self.execute(run.run_id, ["git", "status", "--porcelain"])
        porcelain = (status.get("stdout") or "") if status.get("executed") else ""
        files: List[str] = []
        for line in porcelain.splitlines():
            name = line[3:].strip().replace("\\", "/")
            if " -> " in name:
                name = name.split(" -> ", 1)[1]
            if name:
                files.append(name)
        diff_text = (full.get("stdout") or "") if full.get("executed") else ""
        if len(diff_text.encode("utf-8", errors="replace")) > self.budget.max_diff_bytes:
            apply_transition(run, SandboxStatus.FAILED.value)
            run.fail_reason = "excessive_diff"
            self._persist()
            return {"ok": False, "reason": "excessive_diff"}
        for rel in files:
            if classify_path(rel) == "PROTECTED":
                apply_transition(run, SandboxStatus.FAILED.value)
                run.fail_reason = "protected_path_in_diff"
                self._emit(BrainEventKind.REGRESSION_DETECTED, run, {"path": rel})
                self._persist()
                return {"ok": False, "reason": "protected_path_in_diff", "path": rel}
        summary = ((stat.get("stdout") or "") if stat.get("executed") else "")[:500]
        added = len(re.findall(r"^\+", diff_text, re.M))
        removed = len(re.findall(r"^-", diff_text, re.M))
        run.diff_summary = summary or f"files={len(files)}"
        run.files_changed = sorted(set(run.files_changed) | set(files))
        self._persist()
        return {
            "ok": True,
            "files": run.files_changed,
            "stat": summary,
            "added_hunk_lines": added,
            "removed_hunk_lines": removed,
            "diff": diff_text[:8000],
        }

    def validate(
        self,
        run: SandboxRun,
        request: ChangeRequest,
        *,
        after_tests: Optional[Mapping[str, Any]] = None,
    ) -> ValidationResult:
        apply_transition(run, SandboxStatus.VALIDATING.value)
        self._emit(BrainEventKind.VALIDATION_STARTED, run)
        started = time.time()
        evidence: List[str] = []
        plan = [str(x).lower() for x in (request.validation_plan or ["syntax", "targeted"])]
        if "syntax" in plan:
            for rel in run.files_changed:
                if rel.endswith(".py"):
                    compiled = self.execute(run.run_id, ["python", "-m", "py_compile", rel])
                    evidence.append(f"syntax:{rel}:{compiled.get('ok')}")
                    if not compiled.get("ok"):
                        result = self._fail_validation(run, "syntax_failed", started, evidence)
                        return result
        collected: Dict[str, List[str]] = {"failing": [], "passed": [], "collection_errors": []}
        if after_tests is not None:
            collected = {
                "failing": list(after_tests.get("failing") or []),
                "passed": list(after_tests.get("passed") or []),
                "collection_errors": list(after_tests.get("collection_errors") or []),
            }
        else:
            targets = [p for p in request.target_files if p.startswith("tests/")]
            if "targeted" in plan and targets:
                cmd = ["python", "-m", "pytest", "-q", "--tb=no", *targets]
                out = self.execute(run.run_id, cmd)
                parsed = parse_pytest_output(out.get("stdout") or "", out.get("stderr") or "")
                for key in collected:
                    collected[key].extend(parsed.get(key) or [])
            if "brain" in plan:
                brain_tests = [p for p in request.target_files if "test_brain" in p]
                if brain_tests:
                    out = self.execute(run.run_id, ["python", "-m", "pytest", "-q", "--tb=no", *brain_tests])
                    parsed = parse_pytest_output(out.get("stdout") or "", out.get("stderr") or "")
                    for key in collected:
                        collected[key].extend(parsed.get(key) or [])
        comparison = compare_baseline(run.baseline or {}, collected)
        duration = time.time() - started
        regressions = list(comparison.get("regressions") or [])
        new_fail = list((comparison.get("tests") or {}).get("new") or [])
        status = "validated"
        if regressions or new_fail:
            status = "needs_investigation"
        result = ValidationResult(
            status=status,
            baseline_passed=len(run.baseline.get("passed") or []) if run.baseline else 0,
            current_passed=len(collected.get("passed") or []),
            preexisting_failures=list((comparison.get("tests") or {}).get("pre_existing") or []),
            new_failures=new_fail,
            fixed_failures=list((comparison.get("tests") or {}).get("fixed") or []),
            unchanged_failures=list(comparison.get("unchanged_failure") or []),
            new_passes=list(comparison.get("new_pass") or []),
            collection_errors=list((comparison.get("collection_errors") or {}).get("new") or []),
            regressions=regressions,
            changed_files=list(run.files_changed),
            diff_summary=run.diff_summary,
            commands_executed=list(run.commands_executed),
            duration_s=round(duration, 4),
            evidence=evidence,
            sandbox_success=not regressions and not new_fail,
            production_success=False,
        )
        run.tests_result = collected
        run.tests_run = result.current_passed + len(result.new_failures) + len(result.preexisting_failures)
        run.validation_result = result.to_dict()
        if result.sandbox_success:
            apply_transition(run, SandboxStatus.VALIDATED.value)
        else:
            apply_transition(run, SandboxStatus.NEEDS_INVESTIGATION.value)
            self._emit(BrainEventKind.REGRESSION_DETECTED, run, {"new_failures": new_fail})
        self._emit(BrainEventKind.VALIDATION_COMPLETED, run, {"status": result.status})
        self._persist()
        return result

    def _fail_validation(self, run: SandboxRun, reason: str, started: float, evidence: List[str]) -> ValidationResult:
        apply_transition(run, SandboxStatus.FAILED.value)
        run.fail_reason = reason
        result = ValidationResult(
            status="failed",
            duration_s=round(time.time() - started, 4),
            evidence=evidence + [reason],
            changed_files=list(run.files_changed),
            commands_executed=list(run.commands_executed),
        )
        run.validation_result = result.to_dict()
        self._persist()
        return result

    def propose(
        self,
        run: SandboxRun,
        *,
        title: str,
        tests_summary: str = "",
        comparison: Optional[Mapping[str, Any]] = None,
        risk: str = "low",
        confidence: float = 0.0,
        request: Optional[ChangeRequest] = None,
        validation: Optional[ValidationResult] = None,
    ) -> ChangeProposal:
        for existing in self.proposals.values():
            if existing.sandbox_run_id == run.run_id:
                return existing
        status = ProposalStatus.DRAFT.value
        new_fails = list((comparison or {}).get("tests", {}).get("new") or [])
        if validation is not None:
            new_fails = list(validation.new_failures)
        if run.status == SandboxStatus.VALIDATED.value and not new_fails:
            moved = apply_transition(run, SandboxStatus.READY_FOR_REVIEW.value)
            if moved.get("ok"):
                status = ProposalStatus.READY_FOR_REVIEW.value
        elif run.status not in {SandboxStatus.DISABLED.value, SandboxStatus.FAILED.value} and comparison and not new_fails:
            if run.status not in {SandboxStatus.DISABLED.value, SandboxStatus.FAILED.value}:
                status = ProposalStatus.DRAFT.value
        if run.status == SandboxStatus.NEEDS_INVESTIGATION.value:
            status = ProposalStatus.NEEDS_INVESTIGATION.value
        evid = list(validation.evidence) if validation is not None else []
        proposal = ChangeProposal(
            proposal_id=new_id("cp"),
            goal_id=run.goal_id,
            sandbox_run_id=run.run_id,
            request_id=run.request_id,
            title=title,
            why=(request.reason if request else ""),
            what=(request.hypothesis if request else ""),
            expected_benefit=(request.expected_effect if request else ""),
            files_changed=len(run.files_changed),
            changed_files=list(run.files_changed),
            tests_summary=tests_summary or "not executed — sandbox disabled",
            diff_summary=run.diff_summary,
            baseline=dict(run.baseline or {}),
            new_failures=new_fails,
            fixed_failures=list((comparison or {}).get("tests", {}).get("fixed") or []),
            rollback_plan=request.rollback_plan if request else "discard isolated worktree; never merge",
            evidence=evid,
            risk=risk,
            confidence=float(confidence or 0.0),
            status=status,
            auto_apply=False,
            correlation_id=run.correlation_id,
        )
        self.proposals[proposal.proposal_id] = proposal
        run.proposal_id = proposal.proposal_id
        if status == ProposalStatus.READY_FOR_REVIEW.value:
            self._emit(BrainEventKind.CHANGE_READY_FOR_REVIEW, run, {"proposal_id": proposal.proposal_id})
        else:
            self._emit(BrainEventKind.CHANGE_PROPOSAL_CREATED, run, {"proposal_id": proposal.proposal_id})
        self._persist()
        return proposal

    def apply(self, proposal_id: str) -> Dict[str, Any]:
        """Never applies. Approve ≠ deploy."""
        return {
            "ok": False,
            "reason": "apply_hard_blocked",
            "proposal_id": proposal_id,
            "merge": False,
            "deploy": False,
            "push": False,
        }

    def approve(self, proposal_id: str) -> Dict[str, Any]:
        """Human ack only. Does not merge, push, or open a remote PR."""
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            return {"ok": False, "reason": "unknown_proposal"}
        if proposal.human_decision:
            return {"ok": True, "reason": "idempotent", "human_decision": proposal.human_decision, "merge": False}
        if proposal.status != ProposalStatus.READY_FOR_REVIEW.value:
            return {"ok": False, "reason": "not_ready_for_review", "status": proposal.status, "merge": False}
        proposal.human_decision = "approved"
        proposal.status = ProposalStatus.ACKNOWLEDGED.value
        self._persist()
        return {
            "ok": True,
            "reason": "acknowledged_not_merged",
            "merge": False,
            "push": False,
            "deploy": False,
            "pr": False,
        }

    def reject(self, proposal_id: str, *, reason: str, human_feedback: str = "") -> LearningRecord:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            return LearningRecord(
                learning_id=new_id("lrn"),
                kind="rejection",
                proposal_id=proposal_id,
                decision="rejected",
                result="unknown_proposal",
            )
        if proposal.human_decision == "rejected":
            for rec in self.learnings.values():
                if rec.proposal_id == proposal_id and rec.decision == "rejected":
                    return rec
        proposal.human_decision = "rejected"
        proposal.status = ProposalStatus.REJECTED.value
        run = self.runs.get(proposal.sandbox_run_id)
        if run is not None and can_transition(run.status, SandboxStatus.REJECTED.value):
            apply_transition(run, SandboxStatus.REJECTED.value)
            self._emit(BrainEventKind.CHANGE_REJECTED, run, {"reason": reason})
        rec = LearningRecord(
            learning_id=new_id("lrn"),
            kind="rejection",
            proposal_id=proposal_id,
            sandbox_run_id=proposal.sandbox_run_id,
            hypothesis=proposal.what,
            result=reason,
            decision="rejected",
            human_feedback=human_feedback,
            context=proposal.why,
            risk=proposal.risk,
            sandbox_success=False,
            production_success=False,
            correlation_id=proposal.correlation_id,
        )
        self.learnings[rec.learning_id] = rec
        self._persist()
        return rec

    def record_learning(
        self,
        proposal_id: str,
        *,
        hypothesis: str,
        result: str,
        decision: str,
        sandbox_success: bool = False,
    ) -> Dict[str, Any]:
        proposal = self.proposals.get(proposal_id)
        if proposal is not None and decision == "rejected":
            proposal.status = ProposalStatus.REJECTED.value
        rec = LearningRecord(
            learning_id=new_id("lrn"),
            kind="improvement" if sandbox_success else "rejection",
            proposal_id=proposal_id,
            hypothesis=hypothesis,
            result=result,
            decision=decision,
            sandbox_success=bool(sandbox_success),
            production_success=False,
        )
        self.learnings[rec.learning_id] = rec
        self._persist()
        return rec.to_dict()

    def cleanup(self, run_id: str) -> Dict[str, Any]:
        run = self.runs.get(run_id)
        if run is None:
            return {"ok": False, "reason": "unknown_run"}
        if run.status == SandboxStatus.CLEANED.value:
            return {"ok": True, "reason": "idempotent"}
        if run.worktree:
            removed = self.worktrees.remove(run.worktree, branch=run.branch)
            if not removed.get("ok") and Path(run.worktree).exists():
                return removed
        if not can_transition(run.status, SandboxStatus.CLEANED.value):
            run.status = SandboxStatus.CLEANED.value
            run.updated_at = utc_ts()
        else:
            apply_transition(run, SandboxStatus.CLEANED.value)
        run.cleanup_status = "cleaned"
        run.rollback_status = "discarded"
        self._emit(BrainEventKind.SANDBOX_CLEANED, run)
        self._persist()
        return {"ok": True, "reason": "cleaned"}

    def run_pipeline(
        self,
        request: ChangeRequest,
        *,
        after_tests: Optional[Mapping[str, Any]] = None,
        baseline_tests: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Full isolated pipeline. Stops at READY_FOR_REVIEW. Never merges."""
        if request.kind != TaskKind.CODE_CHANGE.value:
            return {"ok": False, "reason": "not_code_change_task"}
        if not self._may_execute():
            simulated = self.simulate(request)
            simulated["reason"] = "level3_not_enabled"
            return simulated
        fp = request_fingerprint(request)
        run = self.create_run(
            goal_id=request.goal_id,
            task_id=request.task_id,
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            fingerprint=fp,
        )
        self.requests[request.request_id] = request
        if run.status == SandboxStatus.READY_FOR_REVIEW.value and run.proposal_id:
            return {"ok": True, "reason": "idempotent", "run": run.to_dict(), "proposal_id": run.proposal_id}
        if run.status == SandboxStatus.INTERRUPTED.value:
            return {"ok": False, "reason": "interrupted_requires_new_decision", "run": run.to_dict()}
        if run.status == SandboxStatus.DISABLED.value:
            return {"ok": False, "reason": "sandbox_disabled", "run": run.to_dict()}
        self.capture_baseline(run, tests=baseline_tests)
        wt = self.create_worktree(run.run_id)
        if not wt.get("ok"):
            return {"ok": False, "reason": wt.get("reason"), "run": run.to_dict()}
        if baseline_tests is None and not run.baseline.get("failing") and not run.baseline.get("passed"):
            self.capture_baseline(run, tests={"failing": [], "passed": [], "collection_errors": []})
        applied = self.apply_changes(run, request)
        if not applied.get("ok"):
            return {"ok": False, "reason": applied.get("reason"), "run": run.to_dict()}
        diff = self.inspect_diff(run)
        if not diff.get("ok"):
            return {"ok": False, "reason": diff.get("reason"), "run": run.to_dict()}
        validation = self.validate(run, request, after_tests=after_tests)
        conf = confidence_from_validation(validation, files_ok=bool(run.files_changed))
        proposal = self.propose(
            run,
            title=request.reason[:120],
            tests_summary=f"new={len(validation.new_failures)} fixed={len(validation.fixed_failures)}",
            comparison={"tests": {
                "new": validation.new_failures,
                "fixed": validation.fixed_failures,
                "pre_existing": validation.preexisting_failures,
            }},
            risk=request.risk_level,
            confidence=conf,
            request=request,
            validation=validation,
        )
        return {
            "ok": proposal.status == ProposalStatus.READY_FOR_REVIEW.value,
            "run": run.to_dict(),
            "validation": validation.to_dict(),
            "proposal": proposal.to_dict(),
            "merge": False,
            "deploy": False,
            "push": False,
        }


def for_tests(
    *,
    git_root: Path,
    sandbox_parent: Path,
    state_dir: Path,
    budget: Optional[SandboxBudget] = None,
) -> SandboxExecutor:
    """Developer/test harness. Never used by BrainRuntime."""
    return SandboxExecutor(
        explicit_dev=True,
        git_root=git_root,
        sandbox_parent=sandbox_parent,
        state_dir=state_dir,
        root=git_root,
        budget=budget or SandboxBudget(
            max_commands=8,
            max_duration_s=60,
            max_files_changed=6,
            max_diff_bytes=50_000,
            command_timeout_s=20,
        ),
    )
