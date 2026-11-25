"""CLI da captura em torno do teto: o que acontece com o livre em 1x, 1x + reserva e 2x (QHOS-AUD-215).

O help do `--max-gb` diz "recusa abaixo de 1x"; `captura.avaliar_espaco` recusa quando `livre < max_bytes +
MIN_LIVRE_PADRAO` (512 MiB), como dizem o RUNBOOK e o PLANO ("recusa abaixo de 12,5 GiB"). Entradas sinteticas: o
espaco livre e substituido; sem `--online` nada conecta e a guarda nao e tocada.
"""
import pytest

from qhos2 import captura, disco
from qhos2.gravador import MIN_LIVRE_PADRAO

GIB = 1024 ** 3
TETO = 12 * GIB


@pytest.fixture
def livre(monkeypatch):
    valores = {}
    monkeypatch.setattr(disco, "livre_bytes", lambda _: valores["bytes"])
    return valores


def _rodar(tmp_path, capsys):
    codigo = captura.main(["--symbol", "ESZ6", "--exchange", "CME", "--max-gb", "12", "--raiz", str(tmp_path / "capturas")])
    saida = capsys.readouterr().out
    assert codigo == 0 and "nada foi conectado" in saida
    return saida


def test_reserva_e_de_512_mib():
    assert MIN_LIVRE_PADRAO == 512 * 1024 ** 2


@pytest.mark.parametrize("livre_bytes, situacao", [
    (TETO - 1, "recusa"),                    # abaixo de 1x
    (TETO, "recusa"),                        # exatamente 1x: o help diz que nao recusa; o codigo recusa
    (TETO + MIN_LIVRE_PADRAO - 1, "recusa"), # abaixo de 1x + reserva
    (TETO + MIN_LIVRE_PADRAO, "aviso"),      # 12,5 GiB: para de recusar, ainda avisa
    (2 * TETO - 1, "aviso"),
    (2 * TETO, "ok"),
])
def test_limiares_de_avaliar_espaco(livre, livre_bytes, situacao, tmp_path):
    livre["bytes"] = livre_bytes
    assert captura.avaliar_espaco(tmp_path, TETO) == (situacao, livre_bytes)


def test_cli_recusa_com_exatamente_1x_livre(livre, tmp_path, capsys):
    livre["bytes"] = TETO
    saida = _rodar(tmp_path, capsys)
    assert "ESPACO INSUFICIENTE" in saida and "reserva minima" in saida


def test_cli_so_avisa_a_partir_de_1x_mais_a_reserva(livre, tmp_path, capsys):
    livre["bytes"] = TETO + MIN_LIVRE_PADRAO
    saida = _rodar(tmp_path, capsys)
    assert "ESPACO INSUFICIENTE" not in saida and "AVISO" in saida


def test_cli_nao_avisa_com_2x(livre, tmp_path, capsys):
    livre["bytes"] = 2 * TETO
    saida = _rodar(tmp_path, capsys)
    assert "ESPACO INSUFICIENTE" not in saida and "AVISO" not in saida
