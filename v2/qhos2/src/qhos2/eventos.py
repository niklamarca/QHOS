"""Eventos normalizados da V2 (peca 6a): um formato unico para o livro, o replay e as features.

Cada quadro de mercado vira zero ou mais eventos, na ordem do fio e com a origem anotada (indice do quadro e
posicao dentro dele). Precos viram ticks inteiros: nenhuma comparacao de preco depende de ponto flutuante.

  RETRATO      uma ordem do retrato inicial (116)          RETRATO_FIM  ultima resposta do retrato
  NOVO         ordem nova (160, NEW)                       ALTERA       ordem alterada (160, CHANGE)
  REMOVE       ordem removida (160, DELETE)                NEGOCIO      negocio executado (150)
  FIM_EVENTO   fim de um evento da bolsa (161)

Um 150 cujo `presence_bits` nao traz o bit LAST_TRADE (last_trade.proto da Rithmic: 1 negocio, 2 variacao, 4 variacao
em %, 8 volume, 16 VWAP) so atualiza estatisticas do dia e nao e negocio: nao vira evento (a inspecao os conta como
150 sem preco). Na primeira captura real com negocios (25/10, ESZ6), eram 1523 dos 4675 quadros 150, todos so com o
bit 8 e sem preco nem agressor.

Fail-closed: listas desalinhadas, preco fora da grade de ticks, tipo ou lado desconhecido e negocio sem preco
param a leitura com codigo e indice do quadro. Nada e descartado em silencio. Nao interpreta o que um evento
significa para o livro: isso e da peca 6b.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Optional, Tuple

from qhos2 import mercado as m
from qhos2 import rprotocol as p
from qhos2.catalogo import LINHA_MAX_BYTES, _arquivos_da_captura, _ler_manifesto, verificar_captura

RETRATO, RETRATO_FIM, NOVO, ALTERA, REMOVE, NEGOCIO, FIM_EVENTO = (
    "RETRATO", "RETRATO_FIM", "NOVO", "ALTERA", "REMOVE", "NEGOCIO", "FIM_EVENTO")
COMPRA, VENDA = "C", "V"
_LADOS = {m.COMPRA: COMPRA, m.VENDA: VENDA}
_TIPOS = {m.NOVO: NOVO, m.ALTERACAO: ALTERA, m.REMOCAO: REMOVE}
_GRADE = 1e-6  # tolerancia relativa ao tick para aceitar um preco como multiplo exato
PRESENCA_NEGOCIO = 1  # LastTrade.PresenceBits.LAST_TRADE


class EventoError(ValueError):
    """Quadro que nao vira evento confiavel; o codigo diz o motivo e o indice do quadro."""


class Evento(NamedTuple):
    indice: int                 # indice do quadro de origem na captura
    posicao: int                # posicao do evento dentro do quadro
    recebido_ns: int            # relogio local na chegada do quadro
    tipo: str
    lado: Optional[str] = None  # no NEGOCIO, o lado do agressor
    preco: Optional[int] = None  # em ticks
    quantidade: Optional[int] = None
    ordem: Optional[str] = None  # no NEGOCIO, a ordem passiva
    prioridade: Optional[int] = None
    carimbo_ns: Optional[int] = None  # carimbo do servidor (ssboe + usecs), se houver
    ordem_agressora: Optional[str] = None
    retrato: bool = False       # NEGOCIO que e o ultimo negocio antes da assinatura


def ticks(preco: float, tick: float) -> int:
    """Preco em ticks inteiros; recusa preco que nao cai exatamente na grade."""
    if not (isinstance(preco, float) and math.isfinite(preco)):
        raise EventoError("preco_invalido")
    if not (isinstance(tick, float) and math.isfinite(tick) and tick > 0):
        raise EventoError("tick_invalido")
    q = preco / tick
    if not math.isfinite(q):
        raise EventoError("preco_fora_da_grade")
    n = round(q)
    if abs(q - n) > _GRADE:
        raise EventoError("preco_fora_da_grade")
    return n


def _carimbo(c: dict) -> Optional[int]:
    if "ssboe" not in c:
        return None
    return c["ssboe"] * 10 ** 9 + c.get("usecs", 0) * 1000


def _lado(valor, indice: int) -> str:
    if valor not in _LADOS:
        raise EventoError(f"lado_desconhecido:{indice}")
    return _LADOS[valor]


def normalizar(indice: int, template: int, recebido_ns: int, dados: bytes, tick: float, *,
               simbolo: Optional[str] = None, bolsa: Optional[str] = None) -> Tuple[Evento, ...]:
    """Eventos de um quadro, na ordem do fio."""
    try:
        msg = m.decodificar(dados)
        if simbolo is not None or bolsa is not None:
            m.conferir_identidade(msg.campos, simbolo, bolsa)
    except m.MercadoError as erro:
        raise EventoError(f"{erro}:{indice}") from None
    if msg.template_id != template:
        raise EventoError(f"template_divergente:{indice}")
    c = msg.campos
    carimbo = _carimbo(c)
    try:
        if template == p.RESP_RETRATO_PROFUNDIDADE:
            return _retrato(indice, recebido_ns, c, tick)
        if template == p.PROFUNDIDADE_ORDEM:
            return _livro(indice, recebido_ns, c, tick, carimbo)
        if template == p.ULTIMO_NEGOCIO:
            presenca = c.get("presence_bits")
            if presenca is not None and not presenca & PRESENCA_NEGOCIO:
                return ()          # so volume, VWAP ou variacao do dia: nao e negocio
            if "trade_price" not in c or "trade_size" not in c:
                raise EventoError("negocio_sem_preco")
            if type(c["trade_size"]) is not int or c["trade_size"] <= 0:
                raise EventoError("quantidade_do_negocio_invalida")
            agressor = _lado(c.get("aggressor"), indice) if "aggressor" in c else None
            return (Evento(indice, 0, recebido_ns, NEGOCIO, agressor, ticks(c["trade_price"], tick), c["trade_size"],
                           c.get("exchange_order_id") or None, None, carimbo,
                           c.get("aggressor_exchange_order_id") or None, bool(c.get("is_snapshot"))),)
        if template == p.PROFUNDIDADE_FIM:
            return (Evento(indice, 0, recebido_ns, FIM_EVENTO, carimbo_ns=carimbo),)
    except EventoError as erro:
        codigo = str(erro)
        raise EventoError(codigo if codigo.endswith(f":{indice}") else f"{codigo}:{indice}") from None
    raise EventoError(f"template_fora_do_mercado:{indice}")


def _retrato(indice, recebido_ns, c, tick) -> Tuple[Evento, ...]:
    # Uma resposta com rp_code encerra o retrato SOMENTE se seu unico codigo for 0.
    # Verificar antes de construir eventos impede qualificar um retrato parcial recusado.
    if c.get("rp_code"):
        try:
            p.sucesso(p.Mensagem(p.RESP_RETRATO_PROFUNDIDADE, c))
        except p.ProtocolError as erro:
            raise EventoError(str(erro)) from None
    tamanhos, prioridades, ids = (c.get(k, []) for k in ("depth_size", "depth_order_priority", "exchange_order_id"))
    saida = []
    if tamanhos or prioridades or ids:
        if not len(tamanhos) == len(prioridades) == len(ids):
            raise EventoError("retrato_desalinhado")
        if "depth_price" not in c:
            raise EventoError("retrato_sem_preco")
        lado, preco = _lado(c.get("depth_side"), indice), ticks(c["depth_price"], tick)
        for i, (qtd, prio, ident) in enumerate(zip(tamanhos, prioridades, ids)):
            saida.append(Evento(indice, i, recebido_ns, RETRATO, lado, preco, qtd, ident or None, prio))
    if c.get("rp_code"):
        saida.append(Evento(indice, len(saida), recebido_ns, RETRATO_FIM))
    return tuple(saida)


def _livro(indice, recebido_ns, c, tick, carimbo) -> Tuple[Evento, ...]:
    listas = [c.get(nome, []) for nome in m.LISTAS_160]
    if len({len(lista) for lista in listas}) != 1:
        raise EventoError("quadro_160_desalinhado")
    saida = []
    for i, (tipo, lado, preco, qtd, prio, ident) in enumerate(zip(*listas)):
        if tipo not in _TIPOS:
            raise EventoError("tipo_desconhecido")
        saida.append(Evento(indice, i, recebido_ns, _TIPOS[tipo], _lado(lado, indice), ticks(preco, tick), qtd,
                            ident or None, prio, carimbo))
    return tuple(saida)


def tick_da_captura(manifesto: dict) -> float:
    """Tick gravado pela sessao no manifesto (referencia do instrumento)."""
    try:
        tick = manifesto["status"]["sessao"]["referencia"]["min_qprice_change"]
    except (KeyError, TypeError):
        raise EventoError("tick_desconhecido") from None
    if not (isinstance(tick, float) and math.isfinite(tick) and tick > 0):
        raise EventoError("tick_desconhecido")
    return tick


def conferir_status_captura(manifesto: dict, *, pasta=None) -> None:
    """Integridade dos bytes nao substitui sucesso da sessao/retrato.

    Manifestos antigos podem omitir campos de sessao. Quando presentes, nenhuma
    evidencia explicita de falha, retrato incompleto ou correlacao errada e aceita.
    O fim bem-sucedido do retrato ainda e exigido pelo estado do Livro.
    """
    if "transport_closed_normally" in manifesto and manifesto["transport_closed_normally"] is not True:
        raise EventoError("captura_encerrada_com_falha")
    status = manifesto.get("status", {})
    sessao = status.get("sessao", {}) if isinstance(status, dict) else None
    if not isinstance(sessao, dict):
        raise EventoError("status_sessao_invalido")
    if (sessao.get("estado") not in (None, "FECHADA") or sessao.get("erro") or
            ("retrato_completo" in sessao and sessao["retrato_completo"] is not True)):
        raise EventoError("captura_encerrada_com_falha")
    anomalias = sessao.get("anomalias", {})
    if not isinstance(anomalias, dict) or anomalias.get("retrato_correlacao_divergente", 0):
        raise EventoError("retrato_correlacao_divergente")
    if "capture_identity_sha256" in manifesto:
        from qhos2.operacao import ler_identidade_captura, OperacaoError
        if pasta is None:
            raise EventoError("identidade_persistida_sem_contexto")
        try:
            ler_identidade_captura(pasta, manifesto=manifesto)
        except OperacaoError as erro:
            raise EventoError(str(erro)) from None


def eventos_da_captura(pasta, *, tick: Optional[float] = None) -> Iterator[Evento]:
    """Todos os eventos de uma captura verificada, em ordem. Recusa captura que o catalogo nao verifica."""
    pasta = Path(pasta).resolve()
    relatorio = verificar_captura(pasta)
    if not relatorio["verificada"]:
        raise EventoError("captura_nao_verificada:" + ",".join(relatorio["divergencias"]))
    manifesto = _ler_manifesto(pasta, [])
    conferir_status_captura(manifesto, pasta=pasta)
    explicito = tick is not None
    tick = tick_da_captura(manifesto) if not explicito else tick
    if not (isinstance(tick, float) and math.isfinite(tick) and tick > 0):
        raise EventoError("tick_invalido")
    if explicito:
        try:
            documentado = tick_da_captura(manifesto)
        except EventoError:
            documentado = None  # compatibilidade com capturas legadas sem referencia
        if documentado is not None and documentado != tick:
            raise EventoError("tick_diverge")
    status = manifesto.get("status", {})
    simbolo, bolsa = status.get("requested_symbol"), status.get("exchange")
    try:
        m.conferir_identidade({}, simbolo, bolsa)
    except m.MercadoError as erro:
        raise EventoError(str(erro)) from None
    for arquivo, _ in _arquivos_da_captura(pasta, manifesto, []):
        with arquivo.open("rb") as f:
            for linha in f:
                if len(linha) > LINHA_MAX_BYTES:
                    raise EventoError("linha_grande_demais")
                obj = json.loads(linha)
                dados = base64.b64decode(obj["protobuf_base64"], validate=True)
                yield from normalizar(obj["index"], obj["template_id"], obj["received_ns"], dados, tick,
                                      simbolo=simbolo, bolsa=bolsa)


def assinatura(eventos: Iterable[Evento]) -> str:
    """SHA-256 da sequencia de eventos: a mesma captura sempre da a mesma assinatura."""
    digest = hashlib.sha256()
    for evento in eventos:
        digest.update(repr(tuple(evento)).encode("utf-8") + b"\n")
    return digest.hexdigest()
