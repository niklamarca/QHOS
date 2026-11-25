"""QHOS-AUD-032: lado do coletor no perimetro que o Brain observa. `POST /conexao` (ou qualquer metodo que nao seja GET)
derruba a conexao sem resposta HTTP, porque a camada `websockets` recusa o metodo antes de `process_request`.
Esperado: uma resposta 405 (ou o 404 de sempre), sem token. A correcao mora em `src/qhos2/coletor.py`, congelado em J0:
o teste fica em xfail estrito e o diff proposto em EVIDENCIAS/QHOS-AUD-032/patch_proposto.diff."""
import asyncio
import http.client

import pytest

pytest.importorskip("websockets")

from qhos2.coletor import PAGINA, Coletor  # noqa: E402

TOKEN = "A" * 43
CABECALHOS_PAGINA = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"}


def _pedir(porta, metodo, caminho, cabecalhos):
    con = http.client.HTTPConnection("127.0.0.1", porta, timeout=5)
    con.putrequest(metodo, caminho, skip_host=True, skip_accept_encoding=True)
    con.putheader("Host", f"127.0.0.1:{porta}")
    for nome, valor in cabecalhos.items():
        con.putheader(nome, valor)
    con.endheaders()
    resposta = con.getresponse()
    corpo = resposta.read()
    con.close()
    return resposta.status, dict(resposta.getheaders()), corpo


def _no_coletor(cenario):
    async def rodar():
        coletor = Coletor(token=TOKEN, porta=0, pagina=PAGINA.read_bytes(), avisar=lambda _: None)
        pronto = asyncio.Event()
        tarefa = asyncio.create_task(coletor.rodar(pronto))
        try:
            await asyncio.wait_for(pronto.wait(), 5)
            await cenario(coletor)
        finally:
            tarefa.cancel()
            await asyncio.gather(tarefa, return_exceptions=True)
    asyncio.run(asyncio.wait_for(rodar(), 30))


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-032: a camada websockets fecha a conexao sem resposta para metodo != GET; "
                                       "correcao em src/qhos2/coletor.py (BLOQUEADO_CONGELAMENTO)")
@pytest.mark.parametrize("metodo", ["POST", "PUT", "DELETE"])
def test_metodo_diferente_de_get_em_conexao_recebe_resposta_http_sem_token(metodo):
    async def cenario(coletor):
        status, cabecalhos, corpo = await asyncio.to_thread(_pedir, coletor.porta, metodo, "/conexao", CABECALHOS_PAGINA)
        assert status in (404, 405), status
        assert TOKEN.encode() not in corpo and "Access-Control-Allow-Origin" not in cabecalhos
        assert coletor.contagem["conexoes_entregues"] == 0
    _no_coletor(cenario)


def test_get_fragmentado_em_pedacos_menores_que_4_bytes_continua_atendido():
    """Guarda da correcao proposta: decidir o metodo pelos 4 primeiros bytes nao pode recusar um GET cujo primeiro
    pedaco TCP chegue com menos de 4 bytes (ex.: "GE" + "T /conexao ...")."""
    import socket
    import time

    def pedir_fragmentado(porta):
        pedido = (f"GET /conexao HTTP/1.1\r\nHost: 127.0.0.1:{porta}\r\n"
                  + "".join(f"{k}: {v}\r\n" for k, v in CABECALHOS_PAGINA.items()) + "Connection: close\r\n\r\n").encode()
        with socket.create_connection(("127.0.0.1", porta), timeout=5) as s:
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            for pedaco in (pedido[:1], pedido[1:2], pedido[2:]):
                s.sendall(pedaco)
                time.sleep(0.05)
            resposta = b""
            while True:
                parte = s.recv(65536)
                if not parte:
                    break
                resposta += parte
                if b"\r\n\r\n" in resposta and TOKEN.encode() in resposta:
                    break
        return resposta

    async def cenario(coletor):
        resposta = await asyncio.to_thread(pedir_fragmentado, coletor.porta)
        assert resposta.startswith(b"HTTP/1.1 200"), resposta[:80]
        assert TOKEN.encode() in resposta
    _no_coletor(cenario)


def test_get_em_conexao_continua_entregando_so_para_a_pagina():
    async def cenario(coletor):
        status, _, corpo = await asyncio.to_thread(_pedir, coletor.porta, "GET", "/conexao", CABECALHOS_PAGINA)
        assert status == 200 and TOKEN.encode() in corpo
        status, _, corpo = await asyncio.to_thread(_pedir, coletor.porta, "GET", "/conexao", {})
        assert status == 404 and TOKEN.encode() not in corpo
    _no_coletor(cenario)
