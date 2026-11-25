"""Perimetro HTTP do Brain (brain_service.server), em processo e sem rede externa.

Cobre QHOS-AUD-258 (D2 prontidao, D3 middlewares de escrita, D4 relight, D6 logs, D14 rota desconhecida),
QHOS-AUD-028 (coletor fora do ar e motivo de transporte), QHOS-AUD-029 (/openapi.json), QHOS-AUD-030 (cabecalhos nas
recusas e Host `testserver`), QHOS-AUD-300 (inteiros enormes) e QHOS-AUD-313 (codigo estatico no lugar do texto da excecao).

O coletor nao existe: `quant_url` aponta para a porta 9 do loopback (fechada), nunca para 8766/8443 nem para fora.
O TestClient usa `base_url=http://127.0.0.1`, o Host que a politica do servidor aceita; `testserver` nao faz parte dela.
"""
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

ORIGEM_LOCAL = "http://127.0.0.1"
CABECALHOS_DE_SEGURANCA = ("Cache-Control", "X-Content-Type-Options", "Content-Security-Policy")
_VARIAVEIS = ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL", "BRAIN_PUBLIC_ORIGIN",
              "BRAIN_COMPUTER_C", "BRAIN_TRADER_WORKSPACE", "BRAIN_QUANT_LOGS", "BRAIN_QUANT_DATA_LAKE")


def _ambiente(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for nome in _VARIAVEIS:
        monkeypatch.delenv(nome, raising=False)


def _brain(tmp_path, **extra):
    from brain_service.config import ServiceConfig
    from brain_service.process import StandaloneBrain
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False, quant_url="http://127.0.0.1:9", **extra)
    return StandaloneBrain(cfg)


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


def _escrita(cliente, caminho="/api/brain/relight", **kw):
    return cliente.post(caminho, **kw)


# ---- D3: o unico perimetro de escrita --------------------------------------------------------------------------------

def test_escrita_com_origem_estrangeira_e_recusada_com_motivo_estatico(cliente):
    r = _escrita(cliente, json={}, headers={"Origin": "https://outro.example"})
    assert (r.status_code, r.json()) == (403, {"ok": False, "reason": "cross_origin_refused"})


def test_escrita_de_site_cruzado_e_recusada(cliente):
    r = _escrita(cliente, json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert (r.status_code, r.json()) == (403, {"ok": False, "reason": "cross_site_refused"})


def test_escrita_sem_json_e_recusada_com_415(cliente):
    r = _escrita(cliente, content="confirm=x", headers={"Content-Type": "text/plain"})
    assert (r.status_code, r.json()) == (415, {"ok": False, "reason": "json_required"})
    r = _escrita(cliente, content="{}", headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 200


def test_corpo_de_escrita_maior_que_16_kib_e_recusado_antes_da_rota(cliente):
    grande = json.dumps({"confirm": "x" * (16 * 1024)})
    r = _escrita(cliente, content=grande, headers={"Content-Type": "application/json"})
    assert (r.status_code, r.json()["reason"]) == (413, "body_too_large")
    r = _escrita(cliente, content="{}", headers={"Content-Type": "application/json", "Content-Length": "16385"})
    assert (r.status_code, r.json()["reason"]) == (413, "body_too_large")


def test_content_length_invalido_e_recusado_com_400(cliente):
    r = _escrita(cliente, content="{}", headers={"Content-Type": "application/json", "Content-Length": "abc"})
    assert (r.status_code, r.json()["reason"]) == (400, "invalid_content_length")


def test_host_desconhecido_e_recusado_e_leituras_nunca_sao_bloqueadas_por_origem(cliente):
    assert cliente.get("/api/brain/authority", headers={"Host": "evil.example"}).status_code == 400
    r = cliente.get("/api/brain/authority", headers={"Origin": "https://outro.example", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200
    r = _escrita(cliente, json={}, headers={"Origin": ORIGEM_LOCAL})
    assert r.status_code == 200 and r.json()["accepted"] is False


def test_origem_publica_configurada_e_aceita_com_ou_sem_barra_final(tmp_path, monkeypatch):
    _ambiente(monkeypatch, tmp_path)
    from brain_service.server import build_app
    processo = _brain(tmp_path, public_origin="https://painel.example")
    try:
        with TestClient(build_app(processo), base_url=ORIGEM_LOCAL) as c:
            for origem in ("https://painel.example", "https://painel.example/"):
                assert _escrita(c, json={}, headers={"Origin": origem}).status_code == 200, origem
            assert _escrita(c, json={}, headers={"Origin": "https://painel.example.evil"}).status_code == 403
            assert c.get("/api/brain/authority", headers={"Host": "painel.example"}).status_code == 200
    finally:
        processo.log_feed.close()


# ---- QHOS-AUD-030: cabecalhos em toda resposta; `testserver` fora da politica ----------------------------------------

@pytest.mark.parametrize("pedido, esperado", [
    (dict(json={}, headers={"Origin": "https://outro.example"}), 403),
    (dict(json={}, headers={"Sec-Fetch-Site": "cross-site"}), 403),
    (dict(content="x", headers={"Content-Type": "text/plain"}), 415),
    (dict(content="{}", headers={"Content-Type": "application/json", "Content-Length": "16385"}), 413),
])
def test_recusas_de_escrita_levam_os_mesmos_cabecalhos_de_seguranca_das_respostas_normais(cliente, pedido, esperado):
    ok = cliente.get("/api/brain/authority")
    assert ok.status_code == 200
    r = _escrita(cliente, **pedido)
    assert r.status_code == esperado
    for nome in CABECALHOS_DE_SEGURANCA:
        assert r.headers.get(nome) == ok.headers.get(nome), nome
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Content-Type-Options"] == "nosniff"


def test_host_testserver_nao_faz_parte_da_politica_de_hosts(cliente):
    for caminho in ("/", "/api/brain/authority", "/api/assistant/state"):
        assert cliente.get(caminho, headers={"Host": "testserver"}).status_code == 400, caminho
    for host in ("127.0.0.1", "localhost", "127.0.0.1:8091", "localhost:8091"):
        assert cliente.get("/api/brain/authority", headers={"Host": host}).status_code == 200, host


# ---- QHOS-AUD-029 / D14: superficie fechada --------------------------------------------------------------------------

def test_openapi_docs_e_redoc_nao_sao_servidos(cliente):
    for caminho in ("/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"):
        r = cliente.get(caminho)
        assert r.status_code == 404, caminho
        assert r.json()["reason"] == "NOT_AVAILABLE", caminho


def test_rota_desconhecida_e_metodo_errado_dao_404_not_available(cliente):
    r = cliente.get("/api/deploy")
    assert r.status_code == 404 and r.json() == {"ok": False, "reason": "NOT_AVAILABLE", "process": "qhos-brain-service", "path": "api/deploy"}
    r = cliente.post("/api/brain", json={})
    assert r.status_code == 404 and r.json()["reason"] == "NOT_AVAILABLE"
    r = cliente.request("DELETE", "/api/assistant/state", json={})
    assert r.status_code == 404 and r.json()["reason"] == "NOT_AVAILABLE"


def test_rota_privilegiada_acrescentada_e_detectada(brain):
    from brain_service.server import build_app, assert_no_privileged_routes
    app = build_app(brain)
    assert_no_privileged_routes(app)
    app.add_api_route("/api/apply", lambda: {"ok": True}, methods=["POST"])
    with pytest.raises(AssertionError, match="apply"):
        assert_no_privileged_routes(app)


# ---- D2 / D4: prontidao e relight (caracterizam o comportamento atual; a politica de saude nao muda aqui) -----------

def test_ready_reflete_started_e_health_identifica_o_processo(cliente, brain):
    r = cliente.get("/ready")
    assert r.status_code == 503
    assert r.json() == {"ready": False, "process": "qhos-brain-service",
                        "trader": {"reachable": False, "required_for_brain_readiness": False}, "level3_enabled": False}
    saude = cliente.get("/health")
    assert saude.status_code == 200
    assert saude.json()["process"] == "qhos-brain-service" and saude.json()["service_running"] is False
    brain.started = True
    try:
        assert cliente.get("/ready").status_code == 200 and cliente.get("/health").json()["service_running"] is True
    finally:
        brain.started = False   # o lifespan do TestClient so chama stop() num processo iniciado


def test_relight_exige_confirmacao_nunca_e_aceito_na_v2_e_fica_registrado(cliente):
    for corpo in ({}, {"confirm": ""}, {"confirm": "RELIGHT_QUANT"}):
        r = _escrita(cliente, json=corpo)
        assert r.status_code == 200 and r.json()["accepted"] is False, corpo
    registro = cliente.get("/api/linux/actions").json()
    pedidos = [a for a in registro["actions"] if a.get("action") == "trader_relight_requested"]
    assert pedidos and all(a.get("outcome") == "refused" for a in pedidos)
    assert cliente.get("/api/brain/relight").status_code == 200


# ---- D6 / QHOS-AUD-300: /api/logs --------------------------------------------------------------------------------------

def _registra(feed, n):
    for i in range(n):
        feed.append({"__CURSOR": f"c{i}", "MESSAGE": f"linha {i}", "__REALTIME_TIMESTAMP": (i + 1) * 1_000_000})


def test_api_logs_pagina_por_cursor_e_limita_o_tamanho(cliente, brain):
    _registra(brain.log_feed, 3)
    pagina = cliente.get("/api/logs?after=0&limit=2").json()
    assert pagina["schema"] == "qhos-log-stream-v1" and [r["message"] for r in pagina["records"]] == ["linha 0", "linha 1"]
    assert pagina["next_cursor"] == pagina["records"][-1]["cursor"]
    resto = cliente.get(f"/api/logs?after={pagina['next_cursor']}&limit=10000").json()
    assert [r["message"] for r in resto["records"]] == ["linha 2"]
    fim = cliente.get("/api/logs?after=100").json()
    assert fim["records"] == [] and fim["next_cursor"] == 100
    for invalido in ("abc", "true", "false", "1.5"):
        assert cliente.get(f"/api/logs?after={invalido}").status_code == 422, invalido
    assert cliente.get("/api/logs?after=0&limit=false").status_code == 422
    # negativos: o componente consulta a partir de 0 e limita `limit` a [1, 100]
    negativo = cliente.get("/api/logs?after=-5&limit=-3").json()
    assert [r["message"] for r in negativo["records"]] == ["linha 0"] and negativo["next_cursor"] == 1
    assert [r["message"] for r in cliente.get("/api/logs?after=-99&limit=10").json()["records"]] == ["linha 0", "linha 1", "linha 2"]


def test_api_logs_vazio_com_cursor_negativo_devolve_o_cursor_pedido(cliente):
    assert cliente.get("/api/logs?after=-5").json() | {"collection": None} == {
        "schema": "qhos-log-stream-v1", "records": [], "next_cursor": -5,
        "source": "systemd-journal+application-files", "collection": None}


def test_offset_negativo_e_booleano_no_navegador_de_arquivos(cliente):
    r = cliente.get("/api/files/read?root=trader&path=__init__.py&offset=-10")
    assert r.status_code == 200 and r.json()["offset"] == 0
    assert cliente.get("/api/files/read?root=trader&path=__init__.py&offset=true").status_code == 422


@pytest.mark.parametrize("caminho, motivo", [
    ("/api/logs?after=99999999999999999999", "cursor_out_of_range"),
    ("/api/logs?after=9223372036854775808", "cursor_out_of_range"),
    ("/api/files/read?root=trader&path=__init__.py&offset=99999999999999999999", "offset_out_of_range"),
])
def test_inteiros_acima_da_representacao_dao_400_com_codigo_estatico(brain, caminho, motivo):
    from brain_service.server import build_app
    with TestClient(build_app(brain), base_url=ORIGEM_LOCAL, raise_server_exceptions=False) as c:
        r = c.get(caminho)
        assert r.status_code == 400, (r.status_code, r.text[:200])
        assert r.json()["detail"] == motivo
        # o limite da representacao do sqlite (int64) continua aceito; no seek de arquivo o SO pode recusar antes
        # (EINVAL), e isso continua sendo um 400 com codigo estatico, nunca um 500
        assert c.get("/api/logs?after=9223372036854775807").status_code == 200
        r = c.get("/api/files/read?root=trader&path=__init__.py&offset=9223372036854775807")
        assert r.status_code in (200, 400) and "/" not in r.text


# ---- QHOS-AUD-313: erro do navegador de arquivos vira codigo estatico -----------------------------------------------

@pytest.mark.parametrize("rota", ["/api/files/read", "/api/files/list"])
def test_caminho_inexistente_responde_400_sem_caminho_nem_nome_no_detalhe(cliente, rota):
    marcador = "segredo-FICTICIO-7f3a9c"
    r = cliente.get(f"{rota}?root=trader&path={marcador}.txt")
    assert r.status_code == 400
    detalhe = r.json()["detail"]
    assert detalhe == "not_found"
    assert "/" not in detalhe and "\\" not in detalhe and marcador not in detalhe and "Errno" not in detalhe


def test_codigos_estaticos_do_navegador_de_arquivos_sao_preservados(cliente):
    assert cliente.get("/api/files/list?root=nope&path=").json()["detail"] == "unknown_root"
    assert cliente.get("/api/files/read?root=trader&path=../../..").json()["detail"] in {"outside_read_root", "not_regular_file"}
    assert cliente.get("/api/files/read?root=trader&path=.").json()["detail"] == "not_regular_file"
    r = cliente.get("/api/files/read?root=trader&path=__init__.py")
    assert r.status_code == 200 and r.json()["read_only"] is True


# ---- QHOS-AUD-028: coletor fora do ar e motivo de transporte -----------------------------------------------------------

def test_estado_sem_coletor_declara_indisponivel_por_transporte_e_nao_por_esquema(cliente, brain):
    def observacao():
        r = cliente.get("/api/assistant/state")
        assert r.status_code == 200
        obs = r.json()["trader"]["qhos2_observation"]
        assert obs["available"] is False and obs["environment"] == "unknown" and obs["permissions"]["can_execute"] is False
        return obs["reason"]
    # antes de qualquer coleta: ausencia de observacao, nao um veredito sobre o esquema
    assert observacao() == "qhos2_observation_missing"
    brain.observer.collect()          # porta 9 do loopback fechada: falha de transporte em todas as rotas
    assert brain.observer.last_error and "/api/qhos2/snapshot:" in brain.observer.last_error
    assert observacao() == "unreachable"


class _ColetorFalso:
    """Servidor HTTP local em porta efemera que responde `/api/qhos2/snapshot` de cinco jeitos invalidos."""
    CASOS = {
        "http_500": (500, {"Content-Type": "application/json"}, b'{"erro": true}'),
        "corpo_nao_json": (200, {"Content-Type": "text/html"}, b"<html>pagina</html>"),
        "json_com_nan": (200, {"Content-Type": "application/json"}, b'{"schema_version": "qhos2.observation.v1", "x": NaN}'),
        "redirecionamento_302": (302, {"Location": "http://127.0.0.1:9/outro"}, b""),
        "corpo_maior_que_2mb": (200, {"Content-Type": "application/json"}, b'{"a": "' + b"x" * 2_100_000 + b'"}'),
    }

    def __init__(self):
        import http.server
        import threading
        servidor = self

        class Pedido(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status, cabecalhos, corpo = servidor.CASOS[servidor.caso]
                self.send_response(status)
                for nome, valor in cabecalhos.items():
                    self.send_header(nome, valor)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def log_message(self, *_):
                pass
        self.caso = "http_500"
        self.http = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pedido)
        self.base = f"http://127.0.0.1:{self.http.server_address[1]}"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def fechar(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.mark.parametrize("caso", sorted(_ColetorFalso.CASOS))
def test_resposta_recebida_mas_invalida_e_esquema_invalido_nao_unreachable(caso):
    from brain_service import observer
    from brain_service.qhos2_observation import accept
    falso = _ColetorFalso()
    try:
        falso.caso = caso
        linha = observer.get_json(falso.base, "/api/qhos2/snapshot", timeout_s=5)
        assert linha.get("_observation_error"), linha            # a resposta chegou, mas nao serve
        assert accept(linha)["reason"] == "qhos2_schema_invalid", (caso, linha)
    finally:
        falso.fechar()


def test_porta_fechada_e_unreachable():
    from brain_service import observer
    from brain_service.qhos2_observation import accept
    linha = observer.get_json("http://127.0.0.1:9", "/api/qhos2/snapshot", timeout_s=5)
    assert linha["_observation_error"] == "URLError"
    assert accept(linha)["reason"] == "unreachable"


def test_observer_distingue_transporte_de_esquema(tmp_path, monkeypatch):
    _ambiente(monkeypatch, tmp_path)
    from brain_service import observer
    from brain_service.config import ServiceConfig
    cfg = ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False, quant_url="http://127.0.0.1:9")
    respostas = {}
    monkeypatch.setattr(observer, "get_json", lambda base, path, **_: respostas.get(path, {"_observation_error": "URLError", "path": path}))
    o = observer.QuantObserver(cfg)
    assert o.probe_quant()["qhos2_observation"]["reason"] == "unreachable"
    respostas["/api/qhos2/snapshot"] = {"schema_version": "outro"}
    assert o.probe_quant()["qhos2_observation"]["reason"] == "qhos2_schema_invalid"
