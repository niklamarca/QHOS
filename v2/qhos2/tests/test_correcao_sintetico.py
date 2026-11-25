"""Campanha de correcao, lote l5c-leitura: identidade no corpus sintetico (QHOS-AUD-144).

`sintetico.gerar` nao grava symbol/exchange nos quadros, entao `conferir_identidade` passa por vacuidade em quase
toda a suite. Aqui o corpus e carimbado nos testes (sem mudar `sintetico.py`) e a conferencia e exercitada de
verdade em todo o pipeline: eventos, livro, leitura ao vivo e candles.
"""
import pytest

from qhos2 import eventos as ev
from qhos2 import historico as hi
from qhos2 import leitura as le
from qhos2 import livro as lv
from qhos2 import mercado as m
from qhos2 import rprotocol as p
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}
STATUS = {"estado": "FECHADA", "retrato_completo": True, "referencia": {"min_qprice_change": 0.25}}


def carimbar(cenario: si.Cenario, simbolo: str, bolsa: str) -> si.Cenario:
    """O mesmo cenario, com symbol/exchange em TODOS os quadros (116, 150 e 160 aceitam os dois campos)."""
    quadros = [(t, d + si._txt(p.F_SYMBOL, simbolo) + si._txt(p.F_EXCHANGE, bolsa)) for t, d in cenario.quadros]
    return si.Cenario(cenario.semente, cenario.tick, quadros, list(cenario.verdades), dict(cenario.acoes))


def gravar_es(raiz, cenario, nome="es"):
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id=nome)
    for i, (template, dados) in enumerate(cenario.quadros):
        g(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    g.fechar(STATUS, fechamento_normal=True)
    return g.pasta


def test_caracterizacao_o_corpus_sintetico_nao_carrega_identidade():
    cenario = si.gerar(3, 60)
    campos = [m.decodificar(dados).campos for _, dados in cenario.quadros]
    assert campos and all("symbol" not in c and "exchange" not in c for c in campos)
    for c in campos:
        m.conferir_identidade(c, "QUALQUER", "COISA")      # vacuidade: nada a comparar, nada falha


def test_corpus_carimbado_e_conferido_de_verdade_em_todo_o_pipeline(tmp_path):
    cenario = si.gerar(3, 200)
    certo = gravar_es(tmp_path / "certo", carimbar(cenario, "ESZ6", "CME"))
    campos = [m.decodificar(dados).campos for _, dados in carimbar(cenario, "ESZ6", "CME").quadros]
    assert all(c.get("symbol") == "ESZ6" and c.get("exchange") == "CME" for c in campos)
    eventos = list(ev.eventos_da_captura(certo))
    assert eventos and lv.livro_da_captura(certo).canonico() == cenario.livro_final()
    leitor = le.Leitor(certo, tick=0.25, simbolo="ESZ6", bolsa="CME")
    leitor.avancar()
    assert (leitor.estado, leitor.codigo) == (le.ENCERRADA, None) and leitor.livro.canonico() == cenario.livro_final()
    barras = hi.barras_da_captura(certo, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert barras is not None and barras.minutos


@pytest.mark.parametrize("simbolo,bolsa", [("NQZ6", "CME"), ("ESZ6", "NYMEX")])
def test_corpus_carimbado_com_outra_identidade_e_recusado_em_todo_o_pipeline(tmp_path, simbolo, bolsa):
    errado = gravar_es(tmp_path / "errado", carimbar(si.gerar(3, 50), simbolo, bolsa))
    with pytest.raises(ev.EventoError, match="^simbolo_ou_bolsa_diverge:0$"):
        list(ev.eventos_da_captura(errado))
    with pytest.raises(ev.EventoError, match="simbolo_ou_bolsa_diverge"):
        lv.livro_da_captura(errado)
    leitor = le.Leitor(errado, tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert leitor.avancar() == []
    assert (leitor.estado, leitor.codigo, leitor.quadros) == (le.FALHA, "simbolo_ou_bolsa_diverge", 0)
    with pytest.raises(ev.EventoError, match="simbolo_ou_bolsa_diverge"):
        hi.barras_da_captura(errado, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")


def test_um_unico_quadro_sem_carimbo_no_meio_nao_e_recusado(tmp_path):
    """Ausencia nao inventa identidade (mercado.py:63-67): o carimbo em todos os outros quadros continua valendo."""
    cenario = carimbar(si.gerar(4, 30), "ESZ6", "CME")
    cenario.quadros[5] = si.gerar(4, 30).quadros[5]
    pasta = gravar_es(tmp_path / "misto", cenario)
    assert len(list(ev.eventos_da_captura(pasta))) == len(list(ev.eventos_da_captura(gravar_es(tmp_path / "limpo", si.gerar(4, 30)))))
