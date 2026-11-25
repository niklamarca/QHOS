"""Espaco livre real: no WSL o vhdx so cresce ate o livre do disco do Windows que o guarda (auditoria de 28/10)."""
import shutil
from collections import namedtuple
from pathlib import Path

import pytest

from qhos2 import captura, disco, operacao
from qhos2.gravador import Gravador, GravadorError

GIB = 1024 ** 3
Uso = namedtuple("Uso", "total used free")
IDENT = {"system_name": "Rithmic Paper Trading", "requested_symbol": "ESZ6", "exchange": "CME"}


def _discos(monkeypatch, tmp_path, linux, windows):
    montagem = tmp_path / "mnt_c"
    montagem.mkdir()
    monkeypatch.setenv("QHOS2_DISCO_HOSPEDEIRO", str(montagem))

    def uso(caminho):
        livre = windows if str(caminho) == str(montagem) else linux
        if livre is None:
            raise OSError("disco hospedeiro sem resposta")
        return Uso(2000 * GIB, 0, int(livre * GIB))
    monkeypatch.setattr(shutil, "disk_usage", uso)


@pytest.mark.parametrize("linux, windows, esperado", [(810, 298, 298), (50, 298, 50), (810, None, 810)])
def test_livre_real_e_o_menor_dos_dois_e_ignora_hospedeiro_mudo(monkeypatch, tmp_path, linux, windows, esperado):
    _discos(monkeypatch, tmp_path, linux, windows)
    assert disco.livre_bytes(tmp_path / "ainda_nao_existe") == esperado * GIB


def test_hospedeiro_vazio_desliga_e_padrao_do_wsl_e_mnt_c(monkeypatch):
    monkeypatch.setenv("QHOS2_DISCO_HOSPEDEIRO", "")
    assert disco.hospedeiro() is None
    monkeypatch.delenv("QHOS2_DISCO_HOSPEDEIRO")

    # sem a variavel, decide pelo nucleo: os tres casos sao simulados, nao lidos da maquina que roda a suite
    # (QHOS-AUD-125: no WSL real o teste antigo so via um lado)
    def nucleo(texto):
        def ler(self, *args, **kwargs):
            assert self.as_posix() == "/proc/sys/kernel/osrelease"   # independente do separador (Windows: `\`)
            if texto is None:
                raise OSError("sem /proc")
            return texto
        return ler
    for texto, esperado in (("5.15.167.4-microsoft-standard-WSL2", Path("/mnt/c")), ("6.8.0-45-generic", None),
                            (None, None)):
        monkeypatch.setattr(disco.Path, "read_text", nucleo(texto))
        assert disco.hospedeiro() == esperado, texto


def test_avaliar_espaco_recusa_pelo_disco_do_windows(monkeypatch, tmp_path):
    _discos(monkeypatch, tmp_path, linux=810, windows=10)
    assert captura.avaliar_espaco(tmp_path / "capturas", 12 * GIB) == ("recusa", 10 * GIB)


def test_gravador_para_quando_o_disco_do_windows_enche(monkeypatch, tmp_path):
    _discos(monkeypatch, tmp_path, linux=810, windows=0.1)
    with pytest.raises(GravadorError, match="disco_quase_cheio"):
        Gravador(tmp_path / "dados", identidade=IDENT)


def test_diagnostico_da_operacao_mostra_o_livre_real(monkeypatch, tmp_path):
    _discos(monkeypatch, tmp_path, linux=810, windows=298)
    assert operacao.diagnosticar(tmp_path / "dados")["livre_bytes"] == 298 * GIB
