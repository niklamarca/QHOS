"""Peca 6a: o mercado sintetico e deterministico, coerente com o livro verdadeiro e gravavel como captura."""
import pytest

from qhos2 import catalogo, inspecao
from qhos2 import eventos as ev
from qhos2 import sintetico as si


def aplicar(eventos):
    """Livro minimo, independente do da peca 6b: so para conferir o gerador e a normalizacao."""
    ordens = {}
    for e in eventos:
        if e.tipo in (ev.RETRATO, ev.NOVO, ev.ALTERA):
            ordens[e.ordem] = si.Ordem(e.lado, e.preco, e.quantidade, e.prioridade)
        elif e.tipo == ev.REMOVE:
            del ordens[e.ordem]
    return ordens


def eventos_do_cenario(cenario, ate=None):
    quadros = cenario.quadros if ate is None else cenario.quadros[:ate]
    saida = []
    for i, (template, dados) in enumerate(quadros):
        saida.extend(ev.normalizar(i, template, si.INICIO_NS + i * si.PASSO_NS, dados, cenario.tick))
    return saida


def test_mesma_semente_mesmos_bytes_e_mesma_verdade():
    a, b, c = si.gerar(7, 300), si.gerar(7, 300), si.gerar(8, 300)
    assert a.quadros == b.quadros and a.verdades == b.verdades and a.acoes == b.acoes
    assert a.quadros != c.quadros


def test_livro_reconstruido_bate_com_a_verdade_depois_de_cada_passo():
    cenario = si.gerar(11, 400)
    eventos = eventos_do_cenario(cenario)
    for emitidos, verdade in cenario.verdades:
        ate = [e for e in eventos if e.indice < emitidos]
        assert si.canonico(aplicar(ate)) == verdade, f"diverge apos {emitidos} quadros"


@pytest.mark.parametrize("semente", [1, 2, 3])
def test_invariantes_do_livro_verdadeiro(semente):
    cenario = si.gerar(semente, 600)
    for _, livro in cenario.verdades:
        compras, vendas = livro["C"], livro["V"]
        if compras and vendas:
            assert max(compras) < min(vendas), "livro cruzado"
        filas = [o for lado in livro.values() for fila in lado.values() for o in fila]
        assert all(q > 0 for _, _, q in filas)
        assert len({prio for prio, _, _ in filas}) == len(filas) and len({i for _, i, _ in filas}) == len(filas)


def test_todas_as_acoes_aparecem_e_negocios_ligam_a_ordem_viva():
    cenario = si.gerar(5, 2000)
    assert set(cenario.acoes) == {"novo", "cancela", "reduz", "aumenta", "move", "negocio"}
    vivas, negocios, esperado = {}, 0, {}
    for e in eventos_do_cenario(cenario):
        if e.tipo == ev.NEGOCIO:
            negocios += 1
            assert e.ordem in vivas and e.ordem_agressora not in vivas
            assert vivas[e.ordem].preco == e.preco and vivas[e.ordem].lado != e.lado
            assert e.quantidade <= vivas[e.ordem].quantidade
            esperado[e.ordem] = vivas[e.ordem].quantidade - e.quantidade
        elif e.ordem in esperado:  # conservacao: a baixa no livro e exatamente o que o negocio executou
            sobra = esperado.pop(e.ordem)
            assert (e.tipo, e.quantidade) == ((ev.REMOVE, 0) if sobra == 0 else (ev.ALTERA, sobra))
            assert e.tipo == ev.REMOVE or e.prioridade == vivas[e.ordem].prioridade
        if e.tipo in (ev.RETRATO, ev.NOVO, ev.ALTERA):
            vivas[e.ordem] = si.Ordem(e.lado, e.preco, e.quantidade, e.prioridade)
        elif e.tipo == ev.REMOVE:
            del vivas[e.ordem]
    assert negocios >= cenario.acoes["negocio"]


def test_regras_de_fila_das_alteracoes():
    cenario = si.gerar(9, 1500)
    antes, mantem, muda = {}, 0, 0
    for e in eventos_do_cenario(cenario):
        if e.tipo == ev.ALTERA:
            velha = antes[e.ordem]
            if e.preco == velha.preco and e.quantidade < velha.quantidade:
                assert e.prioridade == velha.prioridade
                mantem += 1
            else:
                assert e.prioridade > max(o.prioridade for o in antes.values())
                muda += 1
        if e.tipo in (ev.RETRATO, ev.NOVO, ev.ALTERA):
            antes[e.ordem] = si.Ordem(e.lado, e.preco, e.quantidade, e.prioridade)
        elif e.tipo == ev.REMOVE:
            del antes[e.ordem]
    assert mantem > 0 and muda > 0


def test_captura_sintetica_e_verificada_e_nunca_passa_por_paper(tmp_path):
    cenario = si.gerar(4, 300)
    pasta = si.gravar(cenario, tmp_path, captura_id="sintetica")
    r = catalogo.verificar_captura(pasta)
    assert r["verificada"] is True and r["quadros"] == len(cenario.quadros) and r["ambiente"] == "desconhecido"
    assert r["sistema"] == "QHOS2 Sintetico"
    i = inspecao.inspecionar(pasta)
    assert i["retrato"]["fim_visto"] is True and i["ciclo"]["durante_retrato"] == {}
    depois = i["ciclo"]["depois_do_retrato"]
    assert not any(k.endswith("desconhecida") or k in ("novo_ja_viva", "sem_id") for k in depois)
    ng = i["negocios"]
    assert ng["quadros"] == ng["id_passivo"] == ng["passivo_vivo"] > 0 and ng["agressor_visto"] == 0
    assert i["ordens_vivas_no_fim"] == sum(len(f) for lado in cenario.livro_final().values() for f in lado.values())


@pytest.mark.parametrize("kw", [{"passos": -1}, {"niveis": 0}, {"por_nivel": (0, 2)}, {"por_nivel": (3, 2)}])
def test_parametros_invalidos(kw):
    args = {"semente": 1, "passos": 1}
    args.update(kw)
    with pytest.raises(ValueError, match="parametros_invalidos"):
        si.gerar(**args)
