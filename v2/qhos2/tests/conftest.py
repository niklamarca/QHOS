"""Isolamento comum da suite (QHOS-AUD-001, QHOS-AUD-010): nenhum teste toca o HOME, a guarda ou os dados de quem executa.

- Antes da coleta, HOME (USERPROFILE no Windows) aponta para uma pasta temporaria da sessao, e QHOS2_DATA_ROOT, QHOS2_ENDPOINT
  e XDG_* saem do ambiente: a raiz de dados padrao (`~/qhos-v2-data`) e o token (`coletor.TOKEN_PADRAO`, avaliado no
  import) ja nascem dentro dela, e um QHOS2_DATA_ROOT real herdado do shell nao chega a teste nenhum.
- Cada teste tem a sua guarda de captura em `tmp_path`: `estado_padrao` de operacao, captura e agenda e substituido, e uma
  QUARENTENA deixada por um teste nao cala o seguinte. `monkeypatch.setenv("HOME")` num teste continua valendo para o
  HOME, mas nao move a guarda, que fica presa a `tmp_path`.
- Este arquivo nao importa `qhos2` no topo: o import aconteceria antes do redirecionamento.
- Rode o pytest a partir de `qhos2/`: a partir da raiz do monorepo este conftest so e carregado durante a coleta.
"""
import importlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Testes importam helpers uns dos outros (`from test_sessao import Servidor`). Isso so funciona por acaso no modo de
# import padrao (prepend); com --import-mode=importlib a coleta falha. A pasta fica no caminho em qualquer modo
# (QHOS-AUD-141); no modo padrao a entrada e redundante e inofensiva.
_PASTA_TESTES = str(Path(__file__).resolve().parent)
if _PASTA_TESTES not in sys.path:
    sys.path.insert(0, _PASTA_TESTES)

HOME_DE_QUEM_EXECUTA = pytest.StashKey()  # (HOME, USERPROFILE) de antes do redirecionamento
PASTA_DA_SUITE = pytest.StashKey()  # pasta temporaria da sessao; o HOME redirecionado e PASTA_DA_SUITE / "home"
_SAI_DO_AMBIENTE = ("QHOS2_DATA_ROOT", "QHOS2_ENDPOINT", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME")
_anterior = {}
_pasta = None


def pytest_configure(config):
    global _pasta
    _pasta = Path(tempfile.mkdtemp(prefix="qhos2-suite-"))
    (_pasta / "home").mkdir()
    for nome in ("HOME", "USERPROFILE", *_SAI_DO_AMBIENTE):
        _anterior[nome] = os.environ.get(nome)
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(_pasta / "home")
    for nome in _SAI_DO_AMBIENTE:
        os.environ.pop(nome, None)
    config.stash[HOME_DE_QUEM_EXECUTA] = _anterior["HOME"], _anterior["USERPROFILE"]
    config.stash[PASTA_DA_SUITE] = _pasta
    # Marker desconhecido falha a coleta. Fica aqui, e nao em addopts, porque o pyproject.toml nao muda em J0.
    # Vale para o pytest 8 (o 9 le `strict_markers` do ini e ignora esta atribuicao; test_markers_registrados acusa).
    config.option.strict_markers = True
    # Recursos que um teste exige ficam declarados; nada aqui pula teste algum (QHOS-AUD-088, 116, 126, 252).
    for marker in ("documentacao: confere a documentacao contra o codigo; nao exercita o pacote",
                   "posix: exige recurso POSIX (FIFO, symlink, sinal); declarado, nao decidido por plataforma",
                   "lento: subprocesso real ou espera de segundos; deselecione com -m 'not lento'",
                   "git: exige o executavel git e um checkout"):
        config.addinivalue_line("markers", marker)


def pytest_unconfigure(config):
    for nome, valor in _anterior.items():
        if valor is None:
            os.environ.pop(nome, None)
        else:
            os.environ[nome] = valor
    if _pasta is not None:
        shutil.rmtree(_pasta, ignore_errors=True)


@pytest.fixture(autouse=True)
def _guarda_no_tmp_path(tmp_path, monkeypatch):
    guarda = tmp_path / "guarda-operacao"
    for nome in ("qhos2.operacao", "qhos2.captura", "qhos2.agenda"):
        try:
            modulo = importlib.import_module(nome)
        except (ImportError, KeyError):  # agenda sem banco de fusos (ZoneInfoNotFoundError e KeyError): QHOS-AUD-004
            continue
        monkeypatch.setattr(modulo, "estado_padrao", lambda: guarda)
