"""Peca 4a: o contrato do fio do R|Protocol. Os bytes de referencia foram gerados pela biblioteca
oficial do protobuf a partir dos .proto vendorizados (vendor/rithmic/0.89.0.0); aqui nao ha dependencia dela."""
import math
import random
import struct
from pathlib import Path

import pytest

import qhos2.rprotocol as p
from qhos2.rprotocol import ProtocolError, decodificar

PEDIDOS = {
    "info": (lambda: p.codificar_info_sistema(), "98b64b10"),
    "login": (lambda: p.codificar_login(template_version="5.42", user="trader@exemplo.com", password="senha-de-teste",
                                        app_name="nila:QHOS", app_version="qhos2-0.1"),
              "92bd3f096e696c613a51484f53a2bd3f0e73656e68612d64652d7465737465dafb3f12747261646572406578656d706c6f2e636f6d"
              "daad400971686f73322d302e31a8814b01e2814b15526974686d69632050617065722054726164696e6792824b04352e3432e0824b00"
              "98b64b0a"),
    "logout": (lambda: p.codificar_logout(), "98b64b0c"),
    "heartbeat": (lambda: p.codificar_heartbeat(), "98b64b12"),
    "referencia": (lambda: p.codificar_dado_referencia("GCZ6", "COMEX"), "a2e1350447435a36aae13505434f4d455898b64b0e"),
    "assinar": (lambda: p.codificar_assinatura_profundidade("ESZ6", "CME", assinar=True),
                "80ea3001a2e1350445535a36aae13503434d4598b64b75"),
    "cancelar": (lambda: p.codificar_assinatura_profundidade("ESZ6", "CME", assinar=False),
                 "80ea3002a2e1350445535a36aae13503434d4598b64b75"),
    "retrato": (lambda: p.codificar_retrato_profundidade("ESZ6", "CME", "tok123"),
                "a2e1350445535a36aae13503434d45c2e94006746f6b31323398b64b73"),
    "negocios": (lambda: p.codificar_assinatura_negocios("ESZ6", "CME", assinar=True),
                 "80ea3001a2e1350445535a36aae13503434d4598a64b0198b64b64"),
    "negocios_cancelar": (lambda: p.codificar_assinatura_negocios("ESZ6", "CME", assinar=False),
                          "80ea3002a2e1350445535a36aae13503434d4598a64b0198b64b64"),
}

RESPOSTAS = {
    "login": ("c2e940026f6bf2e9400130a2f54a02553189824b0000000000004e4092824b04352e3432ea994b02463198b64b0b",
              {"template_version": "5.42", "user_msg": ["ok"], "rp_code": ["0"], "fcm_id": "F1",
               "unique_user_id": "U1", "heartbeat_interval": 60.0}),
    "info": ("f2e9400130e2814b15526974686d69632050617065722054726164696e67e2814b0a526974686d696320303188834b0188834b0098b64b11",
             {"rp_code": ["0"], "system_name": ["Rithmic Paper Trading", "Rithmic 01"],
              "has_aggregated_quotes": [True, False]}),
    "heartbeat": ("f2e9400130a0a54980f09dc706a8a549bf843d98b64b13",
                  {"rp_code": ["0"], "ssboe": 1760000000, "usecs": 999999}),
    "heartbeat_negativo": ("f2e9400130a0a549fbffffffffffffffff0198b64b13", {"rp_code": ["0"], "ssboe": -5}),
    "logout": ("f2e940013098b64b0d", {"rp_code": ["0"]}),
    "rejeicao": ("f2e9400137f2e94011746578746f20646f207365727669646f7298b64b4b",
                 {"rp_code": ["7", "texto do servidor"]}),
    "logout_forcado": ("98b64b4d", {}),
    "referencia": ("9aee30083230323631323239a2e1350447435a36aae13505434f4d4558f2e940013091b14b9a9999999999b93fa9b14b0000000000"
                   "00594098b64b0fe2cd4b0474727565",
                   {"rp_code": ["0"], "symbol": "GCZ6", "exchange": "COMEX", "expiration_date": "20261229",
                    "min_qprice_change": 0.1, "single_point_value": 100.0, "is_tradable": "true"}),
    "retrato": ("a2e1350445535a36aae13503434d4590d83685808080808080808001c2e94006746f6b313233f2e9400130b2ef480161b2ef480162"
                "e0804b02e8804b0ae8804bffffffffffffffffff01a9b24b000000004088b340b0b24b03b0b24b0798b64b74",
                {"user_msg": ["tok123"], "rp_code": ["0"], "symbol": "ESZ6", "exchange": "CME",
                 "sequence_number": 2 ** 63 + 5, "depth_side": 2, "depth_price": 5000.25, "depth_size": [3, 7],
                 "depth_order_priority": [10, 2 ** 64 - 1], "exchange_order_id": ["a", "b"]}),
    "assinatura": ("f2e940013098b64b76", {"rp_code": ["0"]}),
    "negocios": ("f2e940013098b64b65", {"rp_code": ["0"]}),
}
TEMPLATES = {"login": 11, "info": 17, "heartbeat": 19, "heartbeat_negativo": 19, "logout": 13, "rejeicao": 75,
             "logout_forcado": 77, "referencia": 15, "retrato": 116, "assinatura": 118, "negocios": 101}


def _v(n):
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _template(n):
    return _v(p.F_TEMPLATE << 3) + _v(n)


# ---- escrita: bytes identicos aos da biblioteca oficial ------------------------------------------

@pytest.mark.parametrize("nome", sorted(PEDIDOS))
def test_pedido_identico_ao_oficial(nome):
    montar, esperado = PEDIDOS[nome]
    assert montar().hex() == esperado


def test_so_existem_pedidos_de_sessao_referencia_profundidade_e_negocios():
    assert p.PEDIDOS_PERMITIDOS == {10, 12, 14, 16, 18, 100, 115, 117}


@pytest.mark.parametrize("campos,codigo", [
    ({"system_name": "Rithmic Test"}, "so_rithmic_paper_trading"),
    ({"app_name": "QHOS"}, "app_name_precisa_do_prefixo_nila"),
    ({"app_name": "nila:"}, "app_name_precisa_do_prefixo_nila"),
    ({"app_name": ""}, "app_name_invalido"),
    ({"user": ""}, "user_invalido"),
    ({"password": ""}, "password_invalido"),
    ({"password": "a\x00b"}, "password_invalido"),
    ({"user": "x" * 257}, "user_invalido"),
    ({"template_version": ""}, "template_version_invalido"),
    ({"app_version": None}, "app_version_invalido"),
])
def test_login_recusado(campos, codigo):
    base = dict(template_version="5.42", user="u", password="p", app_name="nila:QHOS", app_version="v")
    base.update(campos)
    with pytest.raises(ProtocolError) as erro:
        p.codificar_login(**base)
    assert str(erro.value) == codigo


def test_erro_de_login_nunca_vaza_a_senha():
    with pytest.raises(ProtocolError) as erro:
        p.codificar_login(template_version="5.42", user="u", password="SEGREDO-123", app_name="QHOS", app_version="v")
    assert "SEGREDO" not in str(erro.value) and "SEGREDO" not in repr(erro.value)


@pytest.mark.parametrize("valor", ["", "a" * 81, "ES Z6;", "ESZ6\n", None, 5])
def test_instrumento_invalido(valor):
    with pytest.raises(ProtocolError):
        p.codificar_dado_referencia(valor, "CME")
    with pytest.raises(ProtocolError):
        p.codificar_assinatura_profundidade("ESZ6", valor, assinar=True)
    with pytest.raises(ProtocolError):
        p.codificar_retrato_profundidade(valor, "CME", "tok")


def test_correlacao_do_retrato_precisa_de_texto():
    with pytest.raises(ProtocolError):
        p.codificar_retrato_profundidade("ESZ6", "CME", "")


# ---- leitura: respostas geradas pela biblioteca oficial ------------------------------------------

@pytest.mark.parametrize("nome", sorted(RESPOSTAS))
def test_resposta_do_oficial_e_decodificada(nome):
    hexa, campos = RESPOSTAS[nome]
    m = decodificar(bytes.fromhex(hexa))
    assert m.template_id == TEMPLATES[nome]
    assert m.campos == campos


def test_quadros_de_mercado_so_trazem_o_template():
    assert decodificar(_template(160) + _v(110100 << 3 | 2) + _v(4) + b"ESZ6").campos == {}
    assert decodificar(_template(161)).template_id == 161
    assert decodificar(_template(150) + _v(110100 << 3 | 2) + _v(4) + b"ESZ6").campos == {}


def test_numerico_empacotado_e_aceito():
    pacote = _v(1) + _v(2) + _v(300)
    dados = _template(116) + _v(154406 << 3 | 2) + _v(len(pacote)) + pacote
    assert decodificar(dados).campos["depth_size"] == [1, 2, 300]


def test_campo_desconhecido_e_ignorado_e_o_ultimo_valor_vence():
    dados = _template(19) + _v(99999 << 3 | 0) + _v(1) + _v(150100 << 3) + _v(1) + _v(150100 << 3) + _v(2)
    assert decodificar(dados).campos == {"ssboe": 2}


@pytest.mark.parametrize("dados,codigo", [
    (b"", "quadro_invalido"),
    ("texto", "quadro_invalido"),
    (_v(150100 << 3) + _v(1), "template_id_ausente"),
    (_template(10), "template_de_entrada_inesperado"),          # pedido nao e resposta
    (_template(312), "template_de_entrada_inesperado"),
    (_v(p.F_TEMPLATE << 3 | 2) + _v(1) + b"x", "tipo_de_fio_invalido"),
    (_template(19) + _v(132766 << 3 | 2) + _v(2) + b"\xff\xfe", "texto_invalido"),
    (_template(19) + _v(132766 << 3 | 0) + _v(1), "tipo_de_fio_invalido"),
    (_template(19) + _v(150100 << 3 | 2) + _v(1) + b"x" * 1 + _v(5 << 3 | 3), "tipo_de_fio_nao_suportado"),
    (_template(19) + _v(132766 << 3 | 2) + _v(10) + b"abc", "campo_truncado"),
    (_template(19) + _v(150100 << 3 | 1) + b"\x00\x00", "campo_truncado"),
    (_template(19) + _v(0 << 3 | 0) + _v(1), "campo_zero"),
    (_template(19) + b"\x80", "varint_invalido"),
    (_template(11) + _v(153633 << 3 | 0) + _v(1), "tipo_de_fio_invalido"),   # double precisa de fio 1
])
def test_decodificacao_recusada(dados, codigo):
    with pytest.raises(ProtocolError) as erro:
        decodificar(dados)
    assert str(erro.value) == codigo


def test_quadro_acima_do_limite_e_recusado():
    with pytest.raises(ProtocolError) as erro:
        decodificar(b"\x00" * (p.LIMITE_QUADRO + 1))
    assert str(erro.value) == "quadro_invalido"


# ---- sucesso e heartbeat --------------------------------------------------------------------------

def test_sucesso_so_com_exatamente_zero():
    p.sucesso(decodificar(bytes.fromhex(RESPOSTAS["logout"][0])))
    for hexa in (RESPOSTAS["rejeicao"][0],):
        with pytest.raises(ProtocolError) as erro:
            p.sucesso(decodificar(bytes.fromhex(hexa)))
        assert str(erro.value) == "resposta_recusada_75_codigo_7"
        assert "servidor" not in str(erro.value)


@pytest.mark.parametrize("codigos,esperado", [
    ([], "resposta_recusada_19_codigo_ausente"),
    (["0", "0"], "resposta_recusada_19_codigo_0"),
    (["1"], "resposta_recusada_19_codigo_1"),
    (["abc"], "resposta_recusada_19_codigo_invalido"),
    (["123456789"], "resposta_recusada_19_codigo_invalido"),
    (["é"], "resposta_recusada_19_codigo_invalido"),
])
def test_sucesso_recusado_com_codigo_seguro(codigos, esperado):
    m = p.Mensagem(19, {"rp_code": codigos} if codigos else {})
    with pytest.raises(ProtocolError) as erro:
        p.sucesso(m)
    assert str(erro.value) == esperado


def _login_com_intervalo(valor):
    return p.Mensagem(11, {"heartbeat_interval": valor} if valor is not None else {})


@pytest.mark.parametrize("valor", [0.1, 5.0, 60.0, 3600.0])
def test_intervalo_de_heartbeat_valido(valor):
    assert p.intervalo_de_heartbeat(_login_com_intervalo(valor)) == valor


@pytest.mark.parametrize("valor", [None, math.nan, math.inf, -1.0, 0.09, 3600.1, 60, "60"])
def test_intervalo_de_heartbeat_invalido(valor):
    with pytest.raises(ProtocolError):
        p.intervalo_de_heartbeat(_login_com_intervalo(valor))


def test_intervalo_so_vale_para_resposta_de_login():
    with pytest.raises(ProtocolError):
        p.intervalo_de_heartbeat(p.Mensagem(19, {"heartbeat_interval": 60.0}))


@pytest.mark.parametrize("intervalo,periodo", [(60.0, 10.0), (20.0, 10.0), (8.0, 4.0), (0.1, 0.05)])
def test_periodo_de_heartbeat_nunca_passa_de_10_s(intervalo, periodo):
    assert p.periodo_de_heartbeat(intervalo) == periodo


# ---- robustez diante de lixo da rede --------------------------------------------------------------

def test_lixo_aleatorio_so_gera_protocol_error():
    rng = random.Random(7)
    bases = [bytes.fromhex(h) for h, _ in RESPOSTAS.values()]
    for _ in range(4000):
        if rng.random() < 0.8:
            dados = bytearray(rng.choice(bases))
            for _ in range(rng.randrange(0, 4)):
                if dados:
                    dados[rng.randrange(len(dados))] = rng.randrange(256)
            if rng.random() < 0.3 and dados:
                dados = dados[:rng.randrange(len(dados))]
        else:
            dados = bytearray(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
        if not dados:
            continue
        try:
            decodificar(bytes(dados))
        except ProtocolError:
            pass


def test_modulo_e_python_puro():
    fonte = Path(p.__file__).read_text(encoding="utf-8")
    for proibido in ("import google", "from google", "import websockets", "import grpc", "import socket", "import ssl"):
        assert proibido not in fonte
