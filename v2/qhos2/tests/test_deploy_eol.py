"""Fim de linha das units de `deploy/` no indice do git e delta entre a unit do Brain instalada e a de referencia
(QHOS-AUD-002).

systemd tolera CR, mas uma unit com CRLF no indice nao e "byte a byte igual" a uma unit LF instalada, e o hash de
qualquer copia muda com o checkout. `deploy/.gitattributes` fixa LF para `*.service` e `install.sh`. O teste le o
indice (`git ls-files --eol`), nao a arvore: num clone Windows antigo com `autocrlf=true` a arvore pode continuar CRLF
ate um re-checkout de `deploy/` (ver a ficha da 002), e isso nao e defeito do repositorio.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PACOTE = Path(__file__).resolve().parents[1]
DEPLOY = PACOTE / "deploy"
INSTALADA = PACOTE / "operacao" / "wsl" / "systemd-user"


def _eol_no_indice(nome):
    if not shutil.which("git"):
        pytest.skip("git ausente")
    r = subprocess.run(["git", "-C", str(DEPLOY), "ls-files", "--eol", "--", nome], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        pytest.skip("fora de um checkout git")
    campos = r.stdout.split()
    return campos[0], " ".join(campos[2:-1])   # ("i/lf", "attr/text eol=lf")


@pytest.mark.parametrize("nome", ["qhos2-coletor.service", "qhos2-brain.service", "install.sh"])
def test_arquivos_de_deploy_sao_lf_no_indice(nome):
    indice, atributos = _eol_no_indice(nome)
    assert indice == "i/lf", f"{nome} esta com CRLF no indice do git ({indice}); a unit instalada no WSL e LF"
    assert "eol=lf" in atributos


def test_gitattributes_de_deploy_fixa_lf():
    texto = (DEPLOY / ".gitattributes").read_text(encoding="utf-8")
    assert "*.service text eol=lf" in texto and "install.sh text eol=lf" in texto


def _linhas(caminho):
    return caminho.read_bytes().decode("utf-8").replace("\r\n", "\n").splitlines()


def test_unit_do_brain_instalada_difere_da_referencia_so_pelo_environmentfile():
    """`operacao/README.md` afirma: difere por uma linha, `EnvironmentFile=`. Esta e a afirmacao conferida."""
    referencia = _linhas(DEPLOY / "qhos2-brain.service")
    instalada = _linhas(INSTALADA / "qhos2-brain.service")
    faltam = [linha for linha in referencia if linha not in instalada]
    sobram = [linha for linha in instalada if linha not in referencia]
    assert faltam == []
    assert len(sobram) == 1 and sobram[0].startswith("EnvironmentFile=%h/qhos-v2-data/brain/service.env")


def test_unit_do_coletor_instalada_nao_esta_duplicada_nem_tem_hash():
    """Nao ha copia nem hash da unit do coletor em `operacao/`: a igualdade com a instalada nao e conferivel aqui."""
    assert not (INSTALADA / "qhos2-coletor.service").exists()
    sumas = (PACOTE / "operacao" / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "qhos2-coletor.service" not in sumas
    readme = (PACOTE / "operacao" / "README.md").read_text(encoding="utf-8")
    assert "instalado é idêntico a `deploy/qhos2-coletor.service`" not in readme, \
        "o README afirma identidade byte a byte que nenhum hash sustenta"
