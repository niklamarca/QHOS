"""Contrato do fio do R|Protocol que a V2 usa, em Python puro (sem protobuf, sem rede, sem V1).

So existem codificadores para os pedidos permitidos: sessao (info do sistema, login, logout, heartbeat),
dado de referencia, profundidade MBO (assinatura e retrato) e negocios (atualizacao de mercado so com o
bit de ultimo negocio). Nao existe codificador de ordem: o que nao esta aqui nao sai. Toda resposta
desconhecida e recusada pelo nome.

Mensagens protobuf, sem prefixo de tamanho. Os campos saem em ordem crescente de numero, o mesmo
que o serializador oficial, para que os bytes sejam identicos aos da biblioteca do protobuf.
Mensagens de erro sao codigos curtos e estaticos: nunca incluem senha nem texto do servidor.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from qhos2.config import APP_NAME_PREFIX, HEARTBEAT_MAX_SECONDS, SYSTEM_NAME

LIMITE_QUADRO = 1_048_576
TEXTO_MAX = 256
INTERVALO_MIN_S, INTERVALO_MAX_S = 0.1, 3600.0

LOGIN, RESP_LOGIN = 10, 11
LOGOUT, RESP_LOGOUT = 12, 13
DADO_REFERENCIA, RESP_DADO_REFERENCIA = 14, 15
INFO_SISTEMA, RESP_INFO_SISTEMA = 16, 17
HEARTBEAT, RESP_HEARTBEAT = 18, 19
REJEICAO, LOGOUT_FORCADO = 75, 77
RETRATO_PROFUNDIDADE, RESP_RETRATO_PROFUNDIDADE = 115, 116
ASSINATURA_PROFUNDIDADE, RESP_ASSINATURA_PROFUNDIDADE = 117, 118
PROFUNDIDADE_ORDEM, PROFUNDIDADE_FIM = 160, 161
ATUALIZACAO_MERCADO, RESP_ATUALIZACAO_MERCADO = 100, 101
ULTIMO_NEGOCIO = 150

PEDIDOS_PERMITIDOS = frozenset({LOGIN, LOGOUT, DADO_REFERENCIA, INFO_SISTEMA, HEARTBEAT,
                                RETRATO_PROFUNDIDADE, ASSINATURA_PROFUNDIDADE, ATUALIZACAO_MERCADO})
RESPOSTAS_CONHECIDAS = frozenset({RESP_LOGIN, RESP_LOGOUT, RESP_DADO_REFERENCIA, RESP_INFO_SISTEMA,
                                  RESP_HEARTBEAT, REJEICAO, LOGOUT_FORCADO, RESP_RETRATO_PROFUNDIDADE,
                                  RESP_ASSINATURA_PROFUNDIDADE, PROFUNDIDADE_ORDEM, PROFUNDIDADE_FIM,
                                  RESP_ATUALIZACAO_MERCADO, ULTIMO_NEGOCIO})

TICKER_PLANT = 1
ASSINAR, CANCELAR_ASSINATURA = 1, 2
BIT_ULTIMO_NEGOCIO = 1  # update_bits: so o ultimo negocio (nada de BBO, livro agregado ou outros)

# numeros de campo (PB_OFFSET + MNM)
F_TEMPLATE, F_USER_MSG, F_RP_CODE = 154467, 132760, 132766
F_TEMPLATE_VERSION, F_USER, F_PASSWORD, F_APP_NAME, F_APP_VERSION = 153634, 131003, 130004, 130002, 131803
F_SYSTEM_NAME, F_INFRA_TYPE, F_AGGREGATED_QUOTES = 153628, 153621, 153644
F_SYMBOL, F_EXCHANGE, F_REQUEST = 110100, 110101, 100000
F_SSBOE, F_USECS = 150100, 150101
F_HEARTBEAT_INTERVAL = 153633
F_UPDATE_BITS = 154211

_IDENTIFICADOR = re.compile(r"[A-Za-z0-9_. /:+-]{1,80}")

# nome, tipo, repetido. Tipos: int (varint com sinal), uint, str, bool, double.
ESQUEMAS: Dict[int, Dict[int, Tuple[str, str, bool]]] = {
    RESP_LOGIN: {
        F_TEMPLATE_VERSION: ("template_version", "str", False), F_USER_MSG: ("user_msg", "str", True),
        F_RP_CODE: ("rp_code", "str", True), 154013: ("fcm_id", "str", False), 154014: ("ib_id", "str", False),
        154712: ("country_code", "str", False), 154713: ("state_code", "str", False),
        153428: ("unique_user_id", "str", False), F_HEARTBEAT_INTERVAL: ("heartbeat_interval", "double", False)},
    RESP_LOGOUT: {F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True)},
    RESP_INFO_SISTEMA: {
        F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True),
        F_SYSTEM_NAME: ("system_name", "str", True), 153649: ("has_aggregated_quotes", "bool", True)},
    RESP_HEARTBEAT: {
        F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True),
        F_SSBOE: ("ssboe", "int", False), F_USECS: ("usecs", "int", False)},
    REJEICAO: {F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True)},
    LOGOUT_FORCADO: {},
    RESP_DADO_REFERENCIA: {
        F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True),
        149138: ("presence_bits", "uint", False), F_SYMBOL: ("symbol", "str", False),
        F_EXCHANGE: ("exchange", "str", False), 100003: ("symbol_name", "str", False),
        100749: ("product_code", "str", False), 110116: ("instrument_type", "str", False),
        100067: ("expiration_date", "str", False), 154382: ("currency", "str", False),
        154844: ("is_tradable", "str", False), 154386: ("min_qprice_change", "double", False),
        154387: ("min_fprice_change", "double", False), 154389: ("single_point_value", "double", False)},
    RESP_RETRATO_PROFUNDIDADE: {
        F_USER_MSG: ("user_msg", "str", True), 132764: ("rq_handler_rp_code", "str", True),
        F_RP_CODE: ("rp_code", "str", True), F_EXCHANGE: ("exchange", "str", False),
        F_SYMBOL: ("symbol", "str", False), 112002: ("sequence_number", "uint", False),
        153612: ("depth_side", "int", False), 154405: ("depth_price", "double", False),
        154406: ("depth_size", "int", True), 153613: ("depth_order_priority", "uint", True),
        149238: ("exchange_order_id", "str", True)},
    RESP_ASSINATURA_PROFUNDIDADE: {F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True)},
    RESP_ATUALIZACAO_MERCADO: {F_USER_MSG: ("user_msg", "str", True), F_RP_CODE: ("rp_code", "str", True)},
    # Quadros de mercado: so o template e lido aqui. O conteudo bruto e gravado como chegou.
    PROFUNDIDADE_ORDEM: {},
    PROFUNDIDADE_FIM: {},
    ULTIMO_NEGOCIO: {},
}


class ProtocolError(ValueError):
    """Diagnostico estatico, sem credenciais e sem texto do servidor."""


@dataclass(frozen=True)
class Mensagem:
    template_id: int
    campos: Dict[str, object]


# ---- escrita ----------------------------------------------------------------------------------

def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _int(numero: int, valor: int) -> Tuple[int, bytes]:
    return numero, _varint(numero << 3) + _varint(valor)


def _bool(numero: int, valor: bool) -> Tuple[int, bytes]:
    return _int(numero, 1 if valor else 0)


def _texto(numero: int, valor: str) -> Tuple[int, bytes]:
    bruto = valor.encode("utf-8")
    return numero, _varint(numero << 3 | 2) + _varint(len(bruto)) + bruto


def _montar(campos: Sequence[Tuple[int, bytes]]) -> bytes:
    return b"".join(parte for _, parte in sorted(campos, key=lambda c: c[0]))


def _texto_valido(valor, nome: str) -> str:
    if (not isinstance(valor, str) or not valor or len(valor) > TEXTO_MAX or "\x00" in valor):
        raise ProtocolError(f"{nome}_invalido")
    return valor


def _identificador(valor, nome: str) -> str:
    if not isinstance(valor, str) or not _IDENTIFICADOR.fullmatch(valor):
        raise ProtocolError(f"{nome}_invalido")
    return valor


def codificar_info_sistema() -> bytes:
    return _montar([_int(F_TEMPLATE, INFO_SISTEMA)])


def codificar_login(*, template_version: str, user: str, password: str, app_name: str, app_version: str,
                    system_name: str = SYSTEM_NAME) -> bytes:
    """Login no Ticker Plant do Paper, sem cotacoes agregadas. Nada alem do Paper e aceito."""
    if system_name != SYSTEM_NAME:
        raise ProtocolError("so_rithmic_paper_trading")
    _texto_valido(app_name, "app_name")
    if not app_name.startswith(APP_NAME_PREFIX) or len(app_name) <= len(APP_NAME_PREFIX):
        raise ProtocolError("app_name_precisa_do_prefixo_nila")
    return _montar([
        _int(F_TEMPLATE, LOGIN),
        _texto(F_TEMPLATE_VERSION, _texto_valido(template_version, "template_version")),
        _texto(F_USER, _texto_valido(user, "user")),
        _texto(F_PASSWORD, _texto_valido(password, "password")),
        _texto(F_APP_NAME, app_name),
        _texto(F_APP_VERSION, _texto_valido(app_version, "app_version")),
        _texto(F_SYSTEM_NAME, system_name),
        _int(F_INFRA_TYPE, TICKER_PLANT),
        _bool(F_AGGREGATED_QUOTES, False),
    ])


def codificar_logout() -> bytes:
    return _montar([_int(F_TEMPLATE, LOGOUT)])


def codificar_heartbeat() -> bytes:
    return _montar([_int(F_TEMPLATE, HEARTBEAT)])


def codificar_dado_referencia(symbol: str, exchange: str) -> bytes:
    return _montar([_int(F_TEMPLATE, DADO_REFERENCIA), _texto(F_SYMBOL, _identificador(symbol, "symbol")),
                    _texto(F_EXCHANGE, _identificador(exchange, "exchange"))])


def codificar_assinatura_profundidade(symbol: str, exchange: str, *, assinar: bool) -> bytes:
    return _montar([_int(F_TEMPLATE, ASSINATURA_PROFUNDIDADE),
                    _int(F_REQUEST, ASSINAR if assinar else CANCELAR_ASSINATURA),
                    _texto(F_SYMBOL, _identificador(symbol, "symbol")),
                    _texto(F_EXCHANGE, _identificador(exchange, "exchange"))])


def codificar_retrato_profundidade(symbol: str, exchange: str, correlacao: str) -> bytes:
    return _montar([_int(F_TEMPLATE, RETRATO_PROFUNDIDADE), _texto(F_USER_MSG, _texto_valido(correlacao, "correlacao")),
                    _texto(F_SYMBOL, _identificador(symbol, "symbol")),
                    _texto(F_EXCHANGE, _identificador(exchange, "exchange"))])


def codificar_assinatura_negocios(symbol: str, exchange: str, *, assinar: bool) -> bytes:
    """Atualizacao de mercado (100) so com o bit de ultimo negocio; o mesmo bit para assinar e cancelar."""
    return _montar([_int(F_TEMPLATE, ATUALIZACAO_MERCADO),
                    _int(F_REQUEST, ASSINAR if assinar else CANCELAR_ASSINATURA),
                    _texto(F_SYMBOL, _identificador(symbol, "symbol")),
                    _texto(F_EXCHANGE, _identificador(exchange, "exchange")),
                    _int(F_UPDATE_BITS, BIT_ULTIMO_NEGOCIO)])


# ---- leitura ----------------------------------------------------------------------------------

def _ler_varint(dados: bytes, i: int) -> Tuple[int, int]:
    valor, deslocamento = 0, 0
    while True:
        if i >= len(dados) or deslocamento > 63:
            raise ProtocolError("varint_invalido")
        byte = dados[i]
        i += 1
        valor |= (byte & 0x7F) << deslocamento
        if not byte & 0x80:
            return valor, i
        deslocamento += 7


def _campos_brutos(dados: bytes) -> List[Tuple[int, int, object]]:
    saida, i, n = [], 0, len(dados)
    while i < n:
        tag, i = _ler_varint(dados, i)
        numero, tipo = tag >> 3, tag & 7
        if numero == 0:
            raise ProtocolError("campo_zero")
        if tipo == 0:
            valor, i = _ler_varint(dados, i)
        elif tipo == 1:
            valor, i = dados[i:i + 8], i + 8
        elif tipo == 2:
            tamanho, i = _ler_varint(dados, i)
            valor, i = dados[i:i + tamanho], i + tamanho
            if len(valor) != tamanho:
                raise ProtocolError("campo_truncado")
        elif tipo == 5:
            valor, i = dados[i:i + 4], i + 4
        else:
            raise ProtocolError("tipo_de_fio_nao_suportado")
        if tipo in (1, 5) and len(valor) != (8 if tipo == 1 else 4):
            raise ProtocolError("campo_truncado")
        saida.append((numero, tipo, valor))
    return saida


def _com_sinal(valor: int) -> int:
    return valor - (1 << 64) if valor >= 1 << 63 else valor


def _converter(tipo: str, fio: int, valor) -> list:
    """Converte um campo bruto em uma lista de valores (um, ou varios se vier empacotado)."""
    if tipo == "str":
        if fio != 2:
            raise ProtocolError("tipo_de_fio_invalido")
        try:
            return [valor.decode("utf-8")]
        except UnicodeDecodeError:
            raise ProtocolError("texto_invalido") from None
    if tipo == "double":
        if fio != 1:
            raise ProtocolError("tipo_de_fio_invalido")
        return [struct.unpack("<d", valor)[0]]
    if fio == 2:  # numerico empacotado
        achados, i = [], 0
        while i < len(valor):
            numero, i = _ler_varint(valor, i)
            achados.append(numero)
        brutos = achados
    elif fio == 0:
        brutos = [valor]
    else:
        raise ProtocolError("tipo_de_fio_invalido")
    if tipo == "bool":
        return [bool(v) for v in brutos]
    if tipo == "int":
        return [_com_sinal(v) for v in brutos]
    return list(brutos)


def decodificar(dados: bytes) -> Mensagem:
    """Decodifica uma resposta conhecida. Qualquer outra coisa e recusada por codigo."""
    if not isinstance(dados, (bytes, bytearray)) or not dados or len(dados) > LIMITE_QUADRO:
        raise ProtocolError("quadro_invalido")
    brutos = _campos_brutos(bytes(dados))
    template = None
    for numero, fio, valor in brutos:
        if numero == F_TEMPLATE:
            if fio != 0:
                raise ProtocolError("tipo_de_fio_invalido")
            template = _com_sinal(valor)
    if template is None:
        raise ProtocolError("template_id_ausente")
    if template not in RESPOSTAS_CONHECIDAS:
        raise ProtocolError("template_de_entrada_inesperado")
    esquema = ESQUEMAS[template]
    campos: Dict[str, object] = {}
    for numero, fio, valor in brutos:
        regra = esquema.get(numero)
        if regra is None:
            continue
        nome, tipo, repetido = regra
        valores = _converter(tipo, fio, valor)
        if repetido:
            campos.setdefault(nome, []).extend(valores)
        else:
            campos[nome] = valores[-1] if valores else campos.get(nome)
    return Mensagem(template, campos)


def sucesso(mensagem: Mensagem) -> None:
    """A resposta so vale com exatamente um codigo, "0". Nunca repete texto do servidor."""
    codigos = mensagem.campos.get("rp_code")
    if codigos != ["0"]:
        if not codigos:
            seguro = "ausente"
        else:
            primeiro = codigos[0]
            seguro = primeiro if primeiro.isascii() and primeiro.isdigit() and len(primeiro) <= 8 else "invalido"
        raise ProtocolError(f"resposta_recusada_{mensagem.template_id}_codigo_{seguro}")


def intervalo_de_heartbeat(login: Mensagem) -> float:
    """Intervalo devolvido no login: finito e entre 0,1 s e 1 hora."""
    if login.template_id != RESP_LOGIN:
        raise ProtocolError("nao_e_resposta_de_login")
    valor = login.campos.get("heartbeat_interval")
    if not isinstance(valor, float) or not math.isfinite(valor) or not INTERVALO_MIN_S <= valor <= INTERVALO_MAX_S:
        raise ProtocolError("intervalo_de_heartbeat_invalido")
    return valor


def periodo_de_heartbeat(intervalo: float) -> float:
    """Metade do intervalo do servidor, nunca acima do teto de 10 s combinado com a Rithmic."""
    return min(intervalo / 2, float(HEARTBEAT_MAX_SECONDS))
