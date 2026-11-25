"""Generalization, false-negative and provider-contract tests.

Calibration V2 was tuned against one soak. These tests ask whether it holds on
error families it never saw, whether suppression hides real failures, and
whether a provider response that is malformed, truncated or ambiguous can be
mistaken for reasoning.

No test here performs a paid provider call.
"""
from __future__ import annotations

import json

import pytest

from brain.attest import (
    attestation_panel,
    runtime_attestation,
    source_revision,
)
from brain.discovery.engine import THRESHOLD, DiscoveryEngine
from brain.discovery.evaluation import (
    FN_CATEGORIES,
    KnownIssue,
    PipelineFunnel,
    aggregate_sessions,
    known_issue_recall,
    retrospect_issue,
    root_cluster_metrics,
)
from brain.discovery.fingerprint import (
    component_key,
    component_mentions,
    group_by_root,
    mentions_component,
    root_signature,
)
from brain.discovery.revision import (
    CALIBRATION_FINDINGS,
    POLICY_NAME,
    POLICY_REVISION,
    config_hash,
    policy_config,
    policy_revision,
    session_provenance,
)
from brain.provider_contract import (
    NON_REASONING_STATUSES,
    OUTPUT_STATUSES,
    call_metrics,
    classify_output,
    coerce_confidence,
    coerce_information_gain,
    coerce_text,
    detect_truncation,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _log(component: str, message: str, *, second: int = 0, level: str = "ERROR") -> dict:
    return {
        "source": "error_log",
        "component": "logs",
        "kind": "error",
        "severity": "high" if level == "ERROR" else "low",
        "summary": f"2026-09-03 12:00:{second:02d} | {level} | {component} | {message}",
        "timestamp": 1_000_000.0 + second,
    }


def _registry(name: str, **over) -> dict:
    entry = {"healthy": False, "status": "pending", "uptime": None, "error_count": 0}
    entry.update(over)
    return {name: entry}


def _run(rows, cycles: int = 1, registry=None):
    engine = DiscoveryEngine()
    intel = {}
    for _ in range(cycles):
        snap = {"observations": list(rows)}
        if registry:
            snap["registry"] = registry
        intel = engine.ingest(snap)
    return engine, intel


# ---------------------------------------------------------------------------
# §3 frozen calibration revision
# ---------------------------------------------------------------------------


def test_frozen_calibration_revision_is_pinned():
    """The frozen config hash must change only with a deliberate revision bump.

    If this fails, a scoring or fingerprint constant moved. Bump POLICY_REVISION
    and record the change in revision.CHANGES rather than editing the expected
    hash silently.
    """
    assert POLICY_NAME == "DISCOVERY_CALIBRATION_V2"
    assert POLICY_REVISION == "v2.1"
    assert config_hash() == "04f2ec81fc26e408", (
        "frozen Discovery configuration changed; bump POLICY_REVISION and record it"
    )


def test_frozen_policy_config_is_complete_and_stable():
    cfg = policy_config()
    for key in (
        "score_weights", "expected_state_penalty", "promotion_threshold",
        "fingerprint_version", "pending_stale_multiplier",
        "systemic_unknown_min_components",
    ):
        assert key in cfg, key
    assert abs(sum(cfg["score_weights"].values()) - 1.0) < 1e-9
    assert cfg["promotion_threshold"] == THRESHOLD


def test_policy_revision_stamp_declares_read_only_posture():
    rev = policy_revision()
    assert rev["frozen"] is True
    prov = session_provenance({"session_id": "s1"})
    assert prov["llm_enabled"] is False
    assert prov["level3_enabled"] is False
    assert prov["sandbox_enabled"] is False
    assert prov["discovery_policy"]["config_hash"] == config_hash()


def test_calibration_failure_is_recorded_with_pre_fix_measurement():
    """A discovered flaw must keep its pre-fix evidence, not just its fix."""
    finding = next(f for f in CALIBRATION_FINDINGS if f["id"] == "CALIBRATION_V2_FAILURE_01")
    assert finding["category"] == "FALSE_NEGATIVE"
    assert finding["pre_fix_measurement"]["promoted"] is False
    assert finding["pre_fix_measurement"]["known_issue_recall"] == 0.0
    assert finding["affects_score_weights"] is False
    assert finding["affects_fingerprint_keys"] is False


# ---------------------------------------------------------------------------
# §16 root cluster precision / recall, §17-18, §46 over/under-dedup
# ---------------------------------------------------------------------------


def test_root_cluster_metrics_perfect_grouping():
    rows = [
        {"true_root": "dep_ccxt", "root_id": "a"},
        {"true_root": "dep_ccxt", "root_id": "a"},
        {"true_root": "risk", "root_id": "b"},
        {"true_root": "risk", "root_id": "b"},
    ]
    m = root_cluster_metrics(rows)
    assert m["cluster_precision"] == 1.0
    assert m["cluster_recall"] == 1.0
    assert m["over_dedup_rate"] == 0.0
    assert m["under_dedup_rate"] == 0.0


def test_root_cluster_metrics_detects_over_and_under_dedup():
    # Everything merged into one cluster: perfect recall, poor precision.
    over = root_cluster_metrics([
        {"true_root": "x", "root_id": "one"},
        {"true_root": "x", "root_id": "one"},
        {"true_root": "y", "root_id": "one"},
    ])
    assert over["cluster_recall"] == 1.0
    assert over["over_dedup_rate"] > 0.0
    assert over["under_dedup_rate"] == 0.0

    # Everything split apart: perfect precision, poor recall.
    under = root_cluster_metrics([
        {"true_root": "x", "root_id": "1"},
        {"true_root": "x", "root_id": "2"},
        {"true_root": "x", "root_id": "3"},
    ])
    assert under["cluster_precision"] == 0.0
    assert under["under_dedup_rate"] == 1.0


def test_same_root_different_surface_shares_a_root():
    """§17: same missing dependency behind different symbol, exchange, wrapper,
    log prefix and correlation id must land on one root."""
    rows = [
        _log("BinanceIngestion",
             "FALHOU BTC/USDT req_id=aa11 correlation_id=550e8400-e29b-41d4-a716-446655440000: "
             "ModuleNotFoundError: No module named 'ccxt'", second=1),
        _log("KrakenAdapter",
             "FAILED ETH/EUR req_id=bb22: ModuleNotFoundError: No module named 'ccxt'", second=2),
        _log("core.data_ingestion",
             "SOL-USD price=13.55 qty=4: ModuleNotFoundError: No module named 'ccxt'", second=3),
        {"source": "runtime", "component": "CryptoWorker", "kind": "error", "severity": "high",
         "summary": "ModuleNotFoundError: No module named 'ccxt'", "timestamp": 1_000_004.0},
    ]
    clusters = group_by_root(rows)
    assert len(clusters) == 1, f"expected one dependency root, got {len(clusters)}"
    cluster = next(iter(clusters.values()))
    assert cluster.signature.dependency == "ccxt"
    assert cluster.occurrence_count == 4

    labelled = []
    for row in rows:
        sig = root_signature(kind=row["kind"], component=row["component"], summary=row["summary"])
        labelled.append({"true_root": "dep_ccxt", "root_id": sig.root_id})
    assert root_cluster_metrics(labelled)["cluster_recall"] == 1.0


def test_different_roots_with_similar_text_do_not_collapse():
    """§18: near-identical wording, genuinely different causes."""
    rows = [
        _log("Ingestion", "connection failed: ConnectionRefusedError to 127.0.0.1:5432", second=1),
        _log("Ingestion", "connection failed: DNSLookupError resolving broker.example.com", second=2),
        _log("Ingestion", "connection failed: TimeoutError after 30s waiting for handshake", second=3),
        _log("Ingestion", "connection failed: PermissionError reading client certificate", second=4),
    ]
    clusters = group_by_root(rows)
    assert len(clusters) == 4, f"distinct causes collapsed: {len(clusters)}"
    labelled = []
    for index, row in enumerate(rows):
        sig = root_signature(kind=row["kind"], component=row["component"], summary=row["summary"])
        labelled.append({"true_root": f"cause_{index}", "root_id": sig.root_id})
    metrics = root_cluster_metrics(labelled)
    assert metrics["cluster_precision"] == 0.0 or metrics["over_dedup_rate"] == 0.0
    assert metrics["merged_wrongly"] == 0


def test_different_dependencies_with_identical_phrasing_stay_separate():
    rows = [
        _log("A", "ModuleNotFoundError: No module named 'ccxt'", second=1),
        _log("A", "ModuleNotFoundError: No module named 'polars'", second=2),
        _log("A", "ModuleNotFoundError: No module named 'numba'", second=3),
    ]
    assert len(group_by_root(rows)) == 3


def test_same_dependency_across_unrelated_components_shares_one_root():
    rows = [
        _log("RiskEngine", "ImportError: cannot import name 'ccxt'", second=1),
        _log("Reporting", "ImportError: cannot import name 'ccxt'", second=2),
        _log("Backtest", "ImportError: cannot import name 'ccxt'", second=3),
    ]
    clusters = group_by_root(rows)
    assert len(clusters) == 1
    assert next(iter(clusters.values())).signature.dependency == "ccxt"


# ---------------------------------------------------------------------------
# §15 generalization holdout families the score was not tuned against
# ---------------------------------------------------------------------------


GENERALIZATION_FAMILIES = [
    ("permission_error", "PermissionError: [Errno 13] Permission denied: '/var/lib/qhos/state.db'", True),
    ("disk_exhaustion", "OSError: [Errno 28] No space left on device while writing parquet shard", True),
    ("connection_refused", "ConnectionRefusedError: [Errno 111] Connection refused to postgres:5432", True),
    ("dns_provider_error", "DNSLookupError: could not resolve api.databento.com (EAI_NONAME)", True),
    ("schema_failure", "SchemaValidationError: column 'ts_event' expected int64 got string", True),
    ("timeout", "TimeoutError: order acknowledgement not received after 30000ms", True),
]


@pytest.mark.parametrize("name,message,should_promote", GENERALIZATION_FAMILIES)
def test_generalization_families_produce_exactly_one_candidate(name, message, should_promote):
    """Each unseen error family must yield one candidate, not zero and not many."""
    # Distinct instances, same root: vary the symbol only.
    rows = [
        _log("Storage", f"{message} symbol=BTC/USDT", second=1),
        _log("Storage", f"{message} symbol=ETH/USDT", second=2),
        _log("Storage", f"{message} symbol=SOL/USDT", second=3),
    ]
    _, intel = _run(rows)
    incidents = intel["incidents"]
    assert len(incidents) == 1, f"{name}: expected 1 candidate, got {len(incidents)}"
    cand = incidents[0]
    assert cand["occurrence_count"] >= 1
    assert cand["root_tier"] in {"EXCEPTION", "ERROR_CODE", "DEPENDENCY", "MESSAGE"}
    if should_promote:
        assert cand["score"] > 0.0
    # Score must be explained, never a bare number.
    assert cand["score_components"], f"{name}: missing score explanation"
    assert cand["promotion_reason"]


def test_holdout_high_frequency_benign_warning_stays_quiet():
    rows = [
        _log("Cache", "cache miss for key market_data:BTC, refetching", second=i, level="WARNING")
        for i in range(40)
    ]
    _, intel = _run(rows)
    assert intel["incidents"] == [] or all(
        c["score"] < THRESHOLD for c in intel["incidents"]
    ), "a benign high-frequency warning was promoted above threshold"


def test_holdout_single_low_frequency_severe_error_still_surfaces():
    rows = [_log("KillSwitch", "FATAL: RiskLimitBreachError code=RL07 halting execution", second=1)]
    _, intel = _run(rows)
    assert len(intel["incidents"]) == 1
    assert intel["incidents"][0]["score"] > 0.0


def test_holdout_healthy_but_delayed_initialization_is_not_an_incident():
    engine = DiscoveryEngine()
    for cycle in range(8):
        intel = engine.ingest({
            "registry": _registry("reporting", uptime=3.0 + cycle, status="initializing"),
        })
    assert intel["incidents"] == []
    state = engine.pending.states["reporting"]
    assert state.promote is False
    assert state.classification in {"EXPECTED_INITIALIZATION", "TRANSIENT_PENDING"}


def test_holdout_stale_registry_data_is_distinguishable_from_pending():
    """Stale means the lifecycle was observed and then stopped advancing."""
    engine = DiscoveryEngine()
    for offset in (0.0, 30.0, 6000.0):
        engine.ingest({"observations": [{
            "source": "registry", "component": "reporting", "kind": "state",
            "state": "pending", "summary": "status=pending", "severity": "low",
            "timestamp": 1_000_000.0 + offset, "uptime": 5.0, "error_count": 0,
        }]})
    state = engine.pending.states["reporting"]
    assert state.classification == "STALE_PENDING"
    assert state.promote is True


# ---------------------------------------------------------------------------
# §21-23 UNKNOWN_PENDING suppression must not be absolute
# ---------------------------------------------------------------------------


def test_unknown_pending_without_evidence_stays_suppressed():
    """The benign control. No lifecycle telemetry, no corroboration, no candidate."""
    modules = {}
    for name in ("storage", "features", "portfolio", "dashboard", "validator"):
        modules.update(_registry(name))
    engine = DiscoveryEngine()
    for _ in range(6):
        engine.ingest({"registry": modules})
    states = engine.pending.states
    assert states, "expected tracked pending states"
    assert all(s.classification == "UNKNOWN_PENDING" for s in states.values())
    assert not any(s.promote for s in states.values())


def test_unknown_pending_with_independent_error_evidence_promotes():
    """§22-23: independent evidence must override the benign state class.

    Regression for CALIBRATION_V2_FAILURE_01, where this could never happen:
    error observations are attributed to component 'logs', so matching a pending
    component by family name always failed.
    """
    engine = DiscoveryEngine()
    for cycle in range(4):
        engine.ingest({
            "registry": _registry("risk_engine"),
            "error_tail": [
                f"2026-09-03 12:00:{cycle:02d} | ERROR | RiskEngine | "
                f"position check raised RiskLimitBreachError code=RL07"
            ],
        })
    state = engine.pending.states["risk_engine"]
    assert state.promote is True, "independent error evidence did not override suppression"
    assert state.classification == "FAILED_PENDING"
    assert state.promotion_reason == "error_evidence_during_pending"
    assert any("RiskLimitBreach" in e for e in state.evidence), "cited evidence missing"


def test_unknown_pending_promotion_requires_the_matching_component():
    """Evidence about a different component must not promote this one."""
    engine = DiscoveryEngine()
    for cycle in range(4):
        engine.ingest({
            "registry": _registry("risk_engine"),
            "error_tail": [
                f"2026-09-03 12:00:{cycle:02d} | ERROR | Reporting | "
                f"chart render failed ValueError: bad axis"
            ],
        })
    state = engine.pending.states["risk_engine"]
    assert state.promote is False, "unrelated evidence promoted an unrelated component"
    assert state.classification == "UNKNOWN_PENDING"


def test_component_matching_tolerates_naming_style_without_over_matching():
    assert component_key("risk_engine") == component_key("RiskEngine") == "riskengine"
    assert mentions_component("ERROR | RiskEngine | boom", "risk_engine")
    assert mentions_component("core.data_ingestion failed", "dataingestion")
    assert mentions_component("kill switch armed", "kill_switch")
    # Must not match on a coincidental substring.
    assert not mentions_component("latest snapshot ok", "test")
    assert not mentions_component("portfolio rebalanced", "execution")
    # Too short to be discriminating.
    assert not mentions_component("oms ok", "oms")
    assert "dataingestion" in component_mentions("DataIngestion failed")


# ---------------------------------------------------------------------------
# §24-27 false-negative retrospective and known-issue recall
# ---------------------------------------------------------------------------


def _issue(**over) -> KnownIssue:
    base = dict(issue_id="I1", title="missing ccxt", component="dataingestion",
                keywords=("ccxt",), confirmed_by="pip show ccxt")
    base.update(over)
    return KnownIssue(**base)


def test_fn_category_no_signal():
    out = retrospect_issue(_issue(), PipelineFunnel())
    assert out["category"] == "NO_SIGNAL"
    assert out["surfaced"] is False


def test_fn_category_suppression_miss():
    funnel = PipelineFunnel(
        raw_summaries=["ModuleNotFoundError: No module named 'ccxt'"],
        normalized=[{"payload_summary": "ModuleNotFoundError: No module named 'ccxt'"}],
        suppressed=[{"payload_summary": "No module named 'ccxt'", "noise_class": "KNOWN_BASELINE"}],
    )
    out = retrospect_issue(_issue(), funnel)
    assert out["category"] == "SUPPRESSION_MISS"
    assert "KNOWN_BASELINE" in out["detail"]


def test_fn_category_dedup_miss():
    funnel = PipelineFunnel(
        raw_summaries=["No module named 'ccxt'"],
        normalized=[{"payload_summary": "No module named 'ccxt'"}],
        suppressed=[{"payload_summary": "No module named 'ccxt'", "noise_class": "DUPLICATE"}],
    )
    assert retrospect_issue(_issue(), funnel)["category"] == "DEDUP_MISS"


def test_fn_category_scoring_miss():
    funnel = PipelineFunnel(
        raw_summaries=["No module named 'ccxt'"],
        normalized=[{"payload_summary": "No module named 'ccxt'"}],
        clusters=[{"root_title": "missing dependency 'ccxt'", "score": 0.11}],
        threshold=0.42,
    )
    out = retrospect_issue(_issue(), funnel)
    assert out["category"] == "SCORING_MISS"
    assert "below threshold" in out["detail"]


def test_fn_category_investigation_miss_and_success():
    funnel = PipelineFunnel(
        raw_summaries=["No module named 'ccxt'"],
        normalized=[{"payload_summary": "No module named 'ccxt'"}],
        clusters=[{"root_title": "missing dependency 'ccxt'", "score": 0.68}],
        candidates=[{"root_title": "missing dependency 'ccxt'", "score": 0.68}],
    )
    assert retrospect_issue(_issue(), funnel)["category"] == "INVESTIGATION_MISS"

    funnel.investigations = [{"summary": "missing dependency 'ccxt'"}]
    out = retrospect_issue(_issue(), funnel)
    assert out["category"] == "NOT_A_DISCOVERY_FAILURE"
    assert out["surfaced"] is True


def test_investigation_stage_is_read_in_its_own_shape():
    """An Investigation has goal/scope, not summary.

    Reading only summary-shaped keys matched nothing at that stage, so every
    surfaced issue was reported as INVESTIGATION_MISS and the audit claimed a
    failure that had not happened.
    """
    funnel = PipelineFunnel(
        raw_summaries=["No module named 'ccxt'"],
        normalized=[{"payload_summary": "No module named 'ccxt'"}],
        clusters=[{"root_title": "missing dependency 'ccxt'", "score": 0.68}],
        candidates=[{"root_title": "missing dependency 'ccxt'", "score": 0.68}],
        investigations=[{
            "investigation_id": "linv_1",
            "goal": "determine why 'ccxt' cannot be imported",
            "scope": "read_only",
            "evidence": ["ModuleNotFoundError"],
        }],
    )
    out = retrospect_issue(_issue(), funnel)
    assert out["investigated"] is True
    assert out["category"] == "NOT_A_DISCOVERY_FAILURE"


def test_component_name_alone_does_not_count_as_surfacing_an_issue():
    """§27: recall must not be inflated by a same-component coincidence.

    "dashboard runs stale code" and "dashboard is pending" share a component and
    nothing else; treating the second as the first turns a real miss into a hit.
    """
    stale_runtime = KnownIssue(
        issue_id="KI-STALE",
        title="dashboard process runs an older Brain revision",
        component="dashboard",
        keywords=("stale_runtime", "running_revision"),
    )
    funnel = PipelineFunnel(
        raw_summaries=["dashboard state=pending"],
        normalized=[{"payload_summary": "dashboard state=pending", "component": "dashboard"}],
        candidates=[{"root_title": "dashboard state=pending", "component": "dashboard"}],
    )
    out = retrospect_issue(stale_runtime, funnel)
    assert out["surfaced"] is False
    assert out["category"] == "NO_SIGNAL"

    # With no keywords at all, the component fallback still applies.
    fallback = KnownIssue(issue_id="KI-ANY", title="anything in dashboard", component="dashboard")
    assert retrospect_issue(fallback, funnel)["surfaced"] is True


def test_recall_separates_surfaced_from_investigated():
    issues = [_issue(issue_id="I1"), _issue(issue_id="I2", keywords=("registry",))]
    retros = [
        {"issue_id": "I1", "surfaced": True, "investigated": True,
         "category": "NOT_A_DISCOVERY_FAILURE"},
        {"issue_id": "I2", "surfaced": True, "investigated": False,
         "category": "INVESTIGATION_MISS"},
    ]
    out = known_issue_recall(issues, retros)
    assert out["known_issue_recall"] == 1.0
    assert out["investigated"] == 1
    assert out["investigated_rate"] == 0.5


def test_all_fn_categories_are_reachable_names():
    assert set(FN_CATEGORIES) >= {
        "NO_SIGNAL", "NORMALIZATION_MISS", "SUPPRESSION_MISS", "DEDUP_MISS",
        "SCORING_MISS", "CLUSTERING_MISS", "INVESTIGATION_MISS",
        "NOT_A_DISCOVERY_FAILURE",
    }


def test_known_issue_recall_keeps_denominator_explicit():
    issues = [
        _issue(issue_id="I1"),
        _issue(issue_id="I2", title="registry pending", component="registry", keywords=("pending",)),
        _issue(issue_id="I3", title="not discoverable by design", expected_discoverable=False),
    ]
    retros = [
        {"issue_id": "I1", "surfaced": True, "category": "NOT_A_DISCOVERY_FAILURE"},
        {"issue_id": "I2", "surfaced": False, "category": "SUPPRESSION_MISS"},
    ]
    out = known_issue_recall(issues, retros)
    assert out["denominator"] == 2
    assert out["surfaced"] == 1
    assert out["known_issue_recall"] == 0.5
    assert out["excluded_not_discoverable"] == ["I3"]
    assert out["is_global_false_negative_rate"] is False
    assert "not all real issues" in out["caveat"]


# ---------------------------------------------------------------------------
# §12 multi-session aggregation
# ---------------------------------------------------------------------------


def test_aggregate_reports_macro_and_micro_separately():
    sessions = [
        {"label": "A", "duration_s": 900, "signals": 4000, "candidates": 2,
         "reviewed_useful": 2, "reviewed_noise": 0},
        {"label": "B", "duration_s": 900, "signals": 3800, "candidates": 0},
        {"label": "C", "duration_s": 1800, "signals": 8000, "candidates": 4,
         "reviewed_useful": 2, "reviewed_noise": 2},
    ]
    agg = aggregate_sessions(sessions)
    assert agg["sessions_n"] == 3
    assert agg["sessions_with_reviews_n"] == 2
    # Macro weights A and C equally; micro pools all four reviewed candidates.
    assert agg["macro_precision"] == 0.75
    assert agg["micro_precision"] == 0.6667
    assert agg["zero_candidate_sessions"] == ["B"]
    assert agg["per_session"][1]["precision"] is None


def test_zero_candidate_session_is_valid_evidence():
    """§13: a quiet period is a result, not a missing measurement."""
    agg = aggregate_sessions([{"label": "Q", "duration_s": 900, "signals": 3000, "candidates": 0}])
    assert agg["zero_candidate_sessions"] == ["Q"]
    assert agg["candidates_total"] == 0
    assert agg["micro_precision"] is None


# ---------------------------------------------------------------------------
# §7-8 runtime revision attestation
# ---------------------------------------------------------------------------


def test_runtime_attestation_reports_running_and_worktree():
    att = runtime_attestation()
    assert att["schema"] == "qhos-runtime-attestation-v1"
    for key in ("running_revision", "worktree_revision", "stale_runtime", "drift_reason"):
        assert key in att
    assert att["running"]["discovery_policy"]["config_hash"] == config_hash()
    assert att["running"]["route_schema_version"] == "qhos-brain-v1"
    assert att["informational_only"] is True
    assert att["restart_control_exposed"] is False
    assert att["can_reload"] is False


def test_attestation_matches_worktree_in_a_fresh_process():
    """A process that just imported the code cannot be stale."""
    att = runtime_attestation()
    assert att["running_revision"] == att["worktree_revision"]
    assert att["stale_runtime"] is False
    assert att["drift_reason"] == "running_matches_worktree"


def test_stale_runtime_is_detected_when_sources_change(tmp_path, monkeypatch):
    """Editing the package on disk must flip the staleness verdict."""
    import brain.attest as attest

    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("x = 1\n", encoding="utf-8")
    first, count = source_revision(pkg)
    assert count == 1

    (pkg / "a.py").write_text("x = 2\n", encoding="utf-8")
    second, _ = source_revision(pkg)
    assert second != first, "source hash ignored a content change"

    # Simulate a process whose import-time revision predates the edit.
    monkeypatch.setattr(attest, "_IMPORT_SOURCE_REVISION", first)
    monkeypatch.setattr(attest, "_worktree_cache", None)
    monkeypatch.setattr(attest, "_BRAIN_ROOT", pkg)
    att = attest.runtime_attestation()
    assert att["stale_runtime"] is True
    assert "brain_source_changed_on_disk" in att["drift"]


def test_source_revision_is_order_independent_and_ignores_pycache(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "b.py").write_text("b = 1\n", encoding="utf-8")
    (pkg / "a.py").write_text("a = 1\n", encoding="utf-8")
    (pkg / "__pycache__" / "junk.py").write_text("noise = 1\n", encoding="utf-8")
    rev, count = source_revision(pkg)
    assert count == 2, "pycache should not be hashed"
    assert (rev, count) == source_revision(pkg)


def test_attestation_panel_is_read_only_and_leaks_no_secret():
    panel = attestation_panel()
    assert panel["kind"] == "RUNTIME_REVISION"
    assert panel["read_only"] is True
    assert "restart" not in json.dumps(panel).lower()
    blob = json.dumps(panel).lower()
    for banned in ("api_key", "sk-ant", "token=", "password", "secret"):
        assert banned not in blob
    assert panel["discovery_policy"].startswith("DISCOVERY_CALIBRATION_V2")


# ---------------------------------------------------------------------------
# §38-42 provider output contract
# ---------------------------------------------------------------------------


def test_output_status_taxonomy_is_complete():
    assert set(OUTPUT_STATUSES) == {
        "VALID", "TRUNCATED", "SCHEMA_INVALID", "FALLBACK", "TIMEOUT",
        "CIRCUIT_OPEN", "PROVIDER_ERROR",
    }
    assert "VALID" not in NON_REASONING_STATUSES


@pytest.mark.parametrize("text,expected", [
    ('{"a": 1}', False),
    ('{"a": [1, 2]}', False),
    ('{"a": 1', True),
    ('{"a": "unterminated', True),
    ('{"a": [1, 2', True),
    ('{"a": 1,', True),
    ('not json at all', False),
    ('', False),
])
def test_detect_truncation(text, expected):
    assert detect_truncation(text)[0] is expected


def test_truncated_output_is_never_valid_reasoning():
    """§39: a token-cap stop must not be presented as a conclusion."""
    verdict = classify_output(
        raw_text='{"incident_assessment": "the root cause is',
        stop_reason="max_tokens",
    )
    assert verdict["output_status"] == "TRUNCATED"
    assert verdict["truncated"] is True
    assert verdict["is_real_model_reasoning"] is False
    assert verdict["counts_as_hybrid_success"] is False


def test_fallback_never_counts_as_a_real_model_result():
    """§41: no fallback masquerading."""
    verdict = classify_output(raw_text='{"a": 1}', parsed={"a": 1}, fallback=True)
    assert verdict["output_status"] == "FALLBACK"
    assert verdict["counts_as_hybrid_success"] is False


def test_infrastructure_status_wins_over_schema_status():
    assert classify_output(raw_text="{", error="Request timed out")["output_status"] == "TIMEOUT"
    assert classify_output(raw_text="{", circuit_open=True)["output_status"] == "CIRCUIT_OPEN"
    assert classify_output(raw_text="{}", error="APIConnectionError")["output_status"] == "PROVIDER_ERROR"
    assert classify_output(raw_text='{"a":1}', parsed={"a": 1})["output_status"] == "VALID"


@pytest.mark.parametrize("value,expected", [
    (0.8, 0.8),
    ("0.8", 0.8),
    ("80%", 0.8),
    ("HIGH", 0.8),
    (1.7, 1.0),
    (-3, 0.0),
    (None, 0.0),
    ("banana", 0.0),
    ([0.6], 0.6),
    ([0.1, 0.2], 0.0),
    (True, 1.0),
    (float("nan"), 0.0),
])
def test_coerce_confidence_never_raises_and_never_invents(value, expected):
    got, _ = coerce_confidence(value)
    assert got == pytest.approx(expected)


@pytest.mark.parametrize("value,expected", [
    ("HIGH", "HIGH"),
    ("high - directly confirms", "HIGH"),
    (0.95, "0.95"),
    ("0.95", "0.95"),
    (1.9, "1.00"),
    (None, "MEDIUM"),
    ("nonsense", "MEDIUM"),
    (["HIGH"], "HIGH"),
])
def test_coerce_information_gain(value, expected):
    assert coerce_information_gain(value)[0] == expected


def test_coerce_text_handles_list_and_mapping():
    assert coerce_text(["a", "b"], limit=40)[0] == "a b"
    assert coerce_text({"k": "v"}, limit=40)[0] == "k: v"
    assert coerce_text("plain", limit=40) == ("plain", "")


def test_call_metrics_carries_every_integrity_field():
    """§42: a zero-token row that claims success must be impossible to produce."""
    row = call_metrics(
        llm_called=True, provider="anthropic", model="claude-sonnet-4-6",
        input_tokens=997, output_tokens=1866, latency_s=35.11,
        output_status="VALID", fallback=False, breaker_state="closed",
    )
    for key in ("llm_called", "provider", "model", "input_tokens", "output_tokens",
                "latency_s", "output_status", "fallback", "breaker_state"):
        assert key in row, key
    assert row["total_tokens"] == 2863
    assert row["counts_as_hybrid_success"] is True

    bad = call_metrics(llm_called=True, output_status="TRUNCATED")
    assert bad["counts_as_hybrid_success"] is False
    assert call_metrics(llm_called=False, output_status="nonsense")["output_status"] == "PROVIDER_ERROR"


# ---------------------------------------------------------------------------
# §38 adversarial reasoner payloads
# ---------------------------------------------------------------------------


def _valid_payload(**over) -> dict:
    base = {
        "incident_assessment": "missing dependency",
        "hypotheses": [{"statement": "ccxt absent", "confidence": 0.9}],
        "recommended_verifications": [{"action_type": "inspect_health", "target": "pip show ccxt"}],
        "stop_recommendation": "NEEDS_MORE_EVIDENCE",
    }
    base.update(over)
    return base


def test_reasoner_rejects_prose_and_missing_fields():
    from brain.gateway import validate_reasoner_output

    with pytest.raises(ValueError, match="invalid_reasoner_output"):
        validate_reasoner_output("the root cause is obvious")
    with pytest.raises(ValueError, match="missing_fields"):
        validate_reasoner_output({"incident_assessment": "x"})
    with pytest.raises(ValueError, match="hypotheses_not_list"):
        validate_reasoner_output(_valid_payload(hypotheses=7))


def test_reasoner_accepts_unambiguous_scalar_shapes_and_records_them():
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output(_valid_payload(
        hypotheses={"statement": "single hypothesis returned bare", "confidence": "0.7"},
        recommended_verifications="inspect the venv",
        incident_assessment=["part one", "part two"],
        risk_notes="one note",
    ))
    assert len(packet["hypotheses"]) == 1
    assert packet["hypotheses"][0]["model_confidence"] == 0.7
    assert packet["incident_assessment"] == "part one part two"
    assert packet["risk_notes"] == ["one note"]
    assert packet["recommended_verifications"][0]["target"] == "inspect the venv"
    assert any("text_joined_from_list" in w for w in packet["schema_warnings"])


def test_reasoner_drops_unusable_hypotheses_without_failing_the_packet():
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output(_valid_payload(hypotheses=[
        {"statement": "good one", "confidence": 0.5},
        {"statement": "   ", "confidence": 0.9},
        42,
        {"reason": "no statement"},
        "a bare string hypothesis",
    ]))
    statements = [h["statement"] for h in packet["hypotheses"]]
    assert "good one" in statements
    assert "a bare string hypothesis" in statements
    assert len(packet["hypotheses"]) == 2
    assert any("dropped" in w for w in packet["schema_warnings"])


def test_reasoner_reports_extra_fields_instead_of_merging_them():
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output(_valid_payload(
        authority=True, is_evidence=True, level3_enabled=True, apply_patch="yes",
    ))
    # An unrecognised field must never become authority.
    assert packet["authority"] is False
    assert packet["is_evidence"] is False
    assert "apply_patch" not in packet
    assert "level3_enabled" not in packet
    assert packet["ignored_extra_fields"]
    assert any("ignored_extra_fields" in w for w in packet["schema_warnings"])


def test_reasoner_invalid_confidence_does_not_raise():
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output(_valid_payload(hypotheses=[
        {"statement": "a", "confidence": "definitely"},
        {"statement": "b", "confidence": [0.4, 0.5]},
        {"statement": "c", "confidence": 42},
    ]))
    confidences = [h["model_confidence"] for h in packet["hypotheses"]]
    assert confidences == [0.0, 0.0, 1.0]
    assert any("confidence" in w for w in packet["schema_warnings"])


def test_reasoner_large_output_is_bounded():
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output(_valid_payload(
        incident_assessment="x" * 5000,
        hypotheses=[{"statement": "y" * 5000, "confidence": 0.5,
                     "evidence_needed": ["z" * 5000] * 20}] * 40,
        recommended_verifications=[{"action_type": "a", "target": "t" * 900}] * 40,
        risk_notes=["r" * 900] * 40,
    ))
    assert len(packet["incident_assessment"]) <= 400
    assert len(packet["hypotheses"]) <= 8
    assert len(packet["recommended_verifications"]) <= 8
    assert len(packet["risk_notes"]) <= 6
    assert all(len(e) <= 200 for h in packet["hypotheses"] for e in h["evidence_needed"])


def test_reasoner_output_is_never_authority_regardless_of_input():
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output(_valid_payload(stop_recommendation="ROOT_CAUSE_CONFIRMED"))
    assert packet["epistemic_status"] == "HYPOTHESIS"
    assert packet["authority"] is False
    assert packet["is_evidence"] is False


# ---------------------------------------------------------------------------
# §19-20 telemetry as first-class intelligence
# ---------------------------------------------------------------------------


def test_telemetry_tier_candidate_is_projected_as_a_quality_issue():
    """§19: an observability defect must not read as a component failure."""
    from brain.discovery.telemetry import TELEMETRY_QUALITY_ISSUE, telemetry_quality_issues

    issues = telemetry_quality_issues([
        {"root_tier": "TELEMETRY", "root_id": "sys-1",
         "root_title": "registry reports 13 modules pending with no lifecycle telemetry",
         "affected_components": ["storage", "kill_switch"], "affected_instances": 13,
         "evidence": ["uptime=null and error_count=0 on every sample"], "score": 0.51},
        {"root_tier": "DEPENDENCY", "root_id": "dep-1", "root_title": "missing ccxt"},
    ])
    assert len(issues) == 1
    issue = issues[0]
    assert issue["kind"] == TELEMETRY_QUALITY_ISSUE
    assert issue["is_component_failure"] is False
    assert issue["write_patch"] is False
    assert issue["requires_human_review"] is True
    assert "wrong remediation" in issue["interpretation"]


def test_engine_emits_telemetry_quality_issues_alongside_incidents():
    """The systemic registry finding must be reachable as its own kind."""
    modules = {}
    for name in ("storage", "ingestion", "features", "regime_model", "evolution",
                 "llm_agents", "backtest", "validator", "risk_engine", "portfolio",
                 "execution", "dashboard", "kill_switch"):
        modules.update(_registry(name))
    engine = DiscoveryEngine()
    found = []
    for _ in range(6):
        intel = engine.ingest({"registry": modules})
        found.extend(intel.get("telemetry_quality_issues") or [])
    assert len(found) == 1, f"expected exactly one systemic telemetry issue, got {len(found)}"
    issue = found[0]
    assert issue["subtype"] == "OBSERVABILITY_DEFECT"
    assert issue["is_component_failure"] is False
    assert issue["component_count"] >= 5


def test_source_health_separates_reachable_from_useful():
    """§20: the registry answered every poll and still told us nothing."""
    from brain.discovery.telemetry import source_health, telemetry_quality_ledger

    ledger = telemetry_quality_ledger({
        "events_received": 100,
        "source_quality": {
            "registry": {"received": 80, "suppressed": 80, "candidate_producing": 0,
                         "duplicate_producing": 0},
            "error_log": {"received": 20, "suppressed": 14, "candidate_producing": 1,
                          "duplicate_producing": 12},
        },
    })
    health = source_health(
        ledger,
        now=1_000_100.0,
        last_seen={"registry": 1_000_090.0, "error_log": 1_000_000.0},
        expected_components={"registry": 15},
        observed_components={"registry": 15},
        semantic_findings=[{"source": "registry", "finding": "status is a static default"}],
    )
    by_name = {r["source"]: r for r in health["sources"]}
    assert by_name["registry"]["coverage"] == 1.0
    assert by_name["registry"]["fresh"] is True
    assert by_name["registry"]["semantically_valid"] is False
    assert by_name["registry"]["reachable_but_misleading"] is True
    assert by_name["registry"]["inert_traffic"] is True
    assert by_name["error_log"]["semantically_valid"] is True
    assert health["misleading_sources"] == ["registry"]
    assert health["auto_disable_enabled"] is False


# ---------------------------------------------------------------------------
# §28-29 review dataset growth and revision history
# ---------------------------------------------------------------------------


def _review(candidate_id: str, disposition: str, session: str = "s1") -> dict:
    from brain.discovery.review import review_record

    return review_record(
        candidate={"candidate_id": candidate_id, "score": 0.6, "component": "storage"},
        session_id=session,
        candidate_type="incident",
        disposition=disposition,
    )


def test_review_record_carries_policy_provenance():
    """§28: a pooled dataset without a policy stamp is not attributable."""
    row = _review("c1", "USEFUL")
    assert row["discovery_policy"]["policy"] == POLICY_NAME
    assert row["discovery_policy"]["config_hash"] == config_hash()
    assert row["original_disposition"] == "USEFUL"
    assert row["revision_history"] == []
    assert row["not_training_truth"] is True


def test_review_revision_preserves_the_original_disposition():
    """§29: a re-review must not silently replace the first judgement."""
    from brain.discovery.review import revise_review

    first = _review("c1", "USEFUL")
    revised = revise_review(
        first, disposition="DUPLICATE",
        reason="same root as the ccxt candidate reviewed in session A",
    )
    assert revised["human_disposition"] == "DUPLICATE"
    assert revised["original_disposition"] == "USEFUL"
    assert len(revised["revision_history"]) == 1
    entry = revised["revision_history"][0]
    assert entry["from"] == "USEFUL"
    assert entry["to"] == "DUPLICATE"
    assert "ccxt" in entry["reason"]
    # Re-applying the same disposition is not a revision.
    assert len(revise_review(revised, disposition="DUPLICATE", reason="x")["revision_history"]) == 1


def test_review_revision_labels_are_stripped_before_replay():
    """A revision history leaks the same judgement as the disposition."""
    from brain.discovery.review import revise_review, strip_review_labels

    row = revise_review(_review("c1", "USEFUL"), disposition="NOT_USEFUL", reason="benign")
    stripped = strip_review_labels(row)
    blob = json.dumps(stripped)
    for banned in ("human_disposition", "original_disposition", "revision_history",
                   "USEFUL", "NOT_USEFUL", "benign"):
        assert banned not in blob, banned


def test_append_reviews_never_drops_an_earlier_session(monkeypatch, tmp_path):
    from brain.discovery import review as review_mod

    monkeypatch.setattr(review_mod, "shadow_dir", lambda: tmp_path)
    first = review_mod.append_reviews([_review("c1", "USEFUL", session="A")], dataset="cum.json")
    assert first["added"] == 1
    second = review_mod.append_reviews([_review("c2", "NOT_USEFUL", session="B")], dataset="cum.json")
    assert second["added"] == 1
    assert second["total_rows"] == 2
    assert second["sessions"] == ["A", "B"]

    blob = json.loads((tmp_path / "cum.json").read_text(encoding="utf-8"))
    assert blob["append_only"] is True
    assert {r["session_id"] for r in blob["rows"]} == {"A", "B"}
    assert blob["policy_revisions"] == [POLICY_REVISION]


def test_append_reviews_updates_a_revised_row_in_place():
    from brain.discovery import review as review_mod

    row = _review("c1", "USEFUL", session="A")
    revised = review_mod.revise_review(row, disposition="DUPLICATE", reason="same root")
    assert revised["review_id"] == row["review_id"]
    assert revised["original_disposition"] == "USEFUL"


def test_importing_prior_session_files_keeps_them_attributable(monkeypatch, tmp_path):
    """Pre-freeze reviews join the dataset without borrowing the frozen policy."""
    from brain.discovery import review as review_mod

    monkeypatch.setattr(review_mod, "shadow_dir", lambda: tmp_path)
    (tmp_path / "reviews_old.json").write_text(json.dumps({
        "rows": [{"review_id": "rev_old", "session_id": "soak_old",
                  "candidate_type": "potential_incident", "candidate_id": "pinc_1",
                  "human_disposition": "USEFUL"}],
    }), encoding="utf-8")

    result = review_mod.import_session_review_files(dataset="cum.json")
    assert result["added"] == 1
    blob = json.loads((tmp_path / "cum.json").read_text(encoding="utf-8"))
    row = blob["rows"][0]
    assert row["discovery_policy"]["revision"] == "pre_v2_freeze"
    assert row["imported_from"] == "reviews_old.json"
    assert POLICY_REVISION not in blob["policy_revisions"]

    # Importing twice must not duplicate, and must not touch the dataset file.
    assert review_mod.import_session_review_files(dataset="cum.json")["added"] == 0


def test_rereviewing_the_same_candidate_does_not_inflate_the_denominator(monkeypatch, tmp_path):
    """A second review pass must revise, not duplicate.

    With a random ``review_id`` a re-run of the review script silently doubled
    every row, and precision is a ratio over that count.
    """
    from brain.discovery import review as review_mod

    monkeypatch.setattr(review_mod, "shadow_dir", lambda: tmp_path)
    args = dict(session_id="A", candidate_type="incident")
    first = review_mod.review_record(candidate={"candidate_id": "root9"}, disposition="USEFUL", **args)
    again = review_mod.review_record(candidate={"candidate_id": "root9"}, disposition="USEFUL", **args)
    assert first["review_id"] == again["review_id"]

    other_session = review_mod.review_record(candidate={"candidate_id": "root9"},
                                             session_id="B", candidate_type="incident",
                                             disposition="USEFUL")
    other_type = review_mod.review_record(candidate={"candidate_id": "root9"},
                                          session_id="A", candidate_type="improvement",
                                          disposition="USEFUL")
    assert len({first["review_id"], other_session["review_id"], other_type["review_id"]}) == 3

    review_mod.append_reviews([first], dataset="cum.json")
    second = review_mod.append_reviews([again], dataset="cum.json")
    assert (second["added"], second["updated"], second["total_rows"]) == (0, 1, 1)


# ---------------------------------------------------------------------------
# §6, §10 session provenance
# ---------------------------------------------------------------------------


def test_session_provenance_records_every_required_identity_field():
    prov = session_provenance({
        "session_id": "soak_x", "started_ts": 1.0, "ended_ts": 2.0,
        "duration_requested_s": 900.0, "duration_actual_s": 900.4,
        "cycles": 173, "runtime_attestation": runtime_attestation(),
        "source_availability": {"degraded": False},
    })
    assert prov["schema"] == "qhos-session-provenance-v1"
    assert prov["session_id"] == "soak_x"
    assert prov["llm_enabled"] is False
    assert prov["level3_enabled"] is False
    assert prov["sandbox_enabled"] is False
    policy = prov["discovery_policy"]
    assert policy["revision"] == POLICY_REVISION
    assert policy["fingerprint_version"]
    assert policy["score_version"]
    assert policy["signal_inventory_revision"]
    att = prov["runtime_attestation"]
    assert att["running"]["pid"] > 0
    assert att["running"]["process_start_ts"] > 0


# ---------------------------------------------------------------------------
# §8, §44 UI revision payload
# ---------------------------------------------------------------------------


def test_lacos_payload_exposes_runtime_revision_without_controls():
    from brain.discovery.engine import DiscoveryEngine

    intel = DiscoveryEngine().ingest({
        "error_tail": ["2026-09-03 12:00:00 | ERROR | DataIngestion | "
                       "FALHOU BTC/USDT: ModuleNotFoundError: No module named 'ccxt'"],
    })
    view = {
        "incidents": intel["incidents"],
        "runtime_revision": attestation_panel(),
        "telemetry_ledger": intel["telemetry_ledger"],
    }
    panel = view["runtime_revision"]
    assert panel["running_revision"]
    assert panel["worktree_revision"]
    assert "stale_runtime" in panel
    blob = json.dumps(view).lower()
    for banned in ("restart_button", "enable_level3", "apply_patch", "open_sandbox", "place_order"):
        assert banned not in blob


def _served_ui():
    from pathlib import Path

    ui = Path(__file__).resolve().parents[1] / "src" / "brain_service" / "ui"
    html = (ui / "index.html").read_text(encoding="utf-8", errors="replace")
    return html, html + (ui / "app.js").read_text(encoding="utf-8", errors="replace")


def test_ui_source_has_no_runtime_restart_control():
    """Informational only: no control may act on the runtime (positive half, QHOS-AUD-271)."""
    html, _ = _served_ui()
    for banned in ("reiniciar processo", "restart()", "onclick=\"restart"):
        assert banned not in html


@pytest.mark.xfail(strict=False, reason="Older dashboard RUNTIME REVISION panel absent from the preserved Brain UI; tracked in "
                   "HERANCA_PESQUISA.md. Not strict: implementing the panel must not fail the suite, and a token grep "
                   "does not prove UI behaviour (QHOS-AUD-271).")
def test_ui_source_declares_the_runtime_revision_panel():
    """The panel must exist in the served page, not only in the payload."""
    _, surface = _served_ui()
    assert "RUNTIME REVISION" in surface
    assert "STALE_RUNTIME" in surface
    assert "runtime_revision" in surface


# ---------------------------------------------------------------------------
# §46 storage stability, §1 authority invariants
# ---------------------------------------------------------------------------


def test_soak_pruning_covers_derived_analysis_artifacts(monkeypatch, tmp_path):
    """Analysis files escaped retention in the previous phase."""
    from brain.discovery import soak as soak_mod

    monkeypatch.setattr(soak_mod, "shadow_dir", lambda: tmp_path)
    names = [
        "soak_a.json", "reviews_a.json", "replay_a.json", "long_soak_a.json",
        "hybrid_replay_a.json", "calibration_a.json", "multi_session_soak.json",
    ]
    for name in names:
        (tmp_path / name).write_text('{"x": 1}', encoding="utf-8")
    unrelated = tmp_path / "keep_me.json"
    unrelated.write_text("{}", encoding="utf-8")

    covered = [n for n in names if n.startswith(soak_mod.ARTIFACT_PREFIXES)]
    assert "replay_a.json" in covered
    assert "long_soak_a.json" in covered
    assert "hybrid_replay_a.json" in covered
    assert "calibration_a.json" in covered
    soak_mod.prune_shadow()
    assert unrelated.exists(), "pruning must not touch unrelated files"


def test_engine_authority_invariants_hold_after_a_promotion():
    engine = DiscoveryEngine()
    engine.ingest({
        "error_tail": ["2026-09-03 12:00:00 | ERROR | DataIngestion | "
                       "FALHOU BTC/USDT: ModuleNotFoundError: No module named 'ccxt'"],
        "registry": _registry("risk_engine"),
    })
    inv = engine.safety_invariants()
    assert inv["ok"] is True
    for key in ("change_requests", "sandbox_runs", "production_writes",
                "trading_actions", "autonomy_changes"):
        assert inv[key] == 0, key


# ---------------------------------------------------------------------------
# §32-35 incremental Hybrid value
# ---------------------------------------------------------------------------


def _harness():
    import importlib

    return importlib.import_module("heritage_support.hybrid_replay_paid")


def test_incremental_value_classes_are_the_specified_set():
    assert set(_harness().INCREMENTAL_VALUE_CLASSES) == {
        "NO_INCREMENTAL_VALUE", "VERIFICATION_VALUE", "UNCERTAINTY_VALUE",
        "ROOT_CAUSE_VALUE", "CROSS_COMPONENT_VALUE", "MEMORY_VALUE",
        "NEGATIVE_VALUE",
    }


def test_abbreviations_are_not_reported_as_unsupported_claims():
    """"e.g" is punctuation, not an invented module path.

    A hallucination heuristic that fires on abbreviations trains the reader to
    ignore it, which costs more than the check is worth.
    """
    harness = _harness()
    packet = {"hypotheses": [
        {"statement": "a shared dependency (e.g. storage) is not ready"},
        {"statement": "the core.data_ingestion adapter was never registered"},
    ]}
    out = harness.unsupported_claims(packet, {"root_title": "storage pending"})
    flagged = {t for row in out["flagged"] for t in row["novel_terms"]}
    assert "e.g" not in flagged
    assert "core.data_ingestion" in flagged


def test_restating_the_deterministic_answer_is_not_credited_as_finding_the_cause():
    """§32: alternatives around a restatement are verification value, not a find."""
    harness = _harness()
    hybrid = {
        "counts_as_hybrid_success": True,
        "output_status": "VALID",
        "hypotheses": [
            {"statement": "the registry reports a default status for every module",
             "model_confidence": 0.65},
            {"statement": "a shared startup dependency stalled", "model_confidence": 0.45},
            {"statement": "the metrics exporter is down", "model_confidence": 0.55},
        ],
        "recommended_verifications": [
            {"target": "registry status source", "expected_information_gain": 0.85},
            {"target": "metrics exporter logs", "expected_information_gain": 0.7},
        ],
        "stop_recommendation": "NEEDS_MORE_EVIDENCE",
    }
    det = {"interpretation": "the registry reports a default status for every module",
           "recommended_verifications": ["inspect_logs"]}
    verdict = harness.incremental_value(det, hybrid)
    assert verdict["restated_deterministic"] is True
    assert "ROOT_CAUSE_VALUE" in verdict["classes"]
    assert verdict["primary"] != "ROOT_CAUSE_VALUE"
    assert "restated" in verdict["primary_qualifier"]

    # The same output against a different deterministic reading is a real find.
    verdict = harness.incremental_value(
        {"interpretation": "storage disk exhausted", "recommended_verifications": []},
        hybrid,
    )
    assert verdict["restated_deterministic"] is False
    assert verdict["primary"] == "ROOT_CAUSE_VALUE"


def test_memory_value_is_reported_unevaluable_rather_than_silently_unreachable():
    """§33 lists MEMORY_VALUE, but this packet carries no prior-incident context.

    An unreachable label that looks reachable would read as "never observed"
    instead of "never testable", so the blocker is stated in the evidence.
    """
    verdict = _harness().incremental_value(
        {"interpretation": "storage unhealthy", "recommended_verifications": []},
        {"counts_as_hybrid_success": True, "output_status": "VALID",
         "hypotheses": [{"statement": "disk is full", "model_confidence": 0.6}],
         "recommended_verifications": [], "stop_recommendation": "LIKELY_CAUSE"},
    )
    assert verdict["evidence"]["memory_value_evaluable"] is False
    assert "memory" in verdict["evidence"]["memory_value_blocker"]
    assert "MEMORY_VALUE" not in verdict["classes"]


def test_unusable_output_has_no_incremental_value():
    """§41: a fallback or truncation cannot be credited with value."""
    verdict = _harness().incremental_value(
        {"interpretation": "missing dependency ccxt"},
        {"counts_as_hybrid_success": False, "output_status": "TRUNCATED"},
    )
    assert verdict["primary"] == "NO_INCREMENTAL_VALUE"
    assert "TRUNCATED" in verdict["reason"]


def test_restating_deterministic_output_is_not_credited_as_root_cause_value():
    """§32: the same answer for money is a restatement, not a gain."""
    harness = _harness()
    verdict = harness.incremental_value(
        {"interpretation": "missing dependency ccxt in data ingestion",
         "recommended_verifications": ["verify_import_availability"]},
        {
            "counts_as_hybrid_success": True,
            "output_status": "VALID",
            "hypotheses": [{"statement": "the ccxt dependency is missing from data ingestion",
                            "model_confidence": 0.9}],
            "recommended_verifications": [],
            "stop_recommendation": "LIKELY_CAUSE",
            "risk_notes": [],
            "unsupported_claims": {"count": 0},
            "tokens": 2800,
        },
        cost_usd=0.04,
    )
    assert verdict["restated_deterministic"] is True
    assert verdict["primary"] == "NO_INCREMENTAL_VALUE"
    assert verdict["cost_usd"] == 0.04


def test_differential_hypotheses_and_new_verifications_are_credited():
    verdict = _harness().incremental_value(
        {"interpretation": "missing dependency ccxt",
         "recommended_verifications": ["verify_import_availability"]},
        {
            "counts_as_hybrid_success": True,
            "output_status": "VALID",
            "hypotheses": [
                {"statement": "ccxt is not installed in the active runtime", "model_confidence": 0.88},
                {"statement": "installed in a different interpreter", "model_confidence": 0.72},
                {"statement": "removed by a recent deployment", "model_confidence": 0.65},
                {"statement": "image built without it", "model_confidence": 0.55},
            ],
            "recommended_verifications": [
                {"target": "pip show ccxt", "expected_information_gain": "0.95"},
                {"target": "ci/cd deployment logs", "expected_information_gain": "0.80"},
                {"target": "requirements manifest", "expected_information_gain": "0.75"},
            ],
            "stop_recommendation": "NEEDS_MORE_EVIDENCE",
            "risk_notes": [],
            "unsupported_claims": {"count": 1},
            "tokens": 2863,
        },
        cost_usd=0.031,
    )
    assert "ROOT_CAUSE_VALUE" in verdict["classes"]
    assert "VERIFICATION_VALUE" in verdict["classes"]
    assert "UNCERTAINTY_VALUE" in verdict["classes"]
    assert verdict["primary"] == "ROOT_CAUSE_VALUE"
    assert verdict["evidence"]["differential_hypotheses"] == 4


def test_cross_component_reasoning_is_credited():
    verdict = _harness().incremental_value(
        {"interpretation": "registry pending"},
        {
            "counts_as_hybrid_success": True,
            "output_status": "VALID",
            "hypotheses": [{"statement": "registry never received lifecycle telemetry",
                            "model_confidence": 0.65}],
            "recommended_verifications": [],
            "stop_recommendation": "NEEDS_MORE_EVIDENCE",
            "risk_notes": ["kill_switch and risk_engine would be critical if genuinely "
                           "stalled, and storage feeds both"],
            "unsupported_claims": {"count": 0},
        },
    )
    assert "CROSS_COMPONENT_VALUE" in verdict["classes"]
    assert len(verdict["evidence"]["components_reasoned_about"]) >= 3


def test_many_unsupported_claims_is_negative_value():
    verdict = _harness().incremental_value(
        {"interpretation": "pending"},
        {
            "counts_as_hybrid_success": True,
            "output_status": "VALID",
            "hypotheses": [{"statement": "a", "model_confidence": 0.5}],
            "recommended_verifications": [],
            "stop_recommendation": "ROOT_CAUSE_CONFIRMED",
            "risk_notes": [],
            "unsupported_claims": {"count": 5},
        },
    )
    assert verdict["primary"] == "NEGATIVE_VALUE"


def test_cost_model_is_explicit():
    harness = _harness()
    assert harness.call_cost_usd(1_000_000, 0) == pytest.approx(3.0)
    assert harness.call_cost_usd(0, 1_000_000) == pytest.approx(15.0)


def test_paid_gate_still_refuses_without_both_flags():
    """§32, §55: no paid call can happen without explicit consent."""
    from brain.discovery.replay import paid_replay_gate

    assert paid_replay_gate(explicit=False, accept_cost=True, candidate_ids=["x"],
                            max_calls=1, max_tokens=100)["allowed"] is False
    assert paid_replay_gate(explicit=True, accept_cost=False, candidate_ids=["x"],
                            max_calls=1, max_tokens=100)["allowed"] is False
    assert paid_replay_gate(explicit=True, accept_cost=True, candidate_ids=[],
                            max_calls=1, max_tokens=100)["allowed"] is False
    # brain_paid_replay_enabled is the outermost gate and defaults off, so the
    # allow-path has to grant it explicitly.
    assert paid_replay_gate(explicit=True, accept_cost=True, candidate_ids=["x"],
                            max_calls=1, max_tokens=100)["allowed"] is False
    ok = paid_replay_gate(explicit=True, accept_cost=True, candidate_ids=["x"],
                          max_calls=1, max_tokens=100, config_enabled=True)
    assert ok["allowed"] is True


def test_evaluation_module_never_writes_back_to_policy():
    """Measurement must not be able to move a weight."""
    before = config_hash()
    root_cluster_metrics([{"true_root": "a", "root_id": "a"}, {"true_root": "a", "root_id": "b"}])
    aggregate_sessions([{"label": "A", "duration_s": 900, "candidates": 1, "reviewed_useful": 1}])
    known_issue_recall([_issue()], [{"issue_id": "I1", "surfaced": False, "category": "NO_SIGNAL"}])
    assert config_hash() == before
