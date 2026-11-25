"""Campanha de correcao, lote l5c-leitura: eventos normalizados (QHOS-AUD-058, 064, 065, 067, 132).

Um so xfail estrito, o da 064 (defeito confirmado; patch em `EVIDENCIAS/QHOS-AUD-064/patch_proposto.diff`). O resto
e caracterizacao (058, 065), golden/tripwire (067) e teste da fronteira de `normalizar` (132); as opcoes de patch da
058 e 065 sao para a decisao do autor. `src/qhos2` nao muda nesta janela. Dados sempre sinteticos, em `tmp_path`.
"""
import json

import pytest

from qhos2 import eventos as ev
from qhos2 import mercado as m
from qhos2 import rprotocol as p
from qhos2 import sintetico as si
from qhos2.catalogo import verificar_captura
from qhos2.eventos import Evento, EventoError
from qhos2.gravador import Gravador

IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}
STATUS = {"estado": "FECHADA", "retrato_completo": True, "referencia": {"min_qprice_change": 0.25}}
FIM_161 = si._int(p.F_TEMPLATE, p.PROFUNDIDADE_FIM)          # um 161 sem carimbo: vira exatamente um FIM_EVENTO


def gravar(raiz, quadros, status=STATUS, *, captura_id="caso"):
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id=captura_id)
    for i, (template, dados) in enumerate(quadros):
        g(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    g.fechar(status, fechamento_normal=True)
    return g.pasta


# ---- QHOS-AUD-058: tick invalido no manifesto tratado como "legado ausente" -------------------------------------

def test_referencia_ausente_com_tick_explicito_continua_aceita(tmp_path):
    """O caso legado de verdade (manifesto sem `referencia`) tem de continuar passando com `tick=` explicito."""
    pasta = gravar(tmp_path, [(161, FIM_161)], {"estado": "FECHADA"})
    with pytest.raises(EventoError, match="tick_desconhecido"):
        list(ev.eventos_da_captura(pasta))
    assert len(list(ev.eventos_da_captura(pasta, tick=0.25))) == 1


@pytest.mark.parametrize("valor", ["0.25", 0.0, -0.25])
def test_caracterizacao_tick_invalido_no_manifesto_com_tick_explicito_passa_como_legado(tmp_path, valor):
    """QHOS-AUD-058, caracterizacao: hoje um tick PRESENTE e invalido no manifesto e tratado como referencia ausente
    (eventos.py:214-218) quando `tick=` e explicito. O comentario do codigo fala so em "legadas sem referencia", mas
    tests/test_eventos.py:155-164 consagra o comportamento para 0.0 e "0.25": as fontes divergem, decisao do autor.
    Se o autor decidir pela recusa, este teste vira `raises(EventoError, match="tick_invalido_no_manifesto")`."""
    pasta = gravar(tmp_path, [(161, FIM_161)], {"estado": "FECHADA", "referencia": {"min_qprice_change": valor}})
    with pytest.raises(EventoError, match="^tick_desconhecido$"):
        list(ev.eventos_da_captura(pasta))
    assert len(list(ev.eventos_da_captura(pasta, tick=0.25))) == 1


# ---- QHOS-AUD-064: os dois decodificadores divergem em template repetido ----------------------------------------

# F_TEMPLATE=160 seguido de F_TEMPLATE=116 (com rp_code "0", como o fim do retrato)
TEMPLATE_REPETIDO = si._int(p.F_TEMPLATE, p.PROFUNDIDADE_ORDEM) + si.quadro_retrato_fim()


def _veredito(decodificar, erro):
    try:
        return ("aceita", decodificar(TEMPLATE_REPETIDO).template_id)
    except erro as e:
        return ("recusa", str(e))


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-064: rprotocol.decodificar fica com o ultimo F_TEMPLATE (rprotocol.py:294-298) "
                                       "e mercado.decodificar recusa template_repetido_divergente (mercado.py:103-104); a sessao "
                                       "grava o que o replay fail-closed (eventos.py:16-17) recusa. Qual veredito e do autor")
def test_sessao_e_replay_dao_o_mesmo_veredito_a_template_repetido():
    sessao = _veredito(p.decodificar, p.ProtocolError)          # Sessao._tratar usa p.decodificar (sessao.py:313)
    replay = _veredito(m.decodificar, m.MercadoError)           # eventos.normalizar usa m.decodificar (eventos.py:92)
    assert sessao[0] == replay[0], (sessao, replay)


def test_captura_com_template_repetido_e_verificada_pelo_catalogo_mas_irreplayavel(tmp_path):
    """Consequencia do 064, hoje: a sessao entregaria o quadro ao gravador com o template que `p.decodificar` ve
    (116), o catalogo verifica os bytes, e o replay recusa o mesmo quadro. Caracterizacao do estado atual."""
    try:
        template_da_sessao = p.decodificar(TEMPLATE_REPETIDO).template_id
    except p.ProtocolError as erro:
        pytest.skip(f"a sessao ja recusa o quadro ({erro}): a consequencia descrita aqui deixou de existir")
    assert template_da_sessao == p.RESP_RETRATO_PROFUNDIDADE
    pasta = gravar(tmp_path, [(template_da_sessao, TEMPLATE_REPETIDO)])
    assert verificar_captura(pasta)["verificada"] is True
    with pytest.raises(EventoError, match="^template_repetido_divergente:0$"):
        list(ev.eventos_da_captura(pasta))


# ---- QHOS-AUD-065: EventoError sem forma unica ------------------------------------------------------------------

def test_caracterizacao_das_tres_formas_de_evento_error(tmp_path):
    """Hoje convivem `codigo:indice`, `codigo` sem sufixo e `captura_nao_verificada:div1,div2`. O contrato nao
    define uma forma unica (o achado pede atributos estruturados); isto pina as formas atuais para a integracao."""
    with pytest.raises(EventoError) as com_indice:
        ev.normalizar(5, 160, 0, b"\x80", 0.25)
    with pytest.raises(EventoError) as sem_sufixo:
        ev.ticks(10, 0.25)
    pasta = gravar(tmp_path, [(161, FIM_161)])
    manifesto = json.loads((pasta / "manifest.json").read_text())
    manifesto.update(frames=manifesto["frames"] + 1, bytes=manifesto["bytes"] + 1)
    (pasta / "manifest.json").write_text(json.dumps(manifesto))
    with pytest.raises(EventoError) as com_lista:
        list(ev.eventos_da_captura(pasta))
    assert str(com_indice.value) == "varint_invalido:5"
    assert str(sem_sufixo.value) == "preco_invalido"
    assert str(com_lista.value) == "captura_nao_verificada:frames_divergem,bytes_divergem"
    # a lista de divergencias tem virgula: `split(":")[0]` (historico.py:150) a descarta inteira
    assert str(com_lista.value).split(":")[0] == "captura_nao_verificada"


# ---- QHOS-AUD-067: Evento e assinatura() sem versao (golden absoluto) -------------------------------------------

CAMPOS_DO_EVENTO = ("indice", "posicao", "recebido_ns", "tipo", "lado", "preco", "quantidade", "ordem",
                    "prioridade", "carimbo_ns", "ordem_agressora", "retrato")
DEFAULTS_DO_EVENTO = {"lado": None, "preco": None, "quantidade": None, "ordem": None, "prioridade": None,
                      "carimbo_ns": None, "ordem_agressora": None, "retrato": False}
# assinatura(eventos_da_captura(si.gravar(si.gerar(3, 50)))) em 36f0e1f (Python 3.12, WSL), 29/10/2026.
# Qualquer campo novo, default mudado ou mudanca no repr do Evento muda este valor: e o tripwire que faltava.
ASSINATURA_GOLDEN = "f1ba2618616d22b984b7bc02752f3416b1c4482a0be97a46ee699be6602ed797"
EVENTOS_GOLDEN = 99


def test_evento_tem_exatamente_os_campos_e_defaults_conhecidos():
    assert Evento._fields == CAMPOS_DO_EVENTO
    assert Evento._field_defaults == DEFAULTS_DO_EVENTO


def test_assinatura_da_captura_sintetica_e_um_golden_absoluto(tmp_path):
    eventos = list(ev.eventos_da_captura(si.gravar(si.gerar(3, 50), tmp_path)))
    assert len(eventos) == EVENTOS_GOLDEN
    assert ev.assinatura(eventos) == ASSINATURA_GOLDEN


# ---- QHOS-AUD-132: Evento aceita instancias invalidas --------------------------------------------------------------

def _eventos_do_corpus():
    cenario = si.gerar(21, 300)
    saida = []
    for i, (template, dados) in enumerate(cenario.quadros):
        saida.extend(ev.normalizar(i, template, si.INICIO_NS + i * si.PASSO_NS, dados, cenario.tick))
    return saida


def test_normalizar_so_produz_eventos_com_campos_bem_tipados():
    """A validacao fica na fronteira (`normalizar`): nada que sai dela tem tipo, lado, preco ou quantidade fora do
    contrato. `Evento` em si e um NamedTuple portador, construivel com qualquer coisa (test_livro.py:99 depende disso)."""
    eventos = _eventos_do_corpus()
    assert eventos and {e.tipo for e in eventos} <= {ev.RETRATO, ev.RETRATO_FIM, ev.NOVO, ev.ALTERA, ev.REMOVE,
                                                     ev.NEGOCIO, ev.FIM_EVENTO}
    for e in eventos:
        assert e.lado in (None, ev.COMPRA, ev.VENDA)
        for campo in (e.indice, e.posicao, e.recebido_ns):
            assert type(campo) is int
        for campo in (e.preco, e.quantidade, e.prioridade, e.carimbo_ns):
            assert campo is None or type(campo) is int
        for campo in (e.ordem, e.ordem_agressora):
            assert campo is None or (isinstance(campo, str) and campo)
        assert type(e.retrato) is bool
        if e.tipo in (ev.RETRATO, ev.NOVO, ev.ALTERA, ev.REMOVE, ev.NEGOCIO):
            assert type(e.preco) is int and type(e.quantidade) is int and e.lado is not None


def test_quantidade_booleana_nao_passa_pelo_fio_e_o_livro_a_recusa():
    quadro = si._int(p.F_TEMPLATE, 150) + si._dbl(100006, 10.0) + si._int(100178, 1)
    (negocio,) = ev.normalizar(0, 150, 0, quadro, 0.25)
    assert type(negocio.quantidade) is int        # varint 1 vira int, nunca bool
    from qhos2.livro import Livro
    livro = Livro()
    livro.aplicar(Evento(0, 0, 0, ev.RETRATO, ev.COMPRA, 100, True, "a", 1))
    assert livro.anomalias == {"durante_retrato_quantidade_invalida": 1} and not livro.ordens and livro.suspeito
