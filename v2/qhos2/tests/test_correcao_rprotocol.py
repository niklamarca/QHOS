"""Campanha de correcao, lote l5c-leitura: esquemas do fio (QHOS-AUD-110).

Sem xfail: um teste do conjunto fechado de tipos (pega um typo no ESQUEMAS; provado por mutacao) e uma
caracterizacao do ramo uint do `_converter`. `rprotocol.py` nao muda nesta janela; recusar tipo desconhecido no
`_converter` (2a parte do teste de aceitacao do ACHADOS) fica como opcao em `EVIDENCIAS/QHOS-AUD-110/patch_proposto.diff`.
"""
import pytest

from qhos2 import mercado as m
from qhos2 import rprotocol as p

TIPOS = {"int", "uint", "str", "bool", "double"}


def test_tipos_dos_esquemas_sao_do_conjunto_fechado():
    """Um typo no esquema (`"doubel"`) passaria pelo `_converter` como uint; este teste o pega na fonte."""
    usados = {tipo for esquemas in (p.ESQUEMAS, m.ESQUEMAS) for esquema in esquemas.values()
              for _, tipo, _ in esquema.values()}
    assert usados <= TIPOS and usados == TIPOS


@pytest.mark.parametrize("fio,valor,esperado", [(0, 1, [1]), (2, b"\x01\x02", [1, 2])])
def test_caracterizacao_tipo_de_esquema_desconhecido_cai_no_ramo_uint(fio, valor, esperado):
    """QHOS-AUD-110, caracterizacao: `_converter` (rprotocol.py:258-285) trata tipo que nao e str/double/bool/int como
    uint. O risco (typo no ESQUEMAS) e fechado pelo teste do conjunto fechado acima; recusar no `_converter` e defesa
    adicional (patch em EVIDENCIAS/QHOS-AUD-110)."""
    assert p._converter("doubel", fio, valor) == esperado


def test_tipos_conhecidos_continuam_convertendo():
    assert p._converter("uint", 0, 7) == [7] and p._converter("int", 0, (1 << 64) - 1) == [-1]
    assert p._converter("bool", 2, b"\x01\x00") == [True, False] and p._converter("str", 2, b"ok") == ["ok"]
    with pytest.raises(p.ProtocolError, match="tipo_de_fio_invalido"):
        p._converter("double", 0, 1)
