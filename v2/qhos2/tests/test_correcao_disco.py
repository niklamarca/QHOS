"""Campanha de correcao (lote l5a-captura): espaco livre real com disco hospedeiro mudo (QHOS-AUD-039).

`src/qhos2` congelado (J0): o defeito confirmado fica em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/QHOS-AUD-039/patch_proposto.diff`.
"""
import shutil
from collections import namedtuple

import pytest

from qhos2 import captura, disco

GIB = 1024 ** 3
Uso = namedtuple("Uso", "total used free")


def _discos(monkeypatch, tmp_path, linux, windows):
    montagem = tmp_path / "mnt_c"
    montagem.mkdir()
    monkeypatch.setenv("QHOS2_DISCO_HOSPEDEIRO", str(montagem))

    def uso(caminho):
        livre = windows if str(caminho) == str(montagem) else linux
        if livre is None:
            raise OSError("disco hospedeiro sem resposta (sintetico)")
        return Uso(2000 * GIB, 0, int(livre * GIB))
    monkeypatch.setattr(shutil, "disk_usage", uso)


def sem_conexao(*_):
    raise AssertionError("nao devia conectar nem pedir credenciais")


# ==== QHOS-AUD-039 ====
# disco.livre_bytes:33-37 engole OSError do disco hospedeiro; avaliar_espaco diz "ok" e a CLI nao avisa.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-039: disco.livre_bytes:36-37 engole o OSError do disco hospedeiro e "
                                        "devolve so o livre do Linux; captura.avaliar_espaco responde 'ok' e a CLI "
                                        "nao avisa que o livre real e desconhecido")
def test_hospedeiro_mudo_e_sinalizado(monkeypatch, tmp_path, capsys):
    _discos(monkeypatch, tmp_path, linux=810, windows=None)
    situacao, livre = captura.avaliar_espaco(tmp_path / "capturas", 12 * GIB)
    assert situacao != "ok", (situacao, livre)  # o livre real e desconhecido: nunca "ok" em silencio
    monkeypatch.setenv("QHOS2_DATA_ROOT", str(tmp_path / "dados"))
    monkeypatch.setattr(captura, "_criar_fabrica", sem_conexao)
    assert captura.main(["--symbol", "ESZ6", "--exchange", "CME"], entrada=sem_conexao, senha=sem_conexao) == 0
    assert "hospedeiro" in capsys.readouterr().out.lower()


def test_hospedeiro_mudo_nao_derruba_a_captura_sozinho(monkeypatch, tmp_path):
    _discos(monkeypatch, tmp_path, linux=810, windows=None)
    assert disco.livre_bytes(tmp_path / "capturas") == 810 * GIB  # vale o livre do Linux (docstring de disco.py)
    situacao, livre = captura.avaliar_espaco(tmp_path / "capturas", 12 * GIB)
    assert situacao != "recusa" and livre == 810 * GIB
