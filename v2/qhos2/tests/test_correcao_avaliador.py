"""Correcao 1, lote l5d: `qhos2.avaliador` (QHOS-AUD-234 parte do avaliador, 236, 246, 247).

Nenhum parametro cientifico e escolhido aqui: os valores esperados vem de enumeracao explicita, de aritmetica exata
(`fractions.Fraction`) ou da regra ja registrada no codigo (o ramo exato de `p_inversao_de_sinal`). `src/qhos2` esta
congelado: os defeitos confirmados ficam como `xfail(strict=True)` ate o descongelamento; as pre-condicoes de cada
xfail ficam em testes normais separados, para que uma pre-condicao quebrada nao se esconda como xfail.
"""
import itertools
import json
import math
import random
from datetime import date
from fractions import Fraction

import pytest

from qhos2 import avaliador as av
from test_avaliador import ARGS, PAR, S, capturas, com_operacao, est, ny, pregao  # noqa: F401 (fixture `capturas`)


# ---- QHOS-AUD-234: a CLI descarta em silencio o que nao e pasta -------------------------------------

def test_caracterizacao_234_cli_avaliador_devolve_0_e_o_caminho_que_nao_e_pasta_some_sem_aviso(tmp_path, capturas, capsys):
    """CARACTERIZACAO, nao requisito: o fail-open e contrato testado pelo autor (test_avaliador.py:352, anterior a
    auditoria) e o ACHADOS (campo 10) ja avisa que scripts com glob misto passariam a falhar. Decisao do autor."""
    pastas = []
    for dia in (9, 10, 11, 12, 13, 16, 17):
        capturas[f"d{dia}"] = com_operacao(dia, 2)
        (tmp_path / f"d{dia}").mkdir()
        pastas.append(str(tmp_path / f"d{dia}"))
    assert av.main(pastas + [str(tmp_path / "nao_existe_234")] + ARGS, hoje=date(2026, 11, 21)) == 0
    saida = capsys.readouterr().out
    r = json.loads(saida)
    assert "nao_existe_234" not in saida and r["recusadas"] == []
    assert [d["captura"] for d in r["dias"]] == [f"d{d}" for d in (9, 10, 11, 12, 13, 16, 17)]


# ---- QHOS-AUD-236: empates nos dois ramos de p_inversao_de_sinal --------------------------------------

SOMAS_COM_EMPATE = [0.1, 0.2, -0.3]        # em reais soma zero; em double a soma e 2,8e-17 (empate em zero)


def _folga(somas):
    return Fraction(1e-9 * (1 + sum(abs(v) for v in somas)))      # a folga que o ramo exato ja usa


def _conta_exata(somas, subconjuntos):
    """Subconjuntos F com soma(F) <= folga, em aritmetica racional exata sobre os proprios doubles."""
    return sum(sum((Fraction(v) for v in F), Fraction(0)) <= _folga(somas) for F in subconjuntos)


def test_ramo_exato_conta_o_empate_em_zero_como_nao_positivo():
    """Caracterizacao do ramo exato: 5 dos 8 subconjuntos de [0.1, 0.2, -0.3] tem soma <= folga (enumeracao)."""
    todos = [F for k in range(4) for F in itertools.combinations(SOMAS_COM_EMPATE, k)]
    assert len(todos) == 8 and _conta_exata(SOMAS_COM_EMPATE, todos) == 5
    assert math.fsum(SOMAS_COM_EMPATE) == 2.7755575615628914e-17             # o empate e so de ponto flutuante
    media, p, exato = av.p_inversao_de_sinal(SOMAS_COM_EMPATE, reamostras=8)
    assert (p, exato) == (5 / 8, True) and media == pytest.approx(2.7755575615628914e-17 / 3)
    # sem empate de ponto flutuante (inteiros) a mesma enumeracao da 5/8, como o PTA registrou
    assert av.p_inversao_de_sinal([1.0, 1.0, -2.0], reamostras=8)[1] == 5 / 8


SOMAS_N10 = [0.1, 0.2, -0.3] * 3 + [0.0]                # 2^10 = 1024 > 1000: ramo sorteado


def _esperado_n10():
    sorteio = random.Random(av.SEMENTE)
    invertidos = [[v for v in SOMAS_N10 if not sorteio.random() < 0.5] for _ in range(1000)]   # F = sinal invertido
    return (_conta_exata(SOMAS_N10, invertidos) + 1) / (1000 + 1)


def test_pre_condicoes_do_ramo_sorteado():
    """Pre-condicoes dos dois xfail abaixo, fora deles."""
    sorteio = random.Random(5)                       # semente 5: as tres primeiras saidas de random() sao >= 0,5
    assert all(sorteio.random() >= 0.5 for _ in SOMAS_COM_EMPATE)
    assert av.p_inversao_de_sinal(SOMAS_COM_EMPATE, reamostras=1, semente=5)[2] is False
    assert av.p_inversao_de_sinal(SOMAS_N10, reamostras=1000)[2] is False
    _, p_exato, exato = av.p_inversao_de_sinal(SOMAS_N10, reamostras=1024)
    assert exato is True and abs(_esperado_n10() - p_exato) < 0.01      # a contagem independente bate com o exato


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="QHOS-AUD-236: o ramo sorteado compara `>= observado` sem a folga do ramo exato")
def test_ramo_sorteado_um_sorteio_que_inverte_todas_as_somas_conta_o_empate():
    # o unico sorteio inverte as tres somas: soma(F) = fsum(somas) = 2,8e-17 <= folga (a folga de avaliador.py:332,
    # inalterada): no ramo exato esse subconjunto conta, e p = (1 + 1) / (1 + 1) = 1.
    assert av.p_inversao_de_sinal(SOMAS_COM_EMPATE, reamostras=1, semente=5)[1] == 1.0   # hoje: 0,5


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="QHOS-AUD-236: com n = 10 e empates o ramo sorteado diverge do exato em ~0,11 de p")
def test_ramo_sorteado_com_n_10_reproduz_a_contagem_exata_dos_mesmos_sorteios():
    assert av.p_inversao_de_sinal(SOMAS_N10, reamostras=1000)[1] == pytest.approx(_esperado_n10())   # hoje: 0,447 vs 0,558


def test_no_teste_registrado_o_ramo_sorteado_nao_e_usado():
    """Alcance do 236: com REAMOSTRAS = 10 000 o ramo e exato ate 13 pregoes; a janela de teste tem 10."""
    assert 2 ** 13 <= av.REAMOSTRAS < 2 ** 14
    assert av.p_inversao_de_sinal([1.0] * 13)[2] is True and av.p_inversao_de_sinal([1.0] * 14)[2] is False


# ---- QHOS-AUD-246: o agregado soma `recuo_maior_ns`, que e um maximo ---------------------------------

def pregao_com_recuo(dia, recuo_ns):
    base, t = pregao(dia), ny(dia, "10:00:00")
    i = next(k for k, e in enumerate(base) if e.recebido_ns == t)
    return base[:i + 1] + [est(t - recuo_ns, 0.0)] + base[i + 1:]


def test_cada_dia_registra_o_seu_maior_recuo():
    for recuo in (30_000_000, 50_000_000):
        dia = av.simular(pregao_com_recuo(9, recuo), PAR)
        assert dia.motivo == "completo" and dia.contagens["recuos_de_relogio"] == 1
        assert dia.contagens["recuo_maior_ns"] == recuo


def _agregado_tres_dias(capturas):
    capturas["d9"], capturas["d10"] = pregao_com_recuo(9, 50_000_000), pregao_com_recuo(10, 50_000_000)
    capturas["d11"] = pregao_com_recuo(11, 30_000_000)
    return av.avaliar(["/x/d9", "/x/d10", "/x/d11"], PAR, reamostras=50)


def test_pre_condicao_do_agregado_tres_dias_completos_e_contadores_somados(capturas):
    r = _agregado_tres_dias(capturas)
    assert [d["motivo"] for d in r["dias"]] == ["completo"] * 3 and r["contagens"]["recuos_de_relogio"] == 3


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="QHOS-AUD-246: `contagens.update` soma recuo_maior_ns entre os dias (50+50+30 ms = 130e6, esperado 50e6)")
def test_agregado_mantem_o_maior_recuo_e_nao_a_soma(capturas):
    r = _agregado_tres_dias(capturas)
    assert r["contagens"]["recuo_maior_ns"] == max(50_000_000, 50_000_000, 30_000_000)      # hoje: 130e6


# ---- QHOS-AUD-247: `mais_de_uma_captura_no_dia` e as capturas recusadas ou de mais de um dia ----------

def test_caracterizacao_captura_recusada_no_mesmo_dia_nao_marca_a_completa(capturas):
    """Hoje: a recusada sai antes de ter data (o erro vem dos estados), logo nao entra em `por_data`."""
    capturas["d9"] = com_operacao(9, 2)
    capturas["d9b"] = [est(ny(9, "9:20:00"), 0.0), est(ny(9, "9:20:30"), float("nan"))]      # desequilibrio_invalido
    r = av.avaliar(["/x/d9", "/x/d9b"], PAR, reamostras=50)
    assert r["recusadas"] == ["d9b: desequilibrio_invalido"]
    assert [d["motivo"] for d in r["dias"]] == ["completo"] and r["pregoes_usados"] == 1


def test_caracterizacao_captura_de_dois_dias_nao_marca_a_captura_do_dia(capturas):
    """Hoje: a captura que cobre dois dias tem `data` None (motivo proprio) e nao entra em `por_data`."""
    capturas["d10"] = com_operacao(10, 2)
    capturas["d10_11"] = pregao(10) + pregao(11)
    r = av.avaliar(["/x/d10", "/x/d10_11"], PAR, reamostras=50)
    motivos = {d["captura"]: d["motivo"] for d in r["dias"]}
    assert motivos == {"d10": "completo", "d10_11": "mais_de_um_dia_ou_vazia"} and r["pregoes_usados"] == 1
    # duas capturas de um dia so, no mesmo dia, continuam marcadas (a regra que existe hoje)
    capturas["d10b"] = com_operacao(10, 2)
    r = av.avaliar(["/x/d10", "/x/d10b"], PAR, reamostras=50)
    assert {d["motivo"] for d in r["dias"]} == {"mais_de_uma_captura_no_dia"} and r["pregoes_usados"] == 0
