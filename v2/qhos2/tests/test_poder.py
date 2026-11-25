"""Etapa 3, checklist 2: menor efeito detectavel pelos dois criterios, so com o que a calibracao pode olhar."""
import calendar
import json
from datetime import datetime
from statistics import NormalDist

import pytest

from qhos2 import avaliador as av
from qhos2 import desequilibrio as de
from qhos2 import poder as po

S = 10 ** 9
T0 = calendar.timegm(datetime(2026, 10, 29, 14, 0, 0).timetuple()) * S          # 10:00 de Nova York (EDT)


def est(t, d, q=True):
    return de.Estado(0, t, q, 100, 101, 10, 10, d)


def test_z_e_mde_por_operacao():
    assert po.z(0.975) == pytest.approx(1.959964, abs=1e-6)
    assert po.mde_por_operacao(1.0, 100, 0.05) == pytest.approx((1.959964 + 0.841621) / 10, abs=1e-5)
    assert po.mde_por_operacao(1.0, 100, 0.05, deff=4.0) == pytest.approx(2 * po.mde_por_operacao(1.0, 100, 0.05))
    assert po.mde_por_operacao(2.0, 2000, 0.05 / 3) == pytest.approx((2.393988 + 0.841621) * 2 / 2000 ** 0.5, abs=1e-4)
    for ruim in [(0.0, 100, 0.05), (1.0, 0, 0.05), (1.0, 100, 1.0), (float("nan"), 100, 0.05)]:
        with pytest.raises(po.PoderError):
            po.mde_por_operacao(*ruim)
    with pytest.raises(po.PoderError):
        po.mde_por_operacao(1.0, 100, 0.05, deff=0.5)


def test_efeito_de_desenho_por_correlacao_intraclasse():
    iguais_dentro = {"a": [2, 2.0, 2.0, 2.0], "b": [2, -2.0, 2.0, 2.0]}        # (1, 1) e (-1, -1): rho = 1
    r = po.efeito_de_desenho(iguais_dentro, 3.0)
    assert (r["rho"], r["rho_usado"], r["deff"], r["blocos"], r["amostras"]) == (1.0, 1.0, 3.0, 2, 4)
    assert r["rho_abs"] == 0.0                                                # |x| = 1 em todos: nada entre blocos
    sem_dependencia = {"a": [2, 0.0, 2.0, 2.0], "b": [2, 0.0, 2.0, 2.0]}      # (1, -1) e (-1, 1): rho = 0
    assert po.efeito_de_desenho(sem_dependencia, 3.0)["deff"] == 1.0
    assert po.efeito_de_desenho({"a": [5, 1.0, 1.0, 1.0]}, 3.0)["rho"] is None    # um bloco so: nao da para estimar
    assert po.efeito_de_desenho(iguais_dentro, 0.5)["deff"] == 1.0            # menos de uma operacao por bloco
    with pytest.raises(po.PoderError):
        po.efeito_de_desenho(iguais_dentro, 0.0)


def test_contar_sinais_com_rearme_uma_posicao_e_filtros():
    estados = [est(T0, 0.9),                        # fora sem rearme
               est(T0 + S, 0.0),                    # arma
               est(T0 + 2 * S, 0.9),                # sinal 1, ocupado ate 12,65 s
               est(T0 + 3 * S, -0.9),               # ignorado: posicao aberta
               est(T0 + 4 * S, 0.0),                # nao rearma enquanto ocupado
               est(T0 + 13 * S, 0.9),               # fora sem rearme
               est(T0 + 14 * S, 0.0), est(T0 + 15 * S, -0.9),     # sinal 2
               est(T0 + 30 * S, 0.0, q=False), est(T0 + 31 * S, None)]
    r = po.contar_sinais(estados, -0.5, 0.5)
    assert (r["sinais"], r["pregoes"], r["sinais_por_pregao"], r["por_dia"]) == (2, 1, 2.0, {"2026-10-29": 2})
    assert r["contagens"] == {"fora_sem_rearme": 2, "sinal_ignorado_posicao_aberta": 1}
    perto = calendar.timegm(datetime(2026, 10, 29, 19, 59, 55).timetuple()) * S          # 15:59:55 de Nova York
    r = po.contar_sinais([est(perto - S, 0.0), est(perto, 0.9)], -0.5, 0.5)
    assert r["sinais"] == 0 and r["contagens"] == {"sinal_perto_do_fechamento": 1}
    fomc = calendar.timegm(datetime(2026, 10, 28, 18, 0, 0).timetuple()) * S             # 14:00 de 28/10
    assert po.contar_sinais([est(fomc, 0.0), est(fomc + S, 0.9)], -0.5, 0.5)["sinais"] == 0
    with pytest.raises(po.PoderError):
        po.contar_sinais([], 0.5, -0.5)


def test_contar_sinais_das_capturas_e_lacuna(monkeypatch):
    mapa = {"boa": [est(T0, 0.0), est(T0 + S, 0.9), est(T0 + 12 * S, 0.0), est(T0 + 13 * S, 0.9)],
            "lacuna": [est(T0, 0.0), est(T0 + S, 0.9), est(T0 + 70 * S, 0.0), est(T0 + 71 * S, 0.9)],
            "ruim": [est(T0, 0.0), est(T0 - 1, 0.9)]}
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta, niveis=de.NIVEIS: iter(mapa[str(pasta).split("/")[-1]]))
    r = po.contar_sinais_das_capturas(["/x/boa", "/x/lacuna", "/x/ruim"], -0.5, 0.5)
    assert (r["sinais"], r["por_dia"], r["capturas"]) == (3, {"2026-10-29": 3}, ["boa", "lacuna"])
    assert r["recusadas"] == ["ruim: estados_fora_de_ordem"]


def test_mde_por_pregao_simulado_e_deterministico():
    r = po.mde_por_pregao(1.0, 7, 0.05 / 3, simulacoes=200, reamostras=128)
    assert r["exato"] and r["p_minimo"] == pytest.approx(1 / 128) and r["motivo"] is None
    # com alfa/2 = 0,83 % so passa com os 7 dias positivos: Phi(delta)^7 = 0,8 da delta = 1,86 sigma
    assert 1.5 < r["mde_ticks"] < 2.4 and r["poder_alcancado"] >= 0.8
    assert r == po.mde_por_pregao(1.0, 7, 0.05 / 3, simulacoes=200, reamostras=128)
    folgado = po.mde_por_pregao(1.0, 7, 0.05, simulacoes=200, reamostras=128)   # alfa/2 = 2,5 %: um dia negativo cabe
    assert folgado["mde_ticks"] < r["mde_ticks"]
    pouco = po.mde_por_pregao(1.0, 5, 0.05 / 3, simulacoes=50)              # 1/32 > 0,83 %: nunca rejeita
    assert pouco["mde_ticks"] is None and pouco["motivo"] == "n_dias_insuficiente_para_rejeitar"
    with pytest.raises(po.PoderError):
        po.mde_por_pregao(0.0, 7, 0.05)


def test_p_inversao_por_metades_e_igual_a_enumeracao():
    import itertools
    for somas in ([2.0] * 7, [6.0, 3.0, -1.0, 3.0, 3.0], [5.0] * 6 + [-1.0] * 2, [0.36, -0.64, 1.36, 0.36]):
        n = len(somas)
        obs = sum(somas) / n
        esperado = sum(sum(s * v for s, v in zip(sinais, somas)) / n >= obs - 1e-9
                       for sinais in itertools.product((1, -1), repeat=n)) / 2 ** n
        assert av.p_inversao_de_sinal(somas, reamostras=1024)[1] == pytest.approx(esperado)


def test_checklist_2_junta_os_dois_criterios():
    medicao = {"desvio_ticks": 2.0, "blocos": {"d/1": [3, 0.0, 6.0, 4.0], "d/2": [3, 0.0, 6.0, 4.0]}}
    r = po.checklist(medicao, ops_por_pregao=200, n_dias=10, alfa=0.05 / 3, custo_ticks=2.0,
                     simulacoes=100, reamostras=1024)
    assert r["desenho"]["deff"] == 1.0 and r["n_ops"] == 2000
    assert r["mde_por_operacao_ticks"] == pytest.approx(po.mde_por_operacao(2.0, 2000, 0.05 / 3))
    assert r["por_pregao"]["exato"] and r["por_pregao"]["mde_ticks"] is not None
    assert r["mde_ticks"] == max(r["mde_por_operacao_ticks"], r["mde_por_pregao_em_ticks_por_operacao"])
    assert r["mde_ticks"] < 2.0 and r["veredito"] == "SEGUE"
    assert po.checklist(medicao, ops_por_pregao=200, n_dias=10, alfa=0.05 / 3, custo_ticks=0.01,
                        simulacoes=100, reamostras=1024)["veredito"] == "ABANDONO_MICROESTRUTURA"
    assert po.checklist(medicao, ops_por_pregao=200, n_dias=5, alfa=0.05 / 3, custo_ticks=2.0,
                        simulacoes=10)["veredito"] == "ABANDONO_MICROESTRUTURA"       # 5 dias nao rejeitam
    with pytest.raises(po.PoderError, match="medicao_sem_desvio"):
        po.checklist({"desvio_ticks": 0.0, "blocos": {}}, ops_por_pregao=200, n_dias=10, alfa=0.05, custo_ticks=1.0)
    with pytest.raises(po.PoderError):
        po.checklist(medicao, ops_por_pregao=0, n_dias=10, alfa=0.05, custo_ticks=1.0)


def test_cli_le_a_medicao_do_custo_e_responde_os_dois_checklists(tmp_path, capsys):
    medicao = {"amostras": 50, "movimento_medio_abs_ticks": 10.0, "custo_spread_medio_ticks": 1.0, "desvio_ticks": 2.0,
               "blocos": {"d/1": [3, 0.0, 6.0, 4.0], "d/2": [3, 0.0, 6.0, 4.0]}}
    arquivo = tmp_path / "custo.json"
    arquivo.write_text(json.dumps({"medicao": medicao, "checklist": None}))
    assert po.main(["--medicao", str(arquivo), "--ops-por-pregao", "200", "--taxa-usd", "12.5", "--simulacoes", "50"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["checklist1"]["custo_total_ticks"] == 2.0 and r["checklist2"]["custo_ticks"] == 2.0
    assert r["checklist2"]["esquema"] == "qhos2.checklist2.v1" and r["checklist2"]["veredito"] == "SEGUE"
    arquivo.write_text(json.dumps({**medicao, "amostras": 0}))
    assert po.main(["--medicao", str(arquivo), "--ops-por-pregao", "200", "--taxa-usd", "12.5"]) == 2
    assert "medicao_sem_amostra" in capsys.readouterr().out


# ---- QHOS-AUD-241: a bissecao do MDE fecha o bracket ---------------------------------------------------------

def _mde_com_poder_de_referencia(monkeypatch, poder_ref, **kw):
    """Troca `_poder_por_pregao` por uma funcao de referencia com raiz conhecida; sigma_dia = 1, tolerancia 0,02."""
    monkeypatch.setattr(po, "_poder_por_pregao", lambda delta, sigma, n, alfa, sims, ream, sem: poder_ref(delta))
    return po.mde_por_pregao(1.0, 7, 0.05 / 3, simulacoes=10, reamostras=128, **kw)


def test_mde_por_bissecao_fecha_o_bracket(monkeypatch):
    """Contrato do buscador de raiz: devolve `alto` com poder(alto) >= 0,8 e alto - raiz < tolerancia * sigma (bracket
    fechado), nao uma trajetoria. A bissecao atual de 36f0e1f da exatamente 21/16, 75/32 e 1/64 (caracterizacao,
    nos comentarios); um buscador correto diferente pode dar outro ponto do mesmo intervalo."""
    tol = 0.02
    # degrau em 1,3: [0, 1] -> [0, 2] -> [1, 2] -> [1, 1.5] -> [1.25, 1.5] -> [1.25, 1.375] -> [1.25, 1.3125]
    # -> [1.28125, 1.3125] -> [1.296875, 1.3125] (largura 1/64 <= 0,02): em 36f0e1f alto = 21/16 = 1,3125
    degrau = _mde_com_poder_de_referencia(monkeypatch, lambda d: 1.0 if d >= 1.3 else 0.0)
    assert 1.3 <= degrau["mde_ticks"] < 1.3 + tol
    assert (degrau["poder_alcancado"], degrau["motivo"]) == (1.0, None)
    # curva continua Phi(delta - 1,5): raiz 1,5 + z(0,8) = 2,3416; dobra ate 4; em 36f0e1f fecha em [149/64, 75/32]
    raiz = 1.5 + NormalDist().inv_cdf(0.8)
    continua = _mde_com_poder_de_referencia(monkeypatch, lambda d: NormalDist().cdf(d - 1.5))
    assert raiz <= continua["mde_ticks"] < raiz + tol
    assert continua["poder_alcancado"] == pytest.approx(NormalDist().cdf(continua["mde_ticks"] - 1.5))
    assert continua["poder_alcancado"] >= 0.8
    # extremos iguais (poder 1 em toda parte, raiz 0): o bracket encolhe ate a tolerancia (36f0e1f: 1/64)
    sempre = _mde_com_poder_de_referencia(monkeypatch, lambda d: 1.0)
    assert 0 <= sempre["mde_ticks"] < tol
    assert (sempre["poder_alcancado"], sempre["motivo"]) == (1.0, None)
    # alvo inalcancavel (poder 0,5 em toda parte): dobra ate passar de 64 sigma e desiste, com o poder visto
    nunca = _mde_com_poder_de_referencia(monkeypatch, lambda d: 0.5)
    assert (nunca["mde_ticks"], nunca["poder_alcancado"], nunca["motivo"]) == (None, 0.5, "poder_inalcancavel")
    # tolerancia mais fina e respeitada
    fino = _mde_com_poder_de_referencia(monkeypatch, lambda d: 1.0 if d >= 1.3 else 0.0, tolerancia=0.001)
    assert 1.3 <= fino["mde_ticks"] < 1.3 + 0.001


@pytest.mark.parametrize("ruim", [
    dict(sigma_dia=float("nan")), dict(sigma_dia=float("inf")), dict(sigma_dia=0.0), dict(alfa=float("nan")),
    dict(alfa=1.0), dict(poder=1.0), dict(poder=float("nan")), dict(tolerancia=0.0), dict(tolerancia=float("inf")),
    dict(tolerancia=1.5), dict(n_dias=0), dict(simulacoes=0),
])
def test_mde_por_pregao_recusa_nao_finitos_e_fora_da_faixa(ruim):
    args = dict(sigma_dia=1.0, n_dias=7, alfa=0.05 / 3, poder=0.8, simulacoes=10, tolerancia=0.02)
    args.update(ruim)
    with pytest.raises(po.PoderError, match="parametros_invalidos"):
        po.mde_por_pregao(args.pop("sigma_dia"), args.pop("n_dias"), args.pop("alfa"), reamostras=128, **args)


def test_mde_por_pregao_real_fecha_o_bracket_e_bate_com_a_formula_fechada():
    """n = 7 e alfa/2 = 0,83 % < 2/128: so rejeita com as 7 somas positivas, logo poder(delta) = Phi(delta/sigma)^7 e
    o MDE fechado e sigma * z(0,8^(1/7)) = 1,861 sigma. A bissecao (mesmos sorteios para todo delta) devolve um `alto`
    com poder >= 0,8 e `alto - tolerancia` com poder < 0,8."""
    alfa, sims = 0.05 / 3, 2000
    r = po.mde_por_pregao(1.0, 7, alfa, simulacoes=sims, reamostras=128)
    fechado = NormalDist().inv_cdf(0.8 ** (1 / 7))
    assert fechado == pytest.approx(1.86096, abs=1e-5)
    assert abs(r["mde_ticks"] - fechado) <= 0.15, r["mde_ticks"]
    poder = lambda delta: po._poder_por_pregao(delta, 1.0, 7, alfa, sims, 128, av.SEMENTE)
    assert poder(r["mde_ticks"]) == r["poder_alcancado"] >= 0.8
    assert poder(r["mde_ticks"] - 0.02) < 0.8


# ---- QHOS-AUD-237: golden do MDE simulado --------------------------------------------------------------------

GOLDEN_MDE_7_DIAS_200_SIMULACOES = (1.90625, 0.8)                 # (mde_ticks, poder_alcancado) em 36f0e1f; 61/32 sigma


def test_mde_simulado_bate_com_o_golden():
    r = po.mde_por_pregao(1.0, 7, 0.05 / 3, simulacoes=200, reamostras=128)
    assert (r["mde_ticks"], r["poder_alcancado"]) == GOLDEN_MDE_7_DIAS_200_SIMULACOES, (r["mde_ticks"], r["poder_alcancado"])


# ---- QHOS-AUD-251: a lacuna de exatos 60 s nao corta a contagem de sinais -------------------------------------

def test_lacuna_de_exatos_60s_nao_corta_a_contagem_e_60s_mais_1ns_corta():
    base = [est(T0, 0.0), est(T0 + S, 0.9)]                                   # sinal 1 em T0 + 1 s
    exatos = base + [est(T0 + 61 * S, 0.0), est(T0 + 62 * S, 0.9)]            # intervalo de 60 s: nao corta; sinal 2
    assert po.contar_sinais(exatos, -0.5, 0.5)["sinais"] == 2
    cortada = base + [est(T0 + 61 * S + 1, 0.0), est(T0 + 62 * S + 1, 0.9)]   # 60 s + 1 ns: corta
    r = po.contar_sinais(cortada, -0.5, 0.5)
    assert r["sinais"] == 1 and r["contagens"] == {}
    assert de.LACUNA_MAX_NS == 60 * S
