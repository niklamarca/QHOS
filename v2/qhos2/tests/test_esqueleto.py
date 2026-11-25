"""Prova que o pacote testado e o qhos2 da V2, e nao codigo da V1."""
from pathlib import Path

import qhos2


def test_versao():
    assert qhos2.__version__ == "0.2.6"


def test_importa_da_pasta_v2():
    origem = Path(qhos2.__file__).resolve()
    assert origem.parent.name == "qhos2"
    assert origem.parent.parent.name == "src"
    assert origem.parent.parent.parent.name == "qhos2"


def test_nao_importa_codigo_da_v1():
    """A V2 nasce isolada: nenhum arquivo de src/ importa os pacotes da V1."""
    import re

    v1 = ("core", "data", "execution", "adapters", "governance", "validation", "evolution",
          "backtest", "orchestrator", "dashboard", "risk", "portfolio", "regime", "strategies")
    padrao = re.compile(r"^\s*(?:from|import)\s+(%s)\b" % "|".join(v1), re.MULTILINE)
    raiz = Path(qhos2.__file__).resolve().parent
    achados = [str(arq) for arq in raiz.rglob("*.py") if padrao.search(arq.read_text(encoding="utf-8"))]
    assert achados == []
