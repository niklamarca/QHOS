"""Regressao embarcada pequena: invariantes de seguranca, sem rede ou dados reais."""
from pathlib import Path

import pytest

from qhos2.config import ConfigError, Settings, validar_data_root, validar_endpoint
from qhos2.operacao import janela_permitida
from qhos2.sessao import Credenciais, ConfigSessao


def test_endpoint_nao_aceita_destino_externo():
    with pytest.raises(ConfigError):
        validar_endpoint("wss://example.invalid")


def test_dados_dentro_de_git_recusados(tmp_path):
    (tmp_path / ".git").write_text("gitdir: /nao_utilizado")
    with pytest.raises(ConfigError):
        validar_data_root(tmp_path / "dados")


def test_repr_credenciais_nao_expoe_identidade():
    value = Credenciais("usuario_sintetico", "senha_sintetica")
    assert "usuario_sintetico" not in repr(value) and "senha_sintetica" not in repr(value)


@pytest.mark.parametrize("duracao", [float("nan"), float("inf"), 0, -1])
def test_duracao_invalida_recusada(tmp_path, duracao):
    with pytest.raises(ValueError):
        ConfigSessao("5.42", "ESZ6", "CME", duracao_s=duracao, settings=Settings(data_root=tmp_path))


def test_calendario_ausente_nao_qualifica_janela():
    from datetime import datetime, timezone
    assert not janela_permitida({}, datetime.now(timezone.utc), 60)
