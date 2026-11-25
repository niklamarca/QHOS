"""Higiene do repositorio: o que e licenciado ou privado nunca chega ao GitHub."""
import shutil
import subprocess
from pathlib import Path

import pytest

PACOTE = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.git


@pytest.fixture(params=["checkout", "independente"])
def raiz(request, tmp_path):
    # Sem git nao ha como conferir o .gitignore: o teste declara o recurso e e pulado com motivo, em vez de falhar como
    # se o repositorio estivesse desprotegido (QHOS-AUD-126). `-ra` lista os pulos.
    if not shutil.which("git"):
        pytest.skip("git ausente: a protecao do .gitignore nao pode ser conferida nesta maquina")
    if request.param == "checkout":
        resultado = subprocess.run(["git", "-C", str(PACOTE), "rev-parse", "--show-toplevel"],
                                   text=True, capture_output=True)
        if resultado.returncode != 0:
            if not any((pasta / ".git").exists() for pasta in (PACOTE, *PACOTE.parents)):
                pytest.skip("fora de um checkout git (ex.: sdist): so o caso 'independente' se aplica")
            # ha um .git acima, mas o git nao o aceita (ex.: safe.directory): isso e falha a corrigir, nao pulo
            pytest.fail(f"checkout git presente mas `git rev-parse` falhou: {resultado.stderr.strip()[-300:]}")
        return PACOTE
    # O mesmo .gitignore deve proteger a V2 quando ela e a raiz de outro repositorio.
    repo = tmp_path / "v2"
    repo.mkdir()
    shutil.copyfile(PACOTE / ".gitignore", repo / ".gitignore")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _git(raiz, *args):
    return subprocess.run(["git", "-C", str(raiz), *args], capture_output=True, text=True)


def _ignorado(raiz, caminho):
    return _git(raiz, "check-ignore", "-q", "--no-index", caminho).returncode == 0


@pytest.mark.parametrize("caminho", [
    "vendor/rithmic/0.89.0.0/RProtocolAPI.0.89.0.0.zip",
    "qualquer/coisa.zip",
    "rapi_20261008_respostas.eml",
    "backup.bundle",
    "backup.tgz",
    "backup.tar",
    "backup.tar.gz",
    "data_lake/v2/captura/market_frames.jsonl",
    "x/rithmic_paper_protocol/c7837cec/manifest.json",
    "capturas/123/manifest.json",
    "derivados/candles_1m/123.json",
    "coletor.token",
    ".env.local",
])
def test_nunca_versionar(raiz, caminho):
    assert _ignorado(raiz, caminho)


@pytest.mark.parametrize("caminho", [
    "vendor/rithmic/0.89.0.0/base.proto",
    "vendor/rithmic/0.89.0.0/manifest.json",
    "qhos2/src/qhos2/config.py",
    "qhos2/tests/test_higiene.py",
    ".env.example",
])
def test_nao_ignorar_o_que_ja_e_versionado(raiz, caminho):
    if caminho.startswith("vendor/rithmic/") and raiz == PACOTE:
        # Na copia publica do QHOS os .proto da Rithmic nao sao distribuidos (licenca) e o .gitignore da raiz exclui
        # vendor/ de proposito. A regra do .gitignore da V2 continua conferida no caso "independente".
        pytest.skip("copia publica: vendor/rithmic nao e distribuido, o .gitignore da raiz o exclui de proposito")
    assert not _ignorado(raiz, caminho)


def test_nada_versionado_esta_ignorado(raiz):
    r = _git(raiz, "ls-files", "-ci", "--exclude-standard")
    assert r.returncode == 0
    assert r.stdout.strip() == ""
