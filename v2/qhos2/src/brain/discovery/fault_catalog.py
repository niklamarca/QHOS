"""The fault matrix: 15 classes, two independent manifestations each.

Each class appears twice with deliberately *different* surfaces — a different
component, a different message shape, a different source — so a pass is evidence
about the class rather than about one string. One manifestation of each class is
``development`` (visible while the harness and detectors were built) and one is
``holdout`` (written last, against the already-frozen policy, and never used to
adjust anything). Reported separately, because a development number and a
holdout number are not the same kind of evidence.

Everything in a manifest except ``overlay`` is evaluator-only. The overlays
produce telemetry and nothing else; ``assert_no_manifest_leakage`` runs on each
one, so a detector cannot read the class name out of the snapshot it is asked to
diagnose.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .faults import (
    COHORT_DEVELOPMENT,
    COHORT_HOLDOUT,
    EXPECT_CANDIDATE,
    EXPECT_MULTIPLE,
    EXPECT_SUPPRESSED,
    FaultManifest,
    _errors,
    _module,
)

# Crypto pairs and futures symbols, so one root surfaces on many instances.
_PAIRS = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT", "DOT/USDT")
_FUTURES = ("ESM6", "NQM6", "CLM6", "GCM6")


def _ts(cycle: int) -> str:
    return f"2026-09-03 20:{cycle:02d}:11.004"


# ---------------------------------------------------------------------------
# F01 MISSING_DEPENDENCY
# ---------------------------------------------------------------------------

def _f01_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | core.crypto.feed | crypto fetch {pair} failed: "
        f"ModuleNotFoundError: No module named 'ccxt'"
        for pair in _PAIRS
    ])


def _f01_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Different dependency, different caller, different phrasing, and an
    # ImportError shape instead of ModuleNotFoundError.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | analytics.surface | vol surface build for {sym} aborted -- "
        f"ImportError: cannot import name 'interpolate' from 'scipy'"
        for sym in _FUTURES
    ])


# ---------------------------------------------------------------------------
# F02 STALE_RUNTIME_REVISION
# ---------------------------------------------------------------------------

def _f02_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    snap["attestation"] = {
        **snap.get("attestation", {}),
        "running_revision": "aaaa111122223333",
        "worktree_revision": "bbbb444455556666",
        "stale_runtime": True,
        "drift_reason": "brain_source_changed_on_disk",
    }
    snap["attestation_context"] = {
        **snap.get("attestation_context", {}),
        "deployment_context": "production",
    }
    return snap


def _f02_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # The process reports nothing at all about its revision: not stale, opaque.
    # This is the exact KI-03 shape from the previous phase.
    snap["attestation"] = None
    snap["attestation_context"] = {
        "deployment_context": "production",
        "required": True,
    }
    return snap


# ---------------------------------------------------------------------------
# F03 CONNECTION_REFUSED
# ---------------------------------------------------------------------------

def _f03_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | storage.timescale | connection attempt {n} failed: "
        f"ConnectionRefusedError: [Errno 111] Connection refused to timescale:5432"
        for n in range(1, 4)
    ])


def _f03_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # DNS rather than a refused TCP connect, on a different component.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | reporting.publisher | publish batch failed: "
        f"socket.gaierror: [Errno -2] Name or service not known: metrics-sink.internal",
        f"{_ts(cycle)} | ERROR | reporting.publisher | publish batch failed: "
        f"socket.gaierror: [Errno -2] Name or service not known: metrics-sink.internal",
    ])


# ---------------------------------------------------------------------------
# F04 REQUEST_TIMEOUT
# ---------------------------------------------------------------------------

def _f04_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | market_data.rest | snapshot request for {sym} exceeded budget: "
        f"ReadTimeoutError: HTTPSConnectionPool read timed out. (read timeout=15)"
        for sym in _FUTURES[:3]
    ])


def _f04_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # A slow path rather than a hard timeout: latency crosses the hint
    # threshold while the component keeps reporting healthy.
    snap["metrics"] = {"component": "parser", "latency_ms": 1850}
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | parser.batch | batch parse exceeded soft deadline: "
        f"asyncio.TimeoutError: waited 30.0s for parser worker lease",
    ])


# ---------------------------------------------------------------------------
# F05 DATA_SCHEMA_MISMATCH
# ---------------------------------------------------------------------------

def _f05_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | ingestion.normalizer | reject row: SchemaValidationError "
        f"code=SCHEMA_07 field='trade_ts' expected int64 got string",
        f"{_ts(cycle)} | ERROR | ingestion.normalizer | reject row: SchemaValidationError "
        f"code=SCHEMA_07 field='trade_ts' expected int64 got string",
    ])


def _f05_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # A column that disappeared upstream, surfaced by a different exception type
    # in a different component.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | storage.writer | parquet append rejected: "
        f"ColumnNotFoundError code=ARROW_12 missing column 'venue_seq' in incoming batch",
        f"{_ts(cycle)} | ERROR | storage.writer | parquet append rejected: "
        f"ColumnNotFoundError code=ARROW_12 missing column 'venue_seq' in incoming batch",
    ])


# ---------------------------------------------------------------------------
# F06 DATA_QUALITY_ANOMALY
# ---------------------------------------------------------------------------

def _f06_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | ingestion.quality | DataQualityError code=DQ_NAN "
        f"non-finite price in {sym} bar; row dropped"
        for sym in _FUTURES[:3]
    ])


def _f06_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Silent gaps rather than bad values, detected downstream.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | analytics.resampler | GapDetectedError code=DQ_GAP "
        f"expected 390 bars for {sym} session, received 214"
        for sym in _PAIRS[:2]
    ])


# ---------------------------------------------------------------------------
# F07 TELEMETRY_STALE_OR_MISLEADING
# ---------------------------------------------------------------------------

def _f07_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Every module reports pending with no lifecycle telemetry at all. This is
    # one fact about the status source, not one incident per module.
    for name in list(snap.get("registry") or {}):
        _module(snap, name, status="pending", healthy=None, uptime=None, error_count=0)
    return snap


def _f07_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # The registry answers, and answers the same thing forever: a frozen
    # snapshot whose uptime never advances across cycles.
    for name in list(snap.get("registry") or {}):
        _module(snap, name, status="ok", healthy=True, uptime=900.0, error_count=0)
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | core.registry | StaleTelemetryError code=REG_04 "
        f"module heartbeat timestamps unchanged for 3600s while cycle counter advanced",
    ])


# ---------------------------------------------------------------------------
# F08 HEALTH_DEGRADATION
# ---------------------------------------------------------------------------

def _f08_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _module(snap, "storage", healthy=False, status="unhealthy",
                   uptime=900.0, error_count=12)


def _f08_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _module(snap, "reporting", healthy=False, status="down",
                   uptime=120.0, error_count=3)


# ---------------------------------------------------------------------------
# F09 UNKNOWN_PENDING_WITH_REAL_INDEPENDENT_FAILURE
# ---------------------------------------------------------------------------

def _f09_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Pending with no lifecycle telemetry, which alone is not promotable, plus
    # independent error evidence naming the same component.
    _module(snap, "ingestion", status="pending", healthy=None, uptime=None, error_count=0)
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | core.ingestion | IngestionStalledError code=ING_09 "
        f"ingestion worker has not committed an offset in 900s",
    ])


def _f09_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Same shape on a different component, and the evidence arrives from a
    # downstream consumer rather than from the component itself.
    _module(snap, "parser", status="initializing", healthy=None, uptime=None, error_count=0)
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | execution.router | downstream dependency unavailable: "
        f"ParserUnavailableError code=EXE_22 parser returned no schema for 41 consecutive requests",
    ])


# ---------------------------------------------------------------------------
# F10 HIGH_FREQUENCY_BENIGN_WARNING
# ---------------------------------------------------------------------------

def _f10_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    snap["observations"] = list(snap.get("observations") or []) + [
        {
            "source": "stream", "component": "orchestrator", "kind": "info",
            "summary": f"scheduler tick {cycle}-{n} completed, no work queued",
            "severity": "low",
        }
        for n in range(40)
    ]
    return snap


def _f10_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Routine heartbeats at volume from a different source and component.
    snap["observations"] = list(snap.get("observations") or []) + [
        {
            "source": "stream", "component": "market_data", "kind": "heartbeat",
            "summary": "healthy",
            "severity": "low",
        }
        for _ in range(60)
    ]
    return snap


# ---------------------------------------------------------------------------
# F11 PROVIDER_DEGRADED
# ---------------------------------------------------------------------------

def _f11_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | market_data.vendor | ProviderDegradedError code=HTTP_503 "
        f"vendor returned 503 for {sym}; served stale cache"
        for sym in _FUTURES[:3]
    ])


def _f11_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | reporting.uploader | RateLimitError code=HTTP_429 "
        f"upstream throttled upload batch; retry-after 60s",
        f"{_ts(cycle)} | ERROR | reporting.uploader | RateLimitError code=HTTP_429 "
        f"upstream throttled upload batch; retry-after 60s",
    ])


# ---------------------------------------------------------------------------
# F12 QUEUE_BACKLOG
# ---------------------------------------------------------------------------

def _f12_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    snap["retries"] = {"count": 47, "component": "ingestion"}
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | core.queue | QueueBacklogError code=Q_17 "
        f"depth 9184 exceeds high-water mark 2000; oldest item age 640s",
    ])


def _f12_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    snap["hpc"] = {"kill_level": None, "queue": {"depth": 4200}}
    snap["retries"] = {"count": 31, "component": "orchestrator"}
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | hpc.scheduler | WorkerStarvationError code=Q_23 "
        f"0 of 8 workers leased a task in the last 300s while depth grew to 4200",
    ])


# ---------------------------------------------------------------------------
# F13 TEST_REGRESSION
# ---------------------------------------------------------------------------

def _f13_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | ci.pytest | AssertionError in tests/test_risk_limits.py::"
        f"test_notional_cap -- assert 1250000 <= 1000000",
    ])


def _f13_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | ci.pytest | CollectionError in tests/test_market_clock.py -- "
        f"ImportError while importing test module: No module named 'zoneinfo_backport'",
    ])


# ---------------------------------------------------------------------------
# F14 MULTI_COMPONENT_SHARED_ROOT
# ---------------------------------------------------------------------------

def _f14_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # One missing dependency, five different callers, five different log
    # prefixes and correlation ids. Expected: one root, many occurrences.
    callers = (
        "core.ingestion.feed", "analytics.factors", "reporting.render",
        "execution.sizing", "storage.compaction",
    )
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | {caller} | correlation_id=req-{cycle}{i:03d} "
        f"startup check failed: ModuleNotFoundError: No module named 'pyarrow'"
        for i, caller in enumerate(callers)
    ])


def _f14_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # One unreachable backing service, four different components describing it
    # in their own words.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | storage.writer | OperationalError code=PG_08006 "
        f"server closed the connection unexpectedly (host=pg-primary)",
        f"{_ts(cycle)} | ERROR | reporting.reader | OperationalError code=PG_08006 "
        f"server closed the connection unexpectedly (host=pg-primary)",
        f"{_ts(cycle)} | ERROR | analytics.loader | OperationalError code=PG_08006 "
        f"server closed the connection unexpectedly (host=pg-primary)",
        f"{_ts(cycle)} | ERROR | execution.audit | OperationalError code=PG_08006 "
        f"server closed the connection unexpectedly (host=pg-primary)",
    ])


# ---------------------------------------------------------------------------
# F15 MULTIPLE_SIMILAR_BUT_DISTINCT_ROOTS
# ---------------------------------------------------------------------------

def _f15_dev(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Near-identical phrasing, genuinely different causes. Collapsing these
    # would hide one real problem inside another.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | storage.writer | write failed: "
        f"DiskQuotaExceededError code=IO_28 no space left on device",
        f"{_ts(cycle)} | ERROR | storage.writer | write failed: "
        f"PermissionError code=IO_13 [Errno 13] Permission denied",
    ])


def _f15_holdout(snap: Dict[str, Any], cycle: int) -> Dict[str, Any]:
    # Two different missing dependencies producing the same sentence.
    return _errors(snap, [
        f"{_ts(cycle)} | ERROR | analytics.engine | feature build failed: "
        f"ModuleNotFoundError: No module named 'numba'",
        f"{_ts(cycle)} | ERROR | analytics.engine | feature build failed: "
        f"ModuleNotFoundError: No module named 'cupy'",
    ])


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------

def _manifests() -> List[FaultManifest]:
    return [
        FaultManifest(
            fault_id="F01a", fault_class="MISSING_DEPENDENCY",
            target_component="core.crypto.feed",
            injection_method="error_log overlay: ModuleNotFoundError per trading pair",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="DEPENDENCY", expected_root_match=("ccxt",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f01_dev,
            rationale="the previously confirmed real fault, reproduced as a control",
        ),
        FaultManifest(
            fault_id="F01b", fault_class="MISSING_DEPENDENCY",
            target_component="analytics.surface",
            injection_method="error_log overlay: ImportError on a submodule import",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="DEPENDENCY", expected_root_match=("scipy",),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f01_holdout,
            rationale="different dependency, different exception type, different caller",
        ),
        FaultManifest(
            fault_id="F02a", fault_class="STALE_RUNTIME_REVISION",
            target_component="brain_runtime",
            injection_method="attestation overlay: running != worktree, production context",
            expected_signals=("attestation",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="MESSAGE", expected_root_match=("revision",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f02_dev,
            rationale="the KI-03 miss, now with a signal source behind it",
        ),
        FaultManifest(
            fault_id="F02b", fault_class="STALE_RUNTIME_REVISION",
            target_component="brain_runtime",
            injection_method="attestation overlay: process reports no attestation at all",
            expected_signals=("attestation",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="MESSAGE", expected_root_match=("attestation", "revision"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f02_holdout,
            rationale="NOT_ATTESTABLE is the real production shape: opaque, not stale",
        ),
        FaultManifest(
            fault_id="F03a", fault_class="CONNECTION_REFUSED",
            target_component="storage.timescale",
            injection_method="error_log overlay: ConnectionRefusedError on repeated attempts",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("connectionrefused",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f03_dev,
        ),
        FaultManifest(
            fault_id="F03b", fault_class="CONNECTION_REFUSED",
            target_component="reporting.publisher",
            injection_method="error_log overlay: DNS resolution failure",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="MESSAGE", expected_root_match=("gaierror", "name or service"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f03_holdout,
            rationale="name resolution, not a refused connect: same family, different layer",
        ),
        FaultManifest(
            fault_id="F04a", fault_class="REQUEST_TIMEOUT",
            target_component="market_data.rest",
            injection_method="error_log overlay: ReadTimeoutError per symbol",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="EXCEPTION", expected_root_match=("readtimeout",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f04_dev,
        ),
        FaultManifest(
            fault_id="F04b", fault_class="REQUEST_TIMEOUT",
            target_component="parser.batch",
            injection_method="latency metric + asyncio.TimeoutError",
            expected_signals=("error_log", "metrics"), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="EXCEPTION", expected_root_match=("timeouterror",),
            symptom_tokens=("latency_ms",),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f04_holdout,
            rationale="a slow path that also shows up as latency, not only as an error",
        ),
        FaultManifest(
            fault_id="F05a", fault_class="DATA_SCHEMA_MISMATCH",
            target_component="ingestion.normalizer",
            injection_method="error_log overlay: SchemaValidationError with a field name",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("schema",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f05_dev,
        ),
        FaultManifest(
            fault_id="F05b", fault_class="DATA_SCHEMA_MISMATCH",
            target_component="storage.writer",
            injection_method="error_log overlay: missing column on parquet append",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("columnnotfound", "arrow"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f05_holdout,
        ),
        FaultManifest(
            fault_id="F06a", fault_class="DATA_QUALITY_ANOMALY",
            target_component="ingestion.quality",
            injection_method="error_log overlay: non-finite price rows",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("dataquality", "dq_nan"),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f06_dev,
        ),
        FaultManifest(
            fault_id="F06b", fault_class="DATA_QUALITY_ANOMALY",
            target_component="analytics.resampler",
            injection_method="error_log overlay: missing bars detected downstream",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("gapdetected", "dq_gap"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f06_holdout,
            rationale="absent data rather than bad data; nothing is malformed",
        ),
        FaultManifest(
            fault_id="F07a", fault_class="TELEMETRY_STALE_OR_MISLEADING",
            target_component="registry",
            injection_method="registry overlay: every module pending, uptime null",
            expected_signals=("registry",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="TELEMETRY", expected_root_match=("pending", "lifecycle"),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f07_dev,
            rationale="the second confirmed real fault, reproduced as a control",
        ),
        FaultManifest(
            fault_id="F07b", fault_class="TELEMETRY_STALE_OR_MISLEADING",
            target_component="core.registry",
            injection_method="registry reports healthy while heartbeats are frozen",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("staletelemetry", "reg_04"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f07_holdout,
            rationale="telemetry that lies in the reassuring direction: everything reads ok",
        ),
        FaultManifest(
            fault_id="F08a", fault_class="HEALTH_DEGRADATION",
            target_component="storage",
            injection_method="registry overlay: healthy=false with an error count",
            expected_signals=("registry",), expected_outcome=EXPECT_CANDIDATE,
            # The evidence token is the health status word, not the component
            # name: evaluator v2 refuses to credit a component-name match, and
            # a manifest that expects one is asking for the v1 defect back.
            expected_root_family="STATE", expected_root_match=("unhealthy",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f08_dev,
        ),
        FaultManifest(
            fault_id="F08b", fault_class="HEALTH_DEGRADATION",
            target_component="reporting",
            injection_method="registry overlay: status=down on a second component",
            expected_signals=("registry",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="STATE", expected_root_match=("down",),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f08_holdout,
        ),
        FaultManifest(
            fault_id="F09a", fault_class="UNKNOWN_PENDING_WITH_REAL_INDEPENDENT_FAILURE",
            target_component="ingestion",
            injection_method="pending with no lifecycle + independent stall error",
            expected_signals=("registry", "error_log"), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("ingestionstalled", "ing_09"),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f09_dev,
            rationale="suppression must not be absolute when independent evidence exists",
        ),
        FaultManifest(
            fault_id="F09b", fault_class="UNKNOWN_PENDING_WITH_REAL_INDEPENDENT_FAILURE",
            target_component="parser",
            injection_method="pending with no lifecycle + downstream consumer failure",
            expected_signals=("registry", "error_log"), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("parserunavailable", "exe_22"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f09_holdout,
            rationale="the evidence arrives from the consumer, not the pending component",
        ),
        FaultManifest(
            fault_id="F10a", fault_class="HIGH_FREQUENCY_BENIGN_WARNING",
            target_component="orchestrator",
            injection_method="40 benign scheduler ticks per cycle",
            expected_signals=("stream",), expected_outcome=EXPECT_SUPPRESSED,
            expected_root_family="", expected_candidates=0,
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f10_dev,
            rationale="volume must not become severity",
        ),
        FaultManifest(
            fault_id="F10b", fault_class="HIGH_FREQUENCY_BENIGN_WARNING",
            target_component="market_data",
            injection_method="60 heartbeats per cycle from a second component",
            expected_signals=("stream",), expected_outcome=EXPECT_SUPPRESSED,
            expected_root_family="", expected_candidates=0,
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f10_holdout,
        ),
        FaultManifest(
            fault_id="F11a", fault_class="PROVIDER_DEGRADED",
            target_component="market_data.vendor",
            injection_method="error_log overlay: vendor 503 with stale cache fallback",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("providerdegraded", "503"),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f11_dev,
        ),
        FaultManifest(
            fault_id="F11b", fault_class="PROVIDER_DEGRADED",
            target_component="reporting.uploader",
            injection_method="error_log overlay: upstream rate limiting",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("ratelimit", "429"),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f11_holdout,
        ),
        FaultManifest(
            fault_id="F12a", fault_class="QUEUE_BACKLOG",
            target_component="core.queue",
            injection_method="retry counter + backlog error",
            expected_signals=("error_log", "queue"), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("queuebacklog", "q_17"),
            symptom_tokens=("retries",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f12_dev,
        ),
        FaultManifest(
            fault_id="F12b", fault_class="QUEUE_BACKLOG",
            target_component="hpc.scheduler",
            injection_method="worker starvation while depth grows",
            expected_signals=("error_log", "queue"), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("workerstarvation", "q_23"),
            symptom_tokens=("retries",),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f12_holdout,
            rationale="the queue is not the thing failing; the consumers are",
        ),
        FaultManifest(
            fault_id="F13a", fault_class="TEST_REGRESSION",
            target_component="ci.pytest",
            injection_method="error_log overlay: assertion failure in a risk test",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="EXCEPTION", expected_root_match=("assertionerror",),
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f13_dev,
        ),
        FaultManifest(
            fault_id="F13b", fault_class="TEST_REGRESSION",
            target_component="ci.pytest",
            injection_method="error_log overlay: collection error from a missing module",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="DEPENDENCY", expected_root_match=("zoneinfo_backport",),
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f13_holdout,
            rationale="a test failure whose root is a dependency, not the test",
        ),
        FaultManifest(
            fault_id="F14a", fault_class="MULTI_COMPONENT_SHARED_ROOT",
            target_component="five callers",
            injection_method="one missing dependency across five components and correlation ids",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="DEPENDENCY", expected_root_match=("pyarrow",),
            expected_candidates=1,
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f14_dev,
            rationale="one root, many occurrences: must not become five incidents",
        ),
        FaultManifest(
            fault_id="F14b", fault_class="MULTI_COMPONENT_SHARED_ROOT",
            target_component="four consumers of pg-primary",
            injection_method="one unreachable database, four components reporting it",
            expected_signals=("error_log",), expected_outcome=EXPECT_CANDIDATE,
            expected_root_family="ERROR_CODE", expected_root_match=("operationalerror", "pg_08006"),
            expected_candidates=1,
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f14_holdout,
        ),
        FaultManifest(
            fault_id="F15a", fault_class="MULTIPLE_SIMILAR_BUT_DISTINCT_ROOTS",
            target_component="storage.writer",
            injection_method="disk quota and permission denied, same sentence stem",
            expected_signals=("error_log",), expected_outcome=EXPECT_MULTIPLE,
            expected_root_family="ERROR_CODE",
            expected_root_match=("diskquota", "permission"), expected_candidates=2,
            cleanup="overlay discarded", cohort=COHORT_DEVELOPMENT, overlay=_f15_dev,
            rationale="collapsing these would hide one real problem inside another",
        ),
        FaultManifest(
            fault_id="F15b", fault_class="MULTIPLE_SIMILAR_BUT_DISTINCT_ROOTS",
            target_component="analytics.engine",
            injection_method="two different missing dependencies, identical phrasing",
            expected_signals=("error_log",), expected_outcome=EXPECT_MULTIPLE,
            expected_root_family="DEPENDENCY",
            expected_root_match=("numba", "cupy"), expected_candidates=2,
            cleanup="overlay discarded", cohort=COHORT_HOLDOUT, overlay=_f15_holdout,
            rationale="the hardest over-dedup case: only the dependency name differs",
        ),
    ]


def fault_manifests(cohort: str = "") -> List[FaultManifest]:
    rows = _manifests()
    if cohort:
        rows = [m for m in rows if m.cohort == cohort]
    return rows


def catalogue_summary() -> Dict[str, Any]:
    rows = _manifests()
    by_class: Dict[str, List[str]] = {}
    for row in rows:
        by_class.setdefault(row.fault_class, []).append(row.fault_id)
    return {
        "kind": "FAULT_CATALOGUE",
        "classes": len(by_class),
        "manifests": len(rows),
        "development": sum(1 for r in rows if r.cohort == COHORT_DEVELOPMENT),
        "holdout": sum(1 for r in rows if r.cohort == COHORT_HOLDOUT),
        "by_class": {k: sorted(v) for k, v in sorted(by_class.items())},
    }
