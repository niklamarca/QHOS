"""Campanha de correcao (lote l5a-captura): configuracao (QHOS-AUD-098).

`src/qhos2` congelado (J0): o defeito confirmado fica em `xfail(strict=True)`, patch proposto em
`09_correcao/EVIDENCIAS/QHOS-AUD-098/patch_proposto.diff`.
"""
from pathlib import Path

import pytest

from qhos2.config import ConfigError, Settings


# ==== QHOS-AUD-098 ====
# Settings.from_environ:109-110 trata QHOS2_DATA_ROOT="" como nao definido e usa ~/qhos-v2-data em silencio.

@pytest.mark.xfail(strict=True, reason="QHOS-AUD-098: Settings.from_environ:109 testa env.get('QHOS2_DATA_ROOT') "
                                        "pela verdade; a string vazia e tratada como ausente e cai em ~/qhos-v2-data")
def test_data_root_vazio_e_recusado():
    with pytest.raises(ConfigError):
        Settings.from_environ({"QHOS2_DATA_ROOT": ""})


def test_data_root_definido_e_respeitado_e_relativo_e_recusado(tmp_path):
    assert Settings.from_environ({"QHOS2_DATA_ROOT": str(tmp_path / "x")}).data_root == (tmp_path / "x").resolve()
    with pytest.raises(ConfigError, match="data_root_precisa_ser_absoluto"):
        Settings.from_environ({"QHOS2_DATA_ROOT": "relativo"})
    assert Settings.from_environ({}).data_root == (Path.home() / "qhos-v2-data").resolve()  # HOME isolado pelo conftest
