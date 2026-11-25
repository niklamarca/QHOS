"""Campanha de correcao, lote l5c-leitura: `resumo.get("quadros", 0)` na observacao (QHOS-AUD-140)."""
import pytest

pytest.importorskip("websockets")

from qhos2 import leitura as le  # noqa: E402
from qhos2 import sintetico as si  # noqa: E402
from qhos2.coletor import Coletor  # noqa: E402
from qhos2.observabilidade import snapshot  # noqa: E402

CHAVES_DO_RESUMO = {"esquema", "estado", "codigo", "quadros", "bytes", "pendente_bytes", "atraso_s", "sem_novidade_s",
                    "livro_vale", "livro"}


def leitor(pasta, **extra):
    return le.Leitor(pasta, **{"tick": 0.25, "simbolo": "SINTETICO", "bolsa": "QHOS2", **extra})


def _nos_quatro_estados(tmp_path):
    aguardando = leitor(tmp_path / "ainda_nao")
    aguardando.avancar()
    lendo = leitor(si.gravar(si.gerar(1, 10), tmp_path / "lendo"))
    (lendo.pasta / "manifest.json").unlink()
    lendo.avancar()
    encerrada = leitor(si.gravar(si.gerar(1, 10), tmp_path / "encerrada"))
    encerrada.avancar()
    falha = leitor(si.gravar(si.gerar(2, 10), tmp_path / "falha"), tick=0.3)
    falha.avancar()
    estados = {l.estado for l in (aguardando, lendo, encerrada, falha)}
    assert estados == {le.AGUARDANDO, le.LENDO, le.ENCERRADA, le.FALHA}
    return aguardando, lendo, encerrada, falha


def test_resumo_do_leitor_traz_quadros_em_todos_os_estados(tmp_path):
    """O default do `.get` (observabilidade.py:33) e inalcancavel: `Leitor.resumo()` sempre tem as chaves."""
    for leit in _nos_quatro_estados(tmp_path):
        assert set(leit.resumo()) == CHAVES_DO_RESUMO
        assert type(leit.resumo()["quadros"]) is int and type(leit.resumo()["bytes"]) is int


def test_snapshot_reflete_os_quadros_lidos_e_nao_um_default(tmp_path):
    for leit in _nos_quatro_estados(tmp_path):
        coletor = Coletor(token="A" * 43, porta=0, leitor=leit, avisar=lambda _: None)
        captura = snapshot(coletor)["capture"]
        assert captura["frames"] == leit.quadros and captura["bytes"] == leit.bytes
        assert captura["reader_state"] == leit.estado and captura["error_code"] == leit.codigo
    assert leit.quadros > 0 or leit.estado == le.FALHA
    assert snapshot(Coletor(token="A" * 43, porta=0, avisar=lambda _: None))["capture"]["frames"] == 0
