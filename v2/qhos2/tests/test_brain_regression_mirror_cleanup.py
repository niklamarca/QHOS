"""Espelhos orfaos da regressao (QHOS-AUD-308): limpeza conservadora numa raiz temporaria, sem worker real."""
import json
import os
import time

import pytest

from brain_service import regression
from brain_service.config import load_config


@pytest.fixture(autouse=True)
def isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    for name in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR"):
        monkeypatch.delenv(name, raising=False)


def _espelho(work, run_id, *, marcado=True, idade_s=0, finalizado=False):
    pasta = work / ("run-" + run_id)
    (pasta / "tests").mkdir(parents=True)
    (pasta / "tests" / "x.py").write_text("", encoding="utf-8")
    if marcado:
        marcador = pasta / ".brain-regression-owned.json"
        marcador.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
        antigo = time.time() - idade_s
        os.utime(marcador, (antigo, antigo))
    if finalizado:
        (pasta / ".brain-regression-finalized.json").write_text(
            json.dumps({"run_id": run_id, "failed": True, "completed_at": time.time() - idade_s}), encoding="utf-8")
    return pasta


def test_espelho_sem_marcador_de_finalizacao_e_removido_apos_o_prazo(tmp_path):
    runner = regression.RegressionRunner(load_config().target_root, tmp_path / "state")
    work = runner.work
    work.mkdir(parents=True)
    prazo = getattr(regression, "ORPHAN_MIRROR_GRACE_S", 3600)  # getattr: o teste tambem roda em 36f0e1f (antes.log)
    velho = _espelho(work, "a" * 32, idade_s=prazo + 60)
    recente = _espelho(work, "b" * 32, idade_s=60)
    falhado_antigo = _espelho(work, "e" * 32, idade_s=10 * 86400, finalizado=True)
    desconhecido = work / "candidato-externo"
    desconhecido.mkdir()
    sem_marcador = _espelho(work, "f" * 32, marcado=False)
    runner.state.update(run_id="c" * 32, phase="PASSED")
    atual = _espelho(work, "c" * 32)
    runner._cleanup(atual)
    assert not atual.exists(), "espelho aprovado e reproduzivel e descartado"
    assert not velho.exists(), "orfao de crash acima do prazo e removido"
    assert recente.exists(), "orfao dentro do prazo pode estar em uso por um worker"
    assert falhado_antigo.exists(), "a retencao dos falhados nao muda"
    assert desconhecido.exists() and sem_marcador.exists(), "o que nao e nosso nao e tocado"
    assert "retention_warning" not in runner.state


def test_espelho_da_propria_execucao_sem_marcador_e_removido(tmp_path):
    runner = regression.RegressionRunner(load_config().target_root, tmp_path / "state")
    work = runner.work
    work.mkdir(parents=True)
    runner.state.update(run_id="d" * 32, phase="STALE")
    # prepare_mirror criou a pasta e falhou (source_changed_while_copying) antes do marcador.
    atual = _espelho(work, "d" * 32, marcado=False)
    outro = _espelho(work, "9" * 32, marcado=False)
    runner._cleanup(atual)
    assert not atual.exists()
    assert outro.exists(), "uma pasta UUID sem marcador que nao e desta execucao continua intocada"
