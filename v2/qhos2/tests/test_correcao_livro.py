"""Campanha de correcao, lote l5c-leitura: livro de ofertas (QHOS-AUD-066).

Sem xfail: caracterizacoes do estado publico (`suspeito` gravavel, limite booleano) e testes de propriedade da
invariante ordens <-> niveis. `livro.py` nao muda nesta janela; impor no tipo e decisao de desenho do autor, com uma
opcao em `EVIDENCIAS/QHOS-AUD-066/patch_proposto.diff`.
"""
import pytest

from qhos2 import eventos as ev
from qhos2 import livro as lv
from qhos2 import sintetico as si
from qhos2.eventos import Evento
from qhos2.livro import Livro

C, V = ev.COMPRA, ev.VENDA


def pronto():
    livro = Livro()
    livro.aplicar(Evento(0, 0, 0, ev.RETRATO, C, 400, 1, "c", 1))
    livro.aplicar(Evento(1, 0, 0, ev.RETRATO, V, 404, 1, "v", 2))
    livro.aplicar(Evento(2, 0, 0, ev.RETRATO_FIM))
    assert livro.qualificado
    return livro


def test_caracterizacao_suspeito_e_gravavel_por_fora_e_requalifica_o_livro():
    """QHOS-AUD-066, caracterizacao: a regra "suspeito so sobe ate uma ressincronizacao" (livro.py:20-21) e cumprida pelo
    codigo, nao pelo tipo: `suspeito` e atributo publico (livro.py:69). Nenhum chamador o desliga hoje (o unico que o
    escreve de fora, leitura.py:198, so o liga). Tornar so-leitura e decisao de desenho (patch em EVIDENCIAS/QHOS-AUD-066)."""
    livro = pronto()
    livro.aplicar(Evento(3, 0, 0, ev.REMOVE, C, 400, 0, "fantasma", None))      # remove_desconhecida: suspeito
    assert livro.suspeito and not livro.qualificado
    livro.suspeito = False
    assert livro.qualificado and livro.anomalias == {"remove_desconhecida": 1}


def test_caracterizacao_limite_pendentes_booleano_vira_limite_um():
    """QHOS-AUD-066, caracterizacao: `Livro(limite_pendentes=True)` e aceito e funciona como 1 (livro.py:64, 189)."""
    livro = Livro(limite_pendentes=True)
    for i in range(2):
        livro.aplicar(Evento(i, 0, 0, ev.NOVO, C, 400, 1, f"n{i}", i))
    assert livro.anomalias == {"retrato_pendentes_demais": 1} and livro.suspeito


def _niveis_a_partir_das_ordens(livro):
    niveis = {C: {}, V: {}}
    for o in livro.ordens.values():
        soma, n = niveis[o.lado].get(o.preco, (0, 0))
        niveis[o.lado][o.preco] = (soma + o.quantidade, n + 1)
    return niveis


def _invariante(livro):
    """`_niveis` e `_precos` sao exatamente o agregado de `ordens` (teste de propriedade do achado, item b)."""
    esperado = _niveis_a_partir_das_ordens(livro)
    for lado in (C, V):
        assert {p: tuple(v) for p, v in livro._niveis[lado].items()} == esperado[lado]
        assert livro._precos[lado] == sorted(livro._niveis[lado])
        assert all(n > 0 and soma > 0 for soma, n in esperado[lado].values())


@pytest.mark.parametrize("semente", [13, 29])
def test_niveis_e_precos_derivam_das_ordens_em_toda_a_captura_sintetica(semente):
    cenario = si.gerar(semente, 500)
    livro = Livro()
    for i, (template, dados) in enumerate(cenario.quadros):
        livro.aplicar_quadro(ev.normalizar(i, template, si.INICIO_NS + i * si.PASSO_NS, dados, cenario.tick))
        _invariante(livro)
    assert livro.qualificado and livro.canonico() == cenario.livro_final()
    for lado in (C, V):
        assert [(p, q) for p, q, _ in livro.niveis(lado, 5)] == [
            (p, livro._niveis[lado][p][0]) for p in (reversed(livro._precos[lado][-5:]) if lado == C else livro._precos[lado][:5])]


def test_livro_da_captura_mantem_a_invariante_no_fim(tmp_path):
    livro = lv.livro_da_captura(si.gravar(si.gerar(5, 200), tmp_path))
    _invariante(livro)
    assert livro.qualificado
