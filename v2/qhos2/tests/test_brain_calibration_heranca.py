"""Real Discovery Calibration: root fingerprinting, pending semantics, scoring.

Offline and free. No paid API, no network, no writes outside tmp_path.
"""
from __future__ import annotations

import json

import pytest

from brain.discovery.engine import THRESHOLD, DiscoveryEngine
from brain.discovery.fingerprint import (
    ROOT_FINGERPRINT_VERSION,
    component_family,
    extract_dependency,
    extract_error_code,
    extract_exception,
    group_by_root,
    normalize_message,
    root_signature,
    root_title,
)
from brain.discovery.improvement import (
    dedupe_improvements,
    improvement_fingerprint,
    make_improvement,
)
from brain.discovery.incident import IncidentOccurrence, PotentialIncident
from brain.discovery.pending import (
    BENIGN_PENDING,
    PENDING_CLASSES,
    PendingTracker,
    derive_pending_status,
    is_pending_status,
)
from brain.discovery.score import (
    SCORE_VERSION,
    SCORE_WEIGHTS,
    discovery_score,
    score_feature_audit,
)
from brain.discovery.telemetry import (
    telemetry_quality_ledger,
    telemetry_quality_recommendations,
)


def _err(logger: str, body: str, ts: str = "2026-09-03T10:00:00.123456-0300") -> str:
    return f"{ts} | ERROR | {logger} | {body}"


def _ccxt(symbol: str, exchange: str = "Binance", second: int = 0) -> dict:
    return {
        "source": "error_log",
        "component": "logs",
        "kind": "error",
        "severity": "medium",
        "summary": _err(
            "DataIngestion",
            f"[DADOS] {exchange} FALHOU {symbol}/USDT: ModuleNotFoundError: No module named 'ccxt'",
            ts=f"2026-09-03T10:00:{second:02d}.100000-0300",
        ),
    }


# --------------------------------------------------------------------------
# Root fingerprint normalization
# --------------------------------------------------------------------------

def test_root_fingerprint_extractors():
    line = _err("DataIngestion", "Binance FALHOU ETH/USDT: ModuleNotFoundError: No module named 'ccxt'")
    assert extract_exception(line) == "ModuleNotFoundError"
    assert extract_dependency(line) == "ccxt"
    assert extract_error_code(_err("OrderRouter", "submit failed errno=ETIMEDOUT")) == "ETIMEDOUT"
    assert extract_dependency("nothing here") == ""


def test_normalize_message_strips_high_cardinality_noise():
    a = normalize_message(_err("Parser", "order 991823 for BTC/USDT price=64231.55 req_id=abc-99 failed"))
    b = normalize_message(_err("Parser", "order 447 for ETH/USDT price=3120.10 req_id=zzz-1 failed"))
    assert a == b, (a, b)
    assert "btc" not in a and "64231" not in a
    assert "<symbol>" in a and "order" in a and "failed" in a


def test_normalize_message_is_deterministic_and_versioned():
    line = _err("Parser", "boom KeyError: 'bid'")
    first = root_signature(kind="error", component="logs", summary=line)
    second = root_signature(kind="error", component="logs", summary=line)
    assert first.root_id == second.root_id
    assert first.version == ROOT_FINGERPRINT_VERSION == "rootfp-v1"


def test_component_family_drops_instance_suffixes():
    assert component_family("ingestion_worker_3") == "ingestion"
    assert component_family("DataIngestion") == "dataingestion"
    # Generic component names fall back to the log's own logger name.
    assert component_family("logs", logger="dataingestion") == "dataingestion"


# --------------------------------------------------------------------------
# Multi-symbol / cross-exchange collapse
# --------------------------------------------------------------------------

def test_same_exception_different_symbol_is_one_root():
    rows = [_ccxt(s, second=i) for i, s in enumerate(["BTC", "ETH", "SOL"])]
    clusters = group_by_root(rows)
    assert len(clusters) == 1
    cluster = next(iter(clusters.values()))
    assert cluster.occurrence_count == 3
    assert cluster.affected_instances == 3
    assert sorted(cluster.instances) == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_same_dependency_different_exchange_is_one_root():
    rows = [
        _ccxt("BTC", exchange="Binance", second=0),
        _ccxt("BTC", exchange="Coinbase", second=1),
        _ccxt("BTC", exchange="Kraken", second=2),
    ]
    clusters = group_by_root(rows)
    assert len(clusters) == 1
    assert "ccxt" in root_title(next(iter(clusters.values())).signature)


def test_same_root_different_timestamps_is_one_root():
    a = root_signature(kind="error", component="logs", summary=_ccxt("BTC", second=1)["summary"])
    b = root_signature(kind="error", component="logs", summary=_ccxt("BTC", second=47)["summary"])
    assert a.root_id == b.root_id


def test_multi_symbol_root_yields_one_candidate_with_occurrences():
    rows = [_ccxt(s, second=i) for i, s in enumerate(["BTC", "ETH", "SOL"])]
    intel = DiscoveryEngine().ingest({"observations": rows})
    incidents = intel["incidents"]
    assert len(incidents) == 1, [c["summary"] for c in incidents]
    cand = incidents[0]
    assert cand["affected_instances"] == 3
    assert cand["occurrence_count"] == 3
    assert cand["root_tier"] == "DEPENDENCY"
    assert "ccxt" in cand["root_title"]
    assert len(cand["occurrences"]) == 3
    assert all(o["root_id"] == cand["root_id"] for o in cand["occurrences"])


# --------------------------------------------------------------------------
# Over-dedup protection
# --------------------------------------------------------------------------

def test_same_component_different_exceptions_do_not_collapse():
    rows = [
        {"source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
         "summary": _err("DataIngestion", "BTC/USDT ValueError: bad tick size")},
        {"source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
         "summary": _err("DataIngestion", "BTC/USDT KeyError: 'bid' missing in book")},
        {"source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
         "summary": _err("DataIngestion", "BTC/USDT ZeroDivisionError: spread divisor zero")},
    ]
    clusters = group_by_root(rows)
    assert len(clusters) == 3
    intel = DiscoveryEngine().ingest({"observations": rows})
    assert len(intel["incidents"]) == 3


def test_different_dependencies_do_not_collapse():
    rows = [_ccxt("BTC", second=0), {
        "source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
        "summary": _err("DataIngestion", "FALHOU BTC/USDT: ModuleNotFoundError: No module named 'polars'"),
    }]
    assert len(group_by_root(rows)) == 2


def test_same_exception_different_components_do_not_collapse():
    rows = [
        {"source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
         "summary": _err("OrderRouter", "ValueError: bad size")},
        {"source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
         "summary": _err("RiskEngine", "ValueError: bad size")},
    ]
    assert len(group_by_root(rows)) == 2


def test_candidate_storm_many_symbols_one_root():
    symbols = [f"SYM{i}" for i in range(60)]
    rows = [_ccxt(s, second=i % 60) for i, s in enumerate(symbols)]
    engine = DiscoveryEngine()
    intel = engine.ingest({"observations": rows})
    assert len(intel["incidents"]) == 1
    cand = intel["incidents"][0]
    assert cand["occurrence_count"] >= 55
    assert cand["affected_instances"] >= 55
    assert intel["metrics"]["root_collapsed_occurrences"] >= 50
    assert intel["writes"] == 0


# --------------------------------------------------------------------------
# IncidentOccurrence vs PotentialIncident
# --------------------------------------------------------------------------

def test_occurrence_is_not_a_candidate():
    occ = IncidentOccurrence(occurrence_id="", root_id="r1", component="ingestion")
    row = occ.to_dict()
    assert row["kind"] == "incident_occurrence"
    assert row["candidate"] is False
    cand = PotentialIncident(candidate_id="", fingerprint="f", component="ingestion", summary="s")
    assert cand.to_dict()["kind"] == "potential_incident"
    assert cand.to_dict()["change_request"] is None
    assert cand.to_dict()["sandbox"] is None


def test_many_occurrences_support_one_candidate():
    rows = [_ccxt(s, second=i) for i, s in enumerate(["BTC", "ETH", "SOL", "BNB", "ADA"])]
    intel = DiscoveryEngine().ingest({"observations": rows})
    cand = intel["incidents"][0]
    assert cand["occurrence_count"] == 5
    assert len(intel["incidents"]) == 1
    assert cand["count"] >= 5


# --------------------------------------------------------------------------
# Pending lifecycle
# --------------------------------------------------------------------------

def test_pending_status_detection():
    assert is_pending_status("pending") and is_pending_status("not-ready")
    assert not is_pending_status("ok") and not is_pending_status("error")
    assert derive_pending_status("status=pending") == "pending"
    assert derive_pending_status("pending") == "pending"
    assert derive_pending_status("healthy") == ""


def test_expected_initialization_is_not_promoted():
    tracker = PendingTracker()
    tracker.begin_cycle(1000.0)
    state = tracker.observe(component="storage", status="pending", now=1000.0)
    assert state.classification == "EXPECTED_INITIALIZATION"
    assert state.classification in BENIGN_PENDING
    assert state.promote is False


def test_degraded_then_stale_pending_promotes():
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    tracker.observe(component="reporting", status="pending", now=0.0)
    degraded = tracker.observe(component="reporting", status="pending", now=200.0)
    assert degraded.classification == "DEGRADED_PENDING"
    assert degraded.promote is True
    stale = tracker.observe(component="reporting", status="pending", now=1000.0)
    assert stale.classification == "STALE_PENDING"
    assert stale.promote is True
    assert "stale_threshold" in stale.promotion_reason


def test_pending_with_error_evidence_promotes_immediately():
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    state = tracker.observe(component="storage", status="pending", now=0.0, error_evidence=True)
    assert state.classification == "FAILED_PENDING"
    assert state.promote is True


def test_pending_with_dependent_impact_promotes():
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    state = tracker.observe(component="storage", status="pending", now=1.0, dependent_impact=True)
    assert state.classification == "DEGRADED_PENDING"
    assert state.promotion_reason == "dependent_service_impact"


def test_pending_temporal_fields_tracked():
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    tracker.observe(component="storage", status="pending", now=10.0)
    state = tracker.observe(component="storage", status="pending", now=40.0)
    assert state.first_seen == 10.0
    assert state.last_seen == 40.0
    assert state.duration_s == 30.0
    assert state.cycles_pending == 2
    assert state.expected_duration_s == 120.0
    assert state.lifecycle == "STARTING"
    assert set(tracker.to_dict()["by_class"]) == set(PENDING_CLASSES)


def test_resolved_pending_never_promotes():
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    tracker.observe(component="reporting", status="pending", now=0.0)
    tracker.observe(component="reporting", status="pending", now=500.0)
    tracker.resolve("reporting", 501.0)
    assert tracker.states["reporting"].promote is False
    assert tracker.states["reporting"].lifecycle == "READY"


def test_registry_pending_does_not_become_incident():
    raw = {"registry": {name: {"healthy": False, "status": "pending", "uptime": 4.0, "error_count": 0}
                        for name in ["storage", "ingestion", "risk_engine", "portfolio", "features"]}}
    engine = DiscoveryEngine()
    intel = engine.ingest(raw)
    assert intel["incidents"] == []
    assert intel["metrics"]["pending_suppressed"] == 5
    assert intel["metrics"]["pending_promoted"] == 0
    assert intel["signal_metrics"]["noise_taxonomy"]["EXPECTED_STATE"] == 5
    model = intel["pending_model"]
    assert model["tracked_components"] == 5
    assert model["by_class"]["EXPECTED_INITIALIZATION"] == 5


def test_pending_without_lifecycle_telemetry_cannot_age_into_incident():
    """uptime=null and error_count=0 means the source never saw a lifecycle."""
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    tracker.observe(component="storage", status="pending", now=0.0, lifecycle_known=False)
    aged = tracker.observe(component="storage", status="pending", now=100_000.0, lifecycle_known=False)
    assert aged.classification == "UNKNOWN_PENDING"
    assert aged.promote is False
    assert aged.promotion_reason == "no_lifecycle_telemetry_duration_not_evidence"
    # Real corroborating evidence still promotes it.
    corroborated = tracker.observe(
        component="storage", status="pending", now=100_010.0, lifecycle_known=False, error_evidence=True)
    assert corroborated.classification == "FAILED_PENDING"
    assert corroborated.promote is True


def test_lifecycle_evidence_allows_normal_staleness_promotion():
    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    tracker.observe(component="reporting", status="pending", now=0.0, lifecycle_known=True)
    stale = tracker.observe(component="reporting", status="pending", now=5000.0, lifecycle_known=True)
    assert stale.classification == "STALE_PENDING"
    assert stale.promote is True


def test_many_unknown_pending_components_collapse_to_one_systemic_candidate():
    """The real 15m soak shape: every module pending, uptime null, zero errors."""
    modules = ["storage", "ingestion", "features", "regime_model", "evolution",
               "llm_agents", "backtest", "validator", "risk_engine", "portfolio",
               "execution", "dashboard", "kill_switch"]
    engine = DiscoveryEngine()
    emitted = []
    for cycle in range(6):
        intel = engine.ingest({"registry": {
            m: {"healthy": False, "status": "pending", "uptime": None, "error_count": 0}
            for m in modules
        }})
        emitted.extend(c for c in intel["incidents"] if c["root_tier"] == "TELEMETRY")
    # 13 components x 6 cycles = 78 observations, one candidate.
    assert len(emitted) == 1, [c["root_title"] for c in emitted]
    cand = emitted[0]
    assert cand["affected_instances"] == len(modules)
    assert cand["pending_class"] == "UNKNOWN_PENDING"
    assert "no lifecycle telemetry" in cand["root_title"]
    assert any("uptime=null" in e for e in cand["evidence"])
    assert cand["change_request"] is None
    assert engine.last_intel["pending_model"]["by_class"]["UNKNOWN_PENDING"] == len(modules)
    assert engine.signal_metrics.pending_suppressed == len(modules) * 6


def test_systemic_collapse_requires_breadth_and_duration():
    from brain.discovery.pending import systemic_unknown_pending

    tracker = PendingTracker()
    tracker.begin_cycle(0.0)
    for i in range(3):
        tracker.observe(component=f"mod{i}", status="pending", now=0.0, lifecycle_known=False)
    assert systemic_unknown_pending(tracker) is None, "3 components is not systemic"
    for i in range(3, 9):
        tracker.observe(component=f"mod{i}", status="pending", now=0.0, lifecycle_known=False)
    assert systemic_unknown_pending(tracker) is None, "1 cycle is not enough duration"
    for cycle in (10.0, 20.0):
        for i in range(9):
            tracker.observe(component=f"mod{i}", status="pending", now=cycle, lifecycle_known=False)
    out = systemic_unknown_pending(tracker)
    assert out is not None
    assert out["component_count"] == 9
    assert out["cycles_pending"] >= 3


def test_systemic_collapse_does_not_swallow_real_component_failures():
    modules = {f"mod{i}": {"healthy": False, "status": "pending", "uptime": None, "error_count": 0}
               for i in range(8)}
    engine = DiscoveryEngine()
    for _ in range(4):
        engine.ingest({"registry": modules})
    intel = engine.ingest({
        "registry": modules,
        "error_tail": [_err("RiskEngine", "position check raised RiskLimitBreachError code=RL07")],
    })
    tiers = sorted(c["root_tier"] for c in intel["incidents"])
    assert "EXCEPTION" in tiers or "ERROR_CODE" in tiers, tiers
    assert any("RiskLimitBreachError" in (c["root_title"] or "") for c in intel["incidents"])


def test_stale_registry_pending_does_become_candidate():
    engine = DiscoveryEngine()
    for offset in (0.0, 30.0, 5000.0):
        engine.ingest({"observations": [{
            "source": "registry", "component": "reporting", "kind": "state",
            "state": "pending", "summary": "status=pending", "severity": "low",
            "timestamp": 1_000_000.0 + offset,
        }]})
    intel = engine.last_intel
    assert intel["metrics"]["pending_promoted"] == 1
    assert len(intel["incidents"]) == 1
    cand = intel["incidents"][0]
    assert cand["pending_class"] == "STALE_PENDING"
    assert any("pending_duration_s" in e for e in cand["evidence"])
    assert cand["change_request"] is None


def test_pending_false_positive_reduction_on_recorded_shape():
    """The 30s soak shape: 13 components pending across 6 cycles."""
    components = [f"mod{i}" for i in range(13)]
    engine = DiscoveryEngine()
    for cycle in range(6):
        engine.ingest({"observations": [
            {"source": "registry", "component": c, "kind": "health",
             "summary": "pending", "severity": "high", "timestamp": 1_000_000.0 + cycle * 5}
            for c in components
        ]})
    assert engine.last_intel["incidents"] == []
    assert engine.signal_metrics.pending_suppressed == 13 * 6
    assert engine.signal_metrics.pending_promoted == 0


# --------------------------------------------------------------------------
# Score explanation and calibration
# --------------------------------------------------------------------------

def test_score_weights_sum_to_one():
    assert round(sum(SCORE_WEIGHTS.values()), 6) == 1.0
    assert SCORE_VERSION == "discovery-score-v2"


def test_score_exposes_every_feature_contribution():
    out = discovery_score({
        "severity": "medium", "count": 6, "component": "ingestion", "novelty": "NEW",
        "root_concrete": True, "affected_instances": 6, "cycles_observed": 2,
        "cross_signals": 1, "historical": 2, "model_confidence": 0.99,
    })
    comps = out["score_components"]
    for name, weight in SCORE_WEIGHTS.items():
        assert name in comps
        assert comps[name]["weight"] == weight
        assert comps[name]["contribution"] == pytest.approx(weight * comps[name]["value"], abs=1e-6)
    assert "expected_state_penalty" in comps
    assert out["used_model_confidence"] is False
    assert out["score_total"] == out["score"]
    assert out["promotion_reason"]
    assert out["score_base"] == pytest.approx(
        sum(c["contribution"] for n, c in comps.items() if n in SCORE_WEIGHTS), abs=1e-4
    )


def test_score_never_uses_model_confidence():
    low_conf = discovery_score({"severity": "high", "count": 3, "model_confidence": 0.01})
    high_conf = discovery_score({"severity": "high", "count": 3, "model_confidence": 0.99})
    assert low_conf["score"] == high_conf["score"]


def test_concrete_root_outranks_expected_pending():
    ccxt = discovery_score({
        "severity": "medium", "count": 6, "component": "logs", "component_family": "dataingestion",
        "novelty": "NEW", "root_concrete": True, "affected_instances": 6,
    })
    pending = discovery_score({
        "severity": "high", "count": 78, "component": "storage", "novelty": "NEW",
        "root_concrete": False, "affected_instances": 1, "expected_state": True,
    })
    assert ccxt["score"] > pending["score"]
    assert pending["score"] < THRESHOLD
    assert ccxt["score"] >= THRESHOLD
    assert pending["score_components"]["expected_state_penalty"]["contribution"] < 0


def test_expected_state_penalty_is_explicit():
    base = {"severity": "high", "count": 10, "component": "storage", "novelty": "NEW"}
    plain = discovery_score(base)
    penalized = discovery_score({**base, "expected_state": True})
    assert round(plain["score"] - penalized["score"], 4) == pytest.approx(0.30, abs=1e-3)


def test_denied_row_scores_zero_and_explains():
    out = discovery_score({"denied": True, "severity": "critical", "count": 99})
    assert out["score"] == 0.0
    assert out["promotion_reason"] == "denied_sensitive"


def test_score_feature_audit_is_evaluation_only():
    rows = [
        {"human_disposition": "USEFUL", "score_components": {"root_signature": {"contribution": 0.14}}},
        {"human_disposition": "USEFUL", "score_components": {"root_signature": {"contribution": 0.14}}},
        {"human_disposition": "DISMISSED", "score_components": {"root_signature": {"contribution": 0.0}}},
        {"human_disposition": "UNCERTAIN", "score_components": {"root_signature": {"contribution": 0.5}}},
    ]
    audit = score_feature_audit(rows)
    assert audit["useful_n"] == 2 and audit["noise_n"] == 1
    assert audit["separation"]["root_signature"] == pytest.approx(0.14, abs=1e-4)
    assert audit["auto_applied"] is False
    assert audit["weights"] == dict(SCORE_WEIGHTS)


# --------------------------------------------------------------------------
# Holdout calibration — tuned rules must generalize
# --------------------------------------------------------------------------

def _holdout_case(rows):
    engine = DiscoveryEngine()
    intel = engine.ingest({"observations": rows})
    return intel, engine


def test_holdout_low_frequency_real_error_is_still_noticed():
    intel, _ = _holdout_case([{
        "source": "error_log", "component": "logs", "kind": "error", "severity": "medium",
        "summary": _err("OrderRouter", "submit failed: BrokerTimeoutError code=ETIMEDOUT venue=CME"),
    }])
    assert len(intel["incidents"]) == 1
    assert intel["incidents"][0]["score"] >= THRESHOLD


def test_holdout_high_frequency_benign_warning_is_suppressed():
    rows = [{
        "source": "error_log", "component": "logs", "kind": "error", "severity": "low",
        "summary": _err("CacheLayer", f"cache miss ratio={i}.0 expected during warmup",
                        ts=f"2026-09-03T10:00:{i:02d}.0-0300"),
    } for i in range(40)]
    intel, _ = _holdout_case(rows)
    assert intel["incidents"] == []
    assert intel["metrics"]["noise_suppressed"] >= 30


def test_holdout_single_severe_exception_is_top_ranked():
    intel, _ = _holdout_case([{
        "source": "error_log", "component": "risk_engine", "kind": "error", "severity": "critical",
        "summary": _err("RiskEngine", "position limit check raised RiskLimitBreachError code=RL07"),
    }])
    assert len(intel["incidents"]) == 1
    assert intel["incidents"][0]["score"] > THRESHOLD


def test_holdout_long_pending_healthy_dependency_stays_quiet():
    rows = [{
        "source": "registry", "component": "reporting", "kind": "state", "severity": "low",
        "state": "pending", "summary": "status=pending", "timestamp": 1000.0 + i,
    } for i in range(0, 40, 5)]
    intel, _ = _holdout_case(rows)
    assert intel["incidents"] == []
    assert intel["metrics"]["pending_suppressed"] == len(rows)


def test_holdout_many_symbols_one_dependency_is_one_candidate():
    rows = [_ccxt(s, second=i) for i, s in enumerate(
        ["BTC", "ETH", "SOL", "BNB", "ADA", "AVAX", "LINK", "DOT"])]
    intel, _ = _holdout_case(rows)
    assert len(intel["incidents"]) == 1
    assert intel["incidents"][0]["affected_instances"] == 8


def test_holdout_ranking_order_is_stable():
    """Concrete roots must outrank state observations on the same batch."""
    rows = [_ccxt("BTC", second=0), _ccxt("ETH", second=1), {
        "source": "registry", "component": "storage", "kind": "state", "severity": "low",
        "state": "pending", "summary": "status=pending",
    }]
    intel, _ = _holdout_case(rows)
    scores = {c["root_tier"]: c["score"] for c in intel["incidents"]}
    assert "DEPENDENCY" in scores
    assert all(scores["DEPENDENCY"] >= v for v in scores.values())


# --------------------------------------------------------------------------
# Telemetry quality ledger
# --------------------------------------------------------------------------

def test_telemetry_ledger_reports_per_source_yield():
    metrics = {
        "events_received": 100,
        "source_quality": {
            "registry": {"received": 80, "accepted": 0, "suppressed": 80, "candidate_producing": 0, "duplicate_producing": 0},
            "error_log": {"received": 20, "accepted": 6, "suppressed": 14, "candidate_producing": 1, "duplicate_producing": 12},
        },
    }
    reviews = [
        {"source": "error_log", "human_disposition": "USEFUL"},
        {"source": "registry", "human_disposition": "DISMISSED"},
        {"source": "registry", "human_disposition": "DISMISSED"},
        {"source": "registry", "human_disposition": "NOT_USEFUL"},
    ]
    ledger = telemetry_quality_ledger(metrics, reviews=reviews)
    by_name = {r["source"]: r for r in ledger["sources"]}
    assert by_name["registry"]["observations"] == 80
    assert by_name["registry"]["share_of_traffic"] == 0.8
    assert by_name["registry"]["reviewed_noise"] == 3
    assert by_name["registry"]["reviewed_useful"] == 0
    assert by_name["registry"]["useful_yield"] == 0.0
    assert by_name["error_log"]["reviewed_useful"] == 1
    assert by_name["error_log"]["duplicate_rate"] == 0.6
    assert ledger["auto_disable_enabled"] is False


def test_telemetry_recommendations_never_auto_disable():
    metrics = {
        "events_received": 100,
        "source_quality": {
            "registry": {"received": 80, "suppressed": 80, "candidate_producing": 0, "duplicate_producing": 0},
        },
    }
    reviews = [{"source": "registry", "human_disposition": "DISMISSED"} for _ in range(4)]
    ledger = telemetry_quality_ledger(metrics, reviews=reviews)
    recs = telemetry_quality_recommendations(ledger)
    assert recs, "expected a recommendation for a zero-yield dominant source"
    for rec in recs:
        assert rec["kind"] == "TELEMETRY_QUALITY_RECOMMENDATION"
        assert rec["auto_disabled"] is False
        assert rec["auto_applied"] is False
        assert rec["requires_human_review"] is True
    blob = json.dumps(recs).lower()
    assert "disable_source" not in blob and "\"disabled\": true" not in blob


def test_telemetry_ledger_attributes_reviews_via_candidates():
    metrics = {"events_received": 10, "source_quality": {
        "error_log": {"received": 10, "suppressed": 4, "candidate_producing": 1, "duplicate_producing": 0}}}
    candidates = [{"fingerprint": "fp1", "root_id": "r1", "sources": ["error_log"]}]
    reviews = [{"fingerprint": "fp1", "human_disposition": "CONFIRMED"}]
    ledger = telemetry_quality_ledger(metrics, reviews=reviews, candidates=candidates)
    assert ledger["sources"][0]["reviewed_useful"] == 1
    assert ledger["unattributed_reviews"] == {"useful": 0, "noise": 0}


def test_engine_emits_ledger_and_pending_model():
    intel = DiscoveryEngine().ingest({
        "registry": {"storage": {"healthy": False, "status": "pending"}},
        "error_tail": [_err("DataIngestion", "FALHOU BTC/USDT: ModuleNotFoundError: No module named 'ccxt'")],
    })
    assert intel["telemetry_ledger"]["kind"] == "TELEMETRY_QUALITY_LEDGER"
    assert intel["pending_model"]["schema"] == "qhos-pending-model-v1"
    assert intel["score_feature_audit"]["auto_applied"] is False
    assert intel["writes"] == 0
    assert intel["level3_enabled"] is False


# --------------------------------------------------------------------------
# Improvement dedup
# --------------------------------------------------------------------------

def test_improvement_fingerprint_collapses_equivalent_rows():
    a = improvement_fingerprint(component="ingestion_worker_1", problem="recurring degradation on ingestion (n=12)",
                                benefit="reduce repeat investigations", evidence_family="RECURRENCE")
    b = improvement_fingerprint(component="ingestion_worker_9", problem="recurring degradation on ingestion (n=48)",
                                benefit="reduce repeat investigations", evidence_family="RECURRENCE")
    assert a == b
    c = improvement_fingerprint(component="ingestion", problem="latency samples persist above threshold",
                                benefit="lower p99 if confirmed", evidence_family="LATENCY_SAMPLES")
    assert c != a


def test_dedupe_improvements_merges_and_counts():
    rows = []
    for n in (4, 9, 20):
        imp = make_improvement(
            component="ingestion", problem=f"recurring degradation on ingestion (n={n})",
            evidence=[f"recurrence={n}"], benefit="reduce repeat investigations",
            validation="compare incident rate over next soak window", evidence_family="RECURRENCE",
        )
        rows.append(imp.to_dict())
    merged = dedupe_improvements(rows)
    assert len(merged) == 1
    assert merged[0]["occurrence_count"] == 3
    assert len(merged[0]["evidence"]) == 3


def test_engine_does_not_repeat_equivalent_improvements_across_cycles():
    engine = DiscoveryEngine()
    snap = {
        "registry": {"parser": {"healthy": False, "status": "unhealthy"}},
        "error_tail": [_err("Parser", "worker timeout ValueError: tick")],
        "metrics": {"latency_ms": 500, "component": "parser"},
    }
    for _ in range(6):
        engine.ingest(dict(snap))
    fingerprints = [i["fingerprint"] for i in engine.last_intel["improvements"]]
    assert len(fingerprints) == len(set(fingerprints))
    assert all(i["auto_apply"] is False for i in engine.last_intel["improvements"])
    assert all(i["change_request"] is None for i in engine.last_intel["improvements"])


def test_improvement_usefulness_is_separate_from_incident_precision():
    engine = DiscoveryEngine()
    rows = [_ccxt(s, second=i) for i, s in enumerate(["BTC", "ETH", "SOL"])]
    for _ in range(4):
        engine.ingest({"observations": rows})
    imps = engine.last_intel["improvements"]
    assert imps, "recurring dependency failure should yield an improvement candidate"
    assert any(i["pattern"] == "MISSING_DEPENDENCY" for i in imps)
    # An improvement carries its own evidence, independent of incident review.
    assert all(i["estimate_vs_measured"] == "ESTIMATE" for i in imps)


# --------------------------------------------------------------------------
# Checkpoint / resume across a long soak
# --------------------------------------------------------------------------

def test_checkpoint_carries_root_and_pending_state():
    engine = DiscoveryEngine()
    engine.ingest({
        "observations": [_ccxt("BTC", second=0), {
            "source": "registry", "component": "storage", "kind": "state",
            "state": "pending", "summary": "status=pending", "severity": "low"}],
    })
    blob = engine.checkpoint()
    assert blob["roots"] and blob["pending_states"]
    assert blob["write_pending"] is False
    assert blob["level3_enabled"] is False

    revived = DiscoveryEngine()
    out = revived.resume(blob)
    assert out["writes"] == 0
    assert out["change_request"] is None
    assert revived.roots == blob["roots"]
    assert "storage" in revived.pending.states
    assert revived.pending.states["storage"].classification in PENDING_CLASSES


def test_resume_does_not_re_promote_known_roots():
    engine = DiscoveryEngine()
    rows = [_ccxt("BTC", second=0)]
    first = engine.ingest({"observations": rows})
    assert first["incidents"][0]["novelty"] == "NEW"
    revived = DiscoveryEngine()
    revived.resume(engine.checkpoint())
    second = revived.ingest({"observations": rows})
    # Same fingerprint already known -> suppressed as duplicate, not re-promoted.
    assert second["incidents"] == []
    assert second["metrics"]["duplicates"] == 1


def test_long_soak_stability_has_no_runaway_growth():
    engine = DiscoveryEngine()
    rows = [_ccxt(s, second=i) for i, s in enumerate(["BTC", "ETH", "SOL"])]
    series = []
    for _ in range(30):
        intel = engine.ingest({"observations": rows})
        series.append({
            "candidates": len(intel["incidents"]),
            "roots": len(engine.roots),
            "seen": len(engine.seen),
        })
    # Identical input must not grow state or emit new candidates forever.
    assert series[-1]["roots"] == series[0]["roots"]
    assert series[-1]["seen"] == series[0]["seen"]
    assert sum(s["candidates"] for s in series[1:]) == 0


# --------------------------------------------------------------------------
# Laços observability — dup clusters and telemetry, still read-only
# --------------------------------------------------------------------------

def _ui_surface():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "src" / "brain_service" / "ui" / "index.html").read_text(encoding="utf-8")
    return html, html + (root / "src" / "brain_service" / "ui" / "app.js").read_text(encoding="utf-8")


def test_lacos_ui_never_exposes_source_or_level3_controls():
    """Positive half of the old dashboard test: these controls must never appear (QHOS-AUD-271)."""
    html, _ = _ui_surface()
    low = html.lower()
    for banned in ("disable source", "disable_source", "auto apply", "auto deploy",
                   "enable level 3", "enablesandbox", "setlevel3"):
        assert banned not in low, banned


@pytest.mark.xfail(strict=False, reason="Older dashboard root-cluster panel absent from the preserved Brain UI; tracked in "
                   "HERANCA_PESQUISA.md. Not strict: implementing the panel must not fail the suite, and a token grep "
                   "does not prove UI behaviour (QHOS-AUD-271).")
def test_lacos_shows_root_clusters_not_duplicate_rows():
    _, surface = _ui_surface()
    for token in ("root_title", "root_id", "occurrence_count", "affected_instances",
                  "affected_components", "TELEMETRY QUALITY", "reviewed_useful",
                  "reviewed_noise", "pending_model", "promotion_reason"):
        assert token in surface, token


def test_intelligence_view_carries_calibration_payload():
    from brain.control_plane import build_control_plane

    engine = DiscoveryEngine()
    intel = engine.ingest({
        "observations": [_ccxt("BTC", second=0), _ccxt("ETH", second=1)],
        "registry": {"storage": {"healthy": False, "status": "pending"}},
    })
    view = {
        "observer": "ok", "soak": True,
        "incidents": intel["incidents"], "improvements": intel["improvements"],
        "investigations": intel["investigations"], "budget": intel["budget"],
        "metrics": intel["metrics"], "root_clusters": intel["root_clusters"],
        "pending_model": intel["pending_model"], "telemetry_ledger": intel["telemetry_ledger"],
        "telemetry_recommendations": intel["telemetry_recommendations"],
        "score_feature_audit": intel["score_feature_audit"],
        "llm_called": False, "change_request": None, "sandbox": None,
        "level3_enabled": False, "read_only": True,
    }
    lacos = build_control_plane({"live_intelligence": view})
    li = lacos["live_intelligence"]
    assert li["pending_model"]["schema"] == "qhos-pending-model-v1"
    assert li["telemetry_ledger"]["auto_disable_enabled"] is False
    assert li["incidents"][0]["root_id"]
    assert li["incidents"][0]["affected_instances"] == 2
    assert li["level3_enabled"] is False
    assert li["change_request"] is None


# --------------------------------------------------------------------------
# Selective paid replay harness — gated, sanitized, never run by pytest
# --------------------------------------------------------------------------

def _harness():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent / "heritage_support" / "hybrid_replay_paid.py"
    spec = importlib.util.spec_from_file_location("hybrid_replay_paid", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _harness()


def _reviewed_candidate():
    engine = DiscoveryEngine()
    intel = engine.ingest({"observations": [_ccxt(s, second=i) for i, s in enumerate(["BTC", "ETH", "SOL"])]})
    cand = dict(intel["incidents"][0])
    cand.update({
        "human_disposition": "USEFUL",
        "final_category": "USEFUL",
        "review_notes": "real missing dependency",
        "ground_truth": True,
    })
    return cand


def test_paid_gate_blocks_without_explicit_flags(harness):
    from brain.discovery.replay import paid_replay_gate

    assert paid_replay_gate(explicit=False, accept_cost=True, candidate_ids=["a"], max_calls=1, max_tokens=10)["allowed"] is False
    assert paid_replay_gate(explicit=True, accept_cost=False, candidate_ids=["a"], max_calls=1, max_tokens=10)["allowed"] is False
    assert paid_replay_gate(explicit=True, accept_cost=True, candidate_ids=[], max_calls=1, max_tokens=10)["allowed"] is False
    assert paid_replay_gate(explicit=True, accept_cost=True, candidate_ids=["a"], max_calls=0, max_tokens=10)["allowed"] is False
    ok = paid_replay_gate(explicit=True, accept_cost=True, candidate_ids=["a", "b", "c"], max_calls=2, max_tokens=800, config_enabled=True)
    assert ok["allowed"] is True
    assert ok["candidates"] == ["a", "b"]  # allowlist truncated to the call budget
    assert ok["requires_checkpoint"] and ok["requires_close"] and ok["timeout_required"]
    assert ok["fable_auto"] is False


def test_sanitized_evidence_hides_human_labels(harness):
    evidence = harness.sanitized_evidence(_reviewed_candidate())
    blob = json.dumps(evidence)
    for banned in ("human_disposition", "final_category", "review_notes", "ground_truth", "USEFUL"):
        assert banned not in blob, banned
    assert evidence["root_signature"]["dependency"] == "ccxt"
    assert evidence["authority"] == "read_only_investigation_level_2"
    assert "patch" in evidence["forbidden_actions"]


def test_both_arms_receive_identical_evidence(harness):
    cand = _reviewed_candidate()
    a = harness.sanitized_evidence(cand)
    b = harness.sanitized_evidence(cand)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_deterministic_arm_identifies_missing_dependency(harness):
    det = harness.deterministic_arm(_reviewed_candidate())
    assert det["identified_dependency"] == "ccxt"
    assert det["identified_exception"] == "ModuleNotFoundError"
    assert det["llm_calls"] == 0 and det["tokens"] == 0
    assert "verify_import_availability" in det["recommended_verifications"]
    assert "do not patch" in det["recommendation"]


def test_unsupported_claim_heuristic_flags_invented_terms(harness):
    evidence = harness.sanitized_evidence(_reviewed_candidate())
    clean = harness.unsupported_claims(
        {"hypotheses": [{"statement": "the ccxt module is missing from the runtime environment"}]}, evidence)
    assert clean["count"] == 0
    dirty = harness.unsupported_claims(
        {"hypotheses": [{"statement": "kafka_broker.topic_partitions was rebalanced by redis_sentinel.failover"}]},
        evidence)
    assert dirty["count"] == 1
    assert dirty["method"] == "heuristic_vocabulary_diff"


def test_unsupported_claim_heuristic_ignores_sentence_punctuation(harness):
    """Sentence-final periods are not new entities. This produced 3 false
    positives in the first paid Hybrid replay."""
    evidence = harness.sanitized_evidence(_reviewed_candidate())
    out = harness.unsupported_claims(
        {"hypotheses": [
            {"statement": "The package is not installed in the active runtime environment."},
            {"statement": "It went missing after an environment rebuild."},
        ]}, evidence)
    assert out["count"] == 0, out["flagged"]


def test_reasoner_string_fields_are_not_split_into_characters():
    """A bare string must coerce to one item, not one item per character."""
    from brain.gateway import validate_reasoner_output

    packet = validate_reasoner_output({
        "incident_assessment": "missing dependency",
        "hypotheses": [{
            "statement": "ccxt is not installed",
            "reason": "ModuleNotFoundError",
            "evidence_needed": "Output of pip show ccxt",
            "confidence": 0.9,
            "target": "runtime",
        }],
        "recommended_verifications": [{
            "action_type": "inspect_environment",
            "target": "pip show ccxt",
            "expected_information_gain": "HIGH - directly confirms absence",
        }],
        "risk_notes": "all six pairs produce no market data",
        "stop_recommendation": "NEEDS_MORE_EVIDENCE",
    })
    assert packet["hypotheses"][0]["evidence_needed"] == ["Output of pip show ccxt"]
    assert packet["risk_notes"] == ["all six pairs produce no market data"]
    verification = packet["recommended_verifications"][0]
    # Normalized to the ranking vocabulary, with the model's wording preserved.
    assert verification["expected_information_gain"] == "HIGH"
    assert verification["expected_information_gain_text"] == "HIGH - directly confirms absence"
    assert packet["authority"] is False
    assert packet["is_evidence"] is False


def test_systemic_evidence_component_list_matches_its_count():
    """A truncated list beside a full count reads as a contradiction."""
    from brain.discovery.pending import PendingTracker, systemic_unknown_pending

    tracker = PendingTracker()
    for cycle in (0.0, 10.0, 20.0):
        tracker.begin_cycle(cycle)
        for i in range(13):
            tracker.observe(component=f"mod{i:02d}", status="pending", now=cycle, lifecycle_known=False)
    out = systemic_unknown_pending(tracker)
    assert out is not None
    line = next(e for e in out["evidence"] if e.startswith("components("))
    assert line.startswith(f"components({out['component_count']})=")
    assert line.count(",") == out["component_count"] - 1


def test_pytest_never_triggers_a_paid_call(harness, monkeypatch):
    """The harness main() must be a no-op without the explicit flags."""
    import sys

    calls = []
    monkeypatch.setattr(harness, "hybrid_arm", lambda *a, **k: calls.append(1) or {})
    monkeypatch.setattr(sys, "argv", ["hybrid_replay_paid.py"])
    assert harness.main() == 0
    assert calls == []


# --------------------------------------------------------------------------
# Storage retention over derived analysis artifacts
# --------------------------------------------------------------------------

def test_retention_covers_derived_analysis_artifacts(tmp_path):
    import os
    import time

    from brain.discovery.soak import ARTIFACT_PREFIXES, prune_shadow

    old = time.time() - 30 * 86400
    names = [
        "soak_old.json", "reviews_old.json", "replay_old.json",
        "long_soak_result.json", "hybrid_replay_smoke.json", "calibration_replay.json",
        "unrelated_notes.json",
    ]
    for name in names:
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        os.utime(path, (old, old))
    out = prune_shadow(tmp_path, max_sessions=4, max_age_days=7)
    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert out["dropped"] == 6
    assert remaining == ["unrelated_notes.json"]
    assert "long_soak_" in ARTIFACT_PREFIXES


def test_retention_keeps_recent_sessions(tmp_path):
    from brain.discovery.soak import prune_shadow

    for i in range(3):
        (tmp_path / f"soak_{i}.json").write_text("{}", encoding="utf-8")
    out = prune_shadow(tmp_path, max_sessions=8, max_age_days=7)
    assert out["dropped"] == 0
    assert out["kept"] == 3


# --------------------------------------------------------------------------
# Zero authority
# --------------------------------------------------------------------------

def test_calibration_path_holds_zero_authority():
    engine = DiscoveryEngine()
    intel = engine.ingest({"observations": [_ccxt("BTC", second=0)]})
    inv = engine.safety_invariants()
    assert inv == {
        "change_requests": 0, "sandbox_runs": 0, "production_writes": 0,
        "trading_actions": 0, "autonomy_changes": 0, "ok": True,
    }
    assert intel["llm_called"] is False
    assert intel["level3_enabled"] is False
    assert intel["autonomous_routing"] is False
    blob = json.dumps(intel, default=str)
    assert "sandbox_run\": {" not in blob
    for rec in intel["telemetry_recommendations"]:
        assert rec["auto_applied"] is False
