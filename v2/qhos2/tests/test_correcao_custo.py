"""Correcao 1, lote l5d: `qhos2.custo` (QHOS-AUD-234 parte do custo, 248, 254 parte do custo).

Instantes em UTC convertidos a mao (29/10/2026 e EDT, UTC-4). `src/qhos2` congelado: defeito confirmado = xfail estrito.
"""
import json

import pytest

from qhos2 import avaliador as av
from qhos2 import custo as cu
from qhos2 import desequilibrio as de
from qhos2 import poder as po
from test_custo import S, T0, capturas, est, utc_ns  # noqa: F401 (fixture `capturas`)


# ---- QHOS-AUD-234 ------------------------------------------------------------------------------------

def test_caracterizacao_234_cli_custo_devolve_0_e_o_caminho_que_nao_e_pasta_some_sem_aviso(tmp_path, capturas, capsys):
    """CARACTERIZACAO, nao requisito: fail-open e contrato testado pelo autor (test_custo.py:114, anterior a auditoria)."""
    capturas["a"] = [est(T0), est(T0 + S), est(T0 + 11 * S, 110, 111), est(T0 + 12 * S)]
    (tmp_path / "a").mkdir()
    assert cu.main([str(tmp_path / "a"), str(tmp_path / "nao_existe_234"), "--json"]) == 0
    saida = capsys.readouterr().out
    r = json.loads(saida)["medicao"]
    assert "nao_existe_234" not in saida and r["capturas"] == ["a"] and r["recusadas"] == []


# ---- QHOS-AUD-248: janela cuja saida cai depois das 16h ---------------------------------------------

T_PERTO = utc_ns("2026-10-29 19:59:55")          # 15:59:55 de Nova York (EDT): a saida (t + 10,65 s) cai as 16:00:05


def test_referencia_das_16h_no_avaliador_e_no_poder():
    """A regra registrada (plano, secao 10, item 5): sinal cuja saida passaria das 16h e ignorado e contado.
    `avaliador.simular` e `poder.contar_sinais` aplicam-na no mesmo instante; o custo e medido aqui para comparacao."""
    assert de.FECHAMENTO.isoformat() == "16:00:00" and cu.SAIDA_NS == 10_650_000_000
    assert de.hora_de_nova_york(T_PERTO).time().isoformat() == "15:59:55"
    assert de.hora_de_nova_york(T_PERTO + cu.SAIDA_NS).time().isoformat() == "16:00:05"
    estados = [est(T_PERTO - S), est(T_PERTO), est(T_PERTO + S), est(T_PERTO + 11 * S, 110, 111), est(T_PERTO + 12 * S)]
    sinal = [e._replace(d=0.9) if e.recebido_ns == T_PERTO else e for e in estados]
    dia = av.simular(sinal, av.Parametros(-0.5, 0.5, 0.0, 0.05))
    assert dia.operacoes == () and dia.contagens == {"sinal_perto_do_fechamento": 1}
    r = po.contar_sinais(sinal, -0.5, 0.5)
    assert r["sinais"] == 0 and r["contagens"] == {"sinal_perto_do_fechamento": 1}


def test_caracterizacao_248_custo_mede_a_janela_cuja_saida_passa_das_16h(capturas):
    """CARACTERIZACAO, nao requisito: a regra das 16h e do item 5 do plano (execucao simulada); o custo e a medicao sem
    sinal do item 3 (PLANO_V2.md:463-464) e `poder.py:102-103` descreve o filtro do custo sem ela. Se o custo deve
    aplica-la e decisao do autor (patch proposto em EVIDENCIAS/QHOS-AUD-248)."""
    capturas["a"] = [est(T_PERTO), est(T_PERTO + S), est(T_PERTO + 11 * S, 110, 111), est(T_PERTO + 12 * S)]
    r = cu.medir(["/x/a"])
    assert r["quadros_que_contam"] == 2 and r["descartados"] == {"fora_do_pregao": 2}
    assert r["amostras"] == 1 and r["por_dia"] == {"2026-10-29": 1}            # saida medida no livro de 15:59:56


# ---- QHOS-AUD-254: inteiro alem do alcance do float -------------------------------------------------

GIGANTE = 10 ** 400                              # int(float) impossivel: math.isfinite levanta OverflowError


@pytest.mark.xfail(strict=True, raises=OverflowError,
                   reason="QHOS-AUD-254: custo.acerto_necessario/checklist nao guardam OverflowError de math.isfinite")
@pytest.mark.parametrize("chamada", [
    lambda: cu.acerto_necessario(GIGANTE, 1.0),
    lambda: cu.acerto_necessario(1.0, GIGANTE),
    lambda: cu.checklist({"amostras": 1, "movimento_medio_abs_ticks": 1.0, "custo_spread_medio_ticks": 1.0}, GIGANTE),
    lambda: cu.checklist({"amostras": 1, "movimento_medio_abs_ticks": GIGANTE, "custo_spread_medio_ticks": 1.0}, 1.0),
])
def test_custo_inteiro_fora_do_alcance_do_float_vira_codigo_estatico(chamada):
    with pytest.raises(cu.CustoError):
        chamada()


def test_custo_rejeita_nao_finitos_e_bool_e_aceita_zero_negativo():
    for ruim in (float("nan"), float("inf"), -float("inf"), True, "1"):
        with pytest.raises(cu.CustoError, match="parametros_invalidos"):
            cu.acerto_necessario(ruim, 1.0)
    assert cu.acerto_necessario(-0.0, 1.0) == 0.5 and cu.acerto_necessario(0, 1) == 0.5
