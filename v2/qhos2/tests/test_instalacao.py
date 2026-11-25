"""Fronteiras da instalacao independente, sem tocar caminhos ou tokens reais."""
from pathlib import Path

import pytest

from qhos2.config import ConfigError, data_root_padrao, validar_data_root
from qhos2.coletor import Coletor, ColetorError, ler_ou_criar_token, raiz_para_captura


def test_padrao_nao_depende_do_runtime_antigo(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert data_root_padrao() == tmp_path / "qhos-v2-data"


def test_captura_explicitada_alinha_historico(tmp_path):
    assert raiz_para_captura(tmp_path / "dados/capturas/abc", environ={}) == tmp_path / "dados"


def test_recusa_misturar_raizes(tmp_path):
    with pytest.raises(ConfigError, match="raizes_diferentes"):
        raiz_para_captura(tmp_path / "a/capturas/id", tmp_path / "b", environ={})
    with pytest.raises(ConfigError, match="raizes_diferentes"):
        raiz_para_captura(tmp_path / "a/capturas/id", environ={"QHOS2_DATA_ROOT": str(tmp_path / "b")})
    for explicita in (None, tmp_path / "b"):
        with pytest.raises(ConfigError, match="raiz_capturas"):
            raiz_para_captura(tmp_path / "diretorio_solto/id", explicita, environ={})


def test_raiz_recusa_worktree_e_link_para_git(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /um/worktree")
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    for p in (repo / "dados", alias / "dados"):
        with pytest.raises(ConfigError, match="dentro_de_repositorio_git"):
            validar_data_root(p)


def test_token_recusa_symlink_sem_alterar_alvo(tmp_path):
    alvo = tmp_path / "privado"
    alvo.write_text("A" * 43)
    alvo.chmod(0o600)
    link = tmp_path / "alias"
    link.symlink_to(alvo)
    for novo in (False, True):
        with pytest.raises(ColetorError, match="nao_regular"):
            ler_ou_criar_token(link, novo=novo)
    assert alvo.read_text() == "A" * 43


def test_token_recusa_hardlink(tmp_path):
    alvo = tmp_path / "privado"
    ler_ou_criar_token(alvo)
    alias = tmp_path / "alias"
    alias.hardlink_to(alvo)
    with pytest.raises(ColetorError, match="nao_regular"):
        ler_ou_criar_token(alias)


def test_sem_identidade_nunca_e_anunciado_como_producao():
    welcome = Coletor(token="A" * 43).welcome()
    assert welcome["environment"] == "unknown"
    assert welcome["canExecute"] is False
