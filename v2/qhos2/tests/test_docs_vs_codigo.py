"""Texto corrente (docs, comentarios, help) conferido contra o codigo (lote l4-docs da correcao de 28/10).

Os casos marcados `xfail(strict=True)` apontam texto dentro de arquivos congelados na janela J0 (`src/qhos2/*.py`,
`terminal/index.html` e `terminal/modulos/*.js`); o texto proposto esta em `EVIDENCIAS/<ID>/patch_proposto.diff`.
Cada xfail testa so a ausencia do erro, para que qualquer redacao do autor o faca passar (XPASS estrito avisa para
remover a marca). Itens de estilo (linhas em branco, espaco apos virgula, ordem de imports) ficam so na ficha da 195.
"""
import io
import re
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from qhos2 import agenda, captura, conector

PACOTE = Path(__file__).resolve().parents[1]
SRC = PACOTE / "src" / "qhos2"


def _texto(caminho: Path) -> str:
    return caminho.read_bytes().decode("utf-8").replace("\r\n", "\n")


# ---- QHOS-AUD-164: cabecalho de qhos-bridge.js -------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-164: qhos-bridge.js diz que o coletor 'ainda precisa implementar "
                                       "docs/QHOS-BRIDGE-CONTRACT.md'; o arquivo nao existe e coletor.py ja serve "
                                       "qhos.dashboard.v1 (terminal/index.html congelado em J0)")
def test_bridge_js_nao_promete_contrato_inexistente():
    fonte = _texto(PACOTE / "terminal" / "modulos" / "qhos-bridge.js")
    cabecalho = fonte.split("*/", 1)[0]
    contrato = PACOTE / "docs" / "QHOS-BRIDGE-CONTRACT.md"
    assert "must implement" not in cabecalho
    assert "QHOS-BRIDGE-CONTRACT.md" not in cabecalho or contrato.is_file()


def test_bridge_js_afirmacoes_que_batem_com_o_codigo():
    """As demais frases do cabecalho conferem: protocolo `qhos.dashboard.v1`, sem dado de demonstracao, sem API de ordem."""
    from qhos2 import coletor
    fonte = _texto(PACOTE / "terminal" / "modulos" / "qhos-bridge.js")
    assert f"export const QHOS_BRIDGE_PROTOCOL = '{coletor.PROTOCOLO}';" in fonte
    assert "localStorage" not in fonte and "sessionStorage" not in fonte
    assert not re.search(r"\b(placeOrder|submitOrder|demoData|DEMO)\b", fonte)


# ---- QHOS-AUD-195: comentario que sugere redundancia (src/qhos2, congelado) ---------------------------------------

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-195: historico.py comenta 'recusa captura nao verificada' numa "
                                       "segunda verificacao proposital (src/qhos2 congelado em J0)")
def test_historico_nao_descreve_a_segunda_verificacao_como_recusa():
    linhas = [l for l in _texto(SRC / "historico.py").splitlines() if "Barras().eventos(ev.eventos_da_captura" in l]
    assert len(linhas) == 1, "ancora da linha mudou: revisar o teste junto com a ficha da 195"
    assert "recusa captura nao verificada" not in linhas[0]


# ---- QHOS-AUD-208: o RUNBOOK aponta o livre real -----------------------------------------------------------------

def test_runbook_aponta_o_livre_real_e_nao_so_o_df_do_linux():
    texto = _texto(PACOTE / "docs" / "RUNBOOK_CAPTURA.md")
    passos = [l for l in texto.splitlines() if l.startswith("1. ") and "24 GiB" in l]
    assert len(passos) == 1
    passo = passos[0]
    # `df -h /home/usuario` sozinho mostra o teto do vhdx; a conta que a CLI e o gravador usam e a de qhos2.disco.
    assert not passo.startswith("1. `df -h /home/usuario`:")
    assert "diagnosticar" in passo and "livre_bytes" in passo
    assert "/mnt/c" in passo and "24 GiB" in passo and "12,5 GiB" in passo


# ---- QHOS-AUD-209: "regra R6" nao existe -------------------------------------------------------------------------

def test_plano_nao_define_regra_r6():
    texto = _texto(PACOTE / "docs" / "PLANO_V2.md")
    assert not re.search(r"\bR6\b", texto)
    assert "## 5. Regras de projeto" in texto


def test_readme_da_operacao_nao_cita_r6():
    texto = _texto(PACOTE / "operacao" / "README.md")
    assert not re.search(r"\bR6\b", texto)


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-209: agenda.py cita '(R6)'; o conteudo e a regra 17 do plano "
                                       "(src/qhos2 congelado em J0)")
def test_agenda_nao_cita_r6():
    assert not re.search(r"\bR6\b", agenda.__doc__)


# ---- QHOS-AUD-215: help do --max-gb ----------------------------------------------------------------------------

def _help_da_captura() -> str:
    saida = io.StringIO()
    with redirect_stdout(saida), pytest.raises(SystemExit) as fim:
        captura.main(["--help"])
    assert fim.value.code == 0
    return " ".join(saida.getvalue().split())


@pytest.mark.xfail(strict=True, reason="QHOS-AUD-215: o help diz 'recusa abaixo de 1x', o codigo recusa abaixo de "
                                       "1x + 512 MiB (captura.avaliar_espaco; src/qhos2 congelado em J0)")
def test_help_do_max_gb_nao_diz_que_so_recusa_abaixo_de_1x():
    assert "recusa abaixo de 1x)" not in _help_da_captura()


# ---- QHOS-AUD-217: docstring do conector -------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-217: 'mesma versao do ambiente Rithmic do V1' nao e verificavel "
                                       "neste repositorio; a versao travada e a do pyproject (src/qhos2 congelado)")
def test_conector_nao_afirma_equivalencia_nao_verificada_com_o_v1():
    assert "mesma versao do ambiente Rithmic do V1" not in conector.__doc__


def test_versao_do_websockets_travada_e_a_mesma_no_pyproject_e_no_lock():
    pyproject = _texto(PACOTE / "pyproject.toml")
    lock = _texto(PACOTE / "requirements-linux.lock")
    versao = re.search(r'"websockets==([0-9.]+)"', pyproject).group(1)
    assert f"websockets=={versao}" in lock
