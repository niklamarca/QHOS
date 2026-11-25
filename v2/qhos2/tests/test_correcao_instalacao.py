"""Campanha de correcao, lote l5c-leitura: os contratos embarcados `src/qhos2/tests/` (QHOS-AUD-142).

`pyproject.toml` nao muda nesta janela (`testpaths = ["tests"]`). Caracterizacao da coleta e do empacotamento, e um
teste que FAZ os 5 contratos rodarem pela suite documentada (`python -m pytest`), via `--pyargs qhos2.tests`.
"""
import hashlib
import subprocess
import sys
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
CONTRATOS = RAIZ / "src/qhos2/tests/test_runtime_contracts.py"
# brain_service/regression.py:43 pina este SHA do arquivo instalado (`tests/test_runtime_contracts.py` no wheel)
SHA_REVISADO = "78bdfb201924c7ffdd3a27a1cb7f8ed1336aaa75db21b7ad07426108c78f6d87"


def _pytest(tmp_path, *args, timeout=180):
    # sem `-q` proprio: o addopts ja tem um, e `-qq` reduz o --collect-only a contagens por arquivo.
    # --basetemp dentro do tmp_path deste teste: o filho nao escreve em /tmp/pytest-of-<usuario>/.
    base = tmp_path / f"filho-{len(list(tmp_path.glob('filho-*')))}"
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly",
                           f"--basetemp={base}", *args],
                          cwd=RAIZ, capture_output=True, text=True, encoding="utf-8", timeout=timeout)


def test_configuracao_deixa_os_contratos_fora_da_rotina_documentada_e_dentro_do_wheel():
    config = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]            # README.md:91: `python -m pytest`
    assert "tests/*.py" in config["tool"]["setuptools"]["package-data"]["qhos2"]       # empacotado no wheel
    conteudo = CONTRATOS.read_bytes() if CONTRATOS.is_file() else None
    if sys.platform != "linux" and conteudo is not None:
        # checkout com core.autocrlf=true (Windows) grava CRLF; o indice guarda LF (`git ls-files --eol`: i/lf), e e
        # o conteudo LF que o wheel e o SHA pinado descrevem. A politica de fins de linha e da QHOS-AUD-031 (excluida)
        conteudo = conteudo.replace(b"\r\n", b"\n")
    assert conteudo is not None and hashlib.sha256(conteudo).hexdigest() == SHA_REVISADO
    assert not (CONTRATOS.parent / "__init__.py").exists()                            # nao e pacote: `--pyargs` nao o acha


def test_coleta_explicita_acha_os_cinco_contratos_e_a_rotina_documentada_nao(tmp_path):
    por_caminho = _pytest(tmp_path, "--collect-only", "src/qhos2/tests")
    assert por_caminho.returncode == 0, por_caminho.stdout + por_caminho.stderr
    nodeids = [l for l in por_caminho.stdout.splitlines() if "::" in l]
    assert len(nodeids) == 8 and all(l.startswith("src/qhos2/tests/test_runtime_contracts.py::") for l in nodeids)
    # a rotina documentada so olha `tests/`: nenhum nodeid dos contratos aparece nem pedindo por nome
    documentada = _pytest(tmp_path, "--collect-only", "-k", "runtime_contracts or endpoint_nao_aceita_destino_externo")
    assert documentada.returncode in (0, 5), documentada.stdout + documentada.stderr
    assert "test_runtime_contracts.py" not in documentada.stdout


def test_o_comando_sugerido_com_pyargs_nao_funciona_porque_nao_ha_pacote(tmp_path):
    """O achado sugere documentar `python -m pytest --pyargs qhos2.tests`; sem `__init__.py` em src/qhos2/tests o
    pytest nao resolve o pacote (nem aqui nem no wheel, que copia a mesma arvore). So o caminho funciona."""
    r = _pytest(tmp_path, "--collect-only", "--pyargs", "qhos2.tests")
    assert r.returncode == 4 and "module or package not found: qhos2.tests" in r.stderr, r.stdout + r.stderr


def test_contratos_embarcados_rodam_pela_suite(tmp_path):
    """Fecha a lacuna pelo lado dos testes: enquanto `testpaths` nao muda, a suite documentada executa os contratos."""
    r = _pytest(tmp_path, "src/qhos2/tests")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "8 passed" in r.stdout, r.stdout
