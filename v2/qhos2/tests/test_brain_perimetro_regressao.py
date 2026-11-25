"""QHOS-AUD-299 (caracterizacao): `RegressionRunner.start_run(explicit=True)` nao distingue um pedido manual de um
pedido do agendador; depois de uma execucao ambos recebem `cooldown` ate `next_due`. A semantica de `explicit` nao esta
definida em codigo nem em documento (nenhum limite proprio para pedidos manuais), por isso este teste documenta o
comportamento atual sem presumir o esperado; a decisao fica registrada em EVIDENCIAS/QHOS-AUD-299."""
import inspect
import time

import pytest

from brain_service.config import load_config


@pytest.fixture
def runner(tmp_path, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    from brain_service.regression import RegressionRunner
    return RegressionRunner(load_config().target_root, tmp_path / "estado")


def test_explicit_nao_altera_a_recusa_por_cooldown(runner):
    agora = time.time()
    runner.state.update(phase="PASSED", verified=True, last_run=agora - 60, next_due=agora + 3540)
    manual = runner.start_run(scope="operational", explicit=True)
    automatico = runner.start_run(scope="operational", explicit=False)
    assert manual == automatico == {"accepted": False, "reason": "cooldown", "next_due": agora + 3540}
    assert runner.snapshot()["phase"] == "PASSED"


def test_explicit_e_parametro_declarado_mas_sem_uso_no_corpo(runner):
    from brain_service.regression import RegressionRunner
    assinatura = inspect.signature(RegressionRunner.start_run)
    assert "explicit" in assinatura.parameters and assinatura.parameters["explicit"].default is False
    corpo = inspect.getsource(RegressionRunner.start_run)
    assert corpo.count("explicit") == 1, "se `explicit` passou a ser usado, a decisao da QHOS-AUD-299 foi tomada: revisar este teste"
    from brain_service.tester import SoftwareTester
    assert "explicit" not in inspect.signature(SoftwareTester.start_run).parameters
