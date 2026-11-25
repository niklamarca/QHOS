"""Docstrings do Brain nao podem descrever o V1 como presente (QHOS-AUD-213) nem prometer sincronia e testes
que a V2 nao tem (QHOS-AUD-211). Le o fonte com ast; nao importa os modulos."""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"

# Cada entrada: arquivo e frases que afirmam um componente/garantia ausente na V2.
AFIRMACOES_V1 = {
    "brain/api.py": ["Reuses FastAPI + WebSocket already in dashboard/control_tower.py"],
    "brain/control_plane.py": ["stays in dashboard/_hybrid.html MAP_NODES"],
    "brain/executive.py": ["remains the runtime kernel"],
    "brain/cognitive.py": ["is a different system"],
    "brain/gateway.py": ["DNA model_committee is not this gateway"],
    "brain/providers.py": ["must not import evolution."],
    "brain/policies.py": ["is a different ladder"],
    "brain/perception.py": ["configured Trader repository"],
    "brain_service/feed_classification.py": ["is distributed byte-identical", "a test asserts they still agree"],
    "brain_service/rithmic_operational.py": ["is distributed unchanged"],
}


@pytest.mark.parametrize("relativo", sorted(AFIRMACOES_V1))
def test_docstring_nao_afirma_componente_ou_garantia_ausente_na_v2(relativo):
    docstring = ast.get_docstring(ast.parse((SRC / relativo).read_text(encoding="utf-8"))) or ""
    assert docstring, relativo
    presentes = [frase for frase in AFIRMACOES_V1[relativo] if frase in docstring]
    assert presentes == [], f"{relativo}: a docstring ainda afirma {presentes}"


def test_feed_classification_declara_que_e_a_unica_copia_na_v2():
    docstring = ast.get_docstring(ast.parse((SRC / "brain_service/feed_classification.py").read_text(encoding="utf-8")))
    assert "only this copy exists" in docstring and "no V2 test compares" in docstring
    copias = [p for p in SRC.rglob("feed_classification.py")]
    assert copias == [SRC / "brain_service" / "feed_classification.py"]
