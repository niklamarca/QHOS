from governance.epistemics import (
    CandidateStage,
    Claim,
    DataState,
    ExecutionEvidence,
    claim_guard,
    current_data_fingerprint,
)
from core.config import config


def test_backtest_cannot_claim_edge():
    decision = claim_guard.validate(Claim(
        claim_type="EDGE",
        subject="candidate-x",
        statement="edge",
        stage=CandidateStage.BACKTESTED.value,
        execution=ExecutionEvidence.BACKTEST.value,
    ))
    assert not decision.allowed
    assert any("forward" in reason for reason in decision.reasons)


def test_edge_requires_complete_evidence_bundle():
    claim = Claim(
        claim_type="EDGE",
        subject="candidate-x",
        statement="forward support",
        stage=CandidateStage.PAPER.value,
        execution=ExecutionEvidence.SIMULATED_CLOSE_FILL.value,
        data_state=DataState.FINALIZED.value,
        settled_sessions=config.promotion_min_forward_sessions,
        benchmark="SPY",
        after_costs=True,
        multiple_testing_controlled=True,
    )
    assert claim_guard.validate(claim).allowed


def test_registration_is_not_learning():
    decision = claim_guard.validate(Claim(
        claim_type="LEARNED",
        subject="context-log",
        statement="stored returns",
        metadata={"parameters_updated": False, "oos_improved": False},
    ))
    assert not decision.allowed


def test_real_requires_broker_fill_id():
    decision = claim_guard.validate(Claim(
        claim_type="REAL",
        subject="fill",
        statement="real fill",
        execution=ExecutionEvidence.LIVE_BROKER_FILL.value,
    ))
    assert not decision.allowed


def test_data_identity_ignores_collection_time_but_not_content(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    monkeypatch.setattr(config, "data_assets_stocks", ["SPY"])
    monkeypatch.setattr(config, "data_assets_crypto", [])
    monkeypatch.setattr(config, "data_assets_reference", [])
    root = tmp_path / "provenance"
    root.mkdir()
    path = root / "SPY_1d.json"
    base = {"asset": "SPY", "timeframe": "1d", "source": "test",
            "status": "approved", "rows": 10, "final_observation": "2026-08-17",
            "data_hash": "aaa", "retrieved_at": "one"}
    import json
    path.write_text(json.dumps(base), encoding="utf-8")
    first = current_data_fingerprint()
    base["retrieved_at"] = "two"
    path.write_text(json.dumps(base), encoding="utf-8")
    assert current_data_fingerprint() == first
    base["data_hash"] = "bbb"
    path.write_text(json.dumps(base), encoding="utf-8")
    assert current_data_fingerprint() != first
