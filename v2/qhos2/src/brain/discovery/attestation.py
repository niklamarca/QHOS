"""Runtime attestation as a Discovery signal source.

The previous phase measured known-issue recall at 2/3. The miss (KI-03, "the
running process loaded an older Brain revision") was categorised ``NO_SIGNAL``:
``brain/attest.py`` could already tell the difference between a current, a
stale and an unattestable process, but nothing turned that verdict into an
``Observation``, so the engine never saw it. Attestation was a reporting
surface, not a signal.

This module is the missing adapter. It converts an attestation verdict into
normalized observation rows on source ``attestation``.

Context decides severity, not the drift itself. A developer editing files under
a running dev server is the expected state of a dev box and must not read as an
incident; a production process whose loaded revision does not match the revision
it was deployed at is a real finding. The same drift therefore carries different
weight depending on ``deployment_context``, and the reason is always stated in
the observation text rather than hidden in a severity number.

Read-only. Nothing here restarts, reloads or mutates a process.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

ATTESTATION_SOURCE = "attestation"
ATTESTATION_COMPONENT = "brain_runtime"

ATTESTATION_MATCH = "RUNTIME_REVISION_MATCH"
ATTESTATION_STALE = "STALE_RUNTIME"
ATTESTATION_UNKNOWN = "NOT_ATTESTABLE"
ATTESTATION_CONFIG_MISMATCH = "CONFIG_REVISION_MISMATCH"
ATTESTATION_ROUTE_MISMATCH = "BRAIN_ROUTE_SCHEMA_MISMATCH"

ATTESTATION_KINDS = (
    ATTESTATION_MATCH,
    ATTESTATION_STALE,
    ATTESTATION_UNKNOWN,
    ATTESTATION_CONFIG_MISMATCH,
    ATTESTATION_ROUTE_MISMATCH,
)

# Where the attested process runs. A dev box is expected to drift; a deployed
# process is not.
CONTEXT_DEV = "dev"
CONTEXT_PRODUCTION = "production"
CONTEXTS = (CONTEXT_DEV, CONTEXT_PRODUCTION)


def _severity(kind: str, context: str) -> str:
    if kind == ATTESTATION_MATCH:
        return "low"
    if kind == ATTESTATION_ROUTE_MISMATCH:
        # A route schema mismatch breaks the contract every consumer reads,
        # in any context.
        return "high"
    if kind == ATTESTATION_CONFIG_MISMATCH:
        return "high" if context == CONTEXT_PRODUCTION else "medium"
    if kind == ATTESTATION_STALE:
        # Editing source under a running dev server is the normal state of a dev
        # box. The same drift on a deployed process means it is not running the
        # code it was deployed at.
        return "high" if context == CONTEXT_PRODUCTION else "low"
    if kind == ATTESTATION_UNKNOWN:
        # "We cannot tell" deserves the same medium attention anywhere: the
        # process is opaque about what it loaded.
        return "medium"
    return "low"


def _informational(kind: str, context: str) -> bool:
    """Whether this row is context-expected rather than a finding."""
    if kind == ATTESTATION_MATCH:
        return True
    return kind == ATTESTATION_STALE and context != CONTEXT_PRODUCTION


def attestation_rows(
    attestation: Optional[Mapping[str, Any]],
    *,
    context: str = CONTEXT_DEV,
    expected_revision: str = "",
    expected_config_hash: str = "",
    expected_route_schema: str = "",
) -> List[Dict[str, Any]]:
    """Observation rows for one attestation verdict.

    The ``expected_*`` arguments describe what the deployment believes it
    shipped. They are supplied by whoever runs the process; when absent the
    corresponding check is skipped rather than assumed to pass.
    """
    ctx = str(context or CONTEXT_DEV).strip().lower()
    if ctx not in CONTEXTS:
        ctx = CONTEXT_DEV
    out: List[Dict[str, Any]] = []

    def emit(kind: str, summary: str, **extra: Any) -> None:
        row: Dict[str, Any] = {
            "source": ATTESTATION_SOURCE,
            "component": ATTESTATION_COMPONENT,
            "kind": kind,
            "summary": summary[:240],
            "severity": _severity(kind, ctx),
            "deployment_context": ctx,
            "informational": _informational(kind, ctx),
        }
        row.update(extra)
        out.append(row)

    if not attestation:
        # A process that reports nothing about its own revision is exactly the
        # KI-03 shape: not "fine", just unmeasured.
        emit(
            ATTESTATION_UNKNOWN,
            "runtime does not report a revision attestation "
            f"(context={ctx}); the loaded code revision is unknown",
            reason="no_attestation_payload",
        )
        return out

    running = str(attestation.get("running_revision") or "")
    worktree = str(attestation.get("worktree_revision") or "")
    if not running:
        emit(
            ATTESTATION_UNKNOWN,
            f"runtime attestation present but carries no running revision (context={ctx})",
            reason="attestation_missing_running_revision",
        )
        return out

    drift_reason = str(attestation.get("drift_reason") or "")
    if bool(attestation.get("stale_runtime")):
        emit(
            ATTESTATION_STALE,
            f"running revision {running} does not match worktree revision "
            f"{worktree} ({drift_reason or 'unspecified drift'}); context={ctx}",
            running_revision=running,
            worktree_revision=worktree,
            drift_reason=drift_reason,
        )
    else:
        emit(
            ATTESTATION_MATCH,
            f"running revision {running} matches worktree; context={ctx}",
            running_revision=running,
        )

    # A deployed process is additionally checked against what the deployment
    # believes it shipped. Worktree drift and deployment drift are different
    # questions, and a process can fail either one alone.
    if expected_revision and running != str(expected_revision):
        emit(
            ATTESTATION_STALE,
            f"running revision {running} does not match expected deployment "
            f"revision {str(expected_revision)[:16]}; context={ctx}",
            running_revision=running,
            expected_revision=str(expected_revision)[:16],
            drift_reason="running_behind_expected_deployment",
        )

    policy = (attestation.get("running") or {}).get("discovery_policy") or {}
    config_hash = str(policy.get("config_hash") or "")
    if expected_config_hash and config_hash and config_hash != str(expected_config_hash):
        emit(
            ATTESTATION_CONFIG_MISMATCH,
            f"discovery policy config hash {config_hash} does not match expected "
            f"{str(expected_config_hash)[:16]}; the scoring policy in force is not the one recorded",
            config_hash=config_hash,
            expected_config_hash=str(expected_config_hash)[:16],
        )

    route = str((attestation.get("running") or {}).get("route_schema_version") or "")
    if expected_route_schema and route and route != str(expected_route_schema):
        emit(
            ATTESTATION_ROUTE_MISMATCH,
            f"route schema {route} does not match expected {str(expected_route_schema)[:24]}; "
            "consumers are reading a different contract than the one served",
            route_schema_version=route,
            expected_route_schema=str(expected_route_schema)[:24],
        )
    return out


def attestation_coverage(observations: Any) -> Dict[str, Any]:
    """Whether a run actually carried attestation signal, and what it said.

    Reported so an absent source is visible as absent instead of reading as a
    clean result.
    """
    def _get(row: Any, key: str) -> str:
        if hasattr(row, "get"):
            return str(row.get(key) or "")
        return str(getattr(row, key, "") or "")

    rows = [r for r in (observations or []) if _get(r, "source") == ATTESTATION_SOURCE]
    by_kind: Dict[str, int] = {}
    for row in rows:
        kind = _get(row, "kind")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "kind": "ATTESTATION_COVERAGE",
        "source_present": bool(rows),
        "observations": len(rows),
        "by_kind": dict(sorted(by_kind.items())),
        "attested": any(k in by_kind for k in (ATTESTATION_MATCH, ATTESTATION_STALE)),
        "unattestable": by_kind.get(ATTESTATION_UNKNOWN, 0) > 0,
    }
