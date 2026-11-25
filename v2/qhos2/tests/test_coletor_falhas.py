"""Invalidacao do lote e do historico apos falha da captura; sem rede."""
from qhos2 import sintetico as si, leitura as le, historico as hi
from qhos2.coletor import Coletor, ESZ6
from test_integridade_heranca import gravar, retrato, identificar, leitor, STATUS


def negocio():
    return (150, identificar(si.quadro_negocio(100.0, 1, "C", "c", "a", si.INICIO_NS)))


def pedido():
    return {"type":"request", "id":"teste-1", "method":"history.candles",
            "params":{"timeframe":"1m", "limit":10, "instrumentId":ESZ6["id"]}}


def test_lote_com_falha_nao_publica_negocio_nem_conserva_candle(tmp_path):
    frames = retrato(("4",))
    g = gravar(tmp_path, frames[:2] + [negocio()] + frames[2:])
    l = leitor(g.pasta); lote = l.avancar()
    assert l.estado == le.FALHA
    c = Coletor(token="a"*43, leitor=l)
    assert c.traduzir(lote, True) == []
    # pelo que um terminal receberia: livro que nao vale e nenhuma cotacao, porque nao ha ultimo preco (QHOS-AUD-072)
    assert [m["type"] for m in c.retrato_atual()] == ["book"] and c.retrato_atual()[0]["ready"] is False


def test_manifesto_falho_posterior_retira_historico_antes_de_novo_lote(tmp_path):
    g = gravar(tmp_path / "capturas", retrato() + [negocio()], fechar=False)
    l = leitor(g.pasta)
    h = hi.Historico(tmp_path/"capturas", tmp_path/"cache", tick=0.25, simbolo="ESZ6", bolsa="CME", relogio=lambda:si.INICIO_NS/1e9+10)
    c = Coletor(token="a"*43, leitor=l, historico=h)
    assert c.traduzir(l.avancar(), True)
    assert c.responder(pedido())["result"]["candles"]
    g.fechar({**STATUS, "estado":"FALHOU", "retrato_completo":False}, fechamento_normal=False)
    l.avancar()
    assert l.estado == le.FALHA
    # A rota ja recusa a origem falha mesmo antes de traduzir ser chamado novamente.
    assert c.responder(pedido())["result"]["candles"] == []
    assert c.traduzir([], True) == []
