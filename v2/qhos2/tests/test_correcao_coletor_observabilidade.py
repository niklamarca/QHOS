"""Campanha de correcao, lote l5b-coletor: observacao HTTP, percurso e diagnostico de relogio
(QHOS-AUD-084, 104, 108, 181, 190).

Coletor sempre em porta efemera do SO; capturas sinteticas em `tmp_path`; relogio injetado. `xfail(strict=True)` =
defeito confirmado em codigo congelado; patch minimo em `09_correcao/EVIDENCIAS/<ID>/patch_proposto.diff`.
"""
import ast
import asyncio
import contextlib
import http.client
import json
import os
from pathlib import Path

import pytest

pytest.importorskip("websockets")

from qhos2 import catalogo, coletor as co, inspecao, leitura as le, observabilidade as ob, sintetico as si  # noqa: E402
from qhos2.gravador import Gravador  # noqa: E402

TOKEN = "A" * 43
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"}


def leitor_sintetico(pasta):
    return le.Leitor(pasta, tick=0.25, simbolo="SINTETICO", bolsa="QHOS2")


def rodar(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


@contextlib.asynccontextmanager
async def no_ar(**extra):
    avisos = []
    coletor = co.Coletor(token=TOKEN, porta=0, avisar=avisos.append, **extra)
    pronto = asyncio.Event()
    tarefa = asyncio.create_task(coletor.rodar(pronto))
    await pronto.wait()
    try:
        yield coletor, avisos
    finally:
        tarefa.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await tarefa


def pedir_http(porta, caminho):
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=5)
    conexao.request("GET", caminho, headers={"Host": f"127.0.0.1:{porta}"})
    resposta = conexao.getresponse()
    corpo = resposta.read()
    conexao.close()
    return resposta.status, json.loads(corpo)


# ---- QHOS-AUD-084: /api/status durante a leitura viva (com `_seguir` ativo) -----------------------------------------

def test_observacao_http_durante_leitura_viva(tmp_path, monkeypatch):
    """Lacuna de teste: as rotas de observacao so eram exercidas com o coletor parado ou com a leitura ENCERRADA."""
    monkeypatch.setattr(co, "LER_A_CADA_S", 0.005)
    gravador = Gravador(tmp_path / "capturas", identidade=IDENTIDADE, captura_id="viva")
    cenario = si.gerar(7, 120)
    relogio = [1_800_000_000.0]                                     # injetado: a idade do arquivo nao depende do relogio real

    async def cena():
        async with no_ar(leitor=leitor_sintetico(gravador.pasta), relogio=lambda: relogio[0]) as (coletor, _):
            for i, (template, dados) in enumerate(cenario.quadros):
                gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
            relogio[0] = (gravador.pasta / "market_frames.jsonl").stat().st_mtime + 1
            for _ in range(1000):                                   # ate 10 s: a thread de leitura alcanca o arquivo
                if coletor.leitor.quadros == len(cenario.quadros):
                    break
                await asyncio.sleep(0.01)
            assert coletor.leitor.estado == le.LENDO                 # sem manifesto ainda: leitura viva
            status = await asyncio.to_thread(pedir_http, coletor.porta, "/api/status")
            saude = await asyncio.to_thread(pedir_http, coletor.porta, "/api/health")
            verdade = await asyncio.to_thread(pedir_http, coletor.porta, "/api/truth")
            instantaneo = await asyncio.to_thread(pedir_http, coletor.porta, "/api/qhos2/snapshot")
            return coletor, status, saude, verdade, instantaneo
    coletor, (s_status, status), (s_saude, saude), (s_verdade, verdade), (s_snap, snap) = rodar(cena())
    gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    assert (s_status, s_saude, s_verdade, s_snap) == (200, 200, 200, 200)
    assert status["modules"] == {"qhos2_collector": {"state": "ONLINE"}, "qhos2_reader": {"state": "LENDO"}}
    observado = status["qhos2_observation"]
    assert observado["capture"]["reader_state"] == "LENDO" and observado["capture"]["closed"] is False
    assert observado["capture"]["frames"] == len(cenario.quadros) and observado["capture"]["error_code"] is None
    # a identidade da captura declara "QHOS2 Sintetico": `source_kind` segue o ambiente, nao o estado da leitura
    assert observado["source_kind"] == "synthetic_fixture" and observado["quality"]["book_valid"] is True
    assert observado["quality"]["recent_file_activity"] is True and 0 <= observado["quality"]["file_age_seconds"] <= 1.5
    assert observado["quality"]["last_frame_received_ns"] == str(si.INICIO_NS + (len(cenario.quadros) - 1) * si.PASSO_NS)
    estados = {c["id"]: c["state"] for c in observado["pipeline"]["stages"]}
    assert estados == {"rithmic": "ok", "sessao": "ok", "gravacao": "ok", "livro": "ok", "leitura": "ok", "coletor": "ok"}
    assert saude["status"] == {"process_online": True, "trading_ready": False}
    assert verdade["book_qualified"] is True and verdade["capture"]["reader_state"] == "LENDO"
    assert snap["capture"] == observado["capture"]
    texto = json.dumps([status, saude, verdade, snap])
    assert TOKEN not in texto and str(tmp_path) not in texto and "6850" not in texto


# ---- QHOS-AUD-104: diagnostico de relogio em silencio em saltos e sem amostra ---------------------------------------

def _sessao(tmp_path):
    from qhos2 import sessao as se
    from qhos2.config import Settings
    config = se.ConfigSessao(template_version="5.42", symbol="ESZ6", exchange="CME",
                             settings=Settings(data_root=tmp_path / "dados"))
    return se, se.Sessao(config, None, ao_receber=lambda *_: None)


def _heartbeat(se, s, monkeypatch, *, mono_envio, parede_envio_s, rtt_s, servidor_s):
    from qhos2 import rprotocol as p
    s._hb_pendente = (mono_envio, int(parede_envio_s * 1e9))
    monkeypatch.setattr(se.time, "monotonic", lambda: mono_envio + rtt_s)
    recebido_ns = int((parede_envio_s + rtt_s) * 1e9)
    s._heartbeat_recebido(p.Mensagem(p.RESP_HEARTBEAT, {"ssboe": int(servidor_s), "usecs": int(round((servidor_s % 1) * 1e6)),
                                                        "rp_code": ["0"]}), recebido_ns)


def test_caracterizacao_salto_de_parede_entre_heartbeats_nao_e_detectado(tmp_path, monkeypatch):
    se, s = _sessao(tmp_path)
    _heartbeat(se, s, monkeypatch, mono_envio=100.0, parede_envio_s=1000.0, rtt_s=0.010, servidor_s=1000.005)
    assert s.status()["relogio_alerta"] is False and abs(s.status()["relogio"]["desvio_ms"]) < 1
    # entre os dois heartbeats a parede avanca 5,8 s enquanto o monotonico avanca 5 s (degrau de 0,8 s)
    _heartbeat(se, s, monkeypatch, mono_envio=105.0, parede_envio_s=1005.8, rtt_s=0.010, servidor_s=1005.005)
    status = s.status()
    assert status["anomalias"].get("relogio_salto_local", 0) == 0
    assert status["relogio_alerta"] is False                       # desvio de -0,8 s fica abaixo do alerta de 1 s
    assert abs(status["relogio"]["ultimo_desvio_ms"] + 800) < 1   # mas a amostra nova ja mostra o degrau


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-104: um salto do relogio de parede so e detectado se cair dentro "
                                       "do RTT de um heartbeat em voo; entre heartbeats nao gera anomalia nem alerta")
def test_salto_entre_heartbeats_e_sinalizado(tmp_path, monkeypatch):
    se, s = _sessao(tmp_path)
    _heartbeat(se, s, monkeypatch, mono_envio=100.0, parede_envio_s=1000.0, rtt_s=0.010, servidor_s=1000.005)
    _heartbeat(se, s, monkeypatch, mono_envio=105.0, parede_envio_s=1005.8, rtt_s=0.010, servidor_s=1005.005)
    status = s.status()
    assert status["relogio_alerta"] or status["anomalias"]["relogio_salto_local"] == 1


def test_caracterizacao_sem_amostra_de_relogio_nao_ha_alerta(tmp_path):
    """QHOS-AUD-104(b): sem nenhuma amostra, `relogio_alerta` e False. Se isso deve virar True (estado NOVA, antes do
    primeiro heartbeat) e decisao do autor; fica caracterizado, nao codificado como defeito."""
    _, s = _sessao(tmp_path)
    status = s.status()
    assert status["relogio"] is None and status["relogio_alerta"] is False


# ---- QHOS-AUD-108: membros privados atravessando modulos; percurso() muta _desde a cada GET -----------------------

_PARES_DO_ACHADO = {
    "observabilidade": {"_idade_do_arquivo_s", "_relogio"},            # coletor._x
    "coletor": {"_falhar", "_ler_manifesto"},                            # leitor._falhar, catalogo._ler_manifesto
    "historico": {"_escrever_atomico", "_ler_manifesto"},
    "leitura": {"_ler_manifesto"},
}


def _privados_de_outro_modulo(modulo):
    arvore = ast.parse(Path(modulo.__file__).read_text(encoding="utf-8"))
    achados = set()
    for n in ast.walk(arvore):
        if isinstance(n, ast.ImportFrom) and (n.module or "").startswith("qhos2"):
            achados |= {a.name for a in n.names if a.name.startswith("_") and not a.name.startswith("__")}
        if isinstance(n, ast.Attribute) and n.attr.startswith("_") and not n.attr.startswith("__"):
            alvo = n.value
            if isinstance(alvo, ast.Attribute) and isinstance(alvo.value, ast.Name) and alvo.value.id == "self":
                achados.add(n.attr)                                   # self.leitor._falhar
            elif isinstance(alvo, ast.Name) and alvo.id not in ("self", "cls", "os", "sys", "time", "json"):
                achados.add(n.attr)                                   # coletor._relogio, catalogo._ler_manifesto
    return achados


def test_caracterizacao_privados_atravessam_modulos_nos_pares_listados():
    from qhos2 import historico
    vistos = {nome: _privados_de_outro_modulo(m) for nome, m in
              (("observabilidade", ob), ("coletor", co), ("historico", historico), ("leitura", le))}
    for nome, esperados in _PARES_DO_ACHADO.items():
        assert esperados <= vistos[nome], (nome, vistos[nome])


def test_caracterizacao_privados_cruzados_sao_exatamente_os_do_achado():
    """QHOS-AUD-108 e refatoracao (promover helpers a nomes publicos), nao defeito observavel: fica caracterizado o
    conjunto atual de privados que atravessam modulos nos pares do achado, para a janela J2 decidir."""
    from qhos2 import historico
    for nome, m in (("observabilidade", ob), ("coletor", co), ("historico", historico), ("leitura", le)):
        assert _privados_de_outro_modulo(m) & _PARES_DO_ACHADO[nome] == _PARES_DO_ACHADO[nome], nome


def test_caracterizacao_percurso_por_get_e_idempotente_no_since(tmp_path):
    """`snapshot()` chama `percurso()` e este muta `_desde`; com o mesmo estado e o mesmo relogio, duas chamadas
    (uma pela observacao HTTP, outra periodica) devolvem o mesmo `since`: a mutacao nao altera o resultado."""
    pasta = si.gravar(si.gerar(5, 30), tmp_path)
    relogio = [1000.0]
    coletor = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(pasta), relogio=lambda: relogio[0])
    coletor.traduzir(coletor.leitor.avancar(), alcancado=True)
    coletor.leitor.avancar()
    primeiro = ob.snapshot(coletor)["pipeline"]
    relogio[0] = 1001.0
    segundo = coletor.percurso()
    assert [c["since"] for c in primeiro["stages"]] == [c["since"] for c in segundo["stages"]] == [1_000_000] * 6


# ---- QHOS-AUD-181: catalogo, inspecao e atraso: OSError e JSON fora do try -------------------------------------------

def _captura_com_manifesto_ilegivel(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignora chmod 000; o cenario precisa de usuario comum")
    pasta = si.gravar(si.gerar(3, 20), tmp_path)
    (pasta / "manifest.json").chmod(0o000)
    return pasta


def test_caracterizacao_manifesto_ilegivel_vira_excecao_e_nao_divergencia(tmp_path):
    pasta = _captura_com_manifesto_ilegivel(tmp_path)
    try:
        with pytest.raises(PermissionError):
            catalogo.verificar_captura(pasta)
        with pytest.raises(PermissionError):
            inspecao.inspecionar(pasta)
    finally:
        (pasta / "manifest.json").chmod(0o600)


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-181: `_ler_manifesto` abre o manifesto fora do `try`; OSError vira "
                                       "excecao (traceback na CLI, queda no coletor) em vez de divergencia")
def test_manifesto_ilegivel_e_divergencia_do_catalogo(tmp_path):
    pasta = _captura_com_manifesto_ilegivel(tmp_path)
    try:
        relatorio = catalogo.verificar_captura(pasta)
    finally:
        (pasta / "manifest.json").chmod(0o600)
    assert relatorio["verificada"] is False and relatorio["divergencias"] == ["manifesto_inacessivel"]


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-181: `inspecionar` so converte InspecaoError; manifesto ilegivel "
                                       "vira traceback")
def test_manifesto_ilegivel_e_erro_de_inspecao(tmp_path):
    pasta = _captura_com_manifesto_ilegivel(tmp_path)
    try:
        with pytest.raises(inspecao.InspecaoError):
            inspecao.inspecionar(pasta)
    finally:
        (pasta / "manifest.json").chmod(0o600)


# ---- QHOS-AUD-190: recuo do relogio: since do percurso maior que time ---------------------------------------------

def _coletor_com_relogio(tmp_path, relogio):
    pasta = si.gravar(si.gerar(5, 30), tmp_path)
    coletor = co.Coletor(token=TOKEN, porta=0, leitor=leitor_sintetico(pasta), relogio=lambda: relogio[0])
    coletor.traduzir(coletor.leitor.avancar(), alcancado=True)
    coletor.leitor.avancar()
    assert coletor.leitor.estado == le.ENCERRADA                   # estados estaveis: `since` nao muda sozinho
    return coletor


def test_caracterizacao_recuo_do_relogio_deixa_since_maior_que_time(tmp_path):
    relogio = [1000.0]
    coletor = _coletor_com_relogio(tmp_path, relogio)
    coletor.percurso()
    relogio[0] = 990.0
    m = coletor.percurso()
    assert m["time"] == 990_000 and all(c["since"] == 1_000_000 for c in m["stages"])


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-190: `since` vem de time.time() e so muda com o estado; apos um recuo "
                                       "do relogio `since > time` e a ponte recusa o percurso inteiro")
def test_since_nunca_passa_de_time_depois_de_recuo_do_relogio(tmp_path):
    relogio = [1000.0]
    coletor = _coletor_com_relogio(tmp_path, relogio)
    coletor.percurso()
    relogio[0] = 990.0
    m = coletor.percurso()
    assert all(c["since"] <= m["time"] for c in m["stages"])
