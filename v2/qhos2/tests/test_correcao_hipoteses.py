"""Correcao 1, lote l5d: `qhos2.hipoteses` (QHOS-AUD-249, 256).

249: `int(bar_end * 1e9)` e conferido contra a aritmetica inteira (`bar_end * 10**9`) e contra `Decimal`.
256: o `status_label` dos dois caminhos de `evaluate_models`. `src/qhos2` congelado: defeito confirmado = xfail estrito.
"""
import calendar
import math
from decimal import Decimal

import pytest

from qhos2 import hipoteses as hi


# ---- QHOS-AUD-249 ------------------------------------------------------------------------------------

def test_fronteiras_inteiras_de_30s_de_2026_e_2027_convertem_sem_perda():
    """Referencia inteira: para todo `bar_end` inteiro, `int(bar_end * 1e9) == bar_end * 10**9` enquanto
    bar_end * 5**9 < 2**53 (o produto e exatamente representavel em double)."""
    inicio, fim = calendar.timegm((2026, 1, 1, 0, 0, 0)), calendar.timegm((2028, 1, 1, 0, 0, 0))
    assert inicio % 30 == 0 and (fim - inicio) // 30 == 2_102_400
    assert all(int(b * 1e9) == b * 10 ** 9 for b in range(inicio, fim, 30))
    limite = 2 ** 53 // 5 ** 9                                                 # 4 611 686 018: ano 2116
    assert 1970 + limite / 31_557_600 > 2116
    for b in (limite - limite % 30, 9_223_372_020):                            # ainda exato bem alem da janela
        assert int(b * 1e9) == b * 10 ** 9 == int(Decimal(b) * 10 ** 9)


def test_disponibilidade_no_instante_exato_do_fim_da_barra_e_observada():
    """O caminho da captura usa `time` inteiro (`received // 30e9 * 30`); no limite `closed_at_received_ns ==
    bar_end * 10**9` a comparacao `<` tem de dar OBSERVED, o que exige a conversao exata."""
    t0 = calendar.timegm((2026, 11, 10, 15, 0, 0))                             # 10:00 de Nova York (EST)
    barras = [dict(time=t0 + 30 * i, closed=True, complete=True, open=100.0, high=100.0, low=100.0, close=100.0,
                   volume=1, pv_ticks=400, tick_size=0.25) for i in range(41)]
    fim_ns = (barras[-1]["time"] + 30) * 10 ** 9
    barras[-1]["closed_at_received_ns"] = fim_ns
    for r in hi.evaluate_models(barras):
        assert r["evidence"]["availability"] == "OBSERVED" and r["evidence"]["decision_available_ns"] == fim_ns
    barras[-1]["closed_at_received_ns"] = fim_ns - 1
    assert {r["evidence"]["availability"] for r in hi.evaluate_models(barras)} == {"NOT_AVAILABLE"}


def test_time_nao_inteiro_perde_ate_meio_ulp_e_a_troca_por_10_elevado_a_9_nao_muda_nada():
    """Residuo do 249: so com `time` float nao inteiro (entrada JSON) o produto arredonda (ulp 256 ns em 1,76e18);
    `bar_end * 10**9` e a mesma multiplicacao em double, logo a correcao recomendada nao altera o resultado."""
    bar_end = 1761246000.0000002 + 30
    assert abs(bar_end / 30 - round(bar_end / 30)) <= 1e-8                      # passa no filtro de alinhamento
    exato = Decimal(bar_end) * 10 ** 9
    assert math.ulp(bar_end * 1e9) == 256.0
    assert 0 < abs(Decimal(int(bar_end * 1e9)) - exato) <= 128
    assert int(bar_end * 10 ** 9) == int(bar_end * 1e9)


# ---- QHOS-AUD-256 ------------------------------------------------------------------------------------

def _barras(n, close=100.0):
    return [dict(time=i * 30, closed=True, complete=True, open=close, high=close, low=close, close=close,
                 volume=1, pv_ticks=1, tick_size=0.25) for i in range(n)]


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="QHOS-AUD-256: status_label com acento em hipoteses.py:87 e sem acento no caminho NUMERICAL_RANGE_EXCEEDED (:181)")
def test_status_label_e_o_mesmo_nos_dois_caminhos():
    normal = {r["status_label"] for r in hi.evaluate_models(_barras(41))}
    estouro = _barras(41)
    for barra in estouro:
        barra["pv_ticks"] = 10 ** 1000
    erro = hi.evaluate_models(estouro)
    assert {r["reason"] for r in erro} == {"NUMERICAL_RANGE_EXCEEDED"}
    assert {r["status_label"] for r in erro} == normal
