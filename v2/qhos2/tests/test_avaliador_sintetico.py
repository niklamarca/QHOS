"""Etapa 3, passo 8: o avaliador em dado sintetico, mecanica e decisao, com semente fixa."""
import hashlib
import json
import math
from datetime import date, datetime, time, timedelta, timezone

import pytest

from qhos2 import avaliador as av
from qhos2 import avaliador_sintetico as si
from qhos2 import custo as cu
from qhos2 import desequilibrio as de
from qhos2 import poder as po

S = 10 ** 9
DIA = date(2026, 11, 10)
PAR = av.Parametros(p5=-0.5, p95=0.5, taxa_usd=0.0, alfa=0.05 / 3)


# ---- mecanica ---------------------------------------------------------------------------------------

def test_sem_edge_o_resultado_bruto_e_o_custo_de_cruzar_o_spread():
    dia = av.simular(si.gerar_estados(1, DIA), PAR)
    assert dia.motivo == "completo" and len(dia.operacoes) >= 30
    media = sum(o.ticks for o in dia.operacoes) / len(dia.operacoes)
    assert media == pytest.approx(-1.0, abs=0.5)                          # compra no ask, vende no bid


@pytest.mark.parametrize("edge", [3, -3])
def test_edge_plantado_aparece_no_resultado_menos_o_spread(edge):
    dia = av.simular(si.gerar_estados(2, DIA, edge_ticks=edge), PAR)
    media = sum(o.ticks for o in dia.operacoes) / len(dia.operacoes)
    assert media == pytest.approx(edge - 1.0, abs=0.6)
    acertos = [o.acerto_direcao for o in dia.operacoes if o.acerto_direcao is not None]
    assert (sum(acertos) / len(acertos) > 0.9) == (edge > 0)


def test_entrada_ate_0_65s_depois_do_sinal_e_saida_10s_depois_da_entrada():
    passo = S // 4
    dia = av.simular(si.gerar_estados(3, DIA, passo_ns=passo), PAR)
    assert dia.operacoes
    for o in dia.operacoes:
        assert cu.ENTRADA_NS - passo < o.entrada_ns - o.sinal_ns <= cu.ENTRADA_NS
        assert av.SEGURA_NS - passo < o.saida_ns - o.entrada_ns <= av.SEGURA_NS + passo
        assert o.saida_ns - o.sinal_ns <= cu.SAIDA_NS


def test_em_dia_de_fomc_nada_dispara_das_13h55_as_16h():
    estados = si.gerar_estados(4, DIA)
    sem, com = av.simular(estados, PAR), av.simular(estados, PAR._replace(fomc=(DIA,)))
    inicio, fim = si.instante(DIA, de.FOMC_INICIO), si.instante(DIA, de.FECHAMENTO)
    assert any(inicio <= o.sinal_ns < fim for o in sem.operacoes)
    assert not any(inicio <= o.sinal_ns < fim for o in com.operacoes)
    assert [o for o in com.operacoes if o.sinal_ns < inicio] == [o for o in sem.operacoes if o.sinal_ns < inicio]


def test_taxa_sai_do_liquido_e_a_mesma_entrada_da_o_mesmo_relatorio(monkeypatch):
    mapa = {f"d{k}": si.gerar_estados(10 + k, d, edge_ticks=2) for k, d in enumerate(si.dias_uteis(7))}
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta, niveis=de.NIVEIS: iter(mapa[str(pasta).split("/")[-1]]))
    pastas = [f"/x/{n}" for n in mapa]
    sem_taxa = av.avaliar(pastas, PAR, reamostras=200)
    com_taxa = av.avaliar(pastas, PAR._replace(taxa_usd=12.5), reamostras=200)
    assert com_taxa["media_bruta_ticks"] == sem_taxa["media_bruta_ticks"]
    assert com_taxa["media_liquida_ticks"] == pytest.approx(sem_taxa["media_liquida_ticks"] - 1.0)
    assert com_taxa["por_pregao"]["media_por_pregao"] == pytest.approx(
        sem_taxa["por_pregao"]["media_por_pregao"] - sem_taxa["operacoes"] / 7)
    de_novo = av.avaliar(pastas, PAR, reamostras=200)
    digerir = lambda r: hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
    assert digerir(de_novo) == digerir(sem_taxa) and digerir(com_taxa) != digerir(sem_taxa)


def test_gerador_e_deterministico_e_valida_parametros():
    assert si.gerar_estados(5, DIA, passo_ns=10 * S) == si.gerar_estados(5, DIA, passo_ns=10 * S)
    assert si.gerar_estados(5, DIA, passo_ns=10 * S) != si.gerar_estados(6, DIA, passo_ns=10 * S)
    assert si.dias_uteis(7) == [date(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17)]
    with pytest.raises(si.SinteticoError):
        si.gerar_estados(1, DIA, passo_ns=0)
    with pytest.raises(si.SinteticoError):
        si.gerar_estados(1, DIA, edge_ticks=1.5)


# ---- decisao ----------------------------------------------------------------------------------------

ENSAIO = dict(sigma_ticks=2.0, ops_por_pregao=20, n_dias=7, alfa=0.05 / 3, corridas=120, reamostras=150)


def test_sem_edge_o_falso_positivo_fica_na_taxa_do_alfa():
    r = si.ensaio_decisao(media_liquida_ticks=0.0, **ENSAIO)
    assert r["fracao_passou"] <= 0.025 and r["fracao_bloco"] <= 0.05 and r["fracao_pregao"] <= 0.05
    assert sum(r["vereditos"].values()) == 120 and "ADIADO" not in r["vereditos"]
    assert r == si.ensaio_decisao(media_liquida_ticks=0.0, **ENSAIO)


def test_edge_do_tamanho_do_mde_da_poder_perto_de_80_pct():
    mde = si.mde_dos_dois_criterios(2.0, 20, 7, 0.05 / 3, simulacoes=200, reamostras=128)
    assert mde["mde_ticks"] == max(mde["mde_por_operacao_ticks"], mde["mde_por_pregao_em_ticks_por_operacao"])
    r = si.ensaio_decisao(media_liquida_ticks=mde["mde_ticks"], **ENSAIO)
    # no MDE o criterio que o define tem poder ~80 %, o outro mais; o conjunto fica entre os dois e o produto
    assert 0.5 <= r["fracao_passou"] <= 0.95 and max(r["fracao_bloco"], r["fracao_pregao"]) >= 0.7
    assert r["fracao_passou"] <= min(r["fracao_bloco"], r["fracao_pregao"]) + 1e-9
    forte = si.ensaio_decisao(media_liquida_ticks=2 * mde["mde_ticks"], **ENSAIO)
    assert forte["fracao_passou"] > r["fracao_passou"]


def test_criterio_por_pregao_do_ensaio_e_exato_como_no_teste_real():
    # 10 pregoes e 300 reamostras: o bootstrap em blocos fica curto, mas o teste por pregao continua exato (1024)
    r = si.ensaio_decisao(media_liquida_ticks=0.0, sigma_ticks=2.0, ops_por_pregao=5, n_dias=10, alfa=0.05 / 3,
                          corridas=3, reamostras=300)
    assert r["reamostras"] == 300 and r["fracao_pregao"] <= 1.0
    dias = [si.gerar_dia(__import__("random").Random(1), d, ops_por_pregao=5, media_bruta_ticks=3.0, sigma_ticks=0.1)
            for d in si.dias_uteis(10)]
    assert av.por_pregao(dias, 0.0, reamostras=300)["exato"] is False       # o que o ensaio evita
    assert av.por_pregao(dias, 0.0, reamostras=1024)["exato"] is True


def test_taxa_entra_no_ensaio_e_parametros_invalidos_recusados():
    bruto = si.ensaio_decisao(media_liquida_ticks=0.5, taxa_usd=0.0, **{**ENSAIO, "corridas": 20})
    com_taxa = si.ensaio_decisao(media_liquida_ticks=0.5, taxa_usd=12.5, **{**ENSAIO, "corridas": 20})
    assert bruto["vereditos"] == com_taxa["vereditos"]                      # a media liquida plantada e a mesma
    with pytest.raises(si.SinteticoError):
        si.ensaio_decisao(media_liquida_ticks=0.0, **{**ENSAIO, "sigma_ticks": 0.0})
    with pytest.raises(si.SinteticoError):
        si.ensaio_decisao(media_liquida_ticks=0.0, **{**ENSAIO, "ops_por_pregao": 0})


def test_cli_imprime_sem_edge_e_com_edge(capsys):
    assert si.main(["--sigma", "2", "--ops-por-pregao", "20", "--n-dias", "7", "--corridas", "20",
                    "--reamostras", "128"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["mde"]["mde_ticks"] > 0 and r["sem_edge"]["media_liquida_ticks"] == 0.0
    assert r["com_edge"]["media_liquida_ticks"] == r["mde"]["mde_ticks"] and r["com_edge"]["esquema"] == si.ESQUEMA
    assert si.main(["--sigma", "2", "--ops-por-pregao", "20", "--n-dias", "5", "--corridas", "5"]) == 2
    assert "n_dias_insuficiente" in capsys.readouterr().out


# ---- QHOS-AUD-226: nulo declarado, taxa observada com incerteza e sensibilidade a erro deliberado -------------

def _ic_binomial_exato(k, n, confianca=0.95):
    """Intervalo de Clopper-Pearson por enumeracao da binomial (math.comb); nada da implementacao sob teste."""
    def cdf(p, ate):
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(ate + 1))

    def raiz(condicao):
        baixo, alto = 0.0, 1.0
        for _ in range(100):
            meio = (baixo + alto) / 2
            baixo, alto = (meio, alto) if condicao(meio) else (baixo, meio)
        return baixo
    cauda = (1 - confianca) / 2
    inferior = 0.0 if k == 0 else raiz(lambda p: 1 - cdf(p, k - 1) <= cauda)
    superior = 1.0 if k == n else raiz(lambda p: cdf(p, k) >= cauda)
    return inferior, superior


def test_ic_binomial_exato_bate_com_a_forma_fechada():
    assert _ic_binomial_exato(0, 120) == (0.0, pytest.approx(1 - 0.025 ** (1 / 120)))       # 3,03 %
    assert _ic_binomial_exato(120, 120) == (pytest.approx(0.025 ** (1 / 120)), 1.0)
    inferior, superior = _ic_binomial_exato(3, 120)
    assert 0.005 < inferior < 0.0083 < 0.025 < superior < 0.072


VEREDITOS_NULO_GOLDEN = {"FALHOU": 119, "PASSOU": 1}            # golden de implementacao (36f0e1f), semente 20261109


def test_nulo_do_ensaio_e_iid_normal_com_taxa_observada_e_incerteza_registradas():
    """QHOS-AUD-226. Modelo nulo DECLARADO do ensaio (`si.gerar_dia`): o resultado bruto de cada operacao e sorteado por
    `random.Random(semente).gauss(media_bruta, sigma)`, independente entre operacoes e entre pregoes (iid normal, sem
    correlacao intrabloco nem entre dias), uma operacao por instante, 20 por pregao, 7 pregoes, semente fixa.
    O que este teste prova e a IMPLEMENTACAO da decisao sob esse nulo; nao e evidencia de validade cientifica do teste
    real, cujo nulo tem dependencia (ICC > 0, qhos2.poder) e cuja calibracao do alfa exigiria milhares de corridas."""
    r = si.ensaio_decisao(media_liquida_ticks=0.0, **ENSAIO)
    assert (r["semente"], r["corridas"], r["alfa"]) == (av.SEMENTE, 120, 0.05 / 3) and av.SEMENTE == 20261109
    assert r["vereditos"] == VEREDITOS_NULO_GOLDEN, r["vereditos"]
    k = r["vereditos"].get("PASSOU", 0)
    inferior, superior = _ic_binomial_exato(k, 120)
    nominal = r["alfa"] / 2                                             # 0,83 %: os dois criterios sao unilaterais
    assert inferior <= nominal <= superior, (k, inferior, superior)      # taxa observada compativel com a nominal
    # registro da incerteza: com 120 corridas o IC de 95 % e mais largo que 2,5 pp; o limiar 0,025 do teste vizinho
    # nao distingue 0,83 % de 2,5 % (o achado cientifico de QHOS-AUD-226 fica aberto; nada aqui o fecha)
    assert superior - inferior > 0.025
    for criterio in ("fracao_bloco", "fracao_pregao"):
        assert _ic_binomial_exato(round(r[criterio] * 120), 120)[0] <= nominal, (criterio, r[criterio])


def test_erro_deliberado_na_decisao_e_detectado_pelo_ensaio(monkeypatch):
    """Sensibilidade do ensaio a um avaliador errado: (a) decisao em que a media sozinha decide (o erro que o docstring
    de `julgar` proibe) infla o falso positivo sob o nulo de ~1 % para ~50 %; (b) criterio por pregao que nunca rejeita
    zera o poder com um edge grande. Os dois aparecem com 40 corridas. Um erro sutil (alfa no lugar de alfa/2) nao
    seria distinguivel com 120 corridas: e o limite registrado no teste acima."""
    curto = {**ENSAIO, "corridas": 40}
    julgar_real = av.julgar
    monkeypatch.setattr(av, "julgar", lambda n, baixo, p, alfa: "PASSOU" if p is not None and p <= 0.5 else "FALHOU")
    assert si.ensaio_decisao(media_liquida_ticks=0.0, **curto)["fracao_passou"] >= 0.25
    monkeypatch.setattr(av, "julgar", julgar_real)
    com_edge = si.ensaio_decisao(media_liquida_ticks=2.0, **curto)       # 2 ticks = 12 erros-padrao: poder ~100 %
    assert com_edge["fracao_passou"] >= 0.95
    monkeypatch.setattr(av, "p_inversao_de_sinal", lambda somas, **kw: (sum(somas) / len(somas), 1.0, True))
    sem_criterio = si.ensaio_decisao(media_liquida_ticks=2.0, **curto)
    assert sem_criterio["fracao_passou"] == 0.0 and sem_criterio["fracao_pregao"] == 0.0
    assert sem_criterio["fracao_bloco"] == com_edge["fracao_bloco"]      # o outro criterio nao mudou


# ---- QHOS-AUD-237: golden do gerador de estados --------------------------------------------------------------

GOLDEN_ESTADOS_SEMENTE_1 = "92dd5771abbb557fcdbb810829f48441a7ecdbd5aa61cb0d0117d95085aa2d22"   # 36f0e1f


def test_gerador_de_estados_bate_com_o_golden():
    """Golden de IMPLEMENTACAO: mesma semente, mesmos estados, byte a byte. Nao afirma correcao (os testes de mecanica
    acima afirmam); detecta mudanca silenciosa do gerador, que e o gabarito dos ensaios registrados."""
    estados = si.gerar_estados(1, DIA)
    digest = hashlib.sha256(json.dumps([list(e) for e in estados]).encode()).hexdigest()
    assert len(estados) == 6 * 3600 + 45 * 60 + 1                       # 9:20 a 16:05, um por segundo, inclusivo
    assert digest == GOLDEN_ESTADOS_SEMENTE_1, digest


# ---- QHOS-AUD-244: `instante` segue os regimes de horario de Nova York ate 2027 --------------------------------

REGIMES = [(date(2026, 10, 30), -4), (date(2026, 11, 2), -5), (date(2027, 3, 12), -5), (date(2027, 3, 15), -4),
           (date(2027, 11, 5), -4), (date(2027, 11, 8), -5)]


@pytest.mark.parametrize("dia, deslocamento_h", REGIMES, ids=[d.isoformat() for d, _ in REGIMES])
def test_instante_segue_os_regimes_de_horario_de_nova_york(dia, deslocamento_h):
    """Transicoes pela regra dos EUA (2.o domingo de marco, 1.o domingo de novembro): 1/11/2026, 14/3/2027, 7/11/2027.
    O deslocamento esperado vem da regra, nao da base de fusos."""
    esperado = datetime.combine(dia, de.ABERTURA, timezone.utc) - timedelta(hours=deslocamento_h)
    assert si.instante(dia, de.ABERTURA) == int(esperado.timestamp()) * S
    assert de.no_pregao(de.hora_de_nova_york(si.instante(dia, de.ABERTURA))) is True
    assert de.no_pregao(de.hora_de_nova_york(si.instante(dia, de.ABERTURA) - S)) is False
    assert de.hora_de_nova_york(si.instante(dia, de.FOMC_INICIO)).time() == de.FOMC_INICIO
