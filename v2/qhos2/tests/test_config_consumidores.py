"""Caracterizacao: campos de `Settings` validados e exportados, mas sem consumidor (QHOS-AUD-207).

`market_data_session_limit`, `ticker_sessions`, `reconnect_backoff_seconds` e `credential_env_prefix` sao politica
declarada: `config.py` os valida e `como_dict` os exporta, mas nenhum modulo do pacote, do Brain, do terminal ou dos
scripts de operacao os le. Nao existe rotina de reconexao, e nenhuma credencial vem de variavel de ambiente
(`captura.obter_credenciais` usa o prompt). Ver `docs/CONFIGURACAO.md`. Se um consumidor aparecer, este teste avisa
para atualizar a documentacao.
"""
import re
from pathlib import Path

import pytest

from qhos2 import captura
from qhos2.config import Settings

PACOTE = Path(__file__).resolve().parents[1]
CAMPOS = ("market_data_session_limit", "ticker_sessions", "reconnect_backoff_seconds", "credential_env_prefix")
ONDE = [*(PACOTE / "src").rglob("*.py"), *(PACOTE / "terminal" / "modulos").glob("*.js"),
        *(PACOTE / "operacao").rglob("*.py"), *(PACOTE / "operacao").rglob("*.sh"),
        *(PACOTE / "operacao").rglob("*.ps1"), *(PACOTE / "deploy").glob("*")]


def _fontes():
    for caminho in ONDE:
        if caminho.is_file() and caminho != PACOTE / "src" / "qhos2" / "config.py":
            yield caminho, caminho.read_bytes().decode("utf-8", errors="replace")


@pytest.mark.parametrize("campo", CAMPOS)
def test_campo_so_aparece_em_config_py(campo):
    usos = [str(c.relative_to(PACOTE)) for c, texto in _fontes() if campo in texto]
    assert usos == [], f"{campo} passou a ter consumidor fora de config.py: atualize docs/CONFIGURACAO.md"


def test_nenhuma_credencial_vem_do_ambiente():
    prefixo = Settings().credential_env_prefix
    usos = [str(c.relative_to(PACOTE)) for c, texto in _fontes() if prefixo in texto]
    assert prefixo == "RITHMIC_PAPER" and usos == []
    assert "getpass" in (PACOTE / "src" / "qhos2" / "captura.py").read_text(encoding="utf-8")
    c = captura.obter_credenciais(lambda _: "usuario", lambda _: "segredo")
    assert (c.user, c.password) == ("usuario", "segredo")


def test_nao_ha_reconexao_automatica_no_pacote():
    fontes = {c.name: texto for c, texto in _fontes() if c.parent == PACOTE / "src" / "qhos2"}
    for nome in ("sessao.py", "conector.py", "captura.py", "agenda.py"):
        assert not re.search(r"reconnect_backoff|reconect(a|ar)\(", fontes[nome]), nome


def test_os_campos_continuam_validados_e_exportados():
    d = Settings().como_dict()
    assert {campo: d[campo] for campo in CAMPOS} == {"market_data_session_limit": 2, "ticker_sessions": 1,
                                                     "reconnect_backoff_seconds": [5, 10, 20, 40, 60],
                                                     "credential_env_prefix": "RITHMIC_PAPER"}
