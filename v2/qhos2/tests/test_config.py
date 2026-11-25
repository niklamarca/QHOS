"""Peca 2: a configuracao da V2 recusa o que nao pode e nunca vaza segredos."""
import dataclasses
import json

import pytest

from qhos2.config import ConfigError, Settings, validar_endpoint


@pytest.fixture
def casa(tmp_path, monkeypatch):
    """HOME isolado: o teste nunca depende do computador onde roda."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_valores_padrao(casa):
    s = Settings()
    assert s.system_name == "Rithmic Paper Trading"
    assert s.app_name == "nila:QHOS"
    assert s.endpoint == "wss://rprotocol-br.rithmic.com:443"
    assert s.heartbeat_seconds == 10
    assert (s.market_data_session_limit, s.ticker_sessions) == (2, 1)
    assert s.credential_env_prefix == "RITHMIC_PAPER"


def test_data_root_padrao_fica_na_casa_do_usuario(casa):
    assert Settings().data_root == (casa / "qhos-v2-data").resolve()


def test_configuracao_e_imutavel(casa):
    with pytest.raises(dataclasses.FrozenInstanceError):
        Settings().heartbeat_seconds = 30


@pytest.mark.parametrize("endpoint", [
    "ws://rprotocol-br.rithmic.com:443",
    "https://rprotocol-br.rithmic.com:443",
    "wss://evil.example.com:443",
    "wss://rithmic.com.evil.com:443",
    "wss://evilrithmic.com:443",
    "wss://user:senha@rprotocol-br.rithmic.com:443",
    "wss://rprotocol-br.rithmic.com:443/caminho",
    "wss://rprotocol-br.rithmic.com:443?x=1",
    "wss://rprotocol-br.rithmic.com:abc",
    "",
])
def test_endpoint_recusado(casa, endpoint):
    with pytest.raises(ConfigError):
        Settings(endpoint=endpoint)


def test_endpoint_e_normalizado():
    assert validar_endpoint("wss://rprotocol.rithmic.com") == "wss://rprotocol.rithmic.com:443"
    assert validar_endpoint("WSS://RPROTOCOL-BR.Rithmic.com:443/") == "wss://rprotocol-br.rithmic.com:443"


def test_so_rithmic_paper_e_aceito(casa):
    with pytest.raises(ConfigError):
        Settings(system_name="Rithmic Test")


@pytest.mark.parametrize("nome", ["QHOS", "nila:", "", "Nila:QHOS"])
def test_app_name_precisa_do_prefixo_nila(casa, nome):
    with pytest.raises(ConfigError):
        Settings(app_name=nome)


@pytest.mark.parametrize("valor", [0, 11, -1, True, "10", 10.0])
def test_heartbeat_recusado(casa, valor):
    with pytest.raises(ConfigError):
        Settings(heartbeat_seconds=valor)


@pytest.mark.parametrize("valor", [1, 5, 10])
def test_heartbeat_aceito(casa, valor):
    assert Settings(heartbeat_seconds=valor).heartbeat_seconds == valor


def test_sessoes_sempre_deixam_uma_vaga_livre(casa):
    with pytest.raises(ConfigError):
        Settings(market_data_session_limit=2, ticker_sessions=2)
    with pytest.raises(ConfigError):
        Settings(ticker_sessions=0)
    with pytest.raises(ConfigError):
        Settings(market_data_session_limit=1, ticker_sessions=1)
    assert Settings(market_data_session_limit=3, ticker_sessions=2).ticker_sessions == 2


@pytest.mark.parametrize("espera", [(), (10, 5), (0, 5), (5, 301), [5, 10], (5.0, 10)])
def test_backoff_recusado(casa, espera):
    with pytest.raises(ConfigError):
        Settings(reconnect_backoff_seconds=espera)


@pytest.mark.parametrize("prefixo", ["", "rithmic_paper", "1ABC", "A B", "A-B"])
def test_prefixo_de_credencial_invalido(casa, prefixo):
    with pytest.raises(ConfigError):
        Settings(credential_env_prefix=prefixo)


def test_data_root_dentro_de_repositorio_git_e_recusado(casa):
    repo = casa / "projeto"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(ConfigError):
        Settings(data_root=repo / "dados")
    with pytest.raises(ConfigError):
        Settings(data_root=repo)


def test_data_root_relativo_e_recusado(casa):
    with pytest.raises(ConfigError):
        Settings(data_root="dados/v2")


def test_data_root_fora_de_repositorio_e_aceito(casa):
    destino = casa / "dados" / "v2"
    assert Settings(data_root=destino).data_root == destino.resolve()


def test_sobrescritas_do_ambiente(casa):
    destino = casa / "outro"
    s = Settings.from_environ({"QHOS2_DATA_ROOT": str(destino),
                               "QHOS2_ENDPOINT": "wss://rprotocol.rithmic.com:443"})
    assert s.data_root == destino.resolve()
    assert s.endpoint == "wss://rprotocol.rithmic.com:443"


def test_sobrescrita_invalida_do_ambiente_e_recusada(casa):
    with pytest.raises(ConfigError):
        Settings.from_environ({"QHOS2_ENDPOINT": "wss://evil.example.com:443"})


def test_ambiente_vazio_usa_os_padroes(casa):
    assert Settings.from_environ({}) == Settings()


def test_como_dict_e_seguro_para_log(casa):
    d = Settings().como_dict()
    json.dumps(d)  # serializavel
    texto = json.dumps(d).lower()
    assert "password" not in texto and "senha" not in texto
    assert set(d) == {"system_name", "app_name", "endpoint", "heartbeat_seconds",
                      "market_data_session_limit", "ticker_sessions",
                      "reconnect_backoff_seconds", "credential_env_prefix", "data_root"}
