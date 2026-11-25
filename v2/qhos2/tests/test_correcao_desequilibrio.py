"""Correcao 1, lote l5d: `qhos2.desequilibrio` (QHOS-AUD-234 parte da calibracao, 245 parte da calibracao).

Corpus: captura sintetica gravada pelo `Gravador` em `tmp_path` (`gravar_em` de test_desequilibrio) e estados montados
a mao. `src/qhos2` congelado: defeito confirmado = xfail estrito; o que o contrato nao define e caracterizado.
"""
import json

import pytest

from qhos2 import desequilibrio as de
from qhos2 import sintetico as si
from test_desequilibrio import estado_em, gravar_em, utc_ns


# ---- QHOS-AUD-234 ------------------------------------------------------------------------------------

def test_caracterizacao_234_cli_calibracao_devolve_0_e_o_caminho_que_nao_e_pasta_some_sem_aviso(tmp_path, capsys):
    """CARACTERIZACAO, nao requisito: fail-open e contrato testado pelo autor (test_desequilibrio.py:164-166, anterior a
    auditoria). Decisao do autor (patch proposto em EVIDENCIAS/QHOS-AUD-234)."""
    boa = gravar_em(si.gerar(1, 5), tmp_path, utc_ns("2026-10-29 15:00:00"), "boa")
    assert de.main([str(boa), str(tmp_path / "nao_existe_234"), "--json"]) == 0
    saida = capsys.readouterr().out
    r = json.loads(saida)
    assert "nao_existe_234" not in saida and r["capturas"] == ["boa"] and r["recusadas"] == []


def test_cli_calibracao_com_as_pastas_certas_devolve_0(tmp_path, capsys):
    boa = gravar_em(si.gerar(1, 5), tmp_path, utc_ns("2026-10-29 15:00:00"), "boa")
    assert de.main([str(boa), "--json"]) == 0


# ---- QHOS-AUD-245: calibracao sem deduplicacao de capturas -----------------------------------------

def test_caracterizacao_mesma_captura_duas_vezes_entra_duas_vezes_na_calibracao(monkeypatch):
    """Hoje: `calibrar` nao deduplica (o caminho oficial `pesquisa._identificar_capturas` recusa `captura_duplicada`)."""
    lista = [estado_em("2026-10-29 14:00:00"), estado_em("2026-10-29 14:00:30"), estado_em("2026-10-29 14:01:00")]
    monkeypatch.setattr(de, "estados_da_captura", lambda pasta, niveis=de.NIVEIS: iter(lista))
    uma = de.calibrar(["/x/a"])
    duas = de.calibrar(["/x/a", "/x/a"])
    assert uma["quadros_usados"] == 3 and uma["capturas"] == ["a"] and uma["por_dia"] == {"2026-10-29": 3}
    assert duas["quadros_usados"] == 6 and duas["capturas"] == ["a", "a"] and duas["por_dia"] == {"2026-10-29": 6}
    assert (duas["p5"], duas["p95"]) == (uma["p5"], uma["p95"])               # os percentis nao mudam, as contagens sim
