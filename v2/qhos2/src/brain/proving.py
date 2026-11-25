"""Isolated proving-ground runner. Ground truth is evaluator-only.

The production Brain cycle never calls this module. Level 3 stays disabled.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .diagnose import BrainResult, FixtureEnv, MemoryHint, diagnose, leak_keys_present, strip_ground_truth
from .evaluate import (
    EvaluationReport,
    aggregate_readiness,
    evaluate_result,
    lacos_evaluation_payload,
)
from .executive import BrainOrchestrator
from .models import ChangeRequest, DiagnosticBudget, TaskKind
from .sandbox import SANDBOX_ENABLED, SandboxBudget, classify_path, for_tests


@dataclass
class GoldenScenario:
    scenario_id: str
    title: str
    description: str
    fixture: str
    observations: List[Dict[str, Any]]
    ground_truth: Dict[str, Any]
    risk_level: str = "low"
    allow_sandbox: bool = False
    fixture_dir: Optional[Path] = None

    def brain_observations(self) -> List[Dict[str, Any]]:
        return strip_ground_truth(list(self.observations))

    def context_payload(self) -> Dict[str, Any]:
        """What the Brain is allowed to see. No gabarito."""
        return {
            "observations": self.brain_observations(),
            "risk_level": self.risk_level,
        }


@dataclass
class ProvingSession:
    scenario_id: str
    result: BrainResult
    report: EvaluationReport
    sandbox: Dict[str, Any] = field(default_factory=dict)
    leaked: List[str] = field(default_factory=list)
    replay_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "result": self.result.to_dict(),
            "report": self.report.to_dict(),
            "sandbox": dict(self.sandbox),
            "leaked": list(self.leaked),
            "replay_key": self.replay_key,
            "level3_enabled": False,
            "sandbox_runtime_enabled": SANDBOX_ENABLED,
        }


class ProvingMemory:
    """Episode memory for proving-ground tests. Not a gabarito store."""

    def __init__(self) -> None:
        self.hints: List[MemoryHint] = []

    def remember(self, result: BrainResult, *, pattern: str, tokens: Sequence[str]) -> MemoryHint:
        hint = MemoryHint(
            pattern=pattern,
            previous_hypothesis=str((result.chosen or {}).get("statement") or ""),
            previous_target=str((result.chosen or {}).get("target") or ""),
            previous_result=result.stop,
            context_tokens=[str(t) for t in tokens if t],
            must_revalidate=True,
        )
        self.hints.append(hint)
        return hint

    def suggest(self) -> List[MemoryHint]:
        return list(self.hints)


def replay_key(observations: Sequence[Mapping[str, Any]], fixture: str = "") -> str:
    blob = json.dumps({"o": strip_ground_truth(list(observations)), "f": fixture}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _init_mini_repo(path: Path, files: Mapping[str, str]) -> None:
    import os
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Brain PG",
        "GIT_AUTHOR_EMAIL": "brain-pg@test",
        "GIT_COMMITTER_NAME": "Brain PG",
        "GIT_COMMITTER_EMAIL": "brain-pg@test",
    }

    def git(args: List[str]) -> None:
        subprocess.run(["git", *args], cwd=str(path), check=False, capture_output=True, text=True, env=env, timeout=20)

    git(["init", "-b", "main"])
    git(["config", "user.email", "brain-pg@test"])
    git(["config", "user.name", "Brain PG"])
    (path / "brain").mkdir(exist_ok=True)
    (path / "brain" / "hello.py").write_text("VALUE = 1\n", encoding="utf-8")
    (path / "tests" / "pg").mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        dest = path / "tests" / "pg" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    (path / "execution").mkdir(exist_ok=True)
    (path / "execution" / "engine.py").write_text("PROTECTED = True\n", encoding="utf-8")
    git(["add", "-A"])
    git(["commit", "-m", "pg-base"])


class ProvingRunner:
    def __init__(self, memory: Optional[ProvingMemory] = None) -> None:
        self.memory = memory or ProvingMemory()
        self.sessions: List[ProvingSession] = []

    def diagnose(
        self,
        scenario: GoldenScenario,
        *,
        budget: Optional[DiagnosticBudget] = None,
        use_memory: bool = False,
        mode: str = "deterministic_only",
        gateway: Any = None,
    ) -> BrainResult:
        obs = scenario.brain_observations()
        leaked = leak_keys_present(obs)
        if leaked:
            raise AssertionError(f"ground truth leaked into Brain observations: {leaked}")
        env = FixtureEnv(scenario.fixture_dir)
        hints = self.memory.suggest() if use_memory else []
        if mode and mode != "deterministic_only":
            from .reasoner import reason
            return reason(
                obs,
                env=env,
                memory=hints,
                budget=budget,
                orchestrator=BrainOrchestrator(),
                mode=mode,
                gateway=gateway,
            )
        return diagnose(
            obs,
            env=env,
            memory=hints,
            budget=budget,
            orchestrator=BrainOrchestrator(),
        )

    def evaluate(self, scenario: GoldenScenario, result: BrainResult, sandbox: Optional[Mapping[str, Any]] = None) -> EvaluationReport:
        return evaluate_result(
            result,
            scenario.ground_truth,
            scenario_id=scenario.scenario_id,
            sandbox=sandbox,
        )

    def maybe_sandbox(
        self,
        scenario: GoldenScenario,
        result: BrainResult,
        tmp: Path,
    ) -> Dict[str, Any]:
        """explicit_dev only. Production SANDBOX_ENABLED stays False."""
        if not scenario.allow_sandbox or not result.change_request:
            return {"invoked": False, "reason": "not_requested", "enabled": SANDBOX_ENABLED}
        if SANDBOX_ENABLED:
            return {"invoked": False, "reason": "refusing_enabled_runtime", "enabled": True}
        files: Dict[str, str] = {}
        if scenario.fixture_dir and scenario.fixture_dir.exists():
            for path in scenario.fixture_dir.rglob("*"):
                if not path.is_file() or path.suffix not in {".py", ".json", ".md", ".txt"}:
                    continue
                if "__pycache__" in path.parts:
                    continue
                files[str(path.relative_to(scenario.fixture_dir)).replace("\\", "/")] = path.read_text(encoding="utf-8")
        repo = tmp / "repo"
        _init_mini_repo(repo, files)
        ex = for_tests(
            git_root=repo,
            sandbox_parent=tmp / "sandboxes",
            state_dir=tmp / "state",
            budget=SandboxBudget(
                max_commands=8,
                max_duration_s=45,
                max_files_changed=4,
                max_diff_bytes=20_000,
                command_timeout_s=20,
            ),
        )
        raw = result.change_request
        patches = dict(raw.get("patches") or {})
        # remap fixture-relative patches into tests/pg/
        remapped: Dict[str, str] = {}
        for rel, content in list(patches.items()) + list(result.patches.items()):
            name = rel.split("/")[-1]
            dest = f"tests/pg/{name}" if not rel.startswith("tests/") else rel
            if classify_path(dest) == "PROTECTED":
                return {
                    "invoked": True,
                    "ok": False,
                    "reason": "protected_path",
                    "protected_hit": True,
                    "enabled": SANDBOX_ENABLED,
                    "files_changed": [],
                }
            remapped[dest] = content
        req = ChangeRequest(
            request_id=str(raw.get("request_id") or "cr_pg"),
            reason=str(raw.get("reason") or "pg"),
            hypothesis=str(raw.get("hypothesis") or ""),
            goal_id=str(raw.get("goal_id") or ""),
            task_id=str(raw.get("task_id") or ""),
            incident_id=str(raw.get("incident_id") or ""),
            target_files=list(remapped),
            allowed_paths=["tests/pg/", "tests/"],
            patches=remapped,
            correlation_id=str(raw.get("correlation_id") or ""),
            kind=TaskKind.CODE_CHANGE.value,
            validation_plan=["syntax"],
        )
        orch = BrainOrchestrator()
        goal = orch.create_goal("pg sandbox", "explicit_dev")
        task = orch.create_task(goal.goal_id, "Propose minimal patch", action="propose_patch", kind=TaskKind.CODE_CHANGE.value)
        if task is None:
            return {"invoked": True, "ok": False, "reason": "no_task", "enabled": SANDBOX_ENABLED}
        req.task_id = task.task_id
        req.goal_id = goal.goal_id
        out = orch.request_sandbox(task.task_id, ex, req)
        status = "unknown"
        run = None
        if ex.runs:
            run = list(ex.runs.values())[-1]
            status = run.status
        proposal_status = ""
        if ex.proposals:
            proposal_status = list(ex.proposals.values())[-1].status
        return {
            "invoked": True,
            "ok": bool(out.get("ok")),
            "reason": out.get("reason") or out.get("status") or status,
            "enabled": SANDBOX_ENABLED,
            "explicit_dev": True,
            "run_status": status,
            "proposal_status": proposal_status,
            "files_changed": list(getattr(run, "files_changed", []) or []) if run else [],
            "merged": False,
            "pushed": False,
            "deployed": False,
            "new_failures": [],
            "protected_hit": False,
            "pipeline": out,
        }

    def run(
        self,
        scenario: GoldenScenario,
        *,
        budget: Optional[DiagnosticBudget] = None,
        use_memory: bool = False,
        mode: str = "deterministic_only",
        gateway: Any = None,
        sandbox_tmp: Optional[Path] = None,
        remember: bool = False,
        pattern: str = "",
        tokens: Optional[Sequence[str]] = None,
    ) -> ProvingSession:
        result = self.diagnose(scenario, budget=budget, use_memory=use_memory, mode=mode, gateway=gateway)
        sandbox: Dict[str, Any] = {"invoked": False, "enabled": SANDBOX_ENABLED}
        if sandbox_tmp is not None and scenario.allow_sandbox:
            sandbox = self.maybe_sandbox(scenario, result, sandbox_tmp)
        report = self.evaluate(scenario, result, sandbox if sandbox.get("invoked") else None)
        if remember:
            self.memory.remember(result, pattern=pattern or scenario.fixture, tokens=tokens or [])
        session = ProvingSession(
            scenario_id=scenario.scenario_id,
            result=result,
            report=report,
            sandbox=sandbox,
            leaked=leak_keys_present(result.replay_inputs),
            replay_key=replay_key(scenario.observations, scenario.fixture),
        )
        self.sessions.append(session)
        return session

    def replay(self, scenario: GoldenScenario) -> ProvingSession:
        return self.run(scenario)

    def readiness(self) -> Dict[str, Any]:
        return aggregate_readiness([s.report for s in self.sessions])

    def lacos_payload(self, session: Optional[ProvingSession] = None) -> Dict[str, Any]:
        row = session or (self.sessions[-1] if self.sessions else None)
        if row is None:
            return lacos_evaluation_payload()
        return lacos_evaluation_payload(
            scenario_id=row.scenario_id,
            result=row.result,
            report=row.report,
            sandbox_status=str((row.sandbox or {}).get("run_status") or "not_invoked"),
        )
