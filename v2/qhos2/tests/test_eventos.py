"""Peca 6a: cada quadro vira eventos normalizados, em ticks, na ordem do fio, e nada passa em silencio."""
import json

import pytest

from qhos2 import eventos as ev
from qhos2 import sintetico as si
from qhos2.eventos import Evento, EventoError
from qhos2.gravador import Gravador

# Vetores da biblioteca oficial, ja usados nas pecas 4d e 4e.
from test_mercado import D116, D116_FIM, D160, D161
from test_negocios import LT, LT_RETRATO

SSBOE = 1761246000


def test_160_vira_um_evento_por_atualizacao_em_ordem():
    carimbo = SSBOE * 10 ** 9 + 123456 * 1000
    assert ev.normalizar(7, 160, 99, D160, 0.25) == (
        Evento(7, 0, 99, ev.NOVO, ev.COMPRA, 27401, 3, "A1", 11, carimbo),
        Evento(7, 1, 99, ev.ALTERA, ev.VENDA, 27402, 7, "B2", 22, carimbo),
        Evento(7, 2, 99, ev.REMOVE, ev.COMPRA, 27399, 0, "C3", 33, carimbo))


def test_retrato_vira_uma_ordem_por_evento_e_o_fim_e_marcado():
    assert ev.normalizar(0, 116, 5, D116, 0.25) == (
        Evento(0, 0, 5, ev.RETRATO, ev.VENDA, 27404, 2, "X9", 7),
        Evento(0, 1, 5, ev.RETRATO, ev.VENDA, 27404, 4, "Y8", 8))
    assert ev.normalizar(1, 116, 6, D116_FIM, 0.25) == (Evento(1, 0, 6, ev.RETRATO_FIM),)


def test_negocio_e_fim_de_evento():
    (n,) = ev.normalizar(3, 150, 9, LT, 0.25)
    assert (n.tipo, n.lado, n.preco, n.quantidade, n.ordem, n.ordem_agressora, n.retrato) == (
        ev.NEGOCIO, ev.VENDA, 27401, 3, "P77", "G88", False)
    assert n.carimbo_ns == SSBOE * 10 ** 9 + 500 * 1000
    (r,) = ev.normalizar(4, 150, 9, LT_RETRATO, 0.25)
    assert (r.lado, r.preco, r.ordem, r.retrato, r.carimbo_ns) == (ev.COMPRA, 27396, None, True, None)
    assert ev.normalizar(5, 161, 9, D161, 0.25) == (Evento(5, 0, 9, ev.FIM_EVENTO, carimbo_ns=SSBOE * 10 ** 9 + 123999000),)


def test_id_vazio_vira_sem_id_e_negocio_sem_agressor_fica_sem_lado():
    (novo,) = ev.normalizar(0, 160, 0, si.quadro_livro([(1, "C", 10.0, 1, 1, "")], SSBOE * 10 ** 9), 0.25)
    assert novo.ordem is None
    retrato = ev.normalizar(1, 116, 0, si.quadro_retrato("V", 10.0, [(1, 1, ""), (2, 2, "z")]), 0.25)
    assert [e.ordem for e in retrato] == [None, "z"]
    (negocio,) = ev.normalizar(2, 150, 0, si.quadro_negocio(10.0, 1, "C", "", "", SSBOE * 10 ** 9), 0.25)
    assert (negocio.ordem, negocio.ordem_agressora, negocio.lado) == (None, None, ev.COMPRA)
    sem_lado = si._int(154467, 150) + si._dbl(100006, 10.0) + si._int(100178, 2)
    assert ev.normalizar(3, 150, 0, sem_lado, 0.25)[0].lado is None


LIVRO_OK = si.quadro_livro([(1, "C", 10.0, 1, 1, "a")], SSBOE * 10 ** 9)


@pytest.mark.parametrize("template,dados,tick,codigo", [
    (160, D160, 0.3, "preco_fora_da_grade:5"),
    (160, LIVRO_OK + si._int(153613, 2), 0.25, "quadro_160_desalinhado:5"),
    (160, si.quadro_livro([(9, "C", 10.0, 1, 1, "a")], 0), 0.25, "tipo_desconhecido:5"),
    (160, si._int(154467, 160) + si._int(110121, 1) + si._int(153612, 7) + si._dbl(154405, 10.0) + si._int(154406, 1)
     + si._int(153613, 1) + si._txt(149238, "a"), 0.25, "lado_desconhecido:5"),
    (150, si._int(154467, 150) + si._int(100178, 1), 0.25, "negocio_sem_preco:5"),
    (116, D160, 0.25, "template_divergente:5"),
    (116, si._int(154467, 116) + si._int(154406, 1) + si._int(153613, 1), 0.25, "retrato_desalinhado:5"),
    (116, si._int(154467, 116) + si._int(153612, 1) + si._int(154406, 1) + si._int(153613, 1) + si._txt(149238, "a"),
     0.25, "retrato_sem_preco:5"),
    (160, b"\x80", 0.25, "varint_invalido:5"),
    (19, si._int(154467, 19), 0.25, "template_fora_do_mercado:5"),
])
def test_quadro_ruim_para_a_leitura_com_codigo_e_indice(template, dados, tick, codigo):
    with pytest.raises(EventoError) as erro:
        ev.normalizar(5, template, 0, dados, tick)
    assert str(erro.value) == codigo


# as tres formas de 150 da primeira captura real com negocios (25/10): bits 23 e 15 sao negocio, bit 8 so volume
NEGOCIO_23 = si._int(154467, 150) + si._int(149138, 23) + si._dbl(100006, 10.0) + si._int(100178, 2) + si._int(112003, 1)
NEGOCIO_15 = si._int(154467, 150) + si._int(149138, 15) + si._int(110121, 1) + si._dbl(100006, 10.0) + si._int(100178, 1)
SO_VOLUME = si._int(154467, 150) + si._int(149138, 8) + si._int(100032, 123456)


def test_150_so_com_volume_nao_e_negocio_e_negocio_sem_preco_continua_parando():
    assert ev.normalizar(3, 150, 0, SO_VOLUME, 0.25) == ()
    for presenca in (2, 4, 8, 16, 30):    # qualquer combinacao sem o bit 1
        assert ev.normalizar(3, 150, 0, si._int(154467, 150) + si._int(149138, presenca), 0.25) == ()
    assert ev.normalizar(3, 150, 0, SO_VOLUME + si._dbl(100006, 10.0) + si._int(100178, 1), 0.25) == ()
    for dados, retrato in ((NEGOCIO_23, False), (NEGOCIO_15, True)):
        (e,) = ev.normalizar(3, 150, 0, dados, 0.25)
        assert (e.tipo, e.preco, e.retrato) == (ev.NEGOCIO, 40, retrato)
    for presenca in (1, 23, 31):      # diz que e negocio, mas veio sem preco: para a leitura
        with pytest.raises(EventoError, match="negocio_sem_preco:3"):
            ev.normalizar(3, 150, 0, si._int(154467, 150) + si._int(149138, presenca) + si._int(100178, 1), 0.25)


def test_captura_com_150_so_de_volume_e_lida_inteira(tmp_path):
    from qhos2 import livro as lv
    cenario = si.gerar(9, 400)
    com_volume = si.Cenario(cenario.semente, cenario.tick, [q for i, quadro in enumerate(cenario.quadros)
                                                            for q in ([quadro, (150, SO_VOLUME)] if i % 7 == 3 else [quadro])])
    pasta, limpa = si.gravar(com_volume, tmp_path / "a"), si.gravar(cenario, tmp_path / "b")
    negocios = lambda p: [(e.preco, e.quantidade, e.lado) for e in ev.eventos_da_captura(p) if e.tipo == ev.NEGOCIO]
    assert len(com_volume.quadros) > len(cenario.quadros) and negocios(pasta) == negocios(limpa) != []
    assert lv.livro_da_captura(pasta).assinatura() == lv.livro_da_captura(limpa).assinatura()
    assert lv.livro_da_captura(pasta).canonico() == cenario.livro_final()


@pytest.mark.parametrize("preco", [10, float("nan"), float("inf")])
def test_ticks_so_aceita_preco_finito_em_ponto_flutuante(preco):
    with pytest.raises(EventoError, match="preco_invalido"):
        ev.ticks(preco, 0.25)


def test_ticks_exatos_e_tolerancia_minima():
    assert ev.ticks(6850.25, 0.25) == 27401 and ev.ticks(-0.5, 0.25) == -2
    assert ev.ticks(1.1, 0.1) == 11  # 1.1 / 0.1 nao e exato em ponto flutuante, mas esta na grade
    assert ev.ticks(0.3, 0.1) == 3  # 0.3 / 0.1 da 2.9999999999999996: arredonda, nao trunca
    with pytest.raises(EventoError, match="preco_fora_da_grade"):
        ev.ticks(6850.26, 0.25)


# ---- captura inteira --------------------------------------------------------------------------------

def test_captura_sintetica_vira_eventos_com_o_tick_do_manifesto(tmp_path):
    cenario = si.gerar(3, 50)
    pasta = si.gravar(cenario, tmp_path)
    eventos = list(ev.eventos_da_captura(pasta))
    assert [e.indice for e in eventos] == sorted(e.indice for e in eventos)
    assert {e.indice for e in eventos} == set(range(len(cenario.quadros)))
    assert sum(e.tipo == ev.RETRATO_FIM for e in eventos) == 1
    carimbados = [e for e in eventos if e.carimbo_ns is not None]
    assert carimbados and all(e.recebido_ns - e.carimbo_ns == 75_000_000 for e in carimbados)
    assert ev.assinatura(eventos) == ev.assinatura(ev.eventos_da_captura(pasta))  # deterministico
    assert ev.assinatura(ev.eventos_da_captura(pasta, tick=cenario.tick)) == ev.assinatura(eventos)
    # O tick de referencia conhecido nao pode ser sobrescrito silenciosamente:
    # isso mudaria a unidade dos precos e das medidas usadas pela pesquisa.
    with pytest.raises(EventoError, match="tick_diverge"):
        list(ev.eventos_da_captura(pasta, tick=0.125))


def test_assinatura_muda_com_qualquer_diferenca():
    a = [Evento(0, 0, 1, ev.NOVO, ev.COMPRA, 10, 1, "x", 1)]
    b = [Evento(0, 0, 1, ev.NOVO, ev.COMPRA, 10, 2, "x", 1)]
    assert ev.assinatura(a) != ev.assinatura(b) and ev.assinatura(a) == ev.assinatura(list(a))
    assert ev.assinatura([]) == ev.assinatura(iter([]))


def test_captura_nao_verificada_e_recusada(tmp_path):
    pasta = si.gravar(si.gerar(1, 5), tmp_path)
    (pasta / "manifest.json").unlink()
    with pytest.raises(EventoError, match="captura_nao_verificada:manifesto_ausente"):
        list(ev.eventos_da_captura(pasta))


@pytest.mark.parametrize("referencia", [None, {}, {"min_qprice_change": None}, {"min_qprice_change": 0.0},
                                        {"min_qprice_change": "0.25"}])
def test_sem_tick_no_manifesto_e_recusada(tmp_path, referencia):
    gravador = Gravador(tmp_path, identidade={"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"})
    gravador(D161, 161, 1)
    status = {"estado": "FECHADA"} if referencia is None else {"referencia": referencia}
    gravador.fechar(status, fechamento_normal=True)
    with pytest.raises(EventoError, match="tick_desconhecido"):
        list(ev.eventos_da_captura(gravador.pasta))
    assert len(list(ev.eventos_da_captura(gravador.pasta, tick=0.25))) == 1


@pytest.mark.parametrize("tick", [0.0, -0.25, float("nan"), 1])
def test_tick_explicito_invalido(tmp_path, tick):
    pasta = si.gravar(si.gerar(1, 1), tmp_path)
    with pytest.raises(EventoError, match="tick_invalido"):
        list(ev.eventos_da_captura(pasta, tick=tick))


def test_linha_grande_demais(tmp_path):
    # uma linha de fato maior que o limite, gravada pelo proprio gravador: nenhum limite e remendado em modulo
    # algum, entao o teste nao depende do estilo de import da constante (QHOS-AUD-118)
    from qhos2.catalogo import LINHA_MAX_BYTES
    cenario = si.gerar(1, 1)
    template, dados = cenario.quadros[0]
    gravador = Gravador(tmp_path, identidade={"system_name": si.SISTEMA, "requested_symbol": "SINTETICO",
                                              "exchange": "QHOS2"}, captura_id="grande")
    gravador(dados, template, si.INICIO_NS)
    gravador(dados + b"\x00" * LINHA_MAX_BYTES, template, si.INICIO_NS + si.PASSO_NS)   # em base64 passa do limite
    gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    with pytest.raises(EventoError, match="linha_grande_demais"):
        list(ev.eventos_da_captura(gravador.pasta))
    assert len(list(ev.eventos_da_captura(si.gravar(cenario, tmp_path / "normal")))) >= 1   # sem a linha grande, le
