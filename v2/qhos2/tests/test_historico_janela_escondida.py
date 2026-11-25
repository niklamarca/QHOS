"""Borda exata da janela escondida do historico (QHOS-AUD-037, mutante M04 da auditoria).

`historico.oculto(minuto_s, agora_s)` e `agora_s < LIBERADO_EM_S and OCULTO_DE_S <= minuto_s < OCULTO_ATE_S`. Os testes
existentes ficam longe das bordas; aqui cada limite e testado no segundo exato, com relogio controlado, e os instantes
vem de calculo independente (zoneinfo), nao das constantes do modulo. Nenhuma regra de inclusao nova e escolhida: so a
que o modulo documenta ("ate 21/11 00h de Brasilia").
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qhos2 import historico as hi

NY, BR = ZoneInfo("America/New_York"), ZoneInfo("America/Sao_Paulo")
OCULTO_DE = int(datetime(2026, 11, 9, 0, 0, tzinfo=NY).timestamp())        # 1_794_200_400
OCULTO_ATE = int(datetime(2026, 11, 21, 0, 0, tzinfo=NY).timestamp())      # 1_795_237_200
LIBERADO_EM = int(datetime(2026, 11, 21, 0, 0, tzinfo=BR).timestamp())     # 1_795_230_000


def test_constantes_independentes():
    assert (OCULTO_DE, OCULTO_ATE, LIBERADO_EM) == (1_794_200_400, 1_795_237_200, 1_795_230_000)
    assert (hi.OCULTO_DE_S, hi.OCULTO_ATE_S, hi.LIBERADO_EM_S) == (OCULTO_DE, OCULTO_ATE, LIBERADO_EM)


@pytest.mark.parametrize("minuto, agora, esperado", [
    # borda do relogio de liberacao: 1 s antes ainda esconde; no segundo exato libera (M04: `<` -> `<=` esconderia)
    (OCULTO_DE, LIBERADO_EM - 1, True),
    (OCULTO_DE, LIBERADO_EM, False),
    (OCULTO_DE, LIBERADO_EM + 1, False),
    # borda inferior do minuto: o minuto exato de 9/11 00h NY esconde; o minuto anterior nao
    (OCULTO_DE - 60, LIBERADO_EM - 1, False),
    (OCULTO_DE, LIBERADO_EM - 1, True),
    # borda superior do minuto: 21/11 00h NY (exclusivo) nunca esconde; o minuto anterior esconde
    (OCULTO_ATE - 60, LIBERADO_EM - 1, True),
    (OCULTO_ATE, LIBERADO_EM - 1, False),
], ids=["liberado-1s", "liberado-exato", "liberado+1s", "de-1min", "de-exato", "ate-1min", "ate-exato"])
def test_oculto_nas_bordas_exatas(minuto, agora, esperado):
    assert hi.oculto(minuto, agora) is esperado


def _historico(minutos, agora):
    h = hi.Historico("/nao/existe", "/nao/existe", tick=0.25, simbolo="ESZ6", bolsa="CME", relogio=lambda: agora)
    b = hi.Barras()
    b.minutos = {m: list(v) for m, v in minutos.items()}
    h.prontas["a"] = b
    return h


MEIO = OCULTO_DE + 5 * 86400                                               # 14/11, dentro da janela


@pytest.mark.parametrize("agora, esperado", [
    # candles tambem nao entregam minuto futuro (minuto > agora): em 21/11 03:00Z os minutos de 21/11 05:00Z ainda
    # nao existem, por isso a borda superior do minuto e testada so por `oculto()` acima
    (LIBERADO_EM - 1, [OCULTO_DE - 60]),
    (LIBERADO_EM, [OCULTO_DE - 60, OCULTO_DE, MEIO]),
    (OCULTO_ATE, [OCULTO_DE - 60, OCULTO_DE, MEIO, OCULTO_ATE - 60, OCULTO_ATE]),
], ids=["1s-antes-de-liberar", "segundo-exato-da-liberacao", "21-11-00h-NY"])
def test_candles_obedecem_a_borda_do_relogio(agora, esperado):
    minutos = {OCULTO_DE - 60: [1, 1, 1, 1, 1], OCULTO_DE: [2, 2, 2, 2, 1], MEIO: [5, 5, 5, 5, 1],
               OCULTO_ATE - 60: [3, 3, 3, 3, 1], OCULTO_ATE: [4, 4, 4, 4, 1]}
    assert [c["time"] for c in _historico(minutos, agora).candles("1m", 100)] == esperado
