"""Independent, standard-library verification of bounded diagnostic evidence.

A green smoke check is not a full regression audit. Hashes attest the copied
source set, not process identity, test adequacy, or an operating-system sandbox.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

SOURCE_DIRS = frozenset({
    "core", "data", "features", "regime", "evolution", "backtest", "validation",
    "risk", "portfolio", "execution", "orchestrator", "dashboard", "governance",
    "strategies", "research", "arms", "signals", "monitoring", "lab", "scripts", "deploy",
})
EXCLUDED = frozenset({
    "tests", "test", "docs", "secrets", "credentials", "outputs", "output", "logs",
    "data_lake", "datasets", "data_cache", "cache", "caches", "node_modules",
    "venv", "raw", "provenance", "fixtures", "reports", "__pycache__", "scratch",
})
SUFFIXES = frozenset({".py", ".html", ".js", ".css", ".bat", ".ps1", ".sh", ".service"})
MAX_SOURCE_BYTES = 64 * 1024 * 1024
EVIDENCE_SCHEMA = "brain-operational-regression-evidence-v1"


def _safe_file(root, path):
    relative = path.relative_to(root)
    if path.is_symlink() or any((root.joinpath(*relative.parts[:i])).is_symlink()
                                or (hasattr(Path, "is_junction") and root.joinpath(*relative.parts[:i]).is_junction())
                                for i in range(1, len(relative.parts) + 1)):
        raise ValueError("source_link_refused")
    if not path.resolve().is_relative_to(root):
        raise ValueError("source_escape_refused")


def source_manifest(root, tests=()):
    """Inventory only application sources and explicitly reviewed test files."""
    root = Path(root).resolve()
    paths = []
    def visit(directory):
        for path in sorted(directory.iterdir()):
            name = path.name.lower()
            if name.startswith(".") or name in EXCLUDED:
                continue
            if path.is_symlink() or (hasattr(Path, "is_junction") and path.is_junction()):
                continue
            if path.is_dir():
                visit(path)
            elif (path.suffix.lower() in SUFFIXES and not name.startswith("test_")
                  and name not in {"conftest.py", "secrets.py", "credentials.py"}):
                paths.append(path)
    for path in sorted(root.iterdir()):
        if path.name.lower() in SOURCE_DIRS and path.is_dir():
            _safe_file(root, path)
            visit(path)
        elif path.is_file() and path.suffix.lower() in SUFFIXES and not path.name.startswith("."):
            if path.name.lower() not in {"secrets.py", "credentials.py", "conftest.py"} and not path.name.startswith("test_"):
                paths.append(path)
    for relative in tests:
        rel = Path(relative)
        if rel.is_absolute() or len(rel.parts) != 2 or rel.parts[0] != "tests" or rel.suffix != ".py":
            raise ValueError("invalid_reviewed_test_path")
        paths.append(root / rel)
    rows, total = [], 0
    for path in sorted(set(paths)):
        _safe_file(root, path)
        size = path.stat().st_size
        total += size
        if size > 8 * 1024 * 1024 or total > MAX_SOURCE_BYTES or len(rows) >= 4000:
            raise ValueError("source_budget_exceeded")
        raw = path.read_bytes()
        rows.append({"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)})
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    return {"sha256": hashlib.sha256(payload).hexdigest(), "files": rows, "bytes": total}


def read_evidence(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("evidence_too_large")
    result = json.loads(raw)
    json.dumps(result, allow_nan=False)
    if not isinstance(result, dict):
        raise ValueError("evidence_not_object")
    return result


def verify_regression(evidence, expected, *, current_hash, mirror_hash, now):
    """Recompute counts from case records; never trust a claimed PASSED/count."""
    def invalid(reason):
        return {"phase": "UNVERIFIED", "verified": False, "reason": reason,
                "passed": 0, "failed": 0, "skipped": 0, "collection_errors": 0}
    if not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA:
        return invalid("schema_mismatch")
    try:
        json.dumps(evidence, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        return invalid("invalid_json_values")
    for field in ("run_id", "source_hash", "worker_pid", "suite", "started_at"):
        if evidence.get(field) != expected.get(field):
            return invalid("identity_mismatch_" + field)
    if type(evidence.get("worker_pid")) is not int or evidence["worker_pid"] <= 0:
        return invalid("invalid_worker_pid")
    completed = evidence.get("completed_at")
    started = expected.get("started_at")
    if (type(completed) not in {int, float} or type(started) not in {int, float}
            or not math.isfinite(completed) or not started <= completed <= now + 5):
        return invalid("invalid_completion_time")
    if evidence.get("boundary") != "python-audit-hooks-not-os-sandbox":
        return invalid("guard_missing")
    if evidence.get("blocked_side_effects") != []:
        return invalid("side_effect_attempted_or_guard_missing")
    if current_hash != expected["source_hash"] or mirror_hash != expected["source_hash"]:
        return {**invalid("source_changed"), "phase": "STALE"}
    records = evidence.get("records")
    if not isinstance(records, list) or not records or len(records) > 15000:
        return invalid("missing_or_excess_records")
    suite = set(expected["suite"])
    calls, failures, skipped, collections, seen = set(), set(), set(), set(), set()
    phases = set()
    for record in records:
        if not isinstance(record, dict):
            return invalid("invalid_case_record")
        node, phase, outcome = (record.get(k) for k in ("nodeid", "phase", "outcome"))
        if not isinstance(node, str) or node.split("::")[0] not in suite or phase not in {"setup", "call", "teardown", "collect"} or outcome not in {"passed", "failed", "skipped"}:
            return invalid("unrequested_or_invalid_case")
        if (node, phase) in phases:
            return invalid("duplicate_case_phase")
        phases.add((node, phase))
        seen.add(node.split("::")[0])
        if phase == "collect" and outcome == "failed":
            collections.add(node)
        elif outcome == "failed":
            failures.add(node)
        elif outcome == "skipped":
            skipped.add(node)
        elif phase == "call":
            calls.add(node)
    passed = len(calls - failures - skipped)
    bad = bool(failures or collections)
    code = evidence.get("exit_code")
    if type(code) is not int or code not in {0, 1, 2, 3, 4, 5}:
        return invalid("invalid_exit_code")
    if not bad and code != 0:
        return invalid("exit_without_case_failure")
    if bad and code == 0:
        return invalid("contradictory_exit_code")
    if not bad and (seen != suite or passed == 0):
        return invalid("suite_not_exercised")
    if not bad and any((node, phase) not in phases for node in calls for phase in ("setup", "teardown")):
        return invalid("incomplete_case_lifecycle")
    return {"phase": "FAILED" if bad else "PASSED_WITH_SKIPS" if skipped else "PASSED",
            "verified": True, "reason": "case_evidence_verified", "passed": passed,
            "failed": len(failures), "skipped": len(skipped), "collection_errors": len(collections)}
