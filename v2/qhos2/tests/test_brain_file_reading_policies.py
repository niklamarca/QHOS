"""Politicas de abertura de arquivo e leitura de JSON no brain_service (QHOS-AUD-315).

A matriz completa esta em docs/MATRIZ_LEITURA_ARQUIVOS_BRAIN.md. Aqui ficam os pontos corrigidos ou
caracterizados neste lote; contextos com politicas diferentes nao sao unificados.
"""
import os

import pytest

from brain_service.config import ServiceConfig, load_config


@pytest.fixture(autouse=True)
def isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL"):
        monkeypatch.delenv(name, raising=False)


def test_runtime_investigations_com_nan_e_rejeitado_como_estado_invalido(tmp_path):
    from brain_service.investigation import InvestigationEngine
    state = tmp_path / "state"
    state.mkdir()
    (state / "runtime_investigations.json").write_text(
        '{"cursor": 3, "incidents": {"abc": {"id": "abc", "occurrences": NaN}}}', encoding="utf-8")
    engine = InvestigationEngine(load_config().target_root, state)
    assert engine.automatic == {} and engine.log_cursor == 0


def test_runtime_investigations_valido_continua_a_ser_lido(tmp_path):
    from brain_service.investigation import InvestigationEngine
    state = tmp_path / "state"
    state.mkdir()
    (state / "runtime_investigations.json").write_text(
        '{"cursor": 3, "incidents": {"abc": {"id": "abc", "occurrences": 2}}}', encoding="utf-8")
    engine = InvestigationEngine(load_config().target_root, state)
    assert engine.log_cursor == 3 and engine.automatic["abc"]["occurrences"] == 2


def test_read_evidence_rejeita_nan_e_documento_grande(tmp_path):
    from brain_service.verification import read_evidence
    alvo = tmp_path / "e.json"
    alvo.write_text('{"x": NaN}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_evidence(alvo)
    alvo.write_bytes(b'{"x": "' + b"a" * (4 * 1024 * 1024) + b'"}')
    with pytest.raises(ValueError, match="evidence_too_large"):
        read_evidence(alvo)


def test_repair_history_nao_segue_link(tmp_path):
    from brain_service.assistant import read_repair_history
    cfg = ServiceConfig(state_path=str(tmp_path / "state"))
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    real = tmp_path / "fora.jsonl"
    real.write_text('{"timestamp": 1700000000, "type": "repair", "message": "fora da raiz"}\n', encoding="utf-8")
    try:
        os.symlink(real, cfg.state_dir / "repair_history.jsonl")
    except (OSError, NotImplementedError):
        pytest.skip("symlink indisponivel neste host")
    assert read_repair_history(cfg) == []
