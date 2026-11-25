"""Conteudo das mensagens de mercado (116, 150, 160, 161), separado da sessao.

A sessao so le o template dessas mensagens e grava os bytes crus. Este modulo le o conteudo, para a
inspecao (peca 4d) e para os eventos normalizados (peca 6a). Python puro, sem protobuf, sem rede, sem V1.

Esquema conferido contra a biblioteca oficial (codigo gerado dos .proto da Rithmic):
- 160 DepthByOrder: update_type, transaction_type, depth_price, prev_depth_price, prev_depth_price_flag,
  depth_size, depth_order_priority e exchange_order_id sao listas REPETIDAS. Um quadro pode trazer varias
  atualizacoes de ordem, alinhadas pela posicao.
- 116 ResponseDepthByOrderSnapshot: um nivel de preco por mensagem (lado e preco unicos; listas de tamanho,
  prioridade e id de ordem). Nao tem campo de tempo.
- 161 DepthByOrderEndEvent: so sequencia e carimbo.
- 150 LastTrade: um negocio por mensagem (preco, tamanho, lado agressor, ids da ordem passiva e da
  agressora); a que vem com is_snapshot e o ultimo negocio antes da assinatura.
"""
from __future__ import annotations

import struct
from typing import Dict, Tuple

from qhos2 import rprotocol as p

NOVO, ALTERACAO, REMOCAO = 1, 2, 3
COMPRA, VENDA = 1, 2
LISTAS_160 = ("update_type", "transaction_type", "depth_price", "depth_size", "depth_order_priority",
              "exchange_order_id")

_CARIMBOS = {150100: ("ssboe", "int", False), 150101: ("usecs", "int", False),
             150400: ("source_ssboe", "int", False), 150401: ("source_usecs", "int", False),
             150404: ("source_nsecs", "int", False), 150600: ("jop_ssboe", "int", False),
             150604: ("jop_nsecs", "int", False)}

# nome, tipo, repetido (os mesmos tipos do rprotocol)
ESQUEMAS: Dict[int, Dict[int, Tuple[str, str, bool]]] = {
    p.PROFUNDIDADE_ORDEM: {
        p.F_SYMBOL: ("symbol", "str", False), p.F_EXCHANGE: ("exchange", "str", False),
        112002: ("sequence_number", "uint", False), 110121: ("update_type", "uint", True),
        153612: ("transaction_type", "uint", True), 154405: ("depth_price", "double", True),
        154906: ("prev_depth_price", "double", True), 154930: ("prev_depth_price_flag", "bool", True),
        154406: ("depth_size", "int", True), 153613: ("depth_order_priority", "uint", True),
        149238: ("exchange_order_id", "str", True), **_CARIMBOS},
    p.RESP_RETRATO_PROFUNDIDADE: p.ESQUEMAS[p.RESP_RETRATO_PROFUNDIDADE],
    p.PROFUNDIDADE_FIM: {
        p.F_SYMBOL: ("symbol", "str", True), p.F_EXCHANGE: ("exchange", "str", True),
        112002: ("sequence_number", "uint", False), p.F_SSBOE: ("ssboe", "int", False),
        p.F_USECS: ("usecs", "int", False)},
    p.ULTIMO_NEGOCIO: {
        p.F_SYMBOL: ("symbol", "str", False), p.F_EXCHANGE: ("exchange", "str", False),
        149138: ("presence_bits", "uint", False), 154571: ("clear_bits", "uint", False),
        110121: ("is_snapshot", "bool", False), 100006: ("trade_price", "double", False),
        100178: ("trade_size", "int", False), 112003: ("aggressor", "uint", False),
        149238: ("exchange_order_id", "str", False), 154641: ("aggressor_exchange_order_id", "str", False),
        100011: ("net_change", "double", False), 100056: ("percent_change", "double", False),
        100032: ("volume", "uint", False), 101379: ("vwap", "double", False),
        100379: ("trade_time", "str", False), **_CARIMBOS},
}


class MercadoError(ValueError):
    """Quadro de mercado que nao pode ser lido, com codigo curto e estatico."""


def conferir_identidade(campos: dict, simbolo: str, bolsa: str) -> None:
    """Confere TODO campo de identidade presente, inclusive as listas do template 161.

    O protocolo permite quadros sem esses campos (por exemplo, o fim do retrato).
    Ausencia nao inventa identidade; o instrumento esperado vem da assinatura/manifesto.
    """
    if not all(isinstance(x, str) and x for x in (simbolo, bolsa)):
        raise MercadoError("identidade_desconhecida")
    for chave, esperado in (("symbol", simbolo), ("exchange", bolsa)):
        valor = campos.get(chave)
        valores = valor if isinstance(valor, list) else [] if valor is None else [valor]
        if any(v != esperado for v in valores):
            raise MercadoError("simbolo_ou_bolsa_diverge")


def _valores(tipo: str, fio: int, valor) -> list:
    if tipo == "double" and fio == 2:  # doubles empacotados: o padrao do protobuf aceita os dois jeitos
        if len(valor) % 8:
            raise MercadoError("double_empacotado_truncado")
        return [x for (x,) in struct.iter_unpack("<d", valor)]
    try:
        return p._converter(tipo, fio, valor)
    except p.ProtocolError as erro:
        raise MercadoError(str(erro)) from None


def decodificar(dados: bytes) -> p.Mensagem:
    """Le um quadro 116, 150, 160 ou 161. Campos desconhecidos sao ignorados; listas ficam na ordem do fio."""
    if not isinstance(dados, (bytes, bytearray)) or not dados or len(dados) > p.LIMITE_QUADRO:
        raise MercadoError("quadro_invalido")
    try:
        brutos = p._campos_brutos(bytes(dados))
    except p.ProtocolError as erro:
        raise MercadoError(str(erro)) from None
    template = None
    for numero, fio, valor in brutos:
        if numero == p.F_TEMPLATE:
            if fio != 0:
                raise MercadoError("tipo_de_fio_invalido")
            novo_template = p._com_sinal(valor)
            if template is not None and template != novo_template:
                raise MercadoError("template_repetido_divergente")
            template = novo_template
    if template not in ESQUEMAS:
        raise MercadoError("template_fora_do_mercado")
    esquema = ESQUEMAS[template]
    campos: Dict[str, object] = {}
    for numero, fio, valor in brutos:
        regra = esquema.get(numero)
        if regra is None:
            continue
        nome, tipo, repetido = regra
        valores = _valores(tipo, fio, valor)
        if repetido:
            campos.setdefault(nome, []).extend(valores)
        elif valores:
            if nome in ("symbol", "exchange") and nome in campos and campos[nome] != valores[-1]:
                raise MercadoError("identidade_repetida_divergente")
            campos[nome] = valores[-1]
    return p.Mensagem(template, campos)
