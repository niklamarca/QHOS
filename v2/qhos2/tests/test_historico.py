"""Integracao, tarefa 10: candles das capturas, cache conferido pelo SHA-256 e janela de teste escondida ate 21/11."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qhos2 import eventos as ev
from qhos2 import historico as hi
from qhos2 import sintetico as si
from qhos2.gravador import Gravador

NY, BR = ZoneInfo("America/New_York"), ZoneInfo("America/Sao_Paulo")
IDENTIDADE = {"system_name": si.SISTEMA, "requested_symbol": "ESZ6", "exchange": "CME"}


def test_cortes_da_janela_de_teste_pelos_fusos():
    # recalculados de forma independente: um erro de ano ou de fuso aqui libera a janela de teste
    assert hi.OCULTO_DE_S == int(datetime(2026, 11, 9, tzinfo=NY).timestamp())
    assert hi.OCULTO_ATE_S == int(datetime(2026, 11, 21, tzinfo=NY).timestamp())
    assert hi.LIBERADO_EM_S == int(datetime(2026, 11, 21, tzinfo=BR).timestamp())


def negocio(segundo, preco, qtd=1, **extra):
    return ev.Evento(**{"indice": 0, "posicao": 0, "recebido_ns": segundo * 10 ** 9 + 75_000_000, "tipo": ev.NEGOCIO,
                        "lado": ev.COMPRA, "preco": preco, "quantidade": qtd, "carimbo_ns": segundo * 10 ** 9, **extra})


def test_barras_de_um_minuto_com_ohlc_e_volume():
    b = hi.Barras().eventos([negocio(600, 100, 2), negocio(610, 103), negocio(620, 99, 5), negocio(659, 101),
                             negocio(660, 104), negocio(661, 50, retrato=True), negocio(662, 0),
                             negocio(663, 102, qtd=0), ev.Evento(0, 0, 1, ev.NOVO, ev.COMPRA, 100, 1, "x", 1)])
    assert b.minutos == {600: [100, 103, 99, 101, 9], 660: [104, 104, 104, 104, 1]}
    sem_carimbo = hi.Barras().eventos([negocio(600, 100, carimbo_ns=None, recebido_ns=665 * 10 ** 9)])
    assert list(sem_carimbo.minutos) == [660]


def gravar(raiz, nome, cenario, identidade=IDENTIDADE):
    gravador = Gravador(raiz, identidade=identidade, captura_id=nome)
    for i, (template, dados) in enumerate(cenario.quadros):
        gravador(dados, template, si.INICIO_NS + i * si.PASSO_NS)
    gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": 0.25}}, fechamento_normal=True)
    return gravador.pasta


def test_cache_criado_reusado_e_refeito_se_a_captura_mudar(tmp_path, monkeypatch):
    pasta = gravar(tmp_path / "capturas", "c1", si.gerar(4, 300))
    cache = tmp_path / "cache"
    # contador na fronteira publica da decodificacao: todo evento passa por `eventos.normalizar`, seja qual for o jeito
    # de chegar a `eventos_da_captura` (atributo do modulo ou import direto); conta, nao proibe (QHOS-AUD-113)
    decodificados, normalizar = [], ev.normalizar

    def contar(*args, **kwargs):
        decodificados.append(args[0])
        return normalizar(*args, **kwargs)
    monkeypatch.setattr(ev, "normalizar", contar)
    primeira = hi.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME")
    assert decodificados                                          # sem cache a captura e decodificada: o contador ve
    esperado = hi.Barras().eventos(ev.eventos_da_captura(pasta)).minutos
    assert primeira.minutos == esperado and primeira.minutos
    arquivo = cache / "c1.json"
    corpo = json.loads(arquivo.read_text())
    assert corpo["esquema"] == hi.ESQUEMA and corpo["sha256"] == json.loads((pasta / "manifest.json").read_text())["sha256"]
    # reusado: uma barra marcada no cache (mesmo sha256) e o que volta; se a captura fosse relida, voltaria `esperado`
    # (prova pelo resultado, nao pelo nome da funcao que nao foi chamada: QHOS-AUD-113)
    marcado = json.loads(json.dumps(corpo))
    minuto, volume = marcado["barras"][0][0], marcado["barras"][0][-1] + 1000
    marcado["barras"][0][-1] = volume
    arquivo.write_text(json.dumps(marcado))
    decodificados.clear()
    do_cache = hi.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME").minutos
    assert do_cache != esperado and do_cache[minuto][-1] == volume and json.loads(arquivo.read_text()) == marcado
    assert decodificados == []                                    # acerto de cache: nenhum quadro decodificado
    corpo["sha256"] = "0" * 64                                   # cache de outra versao da captura: refeito
    arquivo.write_text(json.dumps(corpo))
    assert hi.barras_da_captura(pasta, cache, tick=0.25, simbolo="ESZ6", bolsa="CME").minutos == esperado
    assert decodificados
    assert json.loads(arquivo.read_text())["sha256"] != "0" * 64


def test_captura_de_outro_contrato_ou_nao_verificada(tmp_path):
    raiz = tmp_path / "capturas"
    gravar(raiz, "ouro", si.gerar(4, 50), {**IDENTIDADE, "requested_symbol": "GCZ6", "exchange": "COMEX"})
    estragada = gravar(raiz, "estragada", si.gerar(4, 50))
    arquivo = estragada / "market_frames.jsonl"
    arquivo.write_bytes(arquivo.read_bytes()[:-10] + b"x" * 10)
    andamento = Gravador(raiz, identidade=IDENTIDADE, captura_id="andamento")
    andamento(si.gerar(1, 0).quadros[0][1], 116, 1)
    boa = gravar(raiz, "boa", si.gerar(4, 50))
    h = hi.Historico(raiz, tmp_path / "cache", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h.preparar()
    assert set(h.prontas) == {"boa"} and set(h.recusadas) == {"estragada"}
    assert h.recusadas["estragada"] == "captura_nao_verificada"
    h2 = hi.Historico(raiz, tmp_path / "cache2", tick=0.25, simbolo="ESZ6", bolsa="CME")
    h2.preparar(ignorar="boa")
    assert boa.name not in h2.prontas


def historico(barras_por_captura, agora):
    h = hi.Historico("/nao/existe", "/nao/existe", tick=0.25, simbolo="ESZ6", bolsa="CME", relogio=lambda: agora)
    for nome, minutos in barras_por_captura.items():
        b = hi.Barras()
        b.minutos = {m: list(v) for m, v in minutos.items()}
        h.prontas[nome] = b
    return h


def test_agrega_nos_quadros_do_terminal_com_as_regras_da_ponte():
    base = 1_793_000_000 // 86400 * 86400                         # meia-noite UTC, fora da janela
    minutos = {base + 60 * i: [100 + i, 102 + i, 99 + i, 101 + i, 1 + i] for i in range(0, 600, 7)}
    h = historico({"a": minutos}, agora=base + 86400 * 3)
    for quadro, segundos in hi.QUADROS.items():
        candles = h.candles(quadro, 10000)
        tempos = [c["time"] for c in candles]
        assert tempos == sorted(set(tempos)) and all(t % segundos == 0 for t in tempos)
        assert all(c["high"] >= max(c["open"], c["close"], c["low"]) and c["low"] <= min(c["open"], c["close"])
                   and c["volume"] > 0 for c in candles)
        assert sum(c["volume"] for c in candles) == sum(v[4] for v in minutos.values())
    # primeiro candle de 5 min: so o minuto 0 (o proximo minuto com negocio e o 7, no segundo candle)
    assert h.candles("5m", 10000)[:2] == [
        {"time": base, "open": 25.0, "high": 25.5, "low": 24.75, "close": 25.25, "volume": 1},
        {"time": base + 300, "open": 26.75, "high": 27.25, "low": 26.5, "close": 27.0, "volume": 8}]
    assert len(h.candles("1m", 3)) == 3 and h.candles("1m", 3) == h.candles("1m", 10000)[-3:]


def test_futuro_nunca_vai_e_limites_invalidos_sao_recusados():
    h = historico({"a": {1_000_000_020: [4, 4, 4, 4, 1], 1_000_000_200: [4, 4, 4, 4, 1]}}, agora=1_000_000_100)
    assert [c["time"] for c in h.candles("1m", 100)] == [1_000_000_020 // 60 * 60]
    for quadro, limite in (("2m", 10), ("1m", 0), ("1m", 10001), ("1m", "10")):
        with pytest.raises(hi.HistoricoError):
            h.candles(quadro, limite)


def test_janela_de_teste_escondida_ate_21_11():
    antes = hi.OCULTO_DE_S - 3600                                  # 8/11, fora da janela
    dentro = hi.OCULTO_DE_S + 86400 * 3
    depois = hi.OCULTO_ATE_S + 3600
    minutos = {antes: [4, 4, 4, 4, 1], dentro: [8, 8, 8, 8, 1], depois: [12, 12, 12, 12, 1]}
    durante = historico({"a": minutos}, agora=hi.LIBERADO_EM_S - 1)
    assert [c["time"] for c in durante.candles("1m", 100)] == [antes]
    liberado = historico({"a": minutos}, agora=hi.OCULTO_ATE_S + 7200)
    assert [c["time"] for c in liberado.candles("1m", 100)] == [antes, dentro, depois]
    viva = hi.Barras()
    viva.minutos = {dentro + 60: [9, 9, 9, 9, 1]}                   # a captura seguida tambem obedece
    assert [c["time"] for c in durante.candles("1m", 100, viva)] == [antes]


def test_mesmo_minuto_em_duas_capturas_recusa_agregacao_ambigua():
    h = historico({"segunda": {120: [20, 21, 19, 21, 1], 180: [21, 21, 21, 21, 1]},
                   "primeira": {60: [10, 10, 10, 10, 1], 120: [30, 30, 5, 6, 1]}}, agora=10 ** 9)
    with pytest.raises(hi.HistoricoError, match="capturas_sobrepostas_no_mesmo_minuto"):
        h.candles("1m", 100)
