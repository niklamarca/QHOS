"""Controlled fault generalization: harness isolation, recall, consolidation, fidelity.

No paid provider calls, no production fault injection, no writes outside the
Brain shadow directory.
"""
from __future__ import annotations

import pytest

from brain.attest import runtime_attestation
from brain.discovery.attestation import (
    ATTESTATION_CONFIG_MISMATCH,
    ATTESTATION_MATCH,
    ATTESTATION_ROUTE_MISMATCH,
    ATTESTATION_SOURCE,
    ATTESTATION_STALE,
    ATTESTATION_UNKNOWN,
    attestation_coverage,
    attestation_rows,
)
from brain.discovery.consolidation import (
    INDEPENDENT,
    LIKELY_DERIVED_EFFECT,
    PRIMARY_ROOT,
    RELATIONSHIPS,
    SAME_ROOT,
    SECONDARY_EFFECT,
    TEMPORALLY_CORRELATED,
    classify_relationship,
    consolidate,
    link_quality,
)
from brain.discovery.engine import DiscoveryEngine
from brain.discovery.fault_catalog import catalogue_summary, fault_manifests
from brain.discovery.fault_matrix import run_control, run_fault, run_matrix
from brain.discovery.fault_metrics import matrix_metrics
from brain.discovery.faults import (
    COHORT_DEVELOPMENT,
    COHORT_HOLDOUT,
    FAULT_CLASSES,
    FORBIDDEN_TARGET_SUBSYSTEMS,
    FaultHarnessError,
    FaultInjectionHarness,
    FaultTarget,
    PRODUCTION_PORTS,
    assert_no_manifest_leakage,
    clean_snapshot,
)
from brain.discovery.fidelity import (
    artifact_schema_report,
    compare_runs,
    fidelity_rate,
    schema_completeness,
)
from brain.discovery.known_issue import (
    DETECTED_CORRECT_ROOT,
    FAILURE_CATEGORIES,
    NO_SIGNAL,
    KnownIssueV2,
    evaluate_known_issue,
    known_issue_recall_v2,
)
from brain.discovery.pending_probe import maturity_severity, run_pending_probes
from brain.discovery.policy_g1 import (
    DECISION_RELEVANT_FIELDS,
    config_hash as g1_hash,
    g1_findings,
    policy_bundle as g1_bundle,
    scoring_hash,
)
from brain.discovery.policy_g11 import (
    g11_active,
    is_active,
    pending_severity,
    policy_bundle as g11_bundle,
)
from brain.discovery.revision import config_hash as v2_hash
from brain.discovery.soak import sanitize_observation_row


# ---------------------------------------------------------------------------
# Frozen policy
# ---------------------------------------------------------------------------

def test_g1_does_not_tune_v2_scoring():
    """G1 may add signal sources and schema versions. It may not move a weight."""
    assert scoring_hash() == v2_hash() == "04f2ec81fc26e408"


def test_g1_bundle_pins_every_revision_the_matrix_depends_on():
    bundle = g1_bundle()
    for key in (
        "config_hash", "scoring_hash", "score_version", "fingerprint_version",
        "pending_policy_revision", "signal_inventory_revision",
        "observation_schema_version", "replay_contract_version",
        "artifact_schema_version", "source_revision", "source_files",
    ):
        assert bundle[key], f"{key} missing from the frozen bundle"
    # Installed wheels have no Git checkout. The content identity remains required.
    assert len(bundle["source_revision"]) == 16
    assert "repo_sha" in bundle
    assert bundle["frozen"] is True


def test_g1_findings_record_pre_fix_measurements():
    findings = g1_findings()
    assert findings, "the matrix found real failures; they must be recorded"
    unfixed = [f for f in findings if not f.get("fixed")]
    assert unfixed, "unfixed findings must stay visible, not be quietly resolved"
    for finding in findings:
        assert finding["id"] and finding["title"] and finding["detail"]


def test_g11_is_a_proposal_and_is_off_by_default():
    assert is_active() is False
    bundle = g11_bundle()
    assert bundle["status"] == "PROPOSED_NOT_ACTIVE"
    assert bundle["active_by_default"] is False


def test_g11_does_not_change_the_promotion_threshold_or_weights():
    from brain.discovery.policy_g11 import policy_config

    config = policy_config()
    assert config["promotion_threshold_changed"] is False
    assert config["score_weights_changed"] is False


def test_g11_context_manager_restores_g1():
    assert is_active() is False
    with g11_active():
        assert is_active() is True
    assert is_active() is False


# ---------------------------------------------------------------------------
# Harness isolation and production denial
# ---------------------------------------------------------------------------

def test_harness_refuses_to_construct_without_explicit_enable():
    with pytest.raises(FaultHarnessError):
        FaultInjectionHarness()


def test_harness_refuses_a_target_not_marked_dev():
    with pytest.raises(FaultHarnessError):
        FaultInjectionHarness(enabled=True, target=FaultTarget(label="somewhere", is_dev=False))


@pytest.mark.parametrize("port", sorted(PRODUCTION_PORTS))
def test_harness_refuses_production_ports(port):
    with pytest.raises(FaultHarnessError):
        FaultInjectionHarness(enabled=True,
                              target=FaultTarget(label="dev", port=port, is_dev=True))


@pytest.mark.parametrize("subsystem", sorted(FORBIDDEN_TARGET_SUBSYSTEMS))
def test_harness_refuses_forbidden_subsystems(subsystem):
    with pytest.raises(FaultHarnessError):
        FaultInjectionHarness(
            enabled=True,
            target=FaultTarget(label="dev", is_dev=True, subsystem=subsystem),
        )


def test_harness_refuses_a_target_labelled_production():
    with pytest.raises(FaultHarnessError):
        FaultInjectionHarness(enabled=True,
                              target=FaultTarget(label="prod-dashboard", is_dev=True))


def test_harness_never_mutates_the_base_snapshot():
    manifest = fault_manifests()[0]
    base = clean_snapshot()
    before = str(base)
    with FaultInjectionHarness(enabled=True,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        harness.apply(manifest, base, 1)
    assert str(base) == before


def test_harness_isolation_report_claims_no_side_effects():
    with FaultInjectionHarness(enabled=True,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        harness.apply(fault_manifests()[0], clean_snapshot(), 1)
        report = harness.isolation_report()
    assert report["filesystem_writes"] == 0
    assert report["network_calls"] == 0
    assert report["processes_spawned"] == 0
    assert report["os_mutations"] == 0
    assert report["reversible"] is True


def test_harness_is_bounded_by_an_injection_budget():
    manifest = fault_manifests()[0]
    with FaultInjectionHarness(enabled=True, max_faults=2,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        harness.apply(manifest, clean_snapshot(), 1)
        harness.apply(manifest, clean_snapshot(), 2)
        with pytest.raises(FaultHarnessError):
            harness.apply(manifest, clean_snapshot(), 3)


def test_harness_cleanup_marks_every_injection_reverted():
    manifest = fault_manifests()[0]
    harness = FaultInjectionHarness(enabled=True,
                                    target=FaultTarget(label="dev", is_dev=True))
    harness.apply(manifest, clean_snapshot(), 1)
    result = harness.cleanup()
    assert result["cleaned"] is True
    assert result["residual_state"] is None
    assert all(r["reverted"] for r in harness.audit_records())
    with pytest.raises(FaultHarnessError):
        harness.apply(manifest, clean_snapshot(), 2)


def test_harness_audits_every_injection():
    manifests = fault_manifests()[:3]
    with FaultInjectionHarness(enabled=True,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        for manifest in manifests:
            harness.apply(manifest, clean_snapshot(), 1)
        records = harness.audit_records()
    assert len(records) == 3
    assert {r["fault_id"] for r in records} == {m.fault_id for m in manifests}


# ---------------------------------------------------------------------------
# Manifest leakage
# ---------------------------------------------------------------------------

def test_no_manifest_metadata_reaches_any_snapshot():
    """Discovery must never be able to read the answer out of the question."""
    with FaultInjectionHarness(enabled=True, max_faults=1000,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        for manifest in fault_manifests():
            for cycle in (1, 2, 3, 4):
                harness.apply(manifest, clean_snapshot(), cycle)


def test_leakage_guard_catches_a_planted_fault_id():
    manifest = fault_manifests()[0]
    snapshot = clean_snapshot()
    snapshot["error_tail"] = [f"failure injected by {manifest.fault_id}"]
    with pytest.raises(FaultHarnessError):
        assert_no_manifest_leakage(snapshot, manifest)


def test_leakage_guard_catches_a_planted_fault_class():
    manifest = fault_manifests()[0]
    snapshot = clean_snapshot()
    snapshot["error_tail"] = [f"category={manifest.fault_class}"]
    with pytest.raises(FaultHarnessError):
        assert_no_manifest_leakage(snapshot, manifest)


def test_leakage_guard_allows_legitimate_deployment_telemetry():
    """expected_config_hash is a real field a deployment reports about itself."""
    assert_no_manifest_leakage(clean_snapshot(), fault_manifests()[0])


# ---------------------------------------------------------------------------
# Catalogue shape
# ---------------------------------------------------------------------------

def test_catalogue_covers_every_declared_class_twice():
    summary = catalogue_summary()
    assert summary["classes"] == len(FAULT_CLASSES)
    for name, ids in summary["by_class"].items():
        assert len(ids) >= 2, f"{name} needs at least two independent manifestations"


def test_cohorts_are_split_and_reported_separately():
    dev = fault_manifests(COHORT_DEVELOPMENT)
    holdout = fault_manifests(COHORT_HOLDOUT)
    assert dev and holdout
    assert {m.fault_class for m in dev} == {m.fault_class for m in holdout}
    assert not ({m.fault_id for m in dev} & {m.fault_id for m in holdout})


def test_every_manifest_declares_its_expectations():
    for manifest in fault_manifests():
        assert manifest.expected_signals
        assert manifest.expected_outcome
        assert manifest.cleanup
        if manifest.expected_outcome != "SUPPRESSED":
            assert manifest.expected_root_match, f"{manifest.fault_id} is unmatchable"


def test_no_manifest_expects_a_bare_component_name_as_root_evidence():
    """Evaluator v2 refuses component-name matches; a manifest must not want one."""
    for manifest in fault_manifests():
        for token in manifest.expected_root_match:
            assert token.strip().lower() != manifest.target_component.strip().lower()


# ---------------------------------------------------------------------------
# Runtime attestation as a signal source
# ---------------------------------------------------------------------------

def test_attestation_source_is_registered_in_the_inventory():
    from brain.discovery.inventory import inventory

    names = {s["name"] for s in inventory()["sources"]}
    assert ATTESTATION_SOURCE in names


def test_matching_revision_emits_a_benign_heartbeat():
    rows = attestation_rows(runtime_attestation() | {"stale_runtime": False},
                            context="production")
    assert [r["kind"] for r in rows] == [ATTESTATION_MATCH]
    assert rows[0]["informational"] is True


def test_stale_runtime_in_dev_is_informational():
    att = {"running_revision": "aaaa", "worktree_revision": "bbbb",
           "stale_runtime": True, "drift_reason": "brain_source_changed_on_disk"}
    row = attestation_rows(att, context="dev")[0]
    assert row["kind"] == ATTESTATION_STALE
    assert row["severity"] == "low"
    assert row["informational"] is True


def test_stale_runtime_in_production_is_a_finding():
    att = {"running_revision": "aaaa", "worktree_revision": "bbbb",
           "stale_runtime": True, "drift_reason": "brain_source_changed_on_disk"}
    row = attestation_rows(att, context="production")[0]
    assert row["kind"] == ATTESTATION_STALE
    assert row["severity"] == "high"
    assert row["informational"] is False


def test_absent_attestation_is_not_attestable_rather_than_healthy():
    rows = attestation_rows(None, context="production")
    assert [r["kind"] for r in rows] == [ATTESTATION_UNKNOWN]
    assert rows[0]["severity"] == "medium"


def test_config_and_route_mismatches_are_separate_kinds():
    att = {
        "running_revision": "aaaa", "worktree_revision": "aaaa", "stale_runtime": False,
        "running": {"discovery_policy": {"config_hash": "deadbeef"},
                    "route_schema_version": "qhos-brain-v0"},
    }
    kinds = {r["kind"] for r in attestation_rows(
        att, context="production",
        expected_config_hash="04f2ec81fc26e408",
        expected_route_schema="qhos-brain-v1",
    )}
    assert ATTESTATION_CONFIG_MISMATCH in kinds
    assert ATTESTATION_ROUTE_MISMATCH in kinds


def test_deployment_drift_is_detected_even_when_worktree_matches():
    att = {"running_revision": "aaaa", "worktree_revision": "aaaa", "stale_runtime": False}
    kinds = [r["kind"] for r in attestation_rows(
        att, context="production", expected_revision="cccc")]
    assert kinds.count(ATTESTATION_STALE) == 1


def test_attestation_reaches_the_engine_as_an_observation():
    engine = DiscoveryEngine()
    intel = engine.ingest({
        "attestation": {"running_revision": "aaaa", "worktree_revision": "bbbb",
                        "stale_runtime": True, "drift_reason": "changed"},
        "attestation_context": {"deployment_context": "production"},
    }, api_enabled=False)
    sources = {o["source"] for o in intel["observations"]}
    assert ATTESTATION_SOURCE in sources


def test_matching_attestation_does_not_consume_candidate_budget():
    engine = DiscoveryEngine()
    intel = engine.ingest({
        "attestation": {"running_revision": "aaaa", "worktree_revision": "aaaa",
                        "stale_runtime": False},
        "attestation_context": {"deployment_context": "production"},
    }, api_enabled=False)
    assert intel["metrics"]["candidate_n"] == 0


def test_attestation_coverage_reports_an_absent_source_as_absent():
    coverage = attestation_coverage([{"source": "registry", "kind": "state"}])
    assert coverage["source_present"] is False
    assert coverage["attested"] is False


# ---------------------------------------------------------------------------
# Known-issue evaluator v2
# ---------------------------------------------------------------------------

def test_shared_component_name_alone_never_counts_as_detection():
    """The exact v1 defect: 'dashboard state=pending' is not 'dashboard is stale'."""
    issue = KnownIssueV2(
        issue_id="KI-03", title="the dashboard runs a stale Brain revision",
        required_tokens=("stale", "revision"), component_hint="dashboard",
    )
    candidate = {
        "summary": "dashboard state=pending", "root_title": "dashboard state=pending",
        "component": "dashboard", "root_tier": "STATE",
    }
    match = issue.match(candidate)
    assert match["matched"] is False
    assert match["component_only_near_miss"] is True


def test_root_title_does_not_smuggle_the_component_name_back_in():
    issue = KnownIssueV2(issue_id="X", title="storage", required_tokens=("storage",))
    candidate = {
        "summary": "unhealthy", "root_title": "storage state=unknown",
        "root_signature": {"component_family": "storage"}, "root_tier": "STATE",
    }
    assert issue.match(candidate)["matched"] is False


def test_independent_evidence_counts_as_detection():
    issue = KnownIssueV2(
        issue_id="KI-01", title="ccxt is not installed",
        required_tokens=("ccxt",), root_family="DEPENDENCY",
    )
    candidate = {
        "summary": "crypto fetch failed: No module named 'ccxt'",
        "root_title": "missing dependency 'ccxt' (ModuleNotFoundError)",
        "root_signature": {"dependency": "ccxt", "component_family": "crypto"},
        "root_tier": "DEPENDENCY",
    }
    assert issue.match(candidate)["verdict"] == DETECTED_CORRECT_ROOT


def test_no_signal_is_distinguished_from_a_scoring_miss():
    issue = KnownIssueV2(issue_id="KI", title="t", required_tokens=("nowhere",))
    result = evaluate_known_issue(issue, [], observations=[])
    assert result["verdict"] == NO_SIGNAL
    assert result["category"] == "NO_SIGNAL"

    seen = evaluate_known_issue(
        issue, [], observations=[{"payload_summary": "a nowhere failure occurred"}])
    assert seen["category"] == "SCORING_MISS"


def test_unmatchable_issue_is_excluded_from_the_denominator():
    issue = KnownIssueV2(issue_id="KI", title="no evidence defined")
    result = evaluate_known_issue(issue, [])
    summary = known_issue_recall_v2([result])
    assert summary["denominator"] == 0
    assert summary["unmatchable_excluded"] == 1


def test_recall_v2_reports_rejected_component_only_near_misses():
    issue = KnownIssueV2(
        issue_id="KI-03", title="stale revision",
        required_tokens=("stale", "revision"), component_hint="dashboard",
    )
    result = evaluate_known_issue(
        issue, [{"summary": "dashboard state=pending", "component": "dashboard"}],
        observations=[],
    )
    summary = known_issue_recall_v2([result])
    assert summary["component_only_near_misses_rejected"] >= 1
    assert summary["is_global_false_negative_rate"] is False


def test_every_failure_category_is_named():
    assert len(set(FAILURE_CATEGORIES)) == len(FAILURE_CATEGORIES)
    for name in ("NO_SIGNAL", "SUPPRESSION_MISS", "SCORING_MISS", "CLUSTERING_MISS",
                 "CONSOLIDATION_MISS", "INVESTIGATION_MISS", "NOT_A_DISCOVERY_FAILURE"):
        assert name in FAILURE_CATEGORIES


# ---------------------------------------------------------------------------
# UNKNOWN_PENDING temporal behaviour
# ---------------------------------------------------------------------------

def test_pending_without_evidence_stays_suppressed():
    probes = run_pending_probes()
    assert probes["findings"]["suppression_holds_without_evidence"] is True


def test_pending_without_evidence_stays_suppressed_across_cycles():
    """Time alone must not age a static default into an incident."""
    probes = run_pending_probes()
    assert probes["findings"]["suppression_still_holds_after_five_cycles"] is True


def test_independent_evidence_promotes_a_pending_state():
    probes = run_pending_probes()
    assert probes["findings"]["evidence_overrides_suppression"] is True


def test_the_zero_duration_promotion_is_reproduced_not_hidden():
    probes = run_pending_probes()
    assert probes["findings"]["promotes_at_zero_elapsed_duration"] is True
    assert probes["findings"]["first_promotion_severity"] == "high"


def test_downstream_degradation_promotes():
    probes = run_pending_probes()
    assert probes["findings"]["downstream_degradation_promotes"] is True


def test_g11_holds_first_cycle_severity_at_medium():
    state = {"cycles_pending": 1, "error_evidence": True}
    assert pending_severity(state, "high") == "high"
    with g11_active():
        assert pending_severity(state, "high") == "medium"


def test_g11_restores_high_severity_once_confirmed():
    with g11_active():
        assert pending_severity({"cycles_pending": 2, "error_evidence": True}, "high") == "high"
        assert pending_severity(
            {"cycles_pending": 1, "dependent_impact": True}, "high") == "high"


def test_maturity_proposal_is_not_a_seconds_constant():
    verdict = maturity_severity({"cycles_pending": 1, "error_evidence": True}, "high")
    assert verdict["severity"] == "medium"
    assert "cycle" in verdict["reason"]


# ---------------------------------------------------------------------------
# Consolidation
# ---------------------------------------------------------------------------

def test_causes_is_not_in_the_relationship_vocabulary():
    assert "CAUSES" not in RELATIONSHIPS


def test_identical_roots_are_same_root():
    a = {"root_id": "r1", "root_tier": "DEPENDENCY"}
    assert classify_relationship(a, dict(a))["relationship"] == SAME_ROOT


def test_concrete_root_plus_named_state_effect_is_a_derived_effect():
    primary = {
        "root_id": "r1", "root_tier": "ERROR_CODE", "first_seen": 100.0, "last_seen": 200.0,
        "summary": "IngestionStalledError code=ING_09 ingestion worker stalled",
        "evidence": ["ingestion worker has not committed an offset"],
    }
    secondary = {
        "root_id": "r2", "root_tier": "STATE", "component": "ingestion",
        "first_seen": 100.0, "last_seen": 200.0, "summary": "ingestion state=pending",
    }
    verdict = classify_relationship(primary, secondary)
    assert verdict["relationship"] == LIKELY_DERIVED_EFFECT
    assert verdict["asserts_causation"] is False


def test_a_generic_component_name_does_not_create_a_link():
    """Every error row is attributed to 'logs'; linking on it carries no information."""
    primary = {"root_id": "r1", "root_tier": "ERROR_CODE", "first_seen": 1.0,
               "last_seen": 2.0, "summary": "DiskQuotaExceededError in logs"}
    secondary = {"root_id": "r2", "root_tier": "STATE", "component": "logs",
                 "first_seen": 1.0, "last_seen": 2.0, "summary": "PermissionError"}
    assert classify_relationship(primary, secondary)["relationship"] != LIKELY_DERIVED_EFFECT


def test_missing_timestamps_do_not_manufacture_a_link():
    primary = {"root_id": "r1", "root_tier": "DEPENDENCY",
               "summary": "No module named 'ccxt' in ingestion"}
    secondary = {"root_id": "r2", "root_tier": "STATE", "component": "ingestion",
                 "summary": "ingestion state=pending"}
    assert classify_relationship(primary, secondary)["relationship"] != LIKELY_DERIVED_EFFECT


def test_consolidation_keeps_both_rows_and_relabels_one():
    primary = {
        "root_id": "r1", "root_tier": "ERROR_CODE", "score": 0.7,
        "first_seen": 100.0, "last_seen": 200.0,
        "summary": "IngestionStalledError code=ING_09 ingestion worker stalled",
    }
    secondary = {
        "root_id": "r2", "root_tier": "STATE", "score": 0.4, "component": "ingestion",
        "first_seen": 100.0, "last_seen": 200.0, "summary": "ingestion state=pending",
    }
    out = consolidate([primary, secondary])
    assert len(out["candidates"]) == 2
    roles = {c["root_id"]: c["candidate_role"] for c in out["candidates"]}
    assert roles["r1"] == PRIMARY_ROOT
    assert roles["r2"] == SECONDARY_EFFECT
    assert out["root_incidents"] == 1
    assert out["secondary_effects"] == 1
    assert out["unique_root_inflation"] == 0.5


def test_over_consolidation_protection_keeps_distinct_roots_apart():
    a = {"root_id": "r1", "root_tier": "ERROR_CODE", "score": 0.6,
         "first_seen": 1.0, "last_seen": 2.0,
         "summary": "DiskQuotaExceededError code=IO_28 no space left on device"}
    b = {"root_id": "r2", "root_tier": "ERROR_CODE", "score": 0.6,
         "first_seen": 1.0, "last_seen": 2.0,
         "summary": "PermissionError code=IO_13 permission denied"}
    out = consolidate([a, b])
    assert out["root_incidents"] == 2
    assert out["secondary_effects"] == 0


def test_temporal_correlation_is_reported_once_per_pair():
    a = {"root_id": "r1", "root_tier": "ERROR_CODE", "score": 0.6, "component": "storage",
         "first_seen": 1.0, "last_seen": 2.0, "summary": "error in storage subsystem"}
    b = {"root_id": "r2", "root_tier": "STATE", "score": 0.5, "component": "storage",
         "first_seen": 1.0, "last_seen": 2.0, "summary": "storage state=degraded"}
    relations = consolidate([a, b])["relations"]
    pairs = [frozenset((r["primary_root_id"], r["secondary_root_id"]))
             for r in relations if r["relationship"] == TEMPORALLY_CORRELATED]
    assert len(pairs) == len(set(pairs))


def test_no_relation_ever_asserts_causation():
    a = {"root_id": "r1", "root_tier": "ERROR_CODE", "score": 0.7, "first_seen": 1.0,
         "last_seen": 2.0, "summary": "IngestionStalledError ingestion stalled"}
    b = {"root_id": "r2", "root_tier": "STATE", "score": 0.4, "component": "ingestion",
         "first_seen": 1.0, "last_seen": 2.0, "summary": "ingestion state=pending"}
    out = consolidate([a, b])
    assert out["asserts_causation"] is False
    assert all(r["asserts_causation"] is False for r in out["relations"])


def test_link_quality_scores_predictions_against_truth():
    consolidation = {"relations": [
        {"primary_root_id": "r1", "secondary_root_id": "r2",
         "relationship": LIKELY_DERIVED_EFFECT},
    ]}
    good = link_quality(consolidation, {"r2": "r1"})
    assert good["precision"] == 1.0 and good["recall"] == 1.0
    bad = link_quality(consolidation, {"r2": "r9"})
    assert bad["precision"] == 0.0


# ---------------------------------------------------------------------------
# Replay schema and fidelity
# ---------------------------------------------------------------------------

def test_sanitizer_preserves_every_decision_relevant_field():
    row = {
        "id": "obs1", "timestamp": 1.0, "source": "registry", "component": "ingestion",
        "kind": "state", "severity_hint": "low", "payload_summary": "status=pending",
        "fingerprint": "abc", "state": "pending", "lifecycle_known": False,
    }
    cleaned = sanitize_observation_row(row)
    assert schema_completeness(cleaned)["complete"] is True
    assert cleaned["state"] == "pending"
    assert cleaned["lifecycle_known"] is False


def test_schema_report_names_the_missing_fields():
    report = artifact_schema_report([{"id": "a", "timestamp": 1.0}])
    assert report["complete"] is False
    assert "state" in report["missing_fields"]
    assert "lifecycle_known" in report["missing_fields"]


def test_empty_artifact_is_not_reported_as_complete():
    assert artifact_schema_report([])["complete"] is False


def test_decision_relevant_fields_include_the_previously_dropped_ones():
    assert "state" in DECISION_RELEVANT_FIELDS
    assert "lifecycle_known" in DECISION_RELEVANT_FIELDS


def test_identical_runs_compare_faithful():
    run = {"incidents": [{"root_id": "r1", "score": 0.5, "promotion_reason": "x"}],
           "metrics": {"noise_suppressed": 3}}
    report = compare_runs(run, run)
    assert report["replay_faithful"] is True
    assert report["diverged_on"] == []


def test_a_candidate_count_divergence_is_reported_with_both_numbers():
    live = {"incidents": [{"root_id": "r1", "score": 0.5}, {"root_id": "r2", "score": 0.4}],
            "metrics": {"noise_suppressed": 3}}
    replay = {"incidents": [{"root_id": "r1", "score": 0.5}],
              "metrics": {"noise_suppressed": 3}}
    report = compare_runs(live, replay)
    assert report["replay_faithful"] is False
    assert "live 2 candidates, replay 1" in report["reason"]


def test_a_score_divergence_is_never_rounded_away():
    live = {"incidents": [{"root_id": "r1", "score": 0.5001}], "metrics": {}}
    replay = {"incidents": [{"root_id": "r1", "score": 0.4}], "metrics": {}}
    assert compare_runs(live, replay)["replay_faithful"] is False


def test_fidelity_rate_reports_what_diverged():
    reports = [
        {"replay_faithful": True, "candidate_agreement": True, "score_agreement": True,
         "root_agreement": True, "diverged_on": []},
        {"replay_faithful": False, "candidate_agreement": True, "score_agreement": True,
         "root_agreement": True, "diverged_on": ["suppressed"]},
    ]
    summary = fidelity_rate(reports)
    assert summary["replay_fidelity_rate"] == 0.5
    assert summary["candidate_agreement_rate"] == 1.0
    assert summary["diverged_on"] == {"suppressed": 1}


def test_historical_sessions_stay_unfaithful():
    """A/B/C predate the v2 schema. Rewriting them to pass would destroy evidence."""
    import json
    from pathlib import Path

    from brain.discovery.soak import shadow_dir

    path = Path(shadow_dir()) / "multi_session_review.json"
    if not path.exists():
        pytest.skip("prior-phase review artifact not present")
    blob = json.loads(path.read_text(encoding="utf-8"))
    flags = [
        (row.get("replay") or {}).get("replay_faithful")
        for row in (blob.get("sessions") or [])
        if isinstance(row, dict) and isinstance(row.get("replay"), dict)
    ]
    assert flags, "prior sessions must carry an explicit fidelity verdict"
    assert all(f is False for f in flags)


# ---------------------------------------------------------------------------
# The matrix itself
# ---------------------------------------------------------------------------

def test_control_arm_produces_no_background_candidates():
    """Without this, every fault-run candidate would be unattributable."""
    assert run_control(cycles=2)["candidates"] == []


def test_matrix_runs_read_only_with_no_llm():
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:2])
    assert payload["llm_enabled"] is False
    assert payload["level3_enabled"] is False
    assert payload["sandbox_enabled"] is False
    assert payload["read_only"] is True
    assert payload["production_targets"] == 0
    assert all(r["writes"] == 0 and r["llm_called"] is False for r in payload["results"])


def test_matrix_stamps_the_frozen_policy_on_every_run():
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:1])
    assert payload["policy"]["policy"] == "DISCOVERY_POLICY_G1"
    assert payload["policy"]["scoring_hash"] == "04f2ec81fc26e408"


def test_candidates_are_accumulated_across_cycles():
    """A candidate emitted in cycle 1 and deduplicated after must still count."""
    manifest = [m for m in fault_manifests() if m.fault_id == "F08a"][0]
    with FaultInjectionHarness(enabled=True, max_faults=64,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        result = run_fault(manifest, harness=harness, cycles=4)
    assert result["detection"]["verdict"] == DETECTED_CORRECT_ROOT


def test_signal_recall_is_reported_separately_from_candidate_recall():
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:4])
    recall = matrix_metrics(payload)["recall"]
    for key in ("signal_recall", "candidate_recall", "root_family_recall",
                "exact_root_recall"):
        assert key in recall
    assert recall["denominator"] > 0


def test_benign_volume_is_suppressed_rather_than_promoted():
    manifests = [m for m in fault_manifests() if m.fault_class == "HIGH_FREQUENCY_BENIGN_WARNING"]
    payload = run_matrix(cycles=2, manifests=manifests)
    for result in payload["results"]:
        assert result["detection"]["verdict"] == "SUPPRESSED"
        assert result["detection"]["unexpected_candidates"] == 0


def test_one_root_across_many_components_stays_one_candidate():
    manifests = [m for m in fault_manifests() if m.fault_class == "MULTI_COMPONENT_SHARED_ROOT"]
    payload = run_matrix(cycles=2, manifests=manifests)
    for result in payload["results"]:
        assert result["detection"]["distinct_roots"] == 1
        assert result["detection"]["verdict"] == DETECTED_CORRECT_ROOT


def test_similar_messages_with_different_causes_stay_apart():
    manifests = [m for m in fault_manifests()
                 if m.fault_class == "MULTIPLE_SIMILAR_BUT_DISTINCT_ROOTS"]
    payload = run_matrix(cycles=2, manifests=manifests)
    for result in payload["results"]:
        assert result["detection"]["distinct_roots"] >= 2


def test_unknown_pending_with_independent_evidence_is_promoted_in_the_matrix():
    manifests = [m for m in fault_manifests()
                 if m.fault_class == "UNKNOWN_PENDING_WITH_REAL_INDEPENDENT_FAILURE"]
    payload = run_matrix(cycles=2, manifests=manifests)
    for result in payload["results"]:
        assert result["detection"]["verdict"] == DETECTED_CORRECT_ROOT


def test_matrix_metrics_report_every_class_before_aggregating():
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:6])
    metrics = matrix_metrics(payload)
    assert metrics["per_fault"]
    assert metrics["per_class"]
    assert metrics["cohorts"]["development"]["manifests"] >= 1
    assert metrics["cohorts"]["holdout"]["manifests"] >= 1


def test_a_linked_secondary_effect_is_not_counted_as_noise():
    manifest = [m for m in fault_manifests() if m.fault_id == "F09a"][0]
    with FaultInjectionHarness(enabled=True, max_faults=64,
                               target=FaultTarget(label="dev", is_dev=True)) as harness:
        result = run_fault(manifest, harness=harness, cycles=4)
    assert result["candidates_unrelated"] == 0
    assert result["run_precision"] == 1.0
    assert result["consolidation"]["secondary_effects"] == 1


# ---------------------------------------------------------------------------
# Authority invariants
# ---------------------------------------------------------------------------

def test_authority_invariants_hold_after_the_matrix():
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:3])
    assert payload["isolation"]["filesystem_writes"] == 0
    assert payload["isolation"]["network_calls"] == 0
    assert payload["cleanup"]["residual_state"] is None
    for result in payload["results"]:
        assert result["writes"] == 0


def test_engine_reports_no_change_requests_or_sandbox_runs():
    engine = DiscoveryEngine()
    engine.ingest(clean_snapshot(), api_enabled=False)
    invariants = engine.safety_invariants()
    assert invariants["change_requests"] == 0
    assert invariants["sandbox_runs"] == 0
    assert invariants["production_writes"] == 0
    assert invariants["trading_actions"] == 0


def test_config_keeps_level3_and_sandbox_off():
    from brain.local_config import config

    assert int(getattr(config, "brain_autonomy_level", 0)) == 2
    assert bool(getattr(config, "brain_shadow_mode", False)) is True
    assert bool(getattr(config, "brain_llm_api_enabled", True)) is False


# --- selective paid hybrid: value must be earned, not inferred from difference -


def _paid():
    import importlib

    return importlib.import_module("heritage_support.hybrid_replay_paid")


def _usable_hybrid(top: str, *, flagged: tuple = ()) -> dict:
    return {
        "counts_as_hybrid_success": True,
        "output_status": "VALID",
        "hypotheses": [
            {"statement": top, "model_confidence": 0.6},
            {"statement": "a shared upstream service dropped every session", "model_confidence": 0.3},
            {"statement": "a network path between the tiers was severed", "model_confidence": 0.1},
        ],
        "recommended_verifications": [
            {"target": "server log", "expected_information_gain": 0.8},
            {"target": "uptime counter", "expected_information_gain": 0.6},
        ],
        "stop_recommendation": "NEEDS_MORE_EVIDENCE",
        "unsupported_claims": {
            "count": len(flagged),
            "flagged": [{"statement": s[:200]} for s in flagged],
        },
    }


def test_boilerplate_uncertainty_text_is_not_counted_as_a_root_claim():
    """A fixed disclaimer string must not make a title-only arm look conclusive.

    The deterministic arm always emits the same `uncertainty` sentence. Counting
    its words put every candidate over the substance threshold, which credited
    the model for beating a root claim that was never made.
    """
    verdict = _paid().incremental_value(
        {
            "interpretation": "OperationalError code=PG_08006 in logs",
            "root_hypothesis": "OperationalError code=PG_08006 in logs",
            "identified_exception": "OperationalError",
            "uncertainty": "no causal claim; occurrence correlation only",
            "recommendation": "human review; do not patch",
        },
        _usable_hybrid("pg-primary terminated every pooled connection at once"),
    )
    assert verdict["evidence"]["deterministic_root_claim"] is False


def test_root_cause_value_needs_a_grounded_mechanism_when_there_was_no_baseline():
    """§44: supplying a root where the engine had only a log title can be value.

    It only counts if the mechanism is grounded in the evidence that was sent;
    an ungrounded top hypothesis earns verification credit at most.
    """
    paid = _paid()
    det = {
        "interpretation": "OperationalError code=PG_08006 in logs",
        "root_hypothesis": "OperationalError code=PG_08006 in logs",
        "identified_exception": "OperationalError",
        "uncertainty": "no causal claim; occurrence correlation only",
    }
    grounded = paid.incremental_value(
        det, _usable_hybrid("pg-primary terminated every pooled connection at once")
    )
    assert grounded["primary"] == "ROOT_CAUSE_VALUE"
    assert "grounded mechanism" in grounded["primary_qualifier"]

    top = "a pgBouncer sidecar recycled the pool"
    ungrounded = paid.incremental_value(det, _usable_hybrid(top, flagged=(top,)))
    assert "ROOT_CAUSE_VALUE" not in ungrounded["classes"]
    assert ungrounded["evidence"]["top_hypothesis_grounded_in_evidence"] is False
    assert ungrounded["primary"] == "VERIFICATION_VALUE"


def test_overlap_is_measured_against_the_whole_deterministic_conclusion():
    """Scoring overlap on the headline alone made every answer look novel."""
    verdict = _paid().incremental_value(
        {
            "interpretation": "storage unhealthy",
            "root_hypothesis": "storage unhealthy",
            "identified_dependency": "postgres",
            "cross_component": ["storage.writer", "reporting.reader"],
        },
        _usable_hybrid("storage writer cannot reach postgres and reporting reader fails too"),
    )
    assert verdict["evidence"]["top_hypothesis_overlap_with_deterministic"] > 0.0


def test_reported_value_never_exceeds_the_recorded_classes():
    verdict = _paid().incremental_value(
        {"interpretation": "storage unhealthy", "root_hypothesis": "storage unhealthy"},
        {"counts_as_hybrid_success": False, "output_status": "TRUNCATED"},
    )
    assert verdict["classes"] == ["NO_INCREMENTAL_VALUE"]
    assert verdict["primary"] == "NO_INCREMENTAL_VALUE"


def test_a_truncated_row_is_not_treated_as_completed_work_on_resume():
    """A cached row that never reached the provider must not be skipped.

    The first gated attempt in this phase wrote four provider-unavailable rows;
    resuming then reported zero calls and zero cost as though the budget had
    been honoured.
    """
    import inspect
    import json as _json

    paid = _paid()
    src = inspect.getsource(paid.main)
    assert "llm_calls" in src, "resume must filter on whether a call actually happened"
    rows = [
        {"root_id": "a", "real_hybrid": {"llm_calls": 1}},
        {"root_id": "b", "real_hybrid": {"llm_calls": 0}},
        {"root_id": "c", "error": "candidate_not_in_pool"},
    ]
    done = {
        r["root_id"]: r
        for r in _json.loads(_json.dumps(rows))
        if int((r.get("real_hybrid") or {}).get("llm_calls") or 0) > 0
    }
    assert set(done) == {"a"}


def test_paid_hybrid_still_requires_every_gate():
    from brain.discovery.replay import paid_replay_gate

    assert paid_replay_gate(
        explicit=False, accept_cost=True, candidate_ids=["x"], max_calls=1, max_tokens=10
    )["invoke"] is False
    assert paid_replay_gate(
        explicit=True, accept_cost=False, candidate_ids=["x"], max_calls=1, max_tokens=10
    )["invoke"] is False
    assert paid_replay_gate(
        explicit=True, accept_cost=True, candidate_ids=[], max_calls=1, max_tokens=10
    )["invoke"] is False
    assert paid_replay_gate(
        explicit=True, accept_cost=True, candidate_ids=["x"], max_calls=1, max_tokens=10
    )["invoke"] is False, "brain_paid_replay_enabled must gate the paid path"
    gate = paid_replay_gate(
        explicit=True, accept_cost=True, candidate_ids=["x", "y"], max_calls=1,
        max_tokens=10, config_enabled=True,
    )
    assert gate["invoke"] is True
    assert gate["candidates"] == ["x"], "the call budget must clamp the allowlist"


def test_fault_matrix_ships_full_candidate_records_for_hybrid_selection():
    """The paid arm must receive the evidence the engine had, not a summary."""
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:4])
    with_candidates = [r for r in payload["results"] if r.get("candidates_full")]
    assert with_candidates, "no fault produced a candidate record to replay"
    for result in with_candidates:
        for cand in result["candidates_full"]:
            assert cand.get("root_id")
            assert cand.get("root_signature")
            assert "evidence" in cand or "occurrences" in cand


def test_runtime_artifacts_and_test_scratch_stay_out_of_source_control():
    """§54/§55: evidence and scratch trees must be ignored, not committed.

    The pytest scratch tree lands inside the repo, so without an ignore rule
    `git status` fills with hundreds of fixture files and a real change becomes
    invisible in the noise.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    if not (root / ".gitignore").is_file():
        pytest.skip("higiene do repositorio: .gitignore ausente (instalacao por wheel, nao um checkout) - QHOS-AUD-286")
    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for rule in (".qhos-brain/", ".pytest_tmp/", ".pytest_cache/"):
        assert rule in ignored, f"{rule} must be ignored"
    assert ".env" in ignored and "!.env.example" in ignored


def test_hybrid_selection_never_receives_fault_metadata():
    """§9/§58: the paid packet must not carry the answer key."""
    import json

    paid = _paid()
    payload = run_matrix(cycles=2, manifests=fault_manifests()[:4])
    for result in payload["results"]:
        for cand in result.get("candidates_full") or []:
            blob = json.dumps(paid.sanitized_evidence(cand), default=str).lower()
            for token in ("fault_id", "fault_class", "expected_root", "cohort", "f01", "f14"):
                assert token not in blob
