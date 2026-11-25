"""Campanha de correcao, lote l5b-coletor: caminhos de falha da observacao do coletor pelo Brain (QHOS-AUD-261).

Lado do Brain (`src/brain_service/observer.py`, `log_feed.py`, `process.py`): este lote so acrescenta testes
(o brief pede "so a parte de testes e avise"). Porta fechada real em 127.0.0.1 (obtida do SO e liberada), nenhum
`journalctl` real, nenhuma rede externa.
"""
import socket
import threading

import pytest

pytest.importorskip("fastapi")

from brain_service import log_feed, observer  # noqa: E402
from brain_service.config import ServiceConfig  # noqa: E402


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setenv("BRAIN_CODEX_CHAT", "0")
    for nome in ("BRAIN_QUANT_ROOT", "BRAIN_STATE_DIR", "BRAIN_RUNTIME_DIR", "BRAIN_QUANT_OBSERVE_URL",
                 "BRAIN_PUBLIC_ORIGIN", "BRAIN_QUANT_LOGS", "BRAIN_QUANT_DATA_LAKE"):
        monkeypatch.delenv(nome, raising=False)


def porta_fechada():
    """Uma porta que o SO acabou de liberar: conexao recusada, sem servidor nenhum."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def configuracao(tmp_path, porta):
    return ServiceConfig(state_path=str(tmp_path / "state"), diagnostics_enabled=False,
                         quant_url=f"http://127.0.0.1:{porta}", observe_timeout_s=0.5)


# ---- D1: coletor indisponivel vira evidencia insuficiente, nao falha critica ----------------------------------------

def test_get_json_com_coletor_fora_do_ar_devolve_erro_de_observacao_sem_excecao(tmp_path):
    cfg = configuracao(tmp_path, porta_fechada())
    linha = observer.get_json(cfg.quant_base, "/api/health", timeout_s=0.5)
    assert linha == {"_observation_error": "URLError", "path": "/api/health"}


def test_coletor_indisponivel_vira_evidencia_insuficiente_nao_falha_critica(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, "_git", lambda *_: {"stdout": "", "available": True, "error": None})  # QHOS-AUD-275: _git devolve medicao
    cfg = configuracao(tmp_path, porta_fechada())
    o = observer.QuantObserver(cfg)
    resultado = o.collect()                                           # nao lanca
    assert resultado["application_ready"] is False and o.last_quant_reachable is False
    remoto = resultado["remote"]
    assert remoto["reachable"] is False and remoto["telemetry_partial"] is False
    assert all(f"{caminho}:URLError" in remoto["error"] for caminho in cfg.allowed_get_paths)
    assert resultado["qhos2_observation"]["available"] is False
    assert resultado["quant_process"] == {**resultado["quant_process"], "reachable": False, "pid": None}
    assert resultado["telemetry"]["errors"][0] == remoto["error"]


def test_coletor_que_responde_so_a_saude_e_telemetria_parcial_nao_pronta(tmp_path, monkeypatch):
    """`collect` fora do caminho feliz: health responde, o resto falha. Pronto = so health com PROCESS_ONLINE."""
    monkeypatch.setattr(observer, "_git", lambda *_: {"stdout": "", "available": True, "error": None})  # QHOS-AUD-275: _git devolve medicao
    respostas = {"/api/status": {"modules": {"qhos2_collector": {"state": "ONLINE"}}}}

    def get_json(base, path, **_):
        return respostas.get(path, {"_observation_error": "TimeoutError", "path": path})
    monkeypatch.setattr(observer, "get_json", get_json)
    o = observer.QuantObserver(configuracao(tmp_path, porta_fechada()))
    resultado = o.collect()
    assert resultado["application_ready"] is False and resultado["remote"]["telemetry_partial"] is True
    assert "/api/health:TimeoutError" in resultado["remote"]["error"]
    assert resultado["qhos2_observation"]["available"] is False


def test_estado_do_brain_com_coletor_fora_do_ar_e_insuficiente_e_nao_critico(tmp_path):
    """Como no teste da auditoria (`test_estado_sem_coletor_declara_indisponivel_com_motivo`), via a API do Brain."""
    from fastapi.testclient import TestClient
    from brain_service.process import StandaloneBrain
    from brain_service.server import build_app
    brain = StandaloneBrain(configuracao(tmp_path, porta_fechada()))
    try:
        # host fora da politica (testserver) recebe 400 desde a QHOS-AUD-030
        with TestClient(build_app(brain), base_url="http://127.0.0.1") as cliente:
            estado = cliente.get("/api/assistant/state")
            saude = cliente.get("/health")
            pronto = cliente.get("/ready")
    finally:
        brain.log_feed.close()
    assert estado.status_code == 200 and saude.status_code == 200 and pronto.status_code in (200, 503)
    observacao = estado.json()["trader"]["qhos2_observation"]
    assert observacao["available"] is False
    assert saude.json()["health"]["evidence_state"] == "insufficient"


# ---- D10: thread do journal morta aparece na telemetria --------------------------------------------------------------

def _feed_com_journalctl_ausente(tmp_path, monkeypatch):
    tentou = threading.Event()

    def popen_ausente(*_a, **_kw):
        tentou.set()
        raise FileNotFoundError(2, "journalctl")
    monkeypatch.setattr(log_feed.subprocess, "Popen", popen_ausente)
    feed = log_feed.RuntimeLogFeed(tmp_path / "state")
    return feed, tentou


def test_journalctl_ausente_aparece_como_erro_e_desconectado(tmp_path, monkeypatch):
    feed, tentou = _feed_com_journalctl_ausente(tmp_path, monkeypatch)
    feed.start()
    try:
        assert tentou.wait(5)
        prazo = threading.Event()
        for _ in range(200):                                        # o erro e gravado logo apos o Popen falhar
            if feed.status()["error"]:
                break
            prazo.wait(0.01)
        status = feed.status()
    finally:
        feed.close()
    assert status["connected"] is False and status["error"].startswith("FileNotFoundError")
    assert feed.thread is not None and not feed.thread.is_alive()


def _feed_com_thread_morta(tmp_path, monkeypatch):
    morreu = threading.Event()

    def run_que_morre(self):
        morreu.set()
        raise RuntimeError("falha fora do try do leitor do journal")
    monkeypatch.setattr(log_feed.RuntimeLogFeed, "_run", run_que_morre)
    monkeypatch.setattr(threading, "excepthook", lambda *_: None)   # a excecao da thread nao polui a saida
    feed = log_feed.RuntimeLogFeed(tmp_path / "state")
    feed.start()
    assert morreu.wait(5)
    feed.thread.join(5)
    assert not feed.thread.is_alive()
    return feed


def test_caracterizacao_thread_do_journal_morta_e_igual_a_nunca_iniciada(tmp_path, monkeypatch):
    feed = _feed_com_thread_morta(tmp_path, monkeypatch)
    try:
        status = feed.status()
    finally:
        feed.close()
    assert status["connected"] is False and status["error"] is None
    assert "thread" not in str(sorted(status)) and "alive" not in str(sorted(status))


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-261 (D10): `RuntimeLogFeed.status()` nao distingue thread morta de "
                                       "'ainda nao conectou' (nem `error` nem campo de vivacidade)")
def test_thread_do_journal_morta_aparece_na_telemetria(tmp_path, monkeypatch):
    feed = _feed_com_thread_morta(tmp_path, monkeypatch)
    try:
        status = feed.status()
    finally:
        feed.close()
    assert status["error"] or status.get("thread_alive") is False


def test_observe_do_processo_marca_journal_desconectado_quando_a_thread_morreu(tmp_path, monkeypatch):
    """No nivel do processo o buraco aparece, mas so como 'journal_disconnected' (sem dizer que a thread morreu)."""
    monkeypatch.setattr(observer, "_git", lambda *_: {"stdout": "", "available": True, "error": None})  # QHOS-AUD-275: _git devolve medicao
    from brain_service.process import StandaloneBrain
    brain = StandaloneBrain(configuracao(tmp_path, porta_fechada()))
    try:
        brain.log_feed.close()
        brain.log_feed = _feed_com_thread_morta(tmp_path / "outro", monkeypatch)
        telemetria = brain._observe()["telemetry"]
    finally:
        brain.log_feed.close()
    assert "log_collection: journal_disconnected" in telemetria["errors"]
    assert telemetria["log_collection"]["connected"] is False and telemetria["log_collection"]["error"] is None
