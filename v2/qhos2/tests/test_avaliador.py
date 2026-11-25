"""Etapa 3: o avaliador aplica as regras registradas da hipotese 1 sobre estados do livro montados a mao."""
import calendar
import json
import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from qhos2 import avaliador as av
from qhos2 import desequilibrio as de
from qhos2 import eventos as ev

PAR = av.Parametros(p5=-0.5, p95=0.5, taxa_usd=0.0, alfa=0.05)
S = 10 ** 9


def ny(dia, hora):
    """Instante em ns a partir da data e hora de Nova York (novembro de 2026: EST, UTC-5)."""
    h, m, s = (float(x) for x in hora.split(":"))
    base = calendar.timegm(datetime(2026, 11, dia, 5).timetuple()) * S   # 00:00 de Nova York = 05:00 UTC
    return base + int(round((h * 3600 + m * 60 + s) * S))


def est(t, d, compra=100, venda=101, q=True):
    return de.Estado(0, t, q, compra, venda, 10, 10, d)


def pregao(dia, extras=(), *, inicio="9:20:00", fim="16:05:00", passo=30, sem=(), nao_qualificado=lambda t: False):
    """Um pregao inteiro, um quadro a cada `passo` s com D neutro, mais os quadros `extras`."""
    t0, t1 = ny(dia, inicio), ny(dia, fim)
    base = [est(t, 0.0, q=not nao_qualificado(t)) for t in range(t0, t1 + 1, passo * S)
            if not any(a <= t < b for a, b in sem)]
    return sorted(base + list(extras), key=lambda e: e.recebido_ns)


# ---- regras do sinal e da execucao ------------------------------------------------------------------

def test_rearme_compra_e_precos_do_livro_vigente():
    t = ny(10, "10:00:00")
    estados = [est(t, 0.9),                                       # fora sem rearme
               est(t + 1 * S, 0.0),                               # rearma
               est(t + 2 * S, 0.9),                               # sinal de compra
               est(t + int(2.4 * S), 0.9),                        # ignorado: entrada pendente
               est(t + int(2.7 * S), 0.0, venda=103),             # depois do alvo da entrada (2,65 s)
               est(t + int(12.6 * S), 0.0, compra=106, venda=107),  # livro vigente no alvo da saida (12,65 s)
               est(t + int(12.7 * S), 0.9, compra=90, venda=91),  # fecha antes; fora sem rearme
               est(t + 13 * S, 0.9)]                              # depois da saida precisa rearmar
    dia = av.simular(estados, PAR)
    assert len(dia.operacoes) == 1
    o = dia.operacoes[0]
    assert (o.lado, o.preco_entrada, o.preco_saida, o.ticks) == (ev.COMPRA, 101, 106, 5)
    assert (o.sinal_ns, o.entrada_ns, o.saida_ns) == (t + 2 * S, t + int(2.4 * S), t + int(12.6 * S))
    assert o.acerto_direcao is True                               # meio 100,5 -> 101,5 em 10 s
    assert dia.contagens == {"fora_sem_rearme": 3, "sinais": 1, "sinal_ignorado_posicao_aberta": 1}


def test_venda_entra_na_compra_e_sai_na_venda():
    t = ny(10, "11:00:00")
    estados = [est(t, 0.0), est(t + S, -0.9, compra=200, venda=201), est(t + 2 * S, 0.0, compra=198, venda=199),
               est(t + int(11.6 * S), 0.0, compra=195, venda=196), est(t + 12 * S, 0.0)]   # saida em 11,65 s
    o, = av.simular(estados, PAR).operacoes
    assert (o.lado, o.preco_entrada, o.preco_saida, o.ticks) == (ev.VENDA, 200, 196, 4)
    assert o.acerto_direcao is True


def test_limiar_e_estrito_e_igual_ao_limiar_rearma():
    t = ny(10, "10:00:00")
    estados = [est(t, 0.0), est(t + S, 0.5), est(t + 2 * S, -0.5), est(t + 3 * S, 0.0), est(t + 15 * S, 0.0)]
    dia = av.simular(estados, PAR)
    assert dia.operacoes == () and "sinais" not in dia.contagens


def test_quadro_exatamente_no_instante_do_preenchimento_e_o_livro_vigente():
    t = ny(10, "10:00:00")
    estados = [est(t, 0.0), est(t + S, 0.9), est(t + int(1.65 * S), 0.0, venda=104), est(t + 2 * S, 0.0, venda=109),
               est(t + int(11.65 * S), 0.0, compra=107), est(t + 12 * S, 0.0, compra=50)]
    o, = av.simular(estados, PAR).operacoes
    assert (o.preco_entrada, o.preco_saida, o.ticks) == (104, 107, 3)


def test_entrada_sem_livro_qualificado_e_cancelada_e_exige_rearme():
    t = ny(10, "10:00:00")
    estados = [est(t, 0.0), est(t + S, 0.9), est(t + int(1.6 * S), 0.0, q=False), est(t + 2 * S, 0.9),
               est(t + 3 * S, 0.0), est(t + 4 * S, 0.0)]
    dia = av.simular(estados, PAR)
    assert dia.operacoes == ()
    assert dia.contagens == {"entrada_cancelada_livro_nao_qualificado": 1, "fora_sem_rearme": 1, "sinais": 1}


def test_saida_sem_livro_qualificado_espera_o_proximo_qualificado():
    t = ny(10, "10:00:00")
    estados = [est(t, 0.0), est(t + S, 0.9), est(t + 2 * S, 0.0),
               est(t + int(11.6 * S), 0.0, compra=120, q=False), est(t + 12 * S, 0.0, compra=130, q=False),
               est(t + 14 * S, 0.0, compra=104, venda=105), est(t + 15 * S, 0.0)]
    o, = av.simular(estados, PAR).operacoes
    assert (o.preco_saida, o.saida_ns, o.ticks) == (104, t + 14 * S, 3)


def test_faixa_do_fomc_nao_rearma_nem_dispara():
    par = PAR._replace(fomc=(date(2026, 11, 10),))
    t = ny(10, "13:55:00")
    estados = [est(t - 10 * S, 0.0), est(t, 0.9), est(t + S, 0.0), est(t + 2 * S, 0.9), est(t + 3 * S, -0.9)]
    dia = av.simular(estados, par)
    assert dia.operacoes == () and dia.contagens == {}
    assert av.simular(estados, PAR).contagens["sinais"] == 1     # sem FOMC o mesmo trecho dispara


def test_sinal_cuja_saida_passaria_das_16h_e_ignorado():
    t = ny(10, "15:59:40")
    estados = [est(t, 0.0), est(t + 10 * S, 0.9), est(t + 30 * S, 0.0)]
    dia = av.simular(estados, PAR)
    assert dia.operacoes == () and dia.contagens == {"sinal_perto_do_fechamento": 1}


def test_operacao_sem_fim_e_descartada_e_direcao_sem_movimento_e_none():
    t = ny(10, "10:00:00")
    sem_fim = av.simular([est(t, 0.0), est(t + S, 0.9), est(t + 2 * S, 0.0)], PAR)
    assert sem_fim.operacoes == () and sem_fim.contagens["operacao_sem_fim"] == 1
    parado = av.simular([est(t, 0.0), est(t + S, 0.9), est(t + 12 * S, 0.0), est(t + 13 * S, 0.0)], PAR)
    assert parado.operacoes[0].acerto_direcao is None and parado.operacoes[0].ticks == -1


# ---- recuo do relogio (N5, 28/10) --------------------------------------------------------------------

def test_recuo_pequeno_do_relogio_e_preso_no_ultimo_instante_e_contado():
    t = ny(10, "10:00:00")
    estados = [est(t, 0.0), est(t + S, 0.9), est(t + int(1.6 * S), 0.0, venda=103),
               est(t + int(1.6 * S) - 1, 0.0, venda=104),                 # recua 1 ns: vale em 1,6 s
               est(t + 2 * S, 0.0), est(t + int(11.6 * S), 0.0, compra=106), est(t + 12 * S, 0.0)]
    dia = av.simular(estados, PAR)
    o, = dia.operacoes
    assert (o.preco_entrada, o.entrada_ns, o.preco_saida) == (104, t + int(1.6 * S), 106)
    assert dia.contagens["recuos_de_relogio"] == 1 and dia.contagens["recuo_maior_ns"] == 1


@pytest.mark.parametrize("recuos,maior_ns,motivo", [
    (100, 100_000_000, "completo"),
    (101, 1, "recuo_de_relogio_acima_do_limite"),
    (1, 100_000_001, "recuo_de_relogio_acima_do_limite"),
])
def test_limite_declarado_de_recuos_recusa_o_dia(recuos, maior_ns, motivo):
    base, t = pregao(10), ny(10, "10:00:00")
    i = next(k for k, e in enumerate(base) if e.recebido_ns == t)
    estados = base[:i + 1] + [est(t - maior_ns, 0.0) for _ in range(recuos)] + base[i + 1:]
    dia = av.simular(estados, PAR)
    assert (dia.motivo, dia.contagens["recuos_de_relogio"], dia.contagens["recuo_maior_ns"]) == (motivo, recuos, maior_ns)
    assert (av.RECUO_MAX_NS, av.RECUOS_MAX_POR_PREGAO) == (100_000_000, 100)


# ---- pregao completo ---------------------------------------------------------------------------------

@pytest.mark.parametrize("ajuste,motivo", [
    ({}, "completo"),
    ({"inicio": "9:31:00"}, "comecou_depois_da_abertura"),
    ({"fim": "15:59:00"}, "terminou_antes_do_fechamento"),
    ({"sem": [(ny(10, "11:00:00"), ny(10, "11:01:30"))]}, "completo_ate_lacuna"),
    ({"nao_qualificado": lambda t: t % (10 * S) < S}, "livro_qualificado_abaixo_de_95pct"),
])
def test_pregao_completo(ajuste, motivo):
    dia = av.simular(pregao(10, **ajuste), PAR)
    assert (dia.motivo, dia.completo, dia.data) == (motivo, motivo == "completo", date(2026, 11, 10))


# ---- lacuna de entrega: o dia vale ate a lacuna (N6, 28/10) ------------------------------------------

@pytest.mark.parametrize("sem,motivo", [
    (("9:29:00", "9:31:00"), "lacuna_antes_da_abertura"),
    (("15:59:00", "16:01:00"), "completo_ate_lacuna"),
    (("9:05:00", "9:20:00"), "completo"),                     # nao toca o pregao
])
def test_lacuna_nas_bordas_do_pregao(sem, motivo):
    dia = av.simular(pregao(10, inicio="9:00:00", sem=[(ny(10, sem[0]), ny(10, sem[1]))]), PAR)
    assert dia.motivo == motivo
    assert (dia.corte_ns is None) == (motivo == "completo")


def test_lacuna_de_exatos_60s_nao_corta():
    dia = av.simular(pregao(10, sem=[(ny(10, "11:00:00"), ny(10, "11:00:30"))]), PAR)   # 10:59:30 -> 11:00:30
    assert dia.motivo == "completo" and dia.corte_ns is None


def test_depois_da_lacuna_nada_conta_e_a_operacao_aberta_e_descartada():
    t = ny(10, "10:00:00")
    extras = [est(t + S, 0.9), est(t + 2 * S, 0.0), est(t + int(11.6 * S), 0.0, compra=103), est(t + 12 * S, 0.0),
              est(ny(10, "10:59:55"), 0.9),                                   # sinal cuja entrada cai na lacuna
              est(ny(10, "12:00:01"), 0.9), est(ny(10, "12:00:02"), 0.0),     # depois da lacuna: nao conta
              est(ny(10, "12:00:12"), 0.0, compra=110), est(ny(10, "12:00:13"), 0.0)]
    estados = pregao(10, extras, sem=[(ny(10, "11:00:00"), ny(10, "11:01:30"))])
    dia = av.simular(estados, PAR)
    assert dia.motivo == "completo_ate_lacuna" and dia.corte_ns == ny(10, "10:59:55")
    assert len(dia.operacoes) == 1 and dia.operacoes[0].preco_saida == 103
    assert dia.contagens["sinais"] == 2 and dia.contagens["operacao_sem_fim"] == 1
    assert dia.contagens["quadros_depois_da_lacuna"] == sum(1 for e in estados if e.recebido_ns > dia.corte_ns)
    assert av.LACUNA_MAX_NS is de.LACUNA_MAX_NS


# ---- silencio final: a entrega para antes das 16h e a sessao segue viva (decisao do autor, 29/10) -------

@pytest.mark.parametrize("fim,sessao,motivo", [
    ("15:00:00", "16:13:00", "completo_ate_silencio_final"),   # sessao encerrada normalmente depois das 16h
    ("15:00:00", "16:00:00", "completo_ate_silencio_final"),   # exatamente as 16h ja vale
    ("15:00:00", "15:30:00", "terminou_antes_do_fechamento"),  # sessao parada antes das 16h: o dia continua perdido
    ("15:00:00", None, "terminou_antes_do_fechamento"),        # sem fim de sessao confiavel: regra antiga
    ("15:00:00", "14:00:00", "terminou_antes_do_fechamento"),  # fim da sessao antes do ultimo quadro: incoerente
    ("16:05:00", "16:13:00", "completo"),                      # quadros depois das 16h: nada muda
    ("9:25:00", "16:13:00", "lacuna_antes_da_abertura"),       # silencio ja antes da abertura
])
def test_silencio_final_vale_ate_o_ultimo_quadro_com_a_sessao_viva_depois_das_16h(fim, sessao, motivo):
    estados = pregao(10, fim=fim)
    dia = av.simular(estados, PAR, fim_sessao_ns=None if sessao is None else ny(10, sessao))
    assert (dia.motivo, dia.completo) == (motivo, motivo == "completo")
    corta = motivo in ("completo_ate_silencio_final", "lacuna_antes_da_abertura")
    assert dia.corte_ns == (estados[-1].recebido_ns if corta else None)
    assert "quadros_depois_da_lacuna" not in dia.contagens


def test_silencio_final_descarta_a_operacao_aberta_e_a_lacuna_de_dentro_corta_antes():
    t = ny(10, "14:00:00")
    extras = [est(t + S, 0.9), est(t + 2 * S, 0.0), est(t + int(11.6 * S), 0.0, compra=103), est(t + 12 * S, 0.0),
              est(ny(10, "14:59:59"), 0.9)]                             # sinal cuja entrada cairia depois do ultimo quadro
    dia = av.simular(pregao(10, extras, fim="15:00:00"), PAR, fim_sessao_ns=ny(10, "16:13:00"))
    assert dia.motivo == "completo_ate_silencio_final" and dia.corte_ns == ny(10, "15:00:00")
    assert len(dia.operacoes) == 1 and dia.operacoes[0].preco_saida == 103
    assert dia.contagens["sinais"] == 2 and dia.contagens["operacao_sem_fim"] == 1
    lacuna = av.simular(pregao(10, fim="15:00:00", sem=[(ny(10, "11:00:00"), ny(10, "11:01:30"))]), PAR,
                        fim_sessao_ns=ny(10, "16:13:00"))
    assert lacuna.motivo == "completo_ate_lacuna" and lacuna.corte_ns == ny(10, "10:59:30")
    baixo = av.simular(pregao(10, fim="15:00:00", nao_qualificado=lambda t: t > ny(10, "14:30:00")), PAR,
                       fim_sessao_ns=ny(10, "16:13:00"))
    assert baixo.motivo == "livro_qualificado_abaixo_de_95pct"     # os 95 % sao contados ate o ultimo quadro
    with pytest.raises(de.DesequilibrioError):
        av.simular(pregao(10), PAR, fim_sessao_ns=1.5)


def test_fim_da_sessao_vem_do_manifesto_so_se_ela_foi_encerrada_normalmente(tmp_path):
    def manifesto(nome, **campos):
        pasta = tmp_path / nome
        pasta.mkdir()
        (pasta / "manifest.json").write_text(json.dumps(campos))
        return pasta

    assert av.fim_da_sessao_ns(manifesto("ok", transport_closed_normally=True,
                                         status={"criado_em": "2026-11-10T21:13:00+00:00"})) == ny(10, "16:13:00")
    assert av.fim_da_sessao_ns(manifesto("z", transport_closed_normally=True,
                                         status={"criado_em": "2026-11-10T21:13:00Z"})) == ny(10, "16:13:00")
    assert av.fim_da_sessao_ns(manifesto("caiu", transport_closed_normally=False,
                                         status={"criado_em": "2026-11-10T21:13:00+00:00"})) is None
    assert av.fim_da_sessao_ns(manifesto("sem_fuso", transport_closed_normally=True,
                                         status={"criado_em": "2026-11-10T21:13:00"})) is None
    assert av.fim_da_sessao_ns(manifesto("ruim", transport_closed_normally=True, status={"criado_em": "ontem"})) is None
    assert av.fim_da_sessao_ns(manifesto("sem_status", transport_closed_normally=True, status=[1])) is None
    assert av.fim_da_sessao_ns(tmp_path / "nao_existe") is None


# ---- intervalo e veredito ----------------------------------------------------------------------------

def operacao(dia, hora, ticks):
    t = ny(dia, hora)
    return av.Operacao(ev.COMPRA, t, t, 100, t + 10 * S, 100 + ticks, ticks, None)


def test_intervalo_por_blocos_e_deterministico_e_desconta_a_taxa():
    lucro = [operacao(10, f"{10 + i // 12}:{(i % 12) * 5}:00", 3) for i in range(48)]
    media, baixo, alto = av.intervalo(lucro, taxa_usd=5.0, alfa=0.05, reamostras=500)
    assert media == pytest.approx(3 - 0.4) and baixo == pytest.approx(2.6) and alto == pytest.approx(2.6)
    misto = [operacao(10, f"{10 + i // 12}:{(i % 12) * 5}:00", 1 if i % 2 else -1) for i in range(48)]
    a = av.intervalo(misto, 0.0, 0.05, reamostras=500)
    assert a == av.intervalo(misto, 0.0, 0.05, reamostras=500) and a[1] < 0 < a[2]
    assert av.intervalo([], 0.0, 0.05) == (None, None, None)


def test_intervalo_bicaudal_usa_metade_do_alfa_em_cada_lado():
    # dois blocos (0 e 10 ticks): a media reamostrada e 0, 5 ou 10 com chances de 25%, 50% e 25%
    ops = [operacao(10, "10:00:00", 0), operacao(10, "10:10:00", 10)]
    assert av.intervalo(ops, 0.0, 0.4, reamostras=4000) == (5.0, 0.0, 10.0)


def test_mesmo_bloco_de_5_minutos_e_reamostrado_junto():
    # 3 operacoes no mesmo bloco: com um bloco so, toda reamostragem repete o bloco inteiro
    ops = [operacao(10, "10:00:00", 5), operacao(10, "10:01:00", -1), operacao(10, "10:04:59", 2)]
    assert av.intervalo(ops, 0.0, 0.05, reamostras=200) == (2.0, 2.0, 2.0)


def test_julgamento_usa_o_limite_inferior_e_o_minimo_de_pregoes():
    assert av.julgar(7, 0.01, 0.001, 0.05) == "PASSOU" and av.julgar(7, 0.0, 0.001, 0.05) == "FALHOU"
    assert av.julgar(7, None, 0.001, 0.05) == "FALHOU"
    assert av.julgar(6, 5.0, 0.001, 0.05) == "ADIADO"
    quase = [operacao(10, f"{10 + i // 12}:{(i % 12) * 5}:00", 1 if i < 25 else -1) for i in range(48)]
    media, baixo, _ = av.intervalo(quase, 0.0, 0.05, reamostras=500)
    assert media > 0 and baixo < 0 and av.julgar(7, baixo, 0.001, 0.05) == "FALHOU"     # media positiva nao basta


# ---- criterio por pregao (C3, 28/10) -----------------------------------------------------------------

def dia_com(data, *ticks):
    ops = tuple(operacao(data, f"10:{i:02d}:00", t) for i, t in enumerate(ticks))
    return av.Dia(date(2026, 11, data), True, "completo", ops, {})


def test_permutacao_por_pregao_exata_unilateral_e_deterministica():
    dias = [dia_com(d, 2) for d in (9, 10, 11, 12, 13, 16, 17)]            # 7 pregoes, todos positivos
    r = av.por_pregao(dias, taxa_usd=0.0, reamostras=1000)
    assert r == {"pregoes": 7, "media_por_pregao": 2.0, "p_valor": pytest.approx(1 / 128), "exato": True}
    assert av.julgar(7, 1.0, r["p_valor"], 0.0167) == "PASSOU"               # 1/128 = 0,78 % < 0,83 %
    assert av.julgar(7, 1.0, r["p_valor"], 0.0156) == "FALHOU"               # abaixo de 1/128 ninguem passa
    seis = av.por_pregao(dias[:6], 0.0, reamostras=1000)
    assert seis["p_valor"] == pytest.approx(1 / 64) and av.julgar(7, 1.0, seis["p_valor"], 0.0167) == "FALHOU"


def test_permutacao_por_pregao_conta_a_taxa_e_o_sinal_dos_dias():
    dias = [dia_com(9, 3, 3), dia_com(10, 3), dia_com(11, -1), dia_com(12, 3), dia_com(13, 3)]
    sem_taxa = av.por_pregao(dias, 0.0, reamostras=100)
    assert sem_taxa["media_por_pregao"] == pytest.approx((6 + 3 - 1 + 3 + 3) / 5) and sem_taxa["exato"] is True
    assert sem_taxa["p_valor"] == pytest.approx(2 / 32)                     # so (+,+,-,+,+) e (+,+,+,+,+) >= observado
    com_taxa = av.por_pregao(dias, 12.5 * 2, reamostras=100)                # 2 ticks por operacao
    assert com_taxa["media_por_pregao"] == pytest.approx((2 + 1 - 3 + 1 + 1) / 5)
    assert av.por_pregao([], 0.0) == {"pregoes": 0, "media_por_pregao": None, "p_valor": None, "exato": True}


def test_permutacao_por_pregao_sorteada_quando_nao_cabe_enumerar():
    dias = [dia_com(d, 1) for d in range(1, 21)]                            # 2^20 > reamostras
    a = av.por_pregao(dias, 0.0, reamostras=200)
    assert a == av.por_pregao(dias, 0.0, reamostras=200) and a["exato"] is False
    assert a["p_valor"] == pytest.approx(1 / 201)                           # (0 + 1) / (200 + 1)
    with pytest.raises(de.DesequilibrioError):
        av.por_pregao(dias, -1.0)


def test_veredito_exige_os_dois_criterios(capturas):
    for dia in (9, 10, 11, 12, 13, 16):
        capturas[f"d{dia}"] = com_operacao(dia, 5)
    for dia in (17, 18):
        capturas[f"d{dia}"] = com_operacao(dia, -1)                         # dois dias negativos entre 8
    r = av.avaliar([f"/x/d{d}" for d in (9, 10, 11, 12, 13, 16, 17, 18)], PAR._replace(alfa=0.0167), reamostras=1000)
    assert r["intervalo"][0] > 0                                           # o bootstrap em blocos passaria sozinho
    assert r["por_pregao"] == {"pregoes": 8, "media_por_pregao": 3.5, "p_valor": pytest.approx(4 / 256), "exato": True}
    assert r["veredito"] == "FALHOU" and r["criterio"] == "bloco_5min_e_por_pregao"


def com_operacao(dia, ticks, **ajuste):
    t = ny(dia, "10:00:00")
    extras = [est(t + S, 0.9), est(t + 2 * S, 0.0),                    # sinal em 1 s, saida em 11,65 s
              est(t + int(11.6 * S), 0.0, compra=101 + ticks, venda=102 + ticks),
              est(t + 12 * S, 0.0, compra=101 + ticks, venda=102 + ticks)]
    return pregao(dia, extras, **ajuste)


@pytest.fixture
def capturas(monkeypatch):
    mapa = {}
    # chave = nome da pasta pelo separador da plataforma (no Windows o caminho vem com `\`)
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta: iter(mapa[Path(pasta).name]))
    return mapa


def test_veredito_passou_com_7_pregoes_completos(capturas):
    for dia in (9, 10, 11, 12, 13, 16, 17):
        capturas[f"d{dia}"] = com_operacao(dia, 2)
    r = av.avaliar([f"/x/d{d}" for d in (9, 10, 11, 12, 13, 16, 17)], PAR._replace(taxa_usd=5.0), reamostras=300)
    assert (r["veredito"], r["pregoes_completos"], r["pregoes_usados"], r["operacoes"]) == ("PASSOU", 7, 7, 7)
    assert r["media_bruta_ticks"] == 2 and r["media_liquida_ticks"] == pytest.approx(1.6)


def test_pregao_cortado_pela_lacuna_conta_para_o_minimo_de_7(capturas):
    for dia in (9, 10, 11, 12, 13, 16):
        capturas[f"d{dia}"] = com_operacao(dia, 2)
    capturas["d17"] = com_operacao(17, 2, sem=[(ny(17, "12:00:00"), ny(17, "12:02:00"))])
    r = av.avaliar([f"/x/d{d}" for d in (9, 10, 11, 12, 13, 16, 17)], PAR, reamostras=100)
    assert (r["veredito"], r["pregoes_completos"], r["pregoes_ate_lacuna"], r["pregoes_usados"]) == ("PASSOU", 6, 1, 7)
    d17 = next(d for d in r["dias"] if d["captura"] == "d17")
    assert d17["motivo"] == "completo_ate_lacuna" and d17["corte_ns"] == ny(17, "11:59:30")


def test_pregao_com_silencio_final_conta_para_o_minimo_de_7(capturas, monkeypatch):
    for dia in (9, 10, 11, 12, 13, 16):
        capturas[f"d{dia}"] = com_operacao(dia, 2)
    capturas["d17"] = com_operacao(17, 2, fim="15:00:00")
    fins = {"d17": ny(17, "16:13:00")}
    monkeypatch.setattr(av, "fim_da_sessao_ns", lambda pasta: fins.get(str(pasta).rstrip("/").split("/")[-1]))
    pastas = [f"/x/d{d}" for d in (9, 10, 11, 12, 13, 16, 17)]
    r = av.avaliar(pastas, PAR, reamostras=100)
    assert (r["veredito"], r["pregoes_completos"], r["pregoes_ate_lacuna"], r["pregoes_ate_silencio_final"],
            r["pregoes_usados"]) == ("PASSOU", 6, 0, 1, 7)
    d17 = next(d for d in r["dias"] if d["captura"] == "d17")
    assert d17["motivo"] == "completo_ate_silencio_final" and d17["corte_ns"] == ny(17, "15:00:00")
    fins.clear()                                              # sem o fim da sessao, a regra antiga: o dia se perde
    r = av.avaliar(pastas, PAR, reamostras=100)
    assert (r["veredito"], r["pregoes_usados"]) == ("ADIADO", 6)
    assert next(d for d in r["dias"] if d["captura"] == "d17")["motivo"] == "terminou_antes_do_fechamento"


def test_menos_de_7_pregoes_adia_e_dia_repetido_ou_fora_da_janela_nao_conta(capturas):
    for dia in (9, 10, 11, 12, 13, 16):
        capturas[f"d{dia}"] = com_operacao(dia, 2)
    capturas["d16b"] = com_operacao(16, 2)                        # segunda captura no mesmo dia
    capturas["d23"] = com_operacao(23, 2)                         # fora da janela de teste
    r = av.avaliar([f"/x/{n}" for n in capturas], PAR, reamostras=100)
    motivos = {d["captura"]: d["motivo"] for d in r["dias"]}
    assert r["veredito"] == "ADIADO" and r["pregoes_completos"] == 5
    assert motivos["d16"] == motivos["d16b"] == "mais_de_uma_captura_no_dia" and motivos["d23"] == "fora_da_janela"


def test_resultado_negativo_falha(capturas):
    for dia in (9, 10, 11, 12, 13, 16, 17):
        capturas[f"d{dia}"] = com_operacao(dia, -1)
    assert av.avaliar([f"/x/d{d}" for d in (9, 10, 11, 12, 13, 16, 17)], PAR, reamostras=100)["veredito"] == "FALHOU"


# ---- CLI ---------------------------------------------------------------------------------------------

ARGS = ["--p5", "-0.5", "--p95", "0.5", "--taxa-usd", "5", "--alfa", "0.0167"]


def test_cli_recusa_antes_do_fim_da_janela_e_parametro_invalido(tmp_path, capsys):
    assert av.main([str(tmp_path)] + ARGS, hoje=date(2026, 11, 20)) == 2
    assert "nada foi lido" in capsys.readouterr().out
    assert av.main([str(tmp_path), "--p5", "0.5", "--p95", "-0.5", "--taxa-usd", "5", "--alfa", "0.05"],
                   hoje=date(2026, 11, 21)) == 2
    assert "parametros invalidos" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        av.main([str(tmp_path), "--p5", "-0.5"], hoje=date(2026, 11, 21))


def test_cli_depois_da_janela_imprime_o_relatorio(tmp_path, capturas, capsys):
    pastas = []
    for dia in (9, 10, 11, 12, 13, 16, 17):
        capturas[f"d{dia}"] = com_operacao(dia, 2)
        (tmp_path / f"d{dia}").mkdir()
        pastas.append(str(tmp_path / f"d{dia}"))
    assert av.main(pastas + [str(tmp_path / "nao_e_pasta.txt")] + ARGS, hoje=date(2026, 11, 21)) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["esquema"] == "qhos2.avaliacao.v1" and r["veredito"] == "PASSOU" and r["alfa"] == 0.0167


# ---- QHOS-AUD-243: resolucao do quantil do bootstrap ---------------------------------------------------------

class _SorteioRoteirizado:
    """No lugar de random.Random: devolve indices de bloco de um roteiro, para a lista de medias ser conhecida a mao."""

    def __init__(self, roteiro):
        self.fila = list(roteiro)

    def randrange(self, n):
        indice = self.fila.pop(0)
        assert 0 <= indice < n
        return indice


def _intervalo_com_medias(monkeypatch, medias, alfa):
    """Dois blocos (0 e 10 ticks, uma operacao cada): cada reamostra sorteia 2 blocos e da media 0, 5 ou 10."""
    ops = [operacao(10, "10:00:00", 0), operacao(10, "10:10:00", 10)]
    roteiro = [i for media in medias for i in {0: (0, 0), 5: (0, 1), 10: (1, 1)}[media]]
    monkeypatch.setattr(av.random, "Random", lambda semente: _SorteioRoteirizado(roteiro))
    return av.intervalo(ops, 0.0, alfa, reamostras=len(medias))


def test_resolucao_do_quantil_do_bootstrap(monkeypatch):
    # posto(q) = medias_ordenadas[ceil(q * m) - 1]; os indices abaixo foram calculados a mao para cada (m, alfa)
    duas_em_cem = [5] * 50 + [10] * 48 + [0, 0]                   # fora de ordem: o quantil exige ordenar
    assert _intervalo_com_medias(monkeypatch, duas_em_cem, 0.05) == (5.0, 5.0, 10.0)        # m=100, 2,5 %: 3.a menor
    assert _intervalo_com_medias(monkeypatch, duas_em_cem, 0.05 / 3) == (5.0, 0.0, 10.0)    # m=100, 0,83 %: 1.a menor
    duas_em_duzentas = [10] * 98 + [0, 0] + [5] * 100
    assert _intervalo_com_medias(monkeypatch, duas_em_duzentas, 0.05 / 3) == (5.0, 0.0, 10.0)   # m=200: 2.a menor
    uma_em_duzentas = [10] * 98 + [0] + [5] * 101
    assert _intervalo_com_medias(monkeypatch, uma_em_duzentas, 0.05 / 3) == (5.0, 5.0, 10.0)    # a 2.a menor ja e 5
    # limite superior: indice ceil((1 - alfa/2) m) - 1 = 198 em m=200 (a 2.a maior): uma unica media 10 fica de fora
    uma_dez = [0] * 100 + [5] * 99 + [10]
    assert _intervalo_com_medias(monkeypatch, uma_dez, 0.05 / 3) == (5.0, 0.0, 5.0)
    # os indices do avaliador real (m = 10 000, alfa = 0,05/3): 84.a e 9 917.a menores; nos testes com m=100 e
    # alfa = 0,05 o limite inferior e a 3.a menor media (QHOS-AUD-243)
    assert (math.ceil(0.05 / 3 / 2 * 10_000) - 1, math.ceil((1 - 0.05 / 3 / 2) * 10_000) - 1) == (83, 9916)
    assert (av.REAMOSTRAS, math.ceil(0.05 / 2 * 100) - 1, math.ceil(0.05 / 3 / 2 * 200) - 1) == (10_000, 2, 1)


# ---- QHOS-AUD-244 (C5): a fixture `ny` so vale no regime de inverno de 2026/2027 -----------------------------

def test_fixture_ny_bate_com_a_base_de_fusos_nos_dias_usados():
    """`ny()` fixa UTC-5: vale de 2/11/2026 (depois do fim do horario de verao) a 13/3/2027; os testes deste arquivo
    so usam novembro de 2026. Em 1/11/2026 00:30 (ainda EDT) ela ja diverge: nao serve para outros regimes."""
    fuso = ZoneInfo("America/New_York")
    for dia in range(2, 31):
        for hora in ("0:00:00", "9:30:00", "13:55:00", "15:59:59", "16:05:00"):
            h, mi, s = (int(x) for x in hora.split(":"))
            assert ny(dia, hora) == int(datetime(2026, 11, dia, h, mi, s, tzinfo=fuso).timestamp()) * S, (dia, hora)
    assert ny(1, "0:30:00") == int(datetime(2026, 11, 1, 0, 30, tzinfo=fuso).timestamp()) * S + 3600 * S
