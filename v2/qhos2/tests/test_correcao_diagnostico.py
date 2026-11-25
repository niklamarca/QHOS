"""Campanha de correcao (lote l5a-captura): pre-voo (QHOS-AUD-057).

`src/qhos2` congelado (J0): o defeito confirmado fica em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/QHOS-AUD-057/patch_proposto.diff`.
"""
import json

import pytest

from qhos2 import diagnostico


@pytest.fixture
def raiz_em_git(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(repo / "dados"))
    return repo


# ==== QHOS-AUD-057 ====
# diagnostico.main:39-41 reduz ConfigError/OSError a {"ok": false, "reason": "configuration_or_filesystem_invalid"}.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-057: diagnostico.main:39-41 esconde o codigo da ConfigError "
                                        "(data_root_dentro_de_repositorio_git) atras de configuration_or_filesystem_invalid")
def test_diagnostico_mostra_o_codigo_da_configuracao(raiz_em_git, capsys):
    assert diagnostico.main([]) == 2
    saida = json.loads(capsys.readouterr().out)
    assert saida["ok"] is False and "data_root_dentro_de_repositorio_git" in saida["reason"]


def test_diagnostico_com_configuracao_invalida_devolve_2_sem_tocar_o_disco(raiz_em_git, capsys):
    assert diagnostico.main([]) == 2
    saida = json.loads(capsys.readouterr().out)
    assert saida["ok"] is False and saida["reason"].startswith("configuration_or_filesystem_invalid")
    assert not (raiz_em_git / "dados").exists()


def test_diagnostico_valido_nao_le_credenciais_nem_conecta(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    codigo = diagnostico.main([])
    saida = json.loads(capsys.readouterr().out)
    assert codigo in (0, 1) and saida["ok"] is (codigo == 0)
    assert saida["credentials_read"] is False and saida["market_connected"] is False and saida["trading_ready"] is False
    assert saida["data_root"] == str((tmp_path / "dados").resolve()) and saida["disk_free_bytes"] > 0
