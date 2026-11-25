"""Etapa 3, checklist 1: movimento do meio em 10 s sem sinal, uma janela por vez, e o custo contra ele."""
import calendar
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from qhos2 import custo as cu
from qhos2 import desequilibrio as de
from qhos2 import eventos as ev

S = 10 ** 9


def utc_ns(texto):
    return calendar.timegm(datetime.strptime(texto, "%Y-%m-%d %H:%M:%S").timetuple()) * S


T0 = utc_ns("2026-10-29 14:00:00")             # 10:00 de Nova York (EDT), dia sem FOMC, dentro da janela


def est(t, compra=100, venda=101, q=True):
    return de.Estado(0, t, q, compra, venda, 10, 10, 0.0 if compra and venda else None)


@pytest.fixture
def capturas(monkeypatch):
    mapa = {}
    # chave = nome da pasta pelo separador da plataforma (no Windows o caminho vem com `\`)
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta, niveis=de.NIVEIS: iter(mapa[Path(pasta).name]))
    return mapa


def test_uma_janela_entrada_em_0_65s_saida_em_10_65s_e_quadro_exato_e_o_vigente(capturas):
    capturas["a"] = [est(T0), est(T0 + S // 2), est(T0 + S, 102, 103),
                     est(T0 + int(10.65 * S), 104, 106),                  # exatamente no instante da saida: vigente
                     est(T0 + 11 * S, 104, 105), est(T0 + 12 * S)]
    r = cu.medir(["/x/a"])
    assert (r["amostras"], r["quadros_que_contam"], r["capturas"], r["recusadas"]) == (1, 6, ["a"], [])
    assert r["histograma_meios_ticks"] == {"9": 1}                      # meio 100,5 -> 105 = 4,5 ticks
    assert (r["movimento_medio_abs_ticks"], r["movimento_medio_ticks"], r["desvio_ticks"], r["parado"]) == (4.5, 4.5, 0.0, 0.0)
    assert r["custo_spread_medio_ticks"] == 1.5                         # (1 + 2) / 2
    assert r["blocos"] == {"2026-10-29/120": [1, 4.5, 20.25, 4.5]} and r["por_dia"] == {"2026-10-29": 1}
    assert r["descartados"] == {"janela_sem_fim": 1}                    # a janela aberta em 11 s nao fecha
    assert (r["entrada_ns"], r["horizonte_ns"], r["fomc"]) == (650_000_000, 10 * S, ["2026-10-28", "2026-12-09"])


def test_janelas_nao_se_sobrepoem(capturas):
    capturas["a"] = [est(T0 + i * S, 100 + (i % 3), 101 + (i % 3)) for i in range(61)]
    r = cu.medir(["/x/a"])
    assert r["amostras"] == 5 and r["descartados"] == {"janela_sem_fim": 1}    # janelas em 0, 11, 22, 33, 44 s
    assert sum(r["histograma_meios_ticks"].values()) == 5 and r["quadros_que_contam"] == 61


def test_livro_nao_qualificado_no_instante_descarta_a_janela(capturas):
    capturas["a"] = [est(T0), est(T0 + S // 2, q=False), est(T0 + S), est(T0 + 2 * S),
                     est(T0 + int(11.5 * S), q=False), est(T0 + 13 * S)]
    r = cu.medir(["/x/a"])
    assert r["amostras"] == 0
    assert r["descartados"] == {"livro_nao_qualificado_no_instante": 2, "nao_qualificado": 2, "janela_sem_fim": 1}


@pytest.mark.parametrize("utc,motivo", [
    ("2026-11-10 15:00:00", "fora_da_janela"), ("2026-10-29 12:00:00", "fora_do_pregao"),
    ("2026-10-28 18:00:00", "fomc"), ("2026-10-31 15:00:00", "fora_do_pregao"),
])
def test_quadro_que_nao_conta_nao_abre_janela(capturas, utc, motivo):
    capturas["a"] = [est(utc_ns(utc)), est(utc_ns(utc) + S)]
    r = cu.medir(["/x/a"])
    assert r["amostras"] == 0 and r["descartados"] == {motivo: 2}
    capturas["b"] = [est(T0, compra=None), est(T0 + S, q=False)]
    assert cu.medir(["/x/b"])["descartados"] == {"sem_os_dois_lados": 1, "nao_qualificado": 1}


def test_lacuna_corta_a_captura_e_a_janela_aberta(capturas):
    capturas["a"] = [est(T0), est(T0 + S), est(T0 + 2 * S), est(T0 + 63 * S), est(T0 + 64 * S)]
    r = cu.medir(["/x/a"])
    assert r["amostras"] == 0 and r["quadros_que_contam"] == 3
    assert r["descartados"] == {"janela_cortada_pela_lacuna": 1, "depois_da_lacuna": 2}


def test_captura_recusada_nao_deixa_nada_e_estados_fora_de_ordem_recusam(capturas):
    capturas["boa"] = [est(T0), est(T0 + S), est(T0 + 11 * S), est(T0 + 12 * S)]
    capturas["ruim"] = [est(T0), est(T0 + S), est(T0 + S - 1)]
    r = cu.medir(["/x/ruim", "/x/boa"])
    assert r["recusadas"] == ["ruim: estados_fora_de_ordem"] and r["capturas"] == ["boa"] and r["amostras"] == 1
    with pytest.raises(cu.CustoError, match="janela_invalida"):
        cu.medir([], janela=(date(2026, 11, 6), date(2026, 10, 26)))
    with pytest.raises(cu.CustoError, match="fomc_invalido"):
        cu.medir([], fomc=["2026-10-28"])


def test_acerto_necessario_e_checklist():
    assert cu.acerto_necessario(2.0, 10.0) == 0.6 and cu.acerto_necessario(0.0, 1.0) == 0.5
    assert cu.acerto_necessario(1.0, 0.0) is None
    with pytest.raises(cu.CustoError):
        cu.acerto_necessario(float("nan"), 1.0)
    medicao = {"amostras": 100, "movimento_medio_abs_ticks": 10.0, "custo_spread_medio_ticks": 1.0}
    c = cu.checklist(medicao, 12.5)                                     # 1 tick de taxa + 1 tick de spread
    assert (c["custo_total_ticks"], c["acerto_necessario"], c["movimento_paga_o_custo"], c["veredito"]) == (2.0, 0.6, True, "SEGUE")
    c = cu.checklist({**medicao, "movimento_medio_abs_ticks": 2.0}, 12.5)
    assert (c["acerto_necessario"], c["movimento_paga_o_custo"], c["veredito"]) == (1.0, False, "ABANDONO_MICROESTRUTURA")
    assert cu.checklist({**medicao, "movimento_medio_abs_ticks": 0.0}, 0.0)["veredito"] == "ABANDONO_MICROESTRUTURA"
    assert cu.checklist({**medicao, "amostras": 0}, 0.0)["veredito"] == "SEM_AMOSTRA"
    with pytest.raises(cu.CustoError, match="taxa_invalida"):
        cu.checklist(medicao, -1.0)
    with pytest.raises(cu.CustoError, match="medicao_invalida"):
        cu.checklist({}, 1.0)


def test_cli_sem_taxa_espera_e_com_taxa_responde(tmp_path, capturas, capsys):
    capturas["a"] = [est(T0), est(T0 + S, 100, 102), est(T0 + 11 * S, 110, 111), est(T0 + 12 * S)]
    (tmp_path / "a").mkdir()
    assert cu.main([str(tmp_path / "a"), str(tmp_path / "nao_e_pasta")]) == 0
    saida = capsys.readouterr().out
    assert "amostras=1" in saida and "aguarda a taxa" in saida
    assert cu.main([str(tmp_path / "a"), "--taxa-usd", "12.5", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["medicao"]["esquema"] == "qhos2.custo.v1" and r["checklist"]["esquema"] == "qhos2.checklist1.v1"
    assert r["checklist"]["custo_total_ticks"] == 2.5 and r["checklist"]["movimento_medio_abs_ticks"] == 0.5
    assert r["checklist"]["veredito"] == "ABANDONO_MICROESTRUTURA"       # 0,5 tick de movimento contra 2,5 de custo
    capturas["r"] = [est(T0), est(T0 - 1)]
    (tmp_path / "r").mkdir()
    assert cu.main([str(tmp_path / "r")]) == 2
    assert "recusadas=['r: estados_fora_de_ordem']" in capsys.readouterr().out


# ---- QHOS-AUD-251: pinos de unidades (tick/US$) e da lacuna de exatos 60 s ----------------------------------

def test_unidades_tick_usd_consistentes_entre_modulos():
    from qhos2 import avaliador as av
    from qhos2.laboratorio import monetary_move
    assert av.TICK_USD == 12.5 == 0.25 * 50                                       # ES: 0,25 ponto x US$ 50
    assert monetary_move(.25, 1, {"point_value": 50, "tick_size": .25, "currency": "USD"})["amount"] == 12.5
    medicao = {"amostras": 10, "movimento_medio_abs_ticks": 4.0, "custo_spread_medio_ticks": 1.0}
    c = cu.checklist(medicao, 12.5)
    assert (c["taxa_ticks"], c["custo_total_ticks"], c["acerto_necessario"]) == (1.0, 2.0, 0.75)   # 1/2 + 2/(2*4)
    assert cu.checklist(medicao, 25.0)["taxa_ticks"] == 2.0 and cu.checklist(medicao, 0.0)["custo_total_ticks"] == 1.0
    op = av.Operacao(ev.COMPRA, T0, T0, 100, T0 + 10 * S, 103, 3, None)
    assert (av.liquido(op, 12.5), av.liquido(op, 0.0), av.liquido(op, 6.25)) == (2.0, 3.0, 2.5)


def test_lacuna_de_exatos_60s_nao_corta_a_medicao_e_60s_mais_1ns_corta(capturas):
    # sem corte: a janela aberta em T0 fecha no quadro de 60 s (vigente em 10,65 s e o de T0), a aberta em 60 s fecha
    # no de 71 s (vigente em 70,65 s e o de 60 s) e a aberta em 71 s fica sem fim: 2 amostras
    capturas["exatos"] = [est(T0), est(T0 + 60 * S), est(T0 + 71 * S), est(T0 + 72 * S)]
    r = cu.medir(["/x/exatos"])
    assert (r["quadros_que_contam"], r["amostras"], r["descartados"]) == (4, 2, {"janela_sem_fim": 1})
    capturas["cortada"] = [est(T0), est(T0 + 60 * S + 1), est(T0 + 71 * S + 1), est(T0 + 72 * S + 1)]
    r = cu.medir(["/x/cortada"])
    assert (r["quadros_que_contam"], r["amostras"]) == (1, 0)
    assert r["descartados"] == {"janela_cortada_pela_lacuna": 1, "depois_da_lacuna": 3}
    assert de.LACUNA_MAX_NS == 60 * S
