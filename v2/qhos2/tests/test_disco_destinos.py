"""Bancada com dois destinos: o livre que vale e o do volume onde a raiz fica, limitado pelo disco hospedeiro (QHOS-AUD-208).

Relacao destino -> volume, derivada do codigo:
- `config.data_root_padrao()` = `~/qhos-v2-data` (no WSL, o ext4 do .vhdx); `--raiz`/`QHOS2_DATA_ROOT` podem apontar
  para outro volume, inclusive `/mnt/c/...`.
- `disco.livre_bytes(raiz)` = `shutil.disk_usage` do ancestral existente mais proximo da raiz, e no WSL o menor entre
  isso e o livre de `QHOS2_DISCO_HOSPEDEIRO` (padrao `/mnt/c`). `df -h /home/usuario` sozinho so mostra a primeira parcela.
Nada aqui le disco ou configuracao reais: `shutil.disk_usage` e substituido.
"""
import shutil
from collections import namedtuple

import pytest

from qhos2 import captura, disco, operacao
from qhos2.config import data_root_padrao

GIB = 1024 ** 3
Uso = namedtuple("Uso", "total used free")


@pytest.fixture
def bancada(monkeypatch, tmp_path):
    """Dois volumes falsos: `linux` (onde fica `raiz_linux`) e `windows` (a montagem hospedeira, onde fica `raiz_c`)."""
    montagem = tmp_path / "mnt_c"
    montagem.mkdir()
    monkeypatch.setenv("QHOS2_DISCO_HOSPEDEIRO", str(montagem))
    livres = {}

    def uso(caminho):
        volume = "windows" if str(caminho).startswith(str(montagem)) else "linux"
        return Uso(2000 * GIB, 0, int(livres[volume] * GIB))
    monkeypatch.setattr(shutil, "disk_usage", uso)
    return livres, tmp_path / "home" / "qhos-v2-data", montagem / "qhos-v2-data"


@pytest.mark.parametrize("linux, windows, esperado_linux, esperado_c", [
    (810, 298, 298, 298),   # vhdx com 810 "livres" mas o C: so tem 298: os dois destinos valem 298
    (50, 298, 50, 298),     # vhdx quase cheio: a raiz no Linux ve 50, a raiz em /mnt/c ve 298
    (810, 10, 10, 10),      # C: quase cheio: nenhum destino escapa
])
def test_o_livre_e_o_do_volume_do_destino_limitado_pelo_hospedeiro(bancada, linux, windows, esperado_linux, esperado_c):
    livres, raiz_linux, raiz_c = bancada
    livres.update(linux=linux, windows=windows)
    assert disco.livre_bytes(raiz_linux) == esperado_linux * GIB
    assert disco.livre_bytes(raiz_c) == esperado_c * GIB


def test_a_cli_e_o_diagnostico_usam_a_mesma_conta_nos_dois_destinos(bancada):
    livres, raiz_linux, raiz_c = bancada
    livres.update(linux=50, windows=298)
    assert captura.avaliar_espaco(raiz_linux / "capturas", 12 * GIB) == ("ok", 50 * GIB)
    assert captura.avaliar_espaco(raiz_c / "capturas", 12 * GIB) == ("ok", 298 * GIB)
    livres.update(linux=810, windows=20)
    assert captura.avaliar_espaco(raiz_linux / "capturas", 12 * GIB) == ("aviso", 20 * GIB)
    assert operacao.diagnosticar(raiz_linux)["livre_bytes"] == 20 * GIB
    assert operacao.diagnosticar(raiz_c)["livre_bytes"] == 20 * GIB


def test_raiz_padrao_fica_no_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert data_root_padrao() == tmp_path / "qhos-v2-data"
