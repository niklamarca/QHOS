"""Integracao, etapa 4: o coletor so atende a pagina autorizada, com o token, e fala o protocolo do terminal.

Precisa da biblioteca `websockets` (a mesma da captura); sem ela estes testes sao pulados.
"""
import ast
import asyncio
import base64
import contextlib
import json
import os
import socket
import stat
from pathlib import Path

import pytest

pytest.importorskip("websockets")

from websockets.asyncio.client import connect  # noqa: E402
from websockets.exceptions import ConnectionClosed, InvalidStatus  # noqa: E402

from qhos2 import coletor as co  # noqa: E402

TOKEN = "A" * 43
ORIGEM = "http://127.0.0.1:8010"


def rodar(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


@contextlib.asynccontextmanager
async def no_ar(ao_avisar=None, **extra):
    avisos = []

    def avisar(texto):
        avisos.append(texto)
        if ao_avisar is not None:
            ao_avisar(texto)                 # gancho do teste: o aviso e emitido no loop e serve de sinal
    coletor = co.Coletor(token=TOKEN, origem=ORIGEM, porta=0, avisar=avisar, **extra)
    pronto = asyncio.Event()
    tarefa = asyncio.create_task(coletor.rodar(pronto))
    await pronto.wait()
    try:
        yield coletor, f"ws://127.0.0.1:{coletor.porta}", avisos
    finally:
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa


async def entrar(url, token=TOKEN, origem=ORIGEM, protocolo=co.PROTOCOLO):
    ws = await connect(url, origin=origem)
    hello = {"type": "hello", "protocol": protocolo}
    if token is not None:
        hello["token"] = token
    await ws.send(json.dumps(hello))
    return ws, json.loads(await ws.recv())


async def pedir(ws, metodo, params, ident="qhos-1"):
    await ws.send(json.dumps({"type": "request", "id": ident, "method": metodo, "params": params}))
    while True:
        resposta = json.loads(await ws.recv())
        if resposta["type"] == "response":
            return resposta


def test_welcome_com_o_que_o_terminal_exige():
    async def cenario():
        async with no_ar() as (coletor, url, avisos):
            ws, welcome = await entrar(url)
            assert welcome["type"] == "welcome" and welcome["protocol"] == "qhos.dashboard.v1"
            assert welcome["provider"] == "rithmic" and welcome["environment"] == "unknown"
            assert {"catalog", "selection"} <= set(welcome["capabilities"]) and "history" not in welcome["capabilities"]
            selecao = welcome["selection"]
            assert selecao["activeId"] == "rithmic:CME:ESZ6" and [i["id"] for i in selecao["instruments"]] == [
                "rithmic:CME:ESZ6"]
            assert selecao["instruments"][0]["capabilities"]["history"] is False
            assert len(coletor.clientes) == 1 and len(avisos) == 1            # um terminal, um aviso (o da conexao)
            assert not [k for k, v in coletor.contagem.items() if k.startswith("recusado_") and v]
            await ws.close()
            await asyncio.sleep(0.05)
            assert not coletor.clientes
    rodar(cenario())


def test_esz6_tick_valor_e_vencimento():
    assert co.ESZ6["tickSize"] * co.ESZ6["contractMultiplier"] == 12.5
    assert co.ESZ6["expiry"] == "2026-12-18"     # terceira sexta de dezembro de 2026


@pytest.mark.parametrize("token, codigo", [("B" * 43, "UNAUTHORIZED"), (None, "UNAUTHORIZED"),
                                           (TOKEN[:-1], "UNAUTHORIZED"), (42, "UNAUTHORIZED")])
def test_token_errado_ou_ausente_e_recusado(token, codigo):
    async def cenario():
        async with no_ar() as (coletor, url, avisos):
            ws, resposta = await entrar(url, token=token)
            assert resposta == {"type": "error", "code": codigo}
            with pytest.raises(ConnectionClosed):
                await ws.recv()
            assert not coletor.clientes and coletor.contagem["recusado_token"] == 1
            assert not any(TOKEN in a for a in avisos)
    rodar(cenario())


def test_protocolo_errado_e_recusado():
    async def cenario():
        async with no_ar() as (coletor, url, _):
            _, resposta = await entrar(url, protocolo="outro.v1")
            assert resposta == {"type": "error", "code": "PROTOCOL_UNSUPPORTED"}
    rodar(cenario())


@pytest.mark.parametrize("origem", ["http://127.0.0.1:9999", "https://site-qualquer.exemplo", "null",
                                    "http://localhost:8010", None])
def test_outra_pagina_e_recusada_antes_do_websocket(origem):
    async def cenario():
        async with no_ar() as (coletor, url, avisos):
            with pytest.raises(InvalidStatus) as erro:
                await connect(url, origin=origem)
            assert erro.value.response.status_code == 403
            assert coletor.contagem["recusado_origem"] == 1 and not coletor.clientes
    rodar(cenario())


def test_host_diferente_e_recusado():
    async def cenario():
        async with no_ar() as (coletor, url, _):
            chave = base64.b64encode(b"the sample nonce").decode()   # o exemplo da RFC 6455, nao e segredo
            leitor, escritor = await asyncio.open_connection("127.0.0.1", coletor.porta)
            escritor.write((f"GET / HTTP/1.1\r\nHost: atacante.exemplo:{coletor.porta}\r\nUpgrade: websocket\r\n"
                            f"Connection: Upgrade\r\nSec-WebSocket-Key: {chave}\r\n"
                            f"Sec-WebSocket-Version: 13\r\nOrigin: {ORIGEM}\r\n\r\n").encode())
            await escritor.drain()
            assert (await leitor.readline()).startswith(b"HTTP/1.1 403")
            escritor.close()
            assert coletor.contagem["recusado_host"] == 1
    rodar(cenario())


def test_catalogo_so_tem_o_esz6():
    async def cenario():
        async with no_ar() as (_, url, _a):
            ws, _w = await entrar(url)
            for consulta, ids in [("", ["rithmic:CME:ESZ6"]), ("es", ["rithmic:CME:ESZ6"]),
                                  ("S&P", ["rithmic:CME:ESZ6"]), ("BTC", []), ("ESH7", [])]:
                r = await pedir(ws, "catalog.search", {"query": consulta, "limit": 100})
                assert r["ok"] and [i["id"] for i in r["result"]["instruments"]] == ids
    rodar(cenario())


def test_troca_de_selecao_e_historico_sao_recusados():
    async def cenario():
        async with no_ar() as (_, url, _a):
            ws, welcome = await entrar(url)
            r = await pedir(ws, "selection.set", {"ids": ["rithmic:CME:ESH7"], "activeId": "rithmic:CME:ESH7",
                                                  "expectedRevision": "1"})
            assert r == {"type": "response", "id": "qhos-1", "ok": False, "error": {"code": "FORBIDDEN"}}
            r = await pedir(ws, "history.candles", {"instrumentId": "rithmic:CME:ESZ6", "timeframe": "1m"}, "qhos-2")
            assert r["error"] == {"code": "HISTORY_UNAVAILABLE"} and not r["ok"]
            r = await pedir(ws, "orders.place", {"qty": 1}, "qhos-3")
            assert r["error"] == {"code": "FORBIDDEN"}
    rodar(cenario())


def test_heartbeat_chega(monkeypatch):
    monkeypatch.setattr(co, "HEARTBEAT_S", 0.05)
    async def cenario():
        async with no_ar(relogio=lambda: 1_761_400_000.5) as (_, url, _a):
            ws, _w = await entrar(url)
            mensagem = json.loads(await ws.recv())
            while mensagem["type"] == "pipeline":                 # o percurso vai junto com o welcome (tarefa 9)
                mensagem = json.loads(await ws.recv())
            assert mensagem == {"type": "heartbeat", "time": 1_761_400_000_500}
    rodar(cenario())


def test_sem_hello_no_prazo_fecha(monkeypatch):
    monkeypatch.setattr(co, "PRAZO_HELLO_S", 0.1)
    async def cenario():
        async with no_ar() as (coletor, url, _):
            ws = await connect(url, origin=ORIGEM)
            with pytest.raises(ConnectionClosed):
                await ws.recv()
            assert coletor.contagem["sem_hello"] == 1
    rodar(cenario())


def test_lotado_e_mensagens_invalidas_fecham(monkeypatch):
    monkeypatch.setattr(co, "MAX_CLIENTES", 1)
    async def cenario():
        async with no_ar() as (coletor, url, _):
            ws, _w = await entrar(url)
            segundo = await connect(url, origin=ORIGEM)
            with pytest.raises(ConnectionClosed):
                await segundo.recv()
            assert coletor.contagem["recusado_lotado"] == 1
            for _ in range(co.MAX_INVALIDAS):
                await ws.send("{lixo")
            with pytest.raises(ConnectionClosed):
                while True:                                     # o percurso que ja estava a caminho e lido e ignorado
                    assert json.loads(await ws.recv())["type"] == "pipeline"
            assert coletor.contagem["invalidas"] == co.MAX_INVALIDAS
    rodar(cenario())


@pytest.mark.parametrize("extra", [{"origem": "http://localhost:8010"}, {"origem": "https://127.0.0.1:8010"},
                                   {"origem": "http://127.0.0.1"}, {"porta": 80}, {"token": "curto"}])
def test_configuracao_invalida(extra):
    with pytest.raises(co.ColetorError):
        co.Coletor(**{"token": TOKEN, "origem": ORIGEM, "porta": 8766, **extra})


def test_token_no_arquivo_so_do_usuario(tmp_path):
    caminho = tmp_path / "cfg" / "coletor.token"
    token = co.ler_ou_criar_token(caminho)
    assert len(token) == 43 and stat.S_IMODE(caminho.stat().st_mode) == 0o600
    assert stat.S_IMODE(caminho.parent.stat().st_mode) == 0o700
    assert co.ler_ou_criar_token(caminho) == token
    novo = co.ler_ou_criar_token(caminho, novo=True)
    assert novo != token and co.ler_ou_criar_token(caminho) == novo
    os.chmod(caminho, 0o644)
    with pytest.raises(co.ColetorError, match="token_com_permissao_aberta"):
        co.ler_ou_criar_token(caminho)
    os.chmod(caminho, 0o600)
    caminho.write_text("senha123\n")
    with pytest.raises(co.ColetorError, match="token_invalido_no_arquivo"):
        co.ler_ou_criar_token(caminho)


def test_linha_de_comando_nunca_mostra_o_token(tmp_path, capsys):
    caminho = tmp_path / "coletor.token"
    token = co.ler_ou_criar_token(caminho)
    ocupada = socket.socket()
    ocupada.bind(("127.0.0.1", 0))
    ocupada.listen()
    try:
        assert co.main(["--porta", str(ocupada.getsockname()[1]), "--token", str(caminho),
                        "--captura", str(tmp_path / "capturas" / "captura"), "--raiz", str(tmp_path)]) == 2
    finally:
        ocupada.close()
    saida = capsys.readouterr().out
    assert "porta_indisponivel" in saida and "/conexao" in saida and token not in saida


def test_caminho_na_tela_esconde_a_pasta_pessoal():
    assert co._sem_usuario(Path.home() / ".config" / "qhos2" / "coletor.token") == "~/.config/qhos2/coletor.token"
    assert co._sem_usuario(Path("/tmp/x.token")) == str(Path("/tmp/x.token").resolve())


def test_coletor_nao_toca_na_sessao():
    arvore = ast.parse(Path(co.__file__).read_text(encoding="utf-8"))
    importados = {n.module for n in ast.walk(arvore) if isinstance(n, ast.ImportFrom)}
    importados |= {a.name for n in ast.walk(arvore) if isinstance(n, ast.Import) for a in n.names}
    assert not importados & {"qhos2.sessao", "qhos2.conector", "qhos2.captura", "qhos2.gravador", "websockets.asyncio.client"}
    assert "qhos2.leitura" in importados      # o dado vem so da leitura do arquivo gravado


# ---- tarefa 5: dados da captura para o terminal -------------------------------------------------------

from qhos2 import eventos as ev  # noqa: E402
from qhos2 import leitura as le  # noqa: E402
from qhos2 import livro as lv  # noqa: E402
from qhos2 import sintetico as si  # noqa: E402
from qhos2.gravador import Gravador  # noqa: E402

BASE = {"subscriptionId": "s", "revision": "1", "instrumentId": "rithmic:CME:ESZ6"}
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"}


def leitor_sintetico(pasta):
    return le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2")


def negocio(**extra):
    campos = {"indice": 7, "posicao": 0, "recebido_ns": 2_000_000_123, "tipo": ev.NEGOCIO, "lado": ev.COMPRA,
              "preco": 27401, "quantidade": 3, "carimbo_ns": 1_999_925_000_456}
    return ev.Evento(**{**campos, **extra})


def test_negocio_traduzido_sem_adivinhar_o_lado():
    m = co.mensagem_negocio(negocio(), BASE, 0.25)
    assert m == {"type": "trade", **BASE, "time": 1999925, "timeNs": "1999925000456", "id": "7-0",
                 "price": 6850.25, "size": 3, "side": "buy"}
    assert co.mensagem_negocio(negocio(lado=ev.VENDA), BASE, 0.25)["side"] == "sell"
    assert co.mensagem_negocio(negocio(lado=None), BASE, 0.25)["side"] == "unknown"
    assert co.mensagem_negocio(negocio(carimbo_ns=None), BASE, 0.25)["time"] == 2000
    assert co.mensagem_negocio(negocio(retrato=True), BASE, 0.25) is None
    assert co.mensagem_negocio(negocio(quantidade=0), BASE, 0.25) is None
    assert co.mensagem_negocio(negocio(tipo=ev.NOVO), BASE, 0.25) is None


def test_livro_so_vai_com_ready_quando_vale(tmp_path):
    livro = lv.livro_da_captura(si.gravar(si.gerar(4, 60), tmp_path))
    m = co.mensagem_livro(livro, BASE, 5_000_000_000, True, 0.25)
    assert m["ready"] is True and m["sequenceVerified"] is False and (m["time"], m["timeNs"]) == (5000, "5000000000")
    assert m["bids"] == [[p * 0.25, q] for p, q, _ in livro.niveis(ev.COMPRA, 40)]
    assert m["asks"] == [[p * 0.25, q] for p, q, _ in livro.niveis(ev.VENDA, 40)]
    assert m["bids"][0][0] < m["asks"][0][0] and len(m["bids"]) <= 40
    vazio = co.mensagem_livro(livro, BASE, 5_000_000_000, False, 0.25)
    assert (vazio["ready"], vazio["bids"], vazio["asks"]) == (False, [], [])


def test_cotacao_so_com_o_que_existe(tmp_path):
    livro = lv.livro_da_captura(si.gravar(si.gerar(4, 60), tmp_path))
    assert co.mensagem_cotacao(livro, None, BASE, 1_000_000, False, 0.25) is None
    sem_livro = co.mensagem_cotacao(livro, 6850.5, BASE, 1_000_000, False, 0.25)
    assert sem_livro["price"] == 6850.5 and "bid" not in sem_livro and "ask" not in sem_livro
    completa = co.mensagem_cotacao(livro, None, BASE, 1_000_000, True, 0.25)
    assert completa["bid"] == livro.melhor(ev.COMPRA) * 0.25 and completa["ask"] == livro.melhor(ev.VENDA) * 0.25
    assert "price" not in completa


def test_negocios_antigos_nao_vao_enquanto_a_leitura_alcanca(tmp_path):
    pasta = si.gravar(si.gerar(5, 200), tmp_path)
    coletor = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(pasta))
    quadros = coletor.leitor.avancar()
    assert coletor.traduzir(quadros, alcancado=False) == []
    retrato = coletor.retrato_atual()                # o estado avancou mesmo sem enviar: livro e cotacao com ultimo preco
    assert [m["type"] for m in retrato] == ["book", "ticker"] and retrato[1]["price"] > 0
    coletor2 = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(pasta))
    enviados = coletor2.traduzir(coletor2.leitor.avancar(), alcancado=True)
    esperados = [e for e in ev.eventos_da_captura(pasta) if e.tipo == ev.NEGOCIO and not e.retrato]
    assert [m["id"] for m in enviados] == [f"{e.indice}-{e.posicao}" for e in esperados] and esperados
    assert len({m["id"] for m in enviados}) == len(enviados)


async def receber_ate(ws, condicao, limite=20000):
    recebidas = []
    for _ in range(limite):
        recebidas.append(json.loads(await ws.recv()))
        if condicao(recebidas[-1]):
            return recebidas
    raise AssertionError("condicao nao atingida")


def test_terminal_recebe_o_livro_igual_ao_replay_enquanto_a_captura_grava(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "LIVRO_A_CADA_S", 0.01)
    monkeypatch.setattr(co, "LER_A_CADA_S", 0.005)
    cenario = si.gerar(11, 300)
    gravador = Gravador(tmp_path / "capturas", identidade=IDENTIDADE, captura_id="viva")

    async def cenario_vivo():
        async with no_ar(leitor=leitor_sintetico(gravador.pasta)) as (coletor, url, _):
            ws, welcome = await entrar(url)
            for i, (template, dados) in enumerate(cenario.quadros):
                gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
                if i % 50 == 0:
                    await asyncio.sleep(0.02)
            gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
            final = lv.livro_da_captura(gravador.pasta)
            esperado = [[p * 0.25, q] for p, q, _ in final.niveis(ev.COMPRA, 40)]
            recebidas = await receber_ate(ws, lambda m: m["type"] == "book" and m["bids"] == esperado)
            return coletor, welcome, recebidas, final
    coletor, welcome, recebidas, final = rodar(cenario_vivo())
    livros = [m for m in recebidas if m["type"] == "book"]
    assert livros[-1]["asks"] == [[p * 0.25, q] for p, q, _ in final.niveis(ev.VENDA, 40)] and livros[-1]["ready"]
    assert coletor.leitor.estado == le.ENCERRADA
    sub = welcome["selection"]["subscriptionId"]
    assert all(m["subscriptionId"] == sub and m["revision"] == "1" and m["instrumentId"] == "rithmic:CME:ESZ6"
               for m in recebidas if m["type"] in ("book", "trade", "ticker"))
    negocios = [m for m in recebidas if m["type"] == "trade"]
    assert negocios and len({m["id"] for m in negocios}) == len(negocios)
    assert all(m["price"] > 0 and m["size"] > 0 and m["side"] in ("buy", "sell") for m in negocios)


def test_quem_conecta_depois_recebe_o_livro_atual_na_hora(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "LIVRO_A_CADA_S", 3600)       # so o primeiro envio periodico; o resto viria em 1 h
    pasta = si.gravar(si.gerar(6, 80), tmp_path)

    async def cenario():
        async with no_ar(leitor=leitor_sintetico(pasta)) as (coletor, url, _):
            encerrou, traduzir = asyncio.Event(), coletor.traduzir

            def traduzir_avisando(quadros, alcancado):          # no loop, logo apos cada leitura: sinal, nao sondagem
                saida = traduzir(quadros, alcancado)
                if coletor.leitor.estado == le.ENCERRADA:
                    encerrou.set()
                return saida
            coletor.traduzir = traduzir_avisando
            await encerrou.wait()                                # a captura inteira foi lida e traduzida (QHOS-AUD-087)
            ws, _w = await entrar(url)
            return json.loads(await ws.recv())
    primeira = rodar(cenario())
    final = lv.livro_da_captura(pasta)
    assert primeira["type"] == "book" and primeira["ready"]
    assert primeira["bids"] == [[p * 0.25, q] for p, q, _ in final.niveis(ev.COMPRA, 40)]


def test_leitura_que_falha_manda_livro_vazio_e_lacuna(tmp_path):
    pasta = si.gravar(si.gerar(2, 30), tmp_path)
    arquivo = pasta / "market_frames.jsonl"
    linhas = arquivo.read_bytes().splitlines(keepends=True)
    arquivo.write_bytes(b"".join(linhas[:5] + linhas[6:]))       # buraco no indice

    async def cenario():
        async with no_ar(leitor=leitor_sintetico(pasta)) as (coletor, url, avisos):
            ws, _w = await entrar(url)
            recebidas = await receber_ate(ws, lambda m: m["type"] == "gap")
            return coletor, avisos, recebidas
    coletor, avisos, recebidas = rodar(cenario())
    livro = [m for m in recebidas if m["type"] == "book"][-1]
    assert (livro["ready"], livro["bids"], livro["asks"]) == (False, [], [])
    assert coletor.leitor.codigo == "indice_com_buracos" and any("indice_com_buracos" in a for a in avisos)


# ---- tarefa 6: conferencia contra o replay ------------------------------------------------------------

def conferir(pasta):
    return co.conferir_captura(pasta, simbolo="SINTETICO", bolsa="QHOS2")


def test_conferencia_igual_ao_replay(tmp_path):
    r = conferir(si.gravar(si.gerar(9, 500), tmp_path))
    assert r["ok"] and r["livro_igual"] and r["livro_completo_igual"] and r["negocios_iguais"] and r["ids_unicos"]
    assert r["negocios_enviados"] == r["negocios_gravados"] > 0 and r["fora_do_formato"] == {}


def test_conferencia_com_150_so_de_volume_como_na_captura_real(tmp_path):
    from test_eventos import SO_VOLUME
    cenario = si.gerar(9, 500)
    quadros = [q for i, quadro in enumerate(cenario.quadros) for q in ([quadro, (150, SO_VOLUME)] if i % 5 == 2 else [quadro])]
    r = conferir(si.gravar(si.Cenario(cenario.semente, cenario.tick, quadros), tmp_path))
    assert r["ok"] and r["livro_completo_igual"] and r["negocios_iguais"] and r["fora_do_formato"] == {}
    assert r["negocios_enviados"] == r["negocios_gravados"] == conferir(si.gravar(cenario, tmp_path / "b"))["negocios_gravados"] > 0


def test_conferencia_acusa_negocio_que_some(tmp_path, monkeypatch):
    original = co.mensagem_negocio
    vistos = []

    def perde_o_decimo(evento, base, tick):
        mensagem = original(evento, base, tick)
        if mensagem is not None:
            vistos.append(mensagem["id"])
            if len(vistos) == 10:
                return None
        return mensagem
    monkeypatch.setattr(co, "mensagem_negocio", perde_o_decimo)
    r = conferir(si.gravar(si.gerar(9, 500), tmp_path))
    assert not r["ok"] and not r["negocios_iguais"] and r["negocios_enviados"] == r["negocios_gravados"] - 1


def test_conferencia_acusa_livro_diferente(tmp_path, monkeypatch):
    original = co.mensagem_livro

    def sem_o_ultimo_nivel(livro, base, tempo_ns, vale, tick):
        mensagem = original(livro, base, tempo_ns, vale, tick)
        return {**mensagem, "bids": mensagem["bids"][:-1]}
    monkeypatch.setattr(co, "mensagem_livro", sem_o_ultimo_nivel)
    r = conferir(si.gravar(si.gerar(9, 500), tmp_path))
    assert not r["ok"] and not r["livro_igual"] and r["livro_completo_igual"]


def test_conferencia_de_captura_sem_manifesto_ou_estragada(tmp_path):
    pasta = si.gravar(si.gerar(2, 30), tmp_path / "a")
    (pasta / "manifest.json").unlink()
    assert conferir(pasta) == {"ok": False, "codigo": "captura_sem_manifesto"}
    assert conferir(tmp_path / "nao_existe") == {"ok": False, "codigo": "captura_sem_manifesto"}
    pasta = si.gravar(si.gerar(2, 30), tmp_path / "b")
    arquivo = pasta / "market_frames.jsonl"
    linhas = arquivo.read_bytes().splitlines(keepends=True)
    arquivo.write_bytes(b"".join(linhas[:5] + linhas[6:]))
    assert conferir(pasta) == {"ok": False, "codigo": "indice_com_buracos"}


@pytest.mark.parametrize("mudanca, motivo", [
    ({"bids": [[6850.0, 1]], "asks": [[6850.0, 2]]}, "cruzado"),
    ({"bids": [[6849.75, 1], [6850.0, 1]]}, "ordem_dos_niveis"),
    ({"bids": [[6850.1, 1]]}, "nivel"),
    ({"bids": [[6850.0, 0]]}, "nivel"),
    ({"bids": []}, "niveis"),
    ({"timeNs": "999"}, "horario"),
    ({"ready": None}, "ready"),
])
def test_formato_do_livro_igual_ao_da_ponte(mudanca, motivo):
    livro = {"type": "book", "time": 1, "timeNs": "1000000", "ready": True, "bids": [[6850.0, 3]], "asks": [[6850.25, 2]]}
    assert co.fora_do_formato(livro, 0.25) is None
    assert co.fora_do_formato({**livro, **mudanca}, 0.25) == motivo
    assert co.fora_do_formato({**livro, "ready": False, "bids": [], "asks": []}, 0.25) is None


@pytest.mark.parametrize("mudanca, motivo", [({"id": "-1"}, "id"), ({"price": 6850.1}, "preco_ou_quantidade"),
                                             ({"size": 0}, "preco_ou_quantidade"), ({"side": "maybe"}, "lado")])
def test_formato_do_negocio_igual_ao_da_ponte(mudanca, motivo):
    negocio_ok = {"type": "trade", "time": 1, "timeNs": "1000000", "id": "12-0", "price": 6850.25, "size": 1,
                  "side": "unknown"}
    assert co.fora_do_formato(negocio_ok, 0.25) is None
    assert co.fora_do_formato({**negocio_ok, **mudanca}, 0.25) == motivo


def test_linha_de_comando_da_conferencia_so_mostra_contagens(tmp_path, capsys):
    gravador = Gravador(tmp_path, identidade={**IDENTIDADE, "requested_symbol": "ESZ6", "exchange": "CME"})
    for i, (template, dados) in enumerate(si.gerar(3, 200).quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    assert co.main(["--conferir", "--captura", str(gravador.pasta)]) == 0
    saida = capsys.readouterr().out
    assert "IGUAL ao replay" in saida and "6850" not in saida and "68" + "51" not in saida
    assert co.main(["--conferir", "--em-andamento"]) == 2


# ---- tarefa 8: o coletor serve a pagina do terminal -----------------------------------------------------

import hashlib  # noqa: E402
import http.client  # noqa: E402

PAGINA_TESTE = b"<!doctype html><title>terminal</title>"


def pedir_http(porta, caminho, host=None):
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=5)
    conexao.request("GET", caminho, headers={"Host": host or f"127.0.0.1:{porta}"})
    resposta = conexao.getresponse()
    return resposta.status, dict(resposta.getheaders()), resposta.read()


def test_pagina_do_terminal_servida_com_protecoes():
    async def cenario():
        async with no_ar(pagina=PAGINA_TESTE) as (coletor, url, _):
            porta = coletor.porta
            return [await asyncio.to_thread(pedir_http, porta, c) for c in ("/", "/index.html", "/modulos/app.js")] + [
                await asyncio.to_thread(pedir_http, porta, "/", f"atacante.exemplo:{porta}")], coletor.contagem
    (raiz, indice, outro, host_errado), contagem = rodar(cenario())
    assert raiz[0] == 200 and raiz[2] == PAGINA_TESTE and indice[2] == PAGINA_TESTE
    assert raiz[1]["Content-Type"] == "text/html; charset=utf-8" and raiz[1]["Cache-Control"] == "no-store"
    assert raiz[1]["X-Frame-Options"] == "DENY" and raiz[1]["X-Content-Type-Options"] == "nosniff"
    assert raiz[1]["Referrer-Policy"] == "no-referrer" and "Server" not in raiz[1]
    assert outro[0] == 404 and host_errado[0] == 403
    assert (contagem["paginas"], contagem["nao_encontrado"], contagem["recusado_host"]) == (2, 1, 1)


# ---- tarefa 12: conexao automatica da pagina ----------------------------------------------------------------

DA_PAGINA = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}


def pedir_conexao(porta, cabecalhos, host=None, caminho="/conexao"):
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=5)
    conexao.request("GET", caminho, headers={"Host": host or f"127.0.0.1:{porta}", **cabecalhos})
    resposta = conexao.getresponse()
    return resposta.status, dict(resposta.getheaders()), resposta.read()


def test_conexao_automatica_so_para_o_fetch_da_propria_pagina():
    async def cenario():
        async with no_ar(pagina=PAGINA_TESTE) as (coletor, url, avisos):
            p = coletor.porta
            ok = await asyncio.to_thread(pedir_conexao, p, DA_PAGINA)
            ok_same_origin = await asyncio.to_thread(pedir_conexao, p, {**DA_PAGINA, "Sec-Fetch-Mode": "same-origin"})
            ok_com_origin = await asyncio.to_thread(pedir_conexao, p, {**DA_PAGINA, "Origin": coletor.origem})
            ws, welcome = await entrar(url, token=json.loads(ok[2])["token"])      # o token entregue abre a sessao
            await ws.close()
            recusas = {}
            for nome, cab in {
                "sem_cabecalhos": {},
                "barra_de_enderecos": {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"},
                "outro_site": {**DA_PAGINA, "Sec-Fetch-Site": "cross-site"},
                "mesmo_site_outra_porta": {**DA_PAGINA, "Sec-Fetch-Site": "same-site"},
                "navegacao": {**DA_PAGINA, "Sec-Fetch-Mode": "navigate"},
                "no_cors": {**DA_PAGINA, "Sec-Fetch-Mode": "no-cors"},
                "como_script": {**DA_PAGINA, "Sec-Fetch-Dest": "script"},
                "como_iframe": {**DA_PAGINA, "Sec-Fetch-Dest": "iframe"},
                "como_imagem": {**DA_PAGINA, "Sec-Fetch-Dest": "image"},
                "so_site": {"Sec-Fetch-Site": "same-origin"},
                "so_site_none": {**DA_PAGINA, "Sec-Fetch-Site": "none"},        # sozinho: so a regra do Site pega
                "origin_de_outro_site": {**DA_PAGINA, "Origin": "http://atacante.exemplo"},
            }.items():
                recusas[nome] = await asyncio.to_thread(pedir_conexao, p, cab)
            host_errado = await asyncio.to_thread(pedir_conexao, p, DA_PAGINA, f"atacante.exemplo:{p}")
            inexistente = await asyncio.to_thread(pedir_http, p, "/nao_existe")
            return (coletor, p, ok, ok_same_origin, ok_com_origin, welcome, recusas, host_errado, inexistente,
                    "\n".join(avisos))
    coletor, p, ok, ok_so, ok_origin, welcome, recusas, host_errado, inexistente, avisos = rodar(cenario())
    corpo = json.loads(ok[2])
    assert ok[0] == 200 and corpo == {"url": f"ws://127.0.0.1:{p}", "token": TOKEN} and welcome["type"] == "welcome"
    assert ok_so[0] == 200 and ok_origin[0] == 200
    assert ok[1]["Content-Type"] == "application/json; charset=utf-8" and ok[1]["Cache-Control"] == "no-store"
    assert ok[1]["X-Content-Type-Options"] == "nosniff" and ok[1]["Cross-Origin-Resource-Policy"] == "same-origin"
    assert ok[1]["Referrer-Policy"] == "no-referrer" and ok[1]["X-Frame-Options"] == "DENY"
    assert not [k for k in ok[1] if k.lower().startswith("access-control") or k.lower() == "server"]   # sem CORS
    for nome, resposta in recusas.items():
        assert resposta[0] == 404 and resposta[2] == inexistente[2] == b"nao encontrado\n", nome  # igual a qualquer 404
        assert TOKEN.encode() not in resposta[2] and not [k for k in resposta[1] if k.lower().startswith("access-control")]
    assert host_errado[0] == 403 and TOKEN not in host_errado[2].decode()
    assert (coletor.contagem["conexoes_entregues"], coletor.contagem["recusado_conexao"]) == (3, len(recusas))
    assert TOKEN not in avisos                                   # nunca impresso


def test_conexao_automatica_nao_existe_sem_pagina_e_o_token_nao_vai_na_pagina():
    async def cenario():
        async with no_ar() as (coletor, url, _):
            sem_pagina = await asyncio.to_thread(pedir_conexao, coletor.porta, DA_PAGINA)
        async with no_ar(pagina=PAGINA_TESTE) as (coletor, url, _):
            raiz = await asyncio.to_thread(pedir_http, coletor.porta, "/")
            return sem_pagina, raiz
    sem_pagina, raiz = rodar(cenario())
    assert sem_pagina[0] == 404 and TOKEN.encode() not in sem_pagina[2]
    assert TOKEN.encode() not in raiz[2] and all(TOKEN not in v for v in raiz[1].values())


def test_sem_pagina_configurada_nada_e_servido():
    async def cenario():
        async with no_ar() as (coletor, url, _):
            return await asyncio.to_thread(pedir_http, coletor.porta, "/")
    assert rodar(cenario())[0] == 404


def test_por_padrao_so_a_pagina_do_proprio_coletor_conecta():
    async def cenario():
        coletor = co.Coletor(token=TOKEN, porta=0, avisar=lambda _: None)
        pronto = asyncio.Event()
        tarefa = asyncio.create_task(coletor.rodar(pronto))
        await pronto.wait()
        try:
            url = f"ws://127.0.0.1:{coletor.porta}"
            ws, welcome = await entrar(url, origem=f"http://127.0.0.1:{coletor.porta}")
            with pytest.raises(InvalidStatus):
                await connect(url, origin="http://127.0.0.1:8010")       # a pagina do V1 nao conecta mais
            return coletor.origem, welcome["type"]
        finally:
            tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa
    origem, tipo = rodar(cenario())
    assert origem.startswith("http://127.0.0.1:") and tipo == "welcome"


def test_pagina_padrao_e_o_terminal_do_repositorio():
    assert co.PAGINA.name == "index.html" and co.PAGINA.parent.name == "terminal"
    assert b'<script type="importmap">' in co.PAGINA.read_bytes()


def test_linha_de_comando_mostra_o_endereco_e_o_hash_da_pagina(tmp_path, capsys):
    ocupada = socket.socket()
    ocupada.bind(("127.0.0.1", 0))
    ocupada.listen()
    porta = ocupada.getsockname()[1]
    try:
        assert co.main(["--porta", str(porta), "--token", str(tmp_path / "t"), "--captura", str(tmp_path / "capturas" / "c"),
                        "--raiz", str(tmp_path)]) == 2
    finally:
        ocupada.close()
    saida = capsys.readouterr().out
    resumo = hashlib.sha256(co.PAGINA.read_bytes()).hexdigest()[:16]
    assert f"terminal em http://127.0.0.1:{porta}/ (index.html {resumo})" in saida
    assert f"aceitando so a pagina http://127.0.0.1:{porta};" in saida


# ---- tarefa 9: percurso, uma bolinha por camada ------------------------------------------------------------

CAMADAS = ["rithmic", "sessao", "gravacao", "livro", "leitura", "coletor"]


def conferir_formato(mensagem, sessao):
    assert mensagem["type"] == "pipeline" and mensagem["sessionId"] == sessao and isinstance(mensagem["time"], int)
    assert [c["id"] for c in mensagem["stages"]] == CAMADAS
    for camada in mensagem["stages"]:
        assert camada["state"] in ("ok", "atencao", "falha", "desconhecido") and isinstance(camada["reason"], str)
        assert isinstance(camada["since"], int) and camada["since"] <= mensagem["time"] and len(camada["fields"]) <= 12
        assert all(isinstance(f["label"], str) and isinstance(f["value"], str) and len(f["label"]) <= 40
                   and len(f["value"]) <= 80 for f in camada["fields"])


def estados(mensagem):
    return {c["id"]: c["state"] for c in mensagem["stages"]}


def coletor_lendo(pasta, **extra):
    coletor = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(pasta), avisar=lambda _: None, **extra)
    coletor.traduzir(coletor.leitor.avancar(), alcancado=True)
    return coletor


def test_sem_captura_nada_fica_verde_por_falta_de_informacao(tmp_path):
    sem_leitor = co.Coletor(token=TOKEN, porta=0)
    m = sem_leitor.percurso()
    conferir_formato(m, sem_leitor.sessao)
    assert estados(m) == {**{c: "desconhecido" for c in CAMADAS[:-1]}, "coletor": "ok"}
    aguardando = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(tmp_path / "ainda_nao"))
    assert estados(aguardando.percurso()) == estados(m)


def test_captura_sendo_gravada_tudo_verde_e_atraso_medido(tmp_path):
    gravador = Gravador(tmp_path, identidade=IDENTIDADE, captura_id="viva")
    for i, (template, dados) in enumerate(si.gerar(5, 100).quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    mtime = (gravador.pasta / "market_frames.jsonl").stat().st_mtime
    coletor = coletor_lendo(gravador.pasta, relogio=lambda: mtime + 1)   # 1 s depois do ultimo quadro, sem relogio real
    m = coletor.percurso()
    conferir_formato(m, coletor.sessao)
    assert estados(m) == {c: "ok" for c in CAMADAS}
    rithmic = m["stages"][0]["fields"]
    assert {"label": "Atraso do último quadro", "value": "75 ms"} in rithmic
    texto = json.dumps(m)
    assert "6850" not in texto and "27400" not in texto              # so contagens e distancias, nenhum preco


@pytest.mark.parametrize("idade, esperado", [(2, "ok"), (20, "atencao"), (600, "desconhecido")])
def test_rithmic_pelo_ritmo_do_arquivo(tmp_path, idade, esperado):
    gravador = Gravador(tmp_path, identidade=IDENTIDADE, captura_id="viva")
    for i, (template, dados) in enumerate(si.gerar(5, 30).quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    agora = (gravador.pasta / "market_frames.jsonl").stat().st_mtime + idade
    coletor = coletor_lendo(gravador.pasta, relogio=lambda: agora)
    m = estados(coletor.percurso())
    assert m["rithmic"] == esperado
    assert m["gravacao"] == ("ok" if idade <= 60 else "atencao")


def test_captura_encerrada_e_conferida(tmp_path):
    coletor = coletor_lendo(si.gravar(si.gerar(5, 30), tmp_path))
    coletor.leitor.avancar()
    m = coletor.percurso()
    assert coletor.leitor.estado == le.ENCERRADA
    assert estados(m) == {"rithmic": "desconhecido", "sessao": "ok", "gravacao": "ok", "livro": "ok", "leitura": "ok",
                          "coletor": "ok"}
    assert "encerrada" in m["stages"][0]["reason"] and "SHA-256" in m["stages"][2]["reason"]


def test_leitura_que_falha_fica_vermelha_e_o_livro_nao_vale(tmp_path):
    pasta = si.gravar(si.gerar(2, 30), tmp_path)
    arquivo = pasta / "market_frames.jsonl"
    linhas = arquivo.read_bytes().splitlines(keepends=True)
    arquivo.write_bytes(b"".join(linhas[:5] + linhas[6:]))
    coletor = coletor_lendo(pasta)
    m = coletor.percurso()
    assert estados(m) == {"rithmic": "desconhecido", "sessao": "desconhecido", "gravacao": "falha",
                          "livro": "desconhecido", "leitura": "falha", "coletor": "ok"}
    assert "indice_com_buracos" in m["stages"][4]["reason"]


def test_leitura_alcancando_fica_amarela(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "ALCANCADO_BYTES", 1)
    gravador = Gravador(tmp_path, identidade=IDENTIDADE, captura_id="viva")
    for i, (template, dados) in enumerate(si.gerar(5, 100).quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    coletor = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(gravador.pasta))
    coletor.traduzir(coletor.leitor.avancar(2000), alcancado=False)
    m = coletor.percurso()
    assert estados(m)["leitura"] == "atencao" and "Alcançando" in m["stages"][4]["reason"]


def test_since_so_muda_quando_o_estado_muda(tmp_path):
    relogio = [1000.0]
    gravador = Gravador(tmp_path, identidade=IDENTIDADE, captura_id="viva")
    for i, (template, dados) in enumerate(si.gerar(5, 30).quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    mtime = (gravador.pasta / "market_frames.jsonl").stat().st_mtime
    relogio[0] = mtime + 1
    coletor = coletor_lendo(gravador.pasta, relogio=lambda: relogio[0])
    primeiro = coletor.percurso()["stages"][0]
    relogio[0] = mtime + 2
    assert coletor.percurso()["stages"][0]["since"] == primeiro["since"]          # continua ok: mesmo since
    relogio[0] = mtime + 30
    terceiro = coletor.percurso()["stages"][0]
    assert terceiro["state"] == "atencao" and terceiro["since"] == int((mtime + 30) * 1000)


def test_terminal_recebe_o_percurso_com_o_welcome_e_a_cada_segundo(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "PERCURSO_A_CADA_S", 0.05)
    pasta = si.gravar(si.gerar(5, 30), tmp_path)

    async def cenario():
        async with no_ar(leitor=leitor_sintetico(pasta)) as (coletor, url, _):
            ws, welcome = await entrar(url)
            vistos = []
            recebidas = await receber_ate(ws, lambda m: m["type"] == "pipeline" and (vistos.append(m) or len(vistos) >= 3))
            return coletor, welcome, recebidas
    coletor, welcome, recebidas = rodar(cenario())
    assert "pipeline" in welcome["capabilities"]
    percursos = [m for m in recebidas if m["type"] == "pipeline"]
    assert len(percursos) == 3 and percursos[-1]["stages"][4]["reason"] == "Leu a captura inteira"
    for m in percursos:
        conferir_formato(m, welcome["sessionId"])


def test_livro_no_retrato_fica_amarelo_e_com_anomalia_fica_vermelho(tmp_path):
    from qhos2 import mercado as m
    cenario = si.gerar(5, 30)
    retrato = [(t, d) for t, d in cenario.quadros if t == 116][:-1]          # sem o fim do retrato
    gravador = Gravador(tmp_path / "a", identidade=IDENTIDADE, captura_id="no_retrato")
    for i, (template, dados) in enumerate(retrato):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    no_retrato = estados(coletor_lendo(gravador.pasta).percurso())
    assert (no_retrato["sessao"], no_retrato["livro"]) == ("atencao", "atencao")
    gravador = Gravador(tmp_path / "b", identidade=IDENTIDADE, captura_id="anomalia")
    quadros = cenario.quadros + [(160, si.quadro_livro([(m.REMOCAO, "C", 6850.0, 0, 1, "ordem-que-nao-existe")],
                                                         si.INICIO_NS))]
    for i, (template, dados) in enumerate(quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    coletor = coletor_lendo(gravador.pasta)
    m_anomalia = coletor.percurso()
    assert coletor.leitor.livro.suspeito and estados(m_anomalia)["livro"] == "falha"
    assert "Anomalia" in m_anomalia["stages"][3]["reason"]


# ---- tarefa 10: historico de candles --------------------------------------------------------------------

from qhos2 import historico as hi  # noqa: E402

ES = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}


def gravar_es(raiz, nome, cenario, fechar=True):
    gravador = Gravador(raiz, identidade=ES, captura_id=nome)
    for i, (template, dados) in enumerate(cenario.quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    if fechar:
        gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    return gravador.pasta


def test_sem_historico_o_contrato_diz_que_nao_tem():
    coletor = co.Coletor(token=TOKEN, porta=0)
    assert "history" not in coletor.welcome()["capabilities"]
    assert coletor.instrumento["capabilities"]["history"] is False
    r = coletor.responder({"type": "request", "id": "q1", "method": "history.candles",
                           "params": {"instrumentId": "rithmic:CME:ESZ6", "timeframe": "1m", "limit": 240}})
    assert r["error"] == {"code": "HISTORY_UNAVAILABLE"}


def test_historico_das_capturas_encerradas_mais_a_seguida(tmp_path, monkeypatch):
    raiz = tmp_path / "capturas"
    gravar_es(raiz, "ontem", si.gerar(4, 400))
    # "Ontem" e "hoje" precisam ter tempos diferentes: os dois geradores usavam
    # o mesmo minuto e o teste anterior aceitava somar capturas sobrepostas.
    with monkeypatch.context() as patch:
        patch.setattr(si, "INICIO_NS", si.INICIO_NS + 86400 * 10 ** 9)
        hoje = gravar_es(raiz, "hoje", si.gerar(5, 400), fechar=False)
    historico = hi.Historico(raiz, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    coletor = co.Coletor(token=TOKEN, porta=0, historico=historico,
                         leitor=le.Leitor(hoje, tick=0.25, simbolo="ESZ6", bolsa="CME"))
    assert "history" in coletor.welcome()["capabilities"] and coletor.instrumento["capabilities"]["history"]
    assert coletor.welcome()["selection"]["instruments"][0]["capabilities"]["history"] is True
    historico.preparar(ignorar="hoje")
    coletor.traduzir(coletor.leitor.avancar(), alcancado=False)      # alcancando: negocio nao vai, mas entra no historico
    pedido = {"type": "request", "id": "q1", "method": "history.candles",
              "params": {"instrumentId": "rithmic:CME:ESZ6", "timeframe": "1m", "limit": 240}}
    r = coletor.responder(pedido)
    assert r["ok"] and r["result"]["instrumentId"] == "rithmic:CME:ESZ6" and r["result"]["timeframe"] == "1m"
    esperado = sum(e.quantidade for pasta in (raiz / "ontem", hoje) for e in
                   (ev.eventos_da_captura(pasta) if (pasta / "manifest.json").exists()
                    else [x for q in le.Leitor(hoje, tick=0.25, simbolo="ESZ6", bolsa="CME").avancar() for x in q[2]])
                   if e.tipo == ev.NEGOCIO and not e.retrato)
    assert sum(c["volume"] for c in r["result"]["candles"]) == esperado > 0
    for errado in ({"timeframe": "2m"}, {"instrumentId": "rithmic:CME:ESH7"}, {"limit": 0}):
        r = coletor.responder({**pedido, "params": {**pedido["params"], **errado}})
        assert r["error"] == {"code": "HISTORY_UNAVAILABLE"}


def test_coletor_prepara_o_historico_em_segundo_plano_e_avisa(tmp_path):
    raiz = tmp_path / "capturas"
    gravar_es(raiz, "ontem", si.gerar(4, 100))
    historico = hi.Historico(raiz, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")

    async def cenario():
        pronto = asyncio.Event()
        # o aviso emitido quando o historico ja tem capturas prontas e o sinal (QHOS-AUD-087); o que se confere e o
        # estado do historico, nao o texto do aviso (QHOS-AUD-069)
        async with no_ar(historico=historico, ao_avisar=lambda _: pronto.set() if historico.prontas else None) as (
                coletor, url, avisos):
            await pronto.wait()
            ws, welcome = await entrar(url)
            await ws.send(json.dumps({"type": "request", "id": "h1", "method": "history.candles",
                                      "params": {"instrumentId": "rithmic:CME:ESZ6", "timeframe": "5m", "limit": 240}}))
            resposta = await receber_ate(ws, lambda m: m["type"] == "response")
            return avisos, welcome, resposta[-1]
    avisos, welcome, resposta = rodar(cenario())
    assert set(historico.prontas) == {"ontem"} and historico.recusadas == {}
    assert "history" in welcome["capabilities"] and resposta["ok"] and resposta["result"]["candles"]
    assert (tmp_path / "cache" / "ontem.json").is_file()
