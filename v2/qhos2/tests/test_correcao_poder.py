"""Correcao 1, lote l5d: `qhos2.poder` (QHOS-AUD-235, 245 parte da contagem de sinais, 254 parte do poder).

`src/qhos2` congelado: defeito confirmado = xfail estrito; o que o contrato nao define fica como caracterizacao.
"""
import json
import math

import pytest

from qhos2 import avaliador as av
from qhos2 import poder as po
from test_poder import S, T0, est

MEDICAO = {"amostras": 50, "movimento_medio_abs_ticks": 10.0, "custo_spread_medio_ticks": 1.0, "desvio_ticks": 2.0,
           "blocos": {"d/1": [3, 0.0, 6.0, 4.0], "d/2": [3, 0.0, 6.0, 4.0]}}
ARGS = ["--ops-por-pregao", "200", "--taxa-usd", "12.5", "--simulacoes", "50"]


# ---- QHOS-AUD-235: `recusadas` do JSON do custo ----------------------------------------------------

def test_caracterizacao_235_poder_ignora_recusadas_da_medicao_e_devolve_0(tmp_path, capsys):
    """CARACTERIZACAO, nao requisito: o protocolo nao diz se medicao parcial pode alimentar o checklist 2, nem o codigo
    de saida, nem se o JSON do poder leva `recusadas`. Decisao do autor (patch proposto em EVIDENCIAS/QHOS-AUD-235)."""
    arquivo = tmp_path / "custo.json"
    parcial = {**MEDICAO, "capturas": ["a"], "recusadas": ["r: estados_fora_de_ordem"]}
    arquivo.write_text(json.dumps({"medicao": parcial, "checklist": None}))
    assert po.main(["--medicao", str(arquivo)] + ARGS) == 0
    saida = capsys.readouterr().out
    assert "recusadas" not in saida and "estados_fora_de_ordem" not in saida
    assert set(json.loads(saida)) == {"checklist1", "checklist2"}


def test_medicao_sem_recusadas_segue_devolvendo_0(tmp_path, capsys):
    arquivo = tmp_path / "custo.json"
    arquivo.write_text(json.dumps({"medicao": {**MEDICAO, "capturas": ["a"], "recusadas": []}, "checklist": None}))
    assert po.main(["--medicao", str(arquivo)] + ARGS) == 0
    assert json.loads(capsys.readouterr().out)["checklist2"]["veredito"] == "SEGUE"


# ---- QHOS-AUD-254: inteiro alem do alcance do float ------------------------------------------------

GIGANTE = 10 ** 400


@pytest.mark.xfail(strict=True, raises=OverflowError,
                   reason="QHOS-AUD-254: poder._finito nao guarda o OverflowError de math.isfinite")
@pytest.mark.parametrize("chamada", [
    lambda: po.z(GIGANTE),
    lambda: po.mde_por_operacao(GIGANTE, 10, 0.05),
    lambda: po.efeito_de_desenho({}, GIGANTE),
    lambda: po.checklist({"desvio_ticks": GIGANTE, "blocos": {}}, ops_por_pregao=1.0, n_dias=7, alfa=0.05, custo_ticks=1.0),
])
def test_poder_inteiro_fora_do_alcance_do_float_vira_codigo_estatico(chamada):
    with pytest.raises(po.PoderError):
        chamada()


@pytest.mark.xfail(strict=True, raises=OverflowError,
                   reason="QHOS-AUD-254: um inteiro de 400 digitos no JSON da medicao derruba a CLI com OverflowError")
def test_cli_poder_com_inteiro_gigante_na_medicao_devolve_2(tmp_path, capsys):
    arquivo = tmp_path / "custo.json"
    arquivo.write_text(json.dumps({"medicao": {**MEDICAO, "movimento_medio_abs_ticks": GIGANTE}}))
    assert po.main(["--medicao", str(arquivo)] + ARGS) == 2
    assert "conta de poder impossivel" in capsys.readouterr().out


def test_math_isfinite_e_a_origem_e_o_avaliador_ja_guarda():
    with pytest.raises(OverflowError):
        math.isfinite(GIGANTE)
    assert av._finito(GIGANTE) is False                                        # avaliador.py: try/except OverflowError


@pytest.mark.parametrize("valor,esperado", [
    (True, False), (False, False), (float("nan"), False), (float("inf"), False), (-float("inf"), False),
    (-0.0, True), (0, True), (1.5, True), (10 ** 300, True), ("1", False), (None, False),
])
def test_finito_de_poder_e_avaliador_concordam_no_dominio_representavel(valor, esperado):
    assert po._finito(valor) is esperado and av._finito(valor) is esperado


# ---- QHOS-AUD-245: `pregoes` conta so os dias com sinal ----------------------------------------------

def test_caracterizacao_dia_sem_sinal_nao_entra_em_pregoes():
    """Hoje: `pregoes = len(por_dia)` so ve dias com sinal; um dia inteiro sem sinal nao reduz `sinais_por_pregao`."""
    dia2 = T0 + 24 * 3600 * S                    # 30/10/2026, sexta, 10:00 de Nova York, dentro da janela
    estados = [est(T0, 0.0), est(T0 + S, 0.9), est(T0 + 12 * S, 0.0),            # 1 sinal em 29/10
               est(dia2, 0.0), est(dia2 + S, 0.1), est(dia2 + 2 * S, -0.1)]       # 30/10 sem sinal
    r = po.contar_sinais(estados, -0.5, 0.5)
    assert r["por_dia"] == {"2026-10-29": 1}
    assert (r["sinais"], r["pregoes"], r["sinais_por_pregao"]) == (1, 1, 1.0)   # com o dia sem sinal seria 0,5
