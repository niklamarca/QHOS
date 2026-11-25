"""Campanha de correcao, lote l5b-coletor: servico WebSocket do coletor ao vivo (QHOS-AUD-092, 170, 187c, 188).

Coletor em porta efemera do SO (`porta=0`), token ficticio, capturas sinteticas em `tmp_path`. `xfail(strict=True)` =
defeito confirmado em `src/qhos2/coletor.py` (congelado); patch minimo em `09_correcao/EVIDENCIAS/<ID>/`.
"""
import asyncio
import contextlib
import json

import pytest

pytest.importorskip("websockets")

from websockets.asyncio.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed  # noqa: E402

from qhos2 import coletor as co, leitura as le, livro as lv, sintetico as si  # noqa: E402
from qhos2.gravador import Gravador  # noqa: E402

TOKEN = "A" * 43
ORIGEM = "http://127.0.0.1:8010"
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"}


def rodar(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


@contextlib.asynccontextmanager
async def no_ar(**extra):
    avisos = []
    coletor = co.Coletor(token=TOKEN, origem=ORIGEM, porta=0, avisar=avisos.append, **extra)
    pronto = asyncio.Event()
    tarefa = asyncio.create_task(coletor.rodar(pronto))
    await pronto.wait()
    try:
        yield coletor, f"ws://127.0.0.1:{coletor.porta}", avisos
    finally:
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa


async def entrar(url, token=TOKEN):
    ws = await connect(url, origin=ORIGEM)
    await ws.send(json.dumps({"type": "hello", "protocol": co.PROTOCOLO, "token": token}))
    return ws, json.loads(await ws.recv())


async def receber_ate(ws, condicao, limite=20000):
    recebidas = []
    for _ in range(limite):
        recebidas.append(json.loads(await ws.recv()))
        if condicao(recebidas[-1]):
            return recebidas
    raise AssertionError("condicao nao atingida")


def leitor_sintetico(pasta):
    return le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2")


# ---- QHOS-AUD-092: mensagens qhos.dashboard.v1 validadas no caminho vivo ------------------------------------------

def test_mensagens_difundidas_ao_vivo_passam_nas_regras_da_ponte(tmp_path, monkeypatch):
    """Lacuna de teste: `fora_do_formato` so rodava em `--conferir`. Aqui `_difundir` e interceptado no cenario vivo
    (captura sendo gravada) e cada livro, negocio e cotacao difundido e conferido com as regras da ponte."""
    monkeypatch.setattr(co, "LIVRO_A_CADA_S", 0.01)
    monkeypatch.setattr(co, "LER_A_CADA_S", 0.005)
    cenario = si.gerar(11, 300)
    gravador = Gravador(tmp_path / "capturas", identidade=IDENTIDADE, captura_id="viva")
    difundidas = []
    original = co.Coletor._difundir

    async def difundir_observado(self, mensagens):
        difundidas.extend(mensagens)
        await original(self, mensagens)
    monkeypatch.setattr(co.Coletor, "_difundir", difundir_observado)

    async def cena():
        async with no_ar(leitor=leitor_sintetico(gravador.pasta)) as (coletor, url, _):
            ws, welcome = await entrar(url)
            for i, (template, dados) in enumerate(cenario.quadros):
                gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
                if i % 50 == 0:
                    await asyncio.sleep(0.02)
            gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
            final = lv.livro_da_captura(gravador.pasta)
            esperado = [[p * 0.25, q] for p, q, _ in final.niveis("C", 40)]
            recebidas = await receber_ate(ws, lambda m: m["type"] == "book" and m["bids"] == esperado)
            return coletor, welcome, recebidas
    coletor, welcome, recebidas = rodar(cena())
    assert coletor.leitor.estado == le.ENCERRADA
    por_tipo = {}
    for m in difundidas:
        por_tipo[m["type"]] = por_tipo.get(m["type"], 0) + 1
        if m["type"] in ("book", "trade"):
            assert co.fora_do_formato(m, coletor.tick) is None, m
        if m["type"] in ("book", "trade", "ticker"):
            assert (m["subscriptionId"], m["revision"], m["instrumentId"]) == (
                welcome["selection"]["subscriptionId"], "1", co.ESZ6["id"])
    assert por_tipo["book"] > 1 and por_tipo["trade"] > 1 and por_tipo["ticker"] >= 1
    assert any(m["ready"] for m in difundidas if m["type"] == "book")
    recebidos = [m for m in recebidas if m["type"] in ("book", "trade")]
    assert recebidos and all(co.fora_do_formato(m, coletor.tick) is None for m in recebidos)


def test_retrato_e_percurso_do_coletor_sem_captura_passam_nas_regras_da_ponte():
    coletor = co.Coletor(token=TOKEN, porta=0)
    assert coletor.retrato_atual() == []
    percurso = coletor.percurso()
    assert all(c["since"] <= percurso["time"] and c["state"] in ("ok", "atencao", "falha", "desconhecido")
               for c in percurso["stages"])


# ---- QHOS-AUD-170: excecao inesperada em responder fecha a conexao sem resposta ao pedido ----------------------------

def _responder_que_quebra(monkeypatch):
    def quebra(self, pedido):
        raise KeyError("campo_inesperado")
    monkeypatch.setattr(co.Coletor, "responder", quebra)


def test_caracterizacao_excecao_em_responder_fecha_a_conexao_com_1011(monkeypatch):
    _responder_que_quebra(monkeypatch)

    async def cena():
        async with no_ar() as (coletor, url, _):
            ws, _w = await entrar(url)
            await ws.send(json.dumps({"type": "request", "id": "q1", "method": "catalog.search", "params": {"query": ""}}))
            with pytest.raises(ConnectionClosed) as erro:
                await receber_ate(ws, lambda m: m["type"] == "response", limite=50)
            await asyncio.sleep(0.05)
            return erro.value.rcvd.code, dict(coletor.contagem), len(coletor.clientes)
    codigo, contagem, clientes = rodar(cena())
    assert codigo == 1011 and clientes == 0
    assert not any(k.startswith("intern") for k in contagem)


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-170: `_atender` so captura ConnectionClosed; excecao inesperada em "
                                       "`responder` derruba a conexao (1011) sem resposta nem contador")
def test_excecao_em_responder_vira_resposta_internal_e_e_contada(monkeypatch):
    _responder_que_quebra(monkeypatch)

    async def cena():
        async with no_ar() as (coletor, url, _):
            ws, _w = await entrar(url)
            await ws.send(json.dumps({"type": "request", "id": "q1", "method": "catalog.search", "params": {"query": ""}}))
            recebidas = await receber_ate(ws, lambda m: m["type"] == "response", limite=50)
            return recebidas[-1], dict(coletor.contagem)
    resposta, contagem = rodar(cena())
    assert resposta == {"type": "response", "id": "q1", "ok": False, "error": {"code": "INTERNAL"}}
    assert contagem["internas"] == 1


# ---- QHOS-AUD-187(c): token com surrogate escapa da validacao do hello ------------------------------------------------

def test_caracterizacao_token_com_surrogate_derruba_a_conexao_sem_contar():
    async def cena():
        async with no_ar() as (coletor, url, avisos):
            ws = await connect(url, origin=ORIGEM)
            await ws.send(json.dumps({"type": "hello", "protocol": co.PROTOCOLO, "token": "\ud800"}))
            with pytest.raises(ConnectionClosed) as erro:
                await ws.recv()
            return erro.value.rcvd.code, dict(coletor.contagem)
    codigo, contagem = rodar(cena())
    assert codigo == 1011 and contagem["recusado_token"] == 0 and contagem["invalidas"] == 0


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-187(c): `token.encode('utf-8')` lanca UnicodeEncodeError antes do "
                                       "compare_digest; sem UNAUTHORIZED, sem 1008, sem contador")
def test_token_com_surrogate_e_recusado_como_unauthorized():
    async def cena():
        async with no_ar() as (coletor, url, _):
            ws = await connect(url, origin=ORIGEM)
            await ws.send(json.dumps({"type": "hello", "protocol": co.PROTOCOLO, "token": "\ud800"}))
            resposta = json.loads(await ws.recv())
            with pytest.raises(ConnectionClosed) as erro:
                await ws.recv()
            return resposta, erro.value.rcvd.code, dict(coletor.contagem)
    resposta, codigo, contagem = rodar(cena())
    assert resposta == {"type": "error", "code": "UNAUTHORIZED"} and codigo == 1008
    assert contagem["recusado_token"] == 1


# ---- QHOS-AUD-188: MAX_CLIENTES conferido antes do hello e aplicado depois ----------------------------------------

async def _dois_handshakes_simultaneos(url):
    primeiro = await connect(url, origin=ORIGEM)
    segundo = await connect(url, origin=ORIGEM)          # as duas conexoes passam pela contagem com 0 clientes
    hello = json.dumps({"type": "hello", "protocol": co.PROTOCOLO, "token": TOKEN})
    respostas = []
    for ws in (primeiro, segundo):
        try:                                             # um coletor correto pode recusar (1013) antes do hello
            await ws.send(hello)
            respostas.append(json.loads(await asyncio.wait_for(ws.recv(), 2)))
        except ConnectionClosed as erro:
            respostas.append({"fechada": erro.rcvd.code if erro.rcvd else None})
    return respostas


def test_caracterizacao_duas_conexoes_antes_do_hello_passam_com_max_clientes_1(monkeypatch):
    monkeypatch.setattr(co, "MAX_CLIENTES", 1)

    async def cena():
        async with no_ar() as (coletor, url, avisos):
            respostas = await _dois_handshakes_simultaneos(url)
            await asyncio.sleep(0.05)
            return respostas, len(coletor.clientes), dict(coletor.contagem), list(avisos)
    respostas, clientes, contagem, avisos = rodar(cena())
    assert [r.get("type") for r in respostas] == ["welcome", "welcome"]
    assert clientes == 2 and contagem["recusado_lotado"] == 0
    assert "terminal conectado (2 de 1)" in avisos


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-188: a contagem de clientes acontece antes do `await` do hello e o "
                                       "registro depois; handshakes simultaneos ultrapassam MAX_CLIENTES")
def test_com_max_clientes_1_so_um_dos_handshakes_simultaneos_recebe_welcome(monkeypatch):
    monkeypatch.setattr(co, "MAX_CLIENTES", 1)

    async def cena():
        async with no_ar() as (coletor, url, _):
            respostas = await _dois_handshakes_simultaneos(url)
            await asyncio.sleep(0.05)
            return respostas, len(coletor.clientes)
    respostas, clientes = rodar(cena())
    assert sum(r.get("type") == "welcome" for r in respostas) <= 1 and clientes <= 1
