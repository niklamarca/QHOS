"""Campanha de correcao, lote l5c-leitura: candles de historico (QHOS-AUD-134; e os reflexos de 058 e 065 em historico.py).

Sem xfail: testes das invariantes OHLC na escrita (134) e caracterizacoes de 058 e 065 no historico.
`historico.py` nao muda nesta janela; as opcoes de patch para a decisao estao em `EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import json

import pytest

from qhos2 import eventos as ev
from qhos2 import historico as hi
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}
FIM_161 = si._int(154467, 161)


def gravar(raiz, nome, cenario=None, *, referencia={"min_qprice_change": 0.25}, quadros=None):
    g = Gravador(raiz, identidade=IDENTIDADE, captura_id=nome)
    for i, (template, dados) in enumerate(quadros if quadros is not None else cenario.quadros):
        g(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    status = {"estado": "FECHADA"}
    if referencia is not None:
        status["referencia"] = referencia
    g.fechar(status, fechamento_normal=True)
    return g.pasta


def negocio(segundo, preco, qtd=1, **extra):
    return ev.Evento(**{"indice": 0, "posicao": 0, "recebido_ns": segundo * 10 ** 9, "tipo": ev.NEGOCIO, "lado": ev.COMPRA,
                        "preco": preco, "quantidade": qtd, "carimbo_ns": segundo * 10 ** 9, **extra})


# ---- QHOS-AUD-134: invariantes OHLC so conferidas ao ler o cache ------------------------------------------------

def _invariantes_do_cache(barras):
    """Exatamente o crivo de `_do_cache` (historico.py:82-88), aplicado as barras montadas por `negocio()`."""
    anterior = -1
    for minuto, linha in sorted(barras.minutos.items()):
        assert len(linha) == 5 and all(type(x) is int for x in linha)
        abertura, maxima, minima, fechamento, volume = linha
        assert minuto >= 0 and minuto % 60 == 0 and minuto > anterior and min(linha) > 0
        assert minima <= min(abertura, fechamento) <= max(abertura, fechamento) <= maxima
        anterior = minuto


@pytest.mark.parametrize("semente", [17, 23])
def test_barras_montadas_de_negocios_sinteticos_ja_nascem_dentro_das_invariantes_do_cache(tmp_path, semente):
    pasta = gravar(tmp_path / "capturas", f"c{semente}", si.gerar(semente, 800))
    barras = hi.Barras().eventos(ev.eventos_da_captura(pasta))
    assert barras.minutos
    _invariantes_do_cache(barras)


def test_negocio_sem_preco_ou_quantidade_positivos_nao_entra_na_escrita():
    """A guarda de escrita de `negocio()` (historico.py:45-47) e a razao de as invariantes valerem na origem."""
    b = hi.Barras().eventos([negocio(600, 0), negocio(600, -3), negocio(600, 5, qtd=0), negocio(600, 5, qtd=-1),
                             negocio(600, 7, retrato=True), negocio(600, 9, tipo=ev.NOVO, ordem="x")])
    assert b.minutos == {}
    b.negocio(negocio(600, 9, 2))
    assert b.minutos == {600: [9, 9, 9, 9, 2]}
    _invariantes_do_cache(b)


def test_cache_escrito_e_lido_de_volta_sem_perda(tmp_path, monkeypatch):
    pasta = gravar(tmp_path / "capturas", "c1", si.gerar(4, 300))
    cache = tmp_path / "cache"
    escrito = hi.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")
    _invariantes_do_cache(escrito)
    monkeypatch.setattr(hi.ev, "eventos_da_captura", lambda *a, **k: (_ for _ in ()).throw(AssertionError("releu")))
    lido = hi.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert lido.minutos == escrito.minutos and lido.minutos


# ---- QHOS-AUD-058 em historico.py:108-113 (mesmo padrao de eventos.py:214-218) -----------------------------------

@pytest.mark.parametrize("valor", ["0.25", 0.0])
def test_caracterizacao_barras_aceitam_tick_invalido_no_manifesto_com_tick_explicito(tmp_path, valor):
    """QHOS-AUD-058, caracterizacao: historico.py:108-113 repete o padrao de eventos.py:214-218 (tick presente e invalido
    no manifesto vira "legado sem referencia"). Mesma decisao em aberto do teste em test_correcao_eventos.py."""
    pasta = gravar(tmp_path / "capturas", "c1", quadros=[(161, FIM_161)], referencia={"min_qprice_change": valor})
    barras = hi.barras_da_captura(pasta, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert barras is not None and barras.minutos == {}


# ---- QHOS-AUD-065 em historico.py:150: `split(":")[0]` descarta a lista de divergencias --------------------------

def test_caracterizacao_recusadas_guarda_so_o_codigo_sem_as_divergencias(tmp_path):
    """QHOS-AUD-065, caracterizacao: `recusadas` guarda `str(erro).split(":")[0]` (historico.py:150) e perde a lista
    (`frames_divergem,bytes_divergem`). tests/test_historico.py:76 pina o valor cortado; o unico consumidor e o aviso
    de coletor.py:681. Se o autor decidir preservar, troca-se a ultima assercao (patch em EVIDENCIAS/QHOS-AUD-065)."""
    pasta = gravar(tmp_path / "capturas", "estragada", quadros=[(161, FIM_161)])
    manifesto = json.loads((pasta / "manifest.json").read_text())
    manifesto.update(frames=manifesto["frames"] + 1, bytes=manifesto["bytes"] + 1)
    (pasta / "manifest.json").write_text(json.dumps(manifesto))
    with pytest.raises(ev.EventoError, match="^captura_nao_verificada:frames_divergem,bytes_divergem$"):
        list(ev.eventos_da_captura(pasta))                   # a informacao existe no erro...
    h = hi.Historico(tmp_path / "capturas", tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert h.recusadas == {"estragada": "captura_nao_verificada"}    # ...e e cortada aqui
