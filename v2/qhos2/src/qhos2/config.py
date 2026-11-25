"""Configuracao da V2: valores fixos, validados na criacao, imutaveis.

Origem dos valores (e-mails da Rithmic, 2026-09 e 2026-10):
- sistema "Rithmic Paper Trading" e prefixo de app_name "nila:" (RAPI, 12/10 e 22/09);
- heartbeat de ~10 s em periodos de inatividade (RAPI, 01/10 e 08/10);
- 2 sessoes de market data (Sales, 16/10). A RAPI disse em 21/10 que o limite vale para
  todas as plants juntas, entao a V2 usa 1 sessao de ticker e deixa 1 vaga livre.

Credenciais NUNCA ficam aqui: so o prefixo dos nomes das variaveis de ambiente.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlsplit

SYSTEM_NAME = "Rithmic Paper Trading"
APP_NAME_PREFIX = "nila:"
APP_NAME = "nila:QHOS"
DEFAULT_ENDPOINT = "wss://rprotocol-br.rithmic.com:443"
HEARTBEAT_MAX_SECONDS = 10
_PREFIXO_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ConfigError(ValueError):
    """Configuracao recusada; a mensagem e um codigo curto, sem valores sensiveis."""


def validar_endpoint(valor: str) -> str:
    """So wss em host rithmic.com, sem credenciais, caminho, consulta ou fragmento."""
    if not isinstance(valor, str):
        raise ConfigError("endpoint_precisa_ser_texto")
    partes = urlsplit(valor)
    if partes.scheme != "wss":
        raise ConfigError("endpoint_precisa_ser_wss")
    host = partes.hostname or ""
    if not (host == "rithmic.com" or host.endswith(".rithmic.com")):
        raise ConfigError("endpoint_precisa_ser_host_rithmic_com")
    if partes.username or partes.password:
        raise ConfigError("endpoint_sem_credenciais")
    if partes.path not in ("", "/") or partes.query or partes.fragment:
        raise ConfigError("endpoint_sem_caminho_consulta_ou_fragmento")
    try:
        porta = partes.port or 443
    except ValueError:
        raise ConfigError("endpoint_porta_invalida") from None
    return f"wss://{host}:{porta}"


def validar_data_root(valor) -> Path:
    """Caminho absoluto e fora de qualquer repositorio git (dado de mercado nunca vai ao GitHub)."""
    caminho = Path(valor).expanduser()
    if not caminho.is_absolute():
        raise ConfigError("data_root_precisa_ser_absoluto")
    caminho = caminho.resolve()
    for pasta in (caminho, *caminho.parents):
        if (pasta / ".git").exists():
            raise ConfigError("data_root_dentro_de_repositorio_git")
    return caminho


def data_root_padrao() -> Path:
    return Path.home() / "qhos-v2-data"


@dataclass(frozen=True)
class Settings:
    system_name: str = SYSTEM_NAME
    app_name: str = APP_NAME
    endpoint: str = DEFAULT_ENDPOINT
    heartbeat_seconds: int = HEARTBEAT_MAX_SECONDS
    market_data_session_limit: int = 2
    ticker_sessions: int = 1
    # Politica nossa: a Rithmic nao recomendou um valor de espera para reconexao.
    reconnect_backoff_seconds: tuple = (5, 10, 20, 40, 60)
    credential_env_prefix: str = "RITHMIC_PAPER"
    data_root: Path = field(default_factory=data_root_padrao)

    def __post_init__(self):
        if self.system_name != SYSTEM_NAME:
            raise ConfigError("so_rithmic_paper_trading")
        if not isinstance(self.app_name, str) or not self.app_name.startswith(APP_NAME_PREFIX) \
                or len(self.app_name) <= len(APP_NAME_PREFIX):
            raise ConfigError("app_name_precisa_do_prefixo_nila")
        if not _inteiro(self.heartbeat_seconds) or not 1 <= self.heartbeat_seconds <= HEARTBEAT_MAX_SECONDS:
            raise ConfigError("heartbeat_entre_1_e_10_segundos")
        if not _inteiro(self.market_data_session_limit) or self.market_data_session_limit < 2:
            raise ConfigError("limite_de_sessoes_minimo_2")
        if not _inteiro(self.ticker_sessions) or not 1 <= self.ticker_sessions <= self.market_data_session_limit - 1:
            raise ConfigError("ticker_sessions_deve_deixar_uma_vaga_livre")
        espera = self.reconnect_backoff_seconds
        if (not isinstance(espera, tuple) or not espera
                or not all(_inteiro(x) and 1 <= x <= 300 for x in espera)
                or list(espera) != sorted(espera)):
            raise ConfigError("backoff_invalido")
        if not isinstance(self.credential_env_prefix, str) or not _PREFIXO_ENV.match(self.credential_env_prefix):
            raise ConfigError("prefixo_de_credencial_invalido")
        object.__setattr__(self, "endpoint", validar_endpoint(self.endpoint))
        object.__setattr__(self, "data_root", validar_data_root(self.data_root))

    @classmethod
    def from_environ(cls, environ: Optional[Mapping[str, str]] = None) -> "Settings":
        """Unicas sobrescritas aceitas: QHOS2_DATA_ROOT e QHOS2_ENDPOINT."""
        env = os.environ if environ is None else environ
        mudancas = {}
        if env.get("QHOS2_DATA_ROOT"):
            mudancas["data_root"] = env["QHOS2_DATA_ROOT"]
        if env.get("QHOS2_ENDPOINT"):
            mudancas["endpoint"] = env["QHOS2_ENDPOINT"]
        return cls(**mudancas)

    def como_dict(self) -> dict:
        """Forma segura para log e manifesto: so texto e numeros, nunca segredos."""
        return {
            "system_name": self.system_name,
            "app_name": self.app_name,
            "endpoint": self.endpoint,
            "heartbeat_seconds": self.heartbeat_seconds,
            "market_data_session_limit": self.market_data_session_limit,
            "ticker_sessions": self.ticker_sessions,
            "reconnect_backoff_seconds": list(self.reconnect_backoff_seconds),
            "credential_env_prefix": self.credential_env_prefix,
            "data_root": str(self.data_root),
        }


def _inteiro(valor) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool)
