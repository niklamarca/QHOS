"""A suite roda isolada e com markers estritos (QHOS-AUD-001, QHOS-AUD-010)."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HOME_DE_QUEM_EXECUTA, PASTA_DA_SUITE

RAIZ = Path(__file__).resolve().parents[1]
CONECTOR = ["tests/test_conector.py::test_captura_inteira_sobre_websocket_tls",
            "tests/test_conector.py::test_queda_do_servidor_no_meio_da_sessao"]  # o segundo deixa QUARENTENA
GUARDA_NO_HOME = Path(".local") / "state" / "qhos2" / "operacao" / "estado.json"


def _pytest(args, casa, cwd=RAIZ):
    env = {**os.environ, "HOME": str(casa), "USERPROFILE": str(casa)}
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-p", "no:randomly", *args],
                          cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)


def _precisa_da_guarda():
    pytest.importorskip("fcntl")  # a guarda usa flock: so Linux
    pytest.importorskip("cryptography")


def test_isolamento_nao_toca_home_real(request, tmp_path):
    from qhos2 import captura, coletor, operacao
    from qhos2.config import data_root_padrao
    try:
        from qhos2 import agenda
    except KeyError:  # sem banco de fusos (QHOS-AUD-004): a agenda nao importa, e o resto do isolamento ainda vale
        agenda = None
    casa = Path.home()
    assert casa == request.config.stash[PASTA_DA_SUITE] / "home"  # igualdade exata: %TEMP% pode morar no perfil
    for real in request.config.stash[HOME_DE_QUEM_EXECUTA]:
        if real:
            assert casa != Path(real) and not Path(real).is_relative_to(casa)
    assert "QHOS2_DATA_ROOT" not in os.environ and "QHOS2_ENDPOINT" not in os.environ
    assert data_root_padrao() == casa / "qhos-v2-data"
    assert coletor.TOKEN_PADRAO.is_relative_to(casa)  # avaliado no import, ja com o HOME redirecionado
    for modulo in (operacao, captura, agenda):
        assert modulo is None or modulo.estado_padrao() == tmp_path / "guarda-operacao"
    assert operacao.GuardaCaptura().raiz == tmp_path / "guarda-operacao"


def test_suite_repetivel_e_a_guarda_fica_no_tmp_path_do_teste(tmp_path):
    _precisa_da_guarda()
    casa, bt = tmp_path / "casa", tmp_path / "bt"
    casa.mkdir()
    for _ in range(2):  # sem o isolamento, a segunda falhava com captura_em_quarentena
        r = _pytest(["--basetemp", str(bt), *CONECTOR], casa)
        assert r.returncode == 0 and "2 passed" in r.stdout, r.stdout[-2000:]
    assert sorted(p.name for p in casa.iterdir()) == []
    estado = json.loads((bt / "test_queda_do_servidor_no_meio0" / "guarda-operacao" / "estado.json").read_text(encoding="utf-8"))
    assert estado["estado"] == "QUARENTENA" and estado["motivo"] == "encerramento_nao_confirmado"


def test_sem_o_isolamento_a_guarda_cai_no_home(tmp_path):
    """Controle: sem o conftest a guarda vai para o HOME do filho e a segunda rodada cai em quarentena (o defeito da 001)."""
    _precisa_da_guarda()
    casa = tmp_path / "casa"
    casa.mkdir()
    r = _pytest(["--noconftest", *CONECTOR], casa)
    assert r.returncode == 0 and "2 passed" in r.stdout, r.stdout[-2000:]
    assert json.loads((casa / GUARDA_NO_HOME).read_text(encoding="utf-8"))["estado"] == "QUARENTENA"
    r = _pytest(["--noconftest", *CONECTOR], casa)
    assert r.returncode != 0 and "captura_em_quarentena" in r.stdout, r.stdout[-2000:]


def test_markers_registrados(tmp_path):
    # o conftest vai junto: e ele quem liga os markers estritos, e so vale para quem o carrega
    shutil.copy(RAIZ / "tests" / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_sonda.py").write_text("import pytest\n\n@pytest.mark.inventado\ndef test_x():\n    pass\n",
                                            encoding="utf-8")
    r = _pytest(["-c", str(RAIZ / "pyproject.toml"), "--rootdir", str(tmp_path), "test_sonda.py"], tmp_path, cwd=tmp_path)
    assert r.returncode != 0 and "'inventado' not found in `markers`" in r.stdout + r.stderr, r.stdout[-2000:]
