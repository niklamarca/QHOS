"""Pedidos extras de leitura (front month) e a leitura rapida de identidade dos quadros de mercado.

Numeros de template conferidos no Reference Guide do SDK 0.89.0.0 (arquivo_local/sdk/RProtocolAPI.0.89.0.0.zip,
sha256 44f70b92...): Front Month Contract 113/114, Front Month Contract Update 159. Campos conferidos em
request_front_month_contract.proto e response_front_month_contract.proto do mesmo ZIP.

So existe codificador para o pedido 113, e sempre com need_updates falso (o servidor nao deve mandar 159).
Os demais pedidos saem dos codificadores de qhos2.rprotocol, que nao tem nenhum de ordem.
"""
from __future__ import annotations

from qhos2 import rprotocol as p

FRONT_MONTH, RESP_FRONT_MONTH, ATUALIZACAO_FRONT_MONTH = 113, 114, 159
PEDIDOS_EXTRAS = frozenset({FRONT_MONTH})
F_NEED_UPDATES = 154352

ESQUEMA_114 = {
    p.F_USER_MSG: ("user_msg", "str", True), p.F_RP_CODE: ("rp_code", "str", True),
    p.F_SYMBOL: ("symbol", "str", False), p.F_EXCHANGE: ("exchange", "str", False),
    149166: ("is_front_month_symbol", "bool", False), 100003: ("symbol_name", "str", False),
    157095: ("trading_symbol", "str", False), 157096: ("trading_exchange", "str", False)}

# Respostas cuja recusa vale so para um ativo: a sessao entrega a mensagem sem exigir rp_code "0".
RESPOSTAS_POR_ATIVO = frozenset({p.RESP_DADO_REFERENCIA, RESP_FRONT_MONTH, p.RESP_ASSINATURA_PROFUNDIDADE,
                                 p.RESP_ATUALIZACAO_MERCADO})


def codificar_front_month(raiz: str, exchange: str) -> bytes:
    return p._montar([p._int(p.F_TEMPLATE, FRONT_MONTH), p._texto(p.F_SYMBOL, p._identificador(raiz, "symbol")),
                      p._texto(p.F_EXCHANGE, p._identificador(exchange, "exchange")), p._bool(F_NEED_UPDATES, False)])


def decodificar_resposta(dados: bytes) -> p.Mensagem:
    """114 pelo esquema do proto oficial; 15, 118 e 101 pelo rprotocol. Nao confere rp_code."""
    template = ler_rapido(dados)[0]
    if template != RESP_FRONT_MONTH:
        return p.decodificar(dados)
    campos: dict = {}
    for numero, fio, valor in p._campos_brutos(bytes(dados)):
        regra = ESQUEMA_114.get(numero)
        if regra is None:
            continue
        nome, tipo, repetido = regra
        valores = p._converter(tipo, fio, valor)
        if repetido:
            campos.setdefault(nome, []).extend(valores)
        elif valores:
            campos[nome] = valores[-1]
    return p.Mensagem(template, campos)


def codigo_seguro(rp) -> str:
    """O primeiro rp_code so se for numero curto (nunca texto do servidor)."""
    if not rp:
        return "ausente"
    primeiro = rp[0]
    return primeiro if isinstance(primeiro, str) and primeiro.isascii() and primeiro.isdigit() and len(primeiro) <= 8 \
        else "invalido"


def ler_rapido(dados) -> tuple:
    """(template, [symbol...], [exchange...]) lendo so as etiquetas do quadro, sem decodificar o resto."""
    if not isinstance(dados, (bytes, bytearray)) or not dados or len(dados) > p.LIMITE_QUADRO:
        raise p.ProtocolError("quadro_invalido")
    i, n = 0, len(dados)
    template, simbolos, bolsas = None, [], []
    while i < n:
        tag, i = p._ler_varint(dados, i)
        numero, tipo = tag >> 3, tag & 7
        if numero == 0:
            raise p.ProtocolError("campo_zero")
        if tipo == 0:
            valor, i = p._ler_varint(dados, i)
            if numero == p.F_TEMPLATE:
                novo = p._com_sinal(valor)
                if template is not None and template != novo:
                    raise p.ProtocolError("template_repetido_divergente")
                template = novo
        elif tipo == 2:
            tamanho, i = p._ler_varint(dados, i)
            fim = i + tamanho
            if fim > n:
                raise p.ProtocolError("campo_truncado")
            if numero in (p.F_SYMBOL, p.F_EXCHANGE):
                try:
                    texto = bytes(dados[i:fim]).decode("utf-8")
                except UnicodeDecodeError:
                    raise p.ProtocolError("texto_invalido") from None
                (simbolos if numero == p.F_SYMBOL else bolsas).append(texto)
            elif numero == p.F_TEMPLATE:
                raise p.ProtocolError("tipo_de_fio_invalido")
            i = fim
        elif tipo == 1:
            i += 8
        elif tipo == 5:
            i += 4
        else:
            raise p.ProtocolError("tipo_de_fio_nao_suportado")
        if i > n:
            raise p.ProtocolError("campo_truncado")
    if template is None:
        raise p.ProtocolError("template_id_ausente")
    return template, simbolos, bolsas
