"""Peca 4d: o conteudo das mensagens de mercado e lido igual ao da biblioteca oficial da Rithmic."""
import struct

import pytest

from qhos2 import mercado as m

# Vetores gerados pela biblioteca oficial do protobuf a partir dos .proto da Rithmic (fora do repositorio).
D160 = bytes.fromhex(
    "a2e1350445535a36aae13503434d45c8e23501c8e23502c8e2350390d836b1d1f9d603b2ef48024131b2ef48024232b2ef48024333"
    "a0a549b0f6e9c706a8a549c0c40780b849b0f6e9c70688b84988c407a0b849afdfeb3ac0c449b0f6e9c706e0c449a0d1e53ae0804b01"
    "e0804b02e0804b01e8804b0be8804b16e8804b21a9b24b0000000040c2ba40a9b24b0000000080c2ba40a9b24b00000000c0c1ba40"
    "b0b24b03b0b24b07b0b24b0098b64ba001d1d14b00000000c0c2ba4090d34b01")
D116 = bytes.fromhex("a2e1350445535a36aae13503434d4590d83605b2ef48025839b2ef48025938e0804b02e8804b07e8804b08a9b24b"
                     "0000000000c3ba40b0b24b02b0b24b0498b64b74")
D116_FIM = bytes.fromhex("f2e940013098b64b74")
D161 = bytes.fromhex("a2e1350445535a36aae13503434d4590d836b2d1f9d603a0a549b0f6e9c706a8a549dfc80798b64ba101")


def test_160_com_varias_atualizacoes_alinhadas():
    msg = m.decodificar(D160)
    c = msg.campos
    assert msg.template_id == 160 and (c["symbol"], c["exchange"], c["sequence_number"]) == ("ESZ6", "CME", 987654321)
    assert c["update_type"] == [m.NOVO, m.ALTERACAO, m.REMOCAO]
    assert c["transaction_type"] == [m.COMPRA, m.VENDA, m.COMPRA]
    assert c["depth_price"] == [6850.25, 6850.5, 6849.75] and c["depth_size"] == [3, 7, 0]
    assert c["depth_order_priority"] == [11, 22, 33] and c["exchange_order_id"] == ["A1", "B2", "C3"]
    assert c["prev_depth_price"] == [6850.75] and c["prev_depth_price_flag"] == [True]
    assert (c["ssboe"], c["usecs"], c["source_nsecs"], c["jop_nsecs"]) == (1761246000, 123456, 123400111, 123300000)


def test_116_um_nivel_por_mensagem_sem_carimbo():
    c = m.decodificar(D116).campos
    assert (c["depth_side"], c["depth_price"], c["sequence_number"]) == (m.VENDA, 6851.0, 5)
    assert c["depth_size"] == [2, 4] and c["depth_order_priority"] == [7, 8] and c["exchange_order_id"] == ["X9", "Y8"]
    assert "ssboe" not in c
    assert m.decodificar(D116_FIM).campos == {"rp_code": ["0"]}


def test_161_fim_de_evento():
    c = m.decodificar(D161).campos
    assert (c["sequence_number"], c["ssboe"], c["usecs"], c["symbol"], c["exchange"]) == (
        987654322, 1761246000, 123999, ["ESZ6"], ["CME"])


def _v(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _empacotado(numero, carga):
    return _v(numero << 3 | 2) + _v(len(carga)) + carga


def test_listas_empacotadas_tambem_sao_aceitas():
    dados = (_v(154467 << 3) + _v(160) + _empacotado(110121, _v(1) + _v(3))
             + _empacotado(154405, struct.pack("<2d", 1.5, 2.25)) + _empacotado(153613, _v(5) + _v(300)))
    c = m.decodificar(dados).campos
    assert c["update_type"] == [1, 3] and c["depth_price"] == [1.5, 2.25] and c["depth_order_priority"] == [5, 300]


def test_campo_unico_repetido_vale_o_ultimo_e_prioridade_e_sem_sinal():
    dados = D161 + _v(112002 << 3) + _v(42) + _v(154467 << 3) + _v(161)
    assert m.decodificar(dados).campos["sequence_number"] == 42
    empacotado = _v(154467 << 3) + _v(161) + _empacotado(112002, _v(1) + _v(2))
    assert m.decodificar(empacotado).campos["sequence_number"] == 2
    enorme = (1 << 63) + 5
    dados = _v(154467 << 3) + _v(160) + _v(153613 << 3) + _v(enorme)
    assert m.decodificar(dados).campos["depth_order_priority"] == [enorme]


def test_campo_desconhecido_e_ignorado():
    assert m.decodificar(D161 + _v(999 << 3) + _v(7)).campos["sequence_number"] == 987654322


@pytest.mark.parametrize("dados,codigo", [
    (b"", "quadro_invalido"),
    (_v(154467 << 3) + _v(19), "template_fora_do_mercado"),
    (_v(150100 << 3) + _v(1), "template_fora_do_mercado"),
    (D160[:-1], "varint_invalido"),
    (_v(154467 << 3) + _v(160) + _empacotado(154405, b"\x00" * 9), "double_empacotado_truncado"),
    (_v(154467 << 3) + _v(160) + _empacotado(149238, b"\xff\xfe"), "texto_invalido"),
    (_v(154467 << 3 | 2) + _v(1) + b"x", "tipo_de_fio_invalido"),
])
def test_quadros_ruins_sao_recusados_por_codigo(dados, codigo):
    with pytest.raises(m.MercadoError, match=codigo):
        m.decodificar(dados)


def test_nao_aceita_texto_nem_quadro_grande_demais():
    with pytest.raises(m.MercadoError, match="quadro_invalido"):
        m.decodificar("texto")
    with pytest.raises(m.MercadoError, match="quadro_invalido"):
        m.decodificar(b"\x00" * (1_048_576 + 1))
