"""Perimetro WebSocket do Brain (`/ws/brain`, `/ws/logs`), em processo e sem rede externa.

Cobre QHOS-AUD-258 D13 (origem e ping), QHOS-AUD-312 (uma unica regra de Origin nos tres pontos), QHOS-AUD-297
(`/ws/brain` nao engole RuntimeError) e QHOS-AUD-300 (cursor enorme fecha com 1008).

O TestClient ignora `base_url` em `websocket_connect` com caminho relativo (Host vira `testserver`): as conexoes usam a URL
absoluta `ws://127.0.0.1/...`, o Host da politica. Toda espera por mensagem tem limite de tempo (`_com_limite`): um
servidor que engole o erro e nunca fecha nao pode pendurar a suite.
"""
import threading

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from test_brain_perimetro_http import ORIGEM_LOCAL, _ambiente, _brain  # noqa: E402

WS_LOCAL = "ws://127.0.0.1"


def _com_limite(funcao, limite_s=10):
    """Executa `funcao` numa thread daemon; se nao terminar em `limite_s`, o teste falha em vez de bloquear."""
    saida = {}

    def alvo():
        try:
            saida["valor"] = funcao()
        except BaseException as exc:          # repassado ao teste
            saida["erro"] = exc
    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    t.join(limite_s)
    assert not t.is_alive(), f"WebSocket bloqueou por mais de {limite_s}s (servidor nao fechou nem respondeu)"
    if "erro" in saida:
        raise saida["erro"]
    return saida.get("valor")


@pytest.fixture
def brain(tmp_path, monkeypatch):
    _ambiente(monkeypatch, tmp_path)
    processo = _brain(tmp_path)
    try:
        yield processo
    finally:
        processo.log_feed.close()


@pytest.fixture
def cliente(brain):
    from brain_service.server import build_app
    with TestClient(build_app(brain), base_url=ORIGEM_LOCAL) as c:
        yield c


def test_ws_brain_entrega_snapshot_e_responde_ping(cliente):
    def cenario():
        with cliente.websocket_connect(WS_LOCAL + "/ws/brain", headers={"Origin": ORIGEM_LOCAL}) as ws:
            primeiro = ws.receive_json()
            assert primeiro["type"] == "brain_snapshot" and primeiro["schema"] == "qhos-brain-ws-v1"
            ws.send_text("ping")
            mensagens = [ws.receive_json() for _ in range(2)]
            assert {"type": "pong"} in mensagens
    _com_limite(cenario)


def test_ws_logs_entrega_pagina_do_esquema_de_logs(cliente):
    def cenario():
        with cliente.websocket_connect(WS_LOCAL + "/ws/logs", headers={"Origin": ORIGEM_LOCAL}) as ws:
            pagina = ws.receive_json()
            assert pagina["schema"] == "qhos-log-stream-v1" and pagina["records"] == [] and pagina["next_cursor"] == 0
    _com_limite(cenario)


def test_websockets_sem_origin_sao_aceitos(cliente):
    def cenario():
        for caminho in ("/ws/brain", "/ws/logs"):
            with cliente.websocket_connect(WS_LOCAL + caminho) as ws:
                assert "schema" in ws.receive_json()
    _com_limite(cenario)


def test_websockets_com_host_fora_da_politica_sao_recusados_antes_da_rota(cliente):
    from starlette.testclient import WebSocketDenialResponse
    for caminho in ("/ws/brain", "/ws/logs"):
        with pytest.raises(WebSocketDenialResponse) as erro:
            with cliente.websocket_connect(caminho):          # caminho relativo: Host `testserver`
                pass
        assert erro.value.status_code == 400, caminho


@pytest.mark.parametrize("caminho", ["/ws/brain", "/ws/logs"])
@pytest.mark.parametrize("origem", ["https://outro.example", "ftp://127.0.0.1", "http://[::1", "null", "http://127.0.0.1.evil", "/"])
def test_websockets_recusam_origem_externa_sem_esquema_ou_malformada_com_o_mesmo_motivo(cliente, caminho, origem):
    def cenario():
        with pytest.raises(WebSocketDisconnect) as erro:
            with cliente.websocket_connect(WS_LOCAL + caminho, headers={"Origin": origem}):
                pass
        return erro.value
    erro = _com_limite(cenario)
    assert (erro.code, erro.reason) == (1008, "cross_origin_refused"), (caminho, origem)


def test_websockets_aceitam_a_origem_publica_configurada(tmp_path, monkeypatch):
    _ambiente(monkeypatch, tmp_path)
    from brain_service.server import build_app
    processo = _brain(tmp_path, public_origin="https://painel.example")

    def cenario():
        with TestClient(build_app(processo), base_url=ORIGEM_LOCAL) as c:
            for caminho in ("/ws/brain", "/ws/logs"):
                with c.websocket_connect(WS_LOCAL + caminho, headers={"Origin": "https://painel.example/"}) as ws:
                    assert "schema" in ws.receive_json()
    try:
        _com_limite(cenario)
    finally:
        processo.log_feed.close()


def _sessao_ws_direta(app, caminho, limite_s=5):
    """Conduz um WebSocket direto na aplicacao ASGI, sem TestClient: a tarefa da rota e aguardada com limite e cancelada
    no fim, entao nem um servidor que engole o erro e nunca fecha consegue bloquear o teste. Devolve as mensagens ASGI
    que o servidor enviou; uma excecao que sair da rota e relancada."""
    import asyncio

    async def sessao():
        entrada, saida = asyncio.Queue(), asyncio.Queue()
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "scheme": "ws", "http_version": "1.1", "root_path": "",
                 "path": caminho, "raw_path": caminho.encode(), "query_string": b"", "headers": [(b"host", b"127.0.0.1")],
                 "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8091), "subprotocols": [], "state": {}}
        await entrada.put({"type": "websocket.connect"})
        tarefa = asyncio.create_task(app(scope, entrada.get, saida.put))
        try:
            await asyncio.wait_for(asyncio.shield(tarefa), limite_s)
        finally:
            if not tarefa.done():
                tarefa.cancel()
                await asyncio.gather(tarefa, return_exceptions=True)
        return [saida.get_nowait() for _ in range(saida.qsize())]
    return asyncio.run(sessao())


@pytest.mark.parametrize("classe", [RuntimeError, OSError])
def test_ws_brain_nao_engole_erro_do_modelo_de_visao(brain, monkeypatch, classe):
    from brain import api as brain_api
    from brain_service.server import build_app

    def quebrado(*_a, **_k):
        raise classe("falha_sintetica_do_view_model")
    monkeypatch.setattr(brain_api, "view_model", quebrado)
    # Defeito (QHOS-AUD-297): o erro era engolido e a rota terminava "normalmente" depois do accept, sem fechar o
    # socket nem registrar nada; esperado: o erro sai da rota (o servidor ASGI o registra e fecha com 1011). Um OSError
    # de envio ja vira WebSocketDisconnect(1006) no Starlette 1.10; um OSError do view_model nao e transporte.
    with pytest.raises(classe, match="falha_sintetica_do_view_model"):
        _sessao_ws_direta(build_app(brain), "/ws/brain")


def test_ws_brain_encerra_em_silencio_quando_o_cliente_sai(cliente):
    def cenario():
        with cliente.websocket_connect(WS_LOCAL + "/ws/brain") as ws:
            ws.receive_json()
            ws.close()
    _com_limite(cenario)


def test_ws_logs_cursor_invalido_ou_enorme_fecha_com_1008(cliente):
    def cenario():
        for consulta in ("abc", "99999999999999999999", "9223372036854775808"):
            with cliente.websocket_connect(f"{WS_LOCAL}/ws/logs?after={consulta}") as ws:
                with pytest.raises(WebSocketDisconnect) as erro:
                    ws.receive_json()
            assert (erro.value.code, erro.value.reason) == (1008, "invalid cursor"), consulta
        with cliente.websocket_connect(f"{WS_LOCAL}/ws/logs?after=9223372036854775807") as ws:
            assert ws.receive_json()["records"] == []
    _com_limite(cenario)
