"""QHOS-AUD-284: a busca de fontes da investigacao nao pode segurar o lock que `snapshot()` (e por ele
`/api/assistant/state`) precisa. Feed falso, busca lenta simulada, nenhum modelo e nenhuma rede."""
import threading
import time

import pytest

from brain_service.config import load_config


class FeedFalso:
    def __init__(self, n):
        palavras = ["".join(chr(97 + (i // 26) % 26) + chr(97 + i % 26) + chr(97 + (i * 7) % 26)) for i in range(n)]
        self.rows = [{"cursor": i + 1, "timestamp": 1.7e9 + i, "level": "warning", "unit": "qhos2-coletor.service",
                      "pid": "1", "message": f"| WARNING | comp_{p} | aviso {p} distinto"} for i, p in enumerate(palavras)]

    def page(self, after, limit):
        rows = [r for r in self.rows if r["cursor"] > after][:limit]
        return {"records": rows, "next_cursor": rows[-1]["cursor"] if rows else after}


@pytest.fixture
def motor(tmp_path, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    from brain_service.investigation import InvestigationEngine
    engine = InvestigationEngine(load_config().target_root, tmp_path / "estado")
    chamadas = []

    def busca_lenta(query, max_results=5):
        chamadas.append(query)
        time.sleep(0.05)
        return []
    monkeypatch.setattr(engine.knowledge, "search", busca_lenta)
    return engine, chamadas


def _mede_snapshot(engine, inicio_do_trabalho, limite_s=5):
    """Tempo de `snapshot()` com o trabalho em andamento; numa thread daemon para um lock preso virar falha, nao bloqueio."""
    assert inicio_do_trabalho.wait(5)
    time.sleep(0.2)                      # o trabalho ja esta no meio da busca lenta
    saida = {}

    def alvo():
        t0 = time.monotonic()
        saida["snap"] = engine.snapshot()
        saida["demora"] = time.monotonic() - t0
    t = threading.Thread(target=alvo, daemon=True)
    t.start()
    t.join(limite_s)
    assert not t.is_alive(), f"snapshot() nao respondeu em {limite_s}s"
    return saida["demora"], saida["snap"]


def test_snapshot_responde_enquanto_observe_logs_busca_fontes(motor):
    engine, chamadas = motor
    feed = FeedFalso(30)                 # 30 assinaturas novas x 50 ms de busca = 1,5 s de trabalho
    comecou = threading.Event()
    resultado = {}

    def trabalho():
        comecou.set()
        resultado["novos"] = engine.observe_logs(feed)
    t = threading.Thread(target=trabalho)
    t.start()
    demora, snap = _mede_snapshot(engine, comecou)
    t.join(10)
    assert not t.is_alive()
    assert demora < 0.5, f"snapshot esperou {demora:.2f}s pelo lock da investigacao"
    assert len(resultado["novos"]) == 30 and len(chamadas) == 30
    final = engine.snapshot()
    assert len(final["automatic_incidents"]) == 30 and final["log_cursor"] == 30
    assert all(i["status"] == "investigating" and i["occurrences"] == 1 for i in final["automatic_incidents"])
    # a leitura feita no meio e coerente: so incidentes completos (o cursor avanca por linha, antes da busca, e pode
    # estar a frente do ultimo incidente inserido)
    assert all({"id", "component", "evidence", "hypotheses"} <= set(i) for i in snap["automatic_incidents"])
    assert 0 < snap["log_cursor"] <= 30


def test_snapshot_responde_enquanto_prepare_busca_fontes(motor):
    engine, _ = motor
    engine.knowledge.search = lambda query, max_results=5: (time.sleep(1.0), [])[1]
    comecou = threading.Event()
    pacote = {}

    def trabalho():
        comecou.set()
        pacote["p"] = engine.prepare("por que o coletor caiu", {"trader": {}})
    t = threading.Thread(target=trabalho)
    t.start()
    demora, _ = _mede_snapshot(engine, comecou)
    t.join(10)
    assert not t.is_alive() and pacote["p"]["schema"] == "brain-investigation-v1"
    assert demora < 0.5, f"snapshot esperou {demora:.2f}s pelo lock da investigacao"


def _disco_lento(engine, segundos_por_refresh):
    """Busca REAL (sem stub): cada leitura de fonte do indice demora, como um disco lento, de modo que um refresh
    inteiro leve ~`segundos_por_refresh`. Devolve a duracao medida de um refresh."""
    boundary = engine.knowledge.boundary
    original = boundary.read_source
    arquivos = max(1, engine.knowledge.snapshot()["indexed_files"])
    atraso = segundos_por_refresh / arquivos

    def lento(candidate):
        time.sleep(atraso)
        return original(candidate)
    boundary.read_source = lento
    t0 = time.monotonic()
    engine.knowledge.refresh()
    return time.monotonic() - t0


def test_snapshot_responde_enquanto_a_busca_real_reconstroi_o_indice_em_disco_lento(tmp_path, monkeypatch):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    from brain_service.investigation import InvestigationEngine
    engine = InvestigationEngine(load_config().target_root, tmp_path / "estado")
    refresh = _disco_lento(engine, 1.2)
    assert refresh >= 1.0, f"refresh levou so {refresh:.2f}s: o disco lento nao foi simulado"
    comecou = threading.Event()
    pacote = {}

    def trabalho():
        comecou.set()
        pacote["p"] = engine.prepare("coletor websockets origem recusada", {"trader": {}})
    t = threading.Thread(target=trabalho)
    t.start()
    demora, snap = _mede_snapshot(engine, comecou)
    t.join(15)
    assert not t.is_alive() and pacote["p"]["schema"] == "brain-investigation-v1"
    assert demora < 0.5, f"snapshot esperou {demora:.2f}s pela reconstrucao do indice (KnowledgeIndex._lock)"
    assert snap["knowledge"]["indexed_files"] >= 1 and snap["knowledge"]["available"] is True
    # resultado real da busca: referencias com hash do indice recem-construido
    fontes = [e for e in pacote["p"]["evidence"] if e["kind"] == "source"]
    assert fontes and all(e["sha256"] and e["line_ref"] for e in fontes)


def test_observe_logs_repetido_conta_ocorrencias_sem_nova_busca(motor):
    engine, chamadas = motor
    feed = FeedFalso(3)
    assert len(engine.observe_logs(feed)) == 3 and len(chamadas) == 3
    feed.rows.append({**feed.rows[0], "cursor": 4})
    assert engine.observe_logs(feed) == [] and len(chamadas) == 3
    snap = engine.snapshot()
    assert snap["log_cursor"] == 4 and [i["occurrences"] for i in snap["automatic_incidents"]] == [2, 1, 1]
