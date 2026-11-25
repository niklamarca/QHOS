"""A observacao do servico leva a atestacao real ao Discovery (QHOS-AUD-280). Nada e fabricado: a atestacao
vem do RuntimeAttestor do proprio processo; o teste so verifica que ela chega e que vira linha de observacao."""
import pytest

from brain_service.config import ServiceConfig


@pytest.fixture(autouse=True)
def isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_service_observation_carries_attestation(tmp_path):
    from brain.discovery.observation import observations_from_snapshot
    from brain_service.process import StandaloneBrain
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False, deployment_context="development")
    brain = StandaloneBrain(cfg)
    try:
        brain.observer.last = {"observed_at": 1.0, "registry": {}, "telemetry": {"errors": []}}
        obs = brain._observe()
    finally:
        brain.log_feed.close()
    att, ctx = obs["attestation"], obs["attestation_context"]
    assert att["loaded_code_state"] in {"MATCH", "STALE"}
    assert att["running_revision"] == att["running"]["source_revision"] == brain.attestor.running_source_revision
    assert att["worktree_revision"] == att["worktree"]["source_revision"]
    assert att["stale_runtime"] is (att["loaded_code_state"] == "STALE")
    assert ctx["deployment_context"] == cfg.deployment_context and ctx["required"] is True
    linhas = [row for row in observations_from_snapshot(obs) if row.source == "attestation"]
    assert linhas and linhas[0].component == "brain_runtime"
    assert linhas[0].kind in {"RUNTIME_REVISION_MATCH", "STALE_RUNTIME"}
    assert linhas[0].kind == ("STALE_RUNTIME" if att["stale_runtime"] else "RUNTIME_REVISION_MATCH")
