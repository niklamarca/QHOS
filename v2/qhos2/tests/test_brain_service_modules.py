"""Modulos do brain_service sem teste direto: observe_regression, atomic_json_write, LinuxActionLog (QHOS-AUD-274)."""
import json
import math

import pytest

from brain_service.config import load_config


@pytest.fixture(autouse=True)
def isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL"):
        monkeypatch.delenv(name, raising=False)


# --- InvestigationEngine.observe_regression ------------------------------------------

def _resultado(run_id, target_root, **extra):
    base = {"verified": True, "run_id": run_id, "target_root": str(target_root), "phase": "PASSED",
            "passed": 8, "failed": 0, "collection_errors": 0, "source_hash": "a" * 64, "completed_at": 1_700_000_000.0}
    base.update(extra)
    return base


def test_observe_regression_so_memoriza_verificado_e_uma_vez(tmp_path):
    from brain_service.investigation import InvestigationEngine
    root = load_config().target_root
    engine = InvestigationEngine(root, tmp_path / "state")
    engine.observe_regression(_resultado("a" * 32, root, verified=False))
    engine.observe_regression({"verified": True, "target_root": str(root)})  # sem run_id
    engine.observe_regression(_resultado("c" * 32, tmp_path / "outro"))
    assert engine.memory.recent(5) == []
    engine.observe_regression(_resultado("d" * 32, root))
    engine.observe_regression(_resultado("d" * 32, root))
    registros = engine.memory.recent(5)
    assert [r.get("incident_id", r.get("id")) for r in registros] == ["regression:" + "d" * 32]
    assert registros[0]["after"]["phase"] == "PASSED" and registros[0]["before"] == {"state": "not_assessed_by_this_run"}
    assert registros[0]["outcome"] == "verified" and "fixed" not in json.dumps(registros[0]).lower()


# --- supervisor.atomic_json_write -----------------------------------------------------

def test_atomic_json_write_substitui_sem_deixar_temporario_nem_documento_parcial(tmp_path):
    from brain_service.supervisor import atomic_json_write
    alvo = tmp_path / "pasta" / "estado.json"
    atomic_json_write(alvo, {"b": 1, "a": [1, 2]})
    assert json.loads(alvo.read_text(encoding="utf-8")) == {"a": [1, 2], "b": 1}
    assert alvo.read_text(encoding="utf-8").endswith("\n")
    with pytest.raises(TypeError):
        atomic_json_write(alvo, {"x": object()})
    assert json.loads(alvo.read_text(encoding="utf-8")) == {"a": [1, 2], "b": 1}
    assert sorted(p.name for p in alvo.parent.iterdir()) == ["estado.json"]


# --- linux_actions.LinuxActionLog ------------------------------------------------------

def test_linux_action_log_persiste_redigido_e_limitado(tmp_path):
    from brain_service import linux_actions
    log = linux_actions.LinuxActionLog(tmp_path / "state")
    linha = log.record("service_started", outcome="active", detail="token=ficticio-nao-real ok")
    assert linha["action"] == "service_started" and "ficticio" not in linha["detail"] and "<redacted>" in linha["detail"]
    for i in range(linux_actions.MAX_ACTIONS + 5):
        log.record("tick", detail=str(i))
    snap = log.snapshot()
    assert snap["read_only"] is True and len(snap["actions"]) == linux_actions.MAX_ACTIONS
    assert snap["actions"][-1]["detail"] == str(linux_actions.MAX_ACTIONS + 4)
    recarregado = linux_actions.LinuxActionLog(tmp_path / "state").snapshot()
    assert recarregado["actions"] == snap["actions"]
    assert math.isfinite(recarregado["actions"][0]["timestamp"])
