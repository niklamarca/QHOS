"""Mercado sintetico com verdade conhecida (peca 6a): o gabarito para provar o livro da peca 6b.

A partir de uma semente, gera os mesmos tipos de quadro que a Rithmic manda (116, 160, 150) e, junto, o livro
verdadeiro depois de cada passo. Mesma semente, mesmos bytes. Nada aqui e enviado a rede; a captura gravada se
declara "QHOS2 Sintetico", e o catalogo a classifica como ambiente desconhecido, nunca como paper.

Regras de fila usadas (premissas; os dados reais de 23/10 confirmaram so que CHANGE com preco novo perde a
prioridade e que a maioria das reducoes a mantem):
  - ordem nova recebe a proxima prioridade;
  - reduzir quantidade mantem prioridade e preco; mudar preco ou aumentar quantidade da nova prioridade;
  - DELETE leva quantidade 0;
  - negocio: o agressor consome so o melhor nivel oposto, na ordem de prioridade. Cada ordem passiva tocada gera
    um 150 (preco, quantidade, agressor, id passivo, id agressor); depois vem um 160 com todas as baixas do
    negocio (DELETE se zerou, CHANGE com a mesma prioridade se sobrou). A ordem 150 antes de 160 e premissa.
"""
from __future__ import annotations

import random
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from qhos2 import mercado as m
from qhos2 import rprotocol as p
from qhos2.gravador import Gravador

SISTEMA = "QHOS2 Sintetico"
INICIO_NS = 1_761_246_000 * 10 ** 9
PASSO_NS = 1_000_000          # 1 ms entre quadros
ATRASO_NS = 75_000_000        # carimbo do servidor 75 ms antes da chegada, como no Paper
_ACOES = (("novo", 40), ("cancela", 22), ("reduz", 10), ("aumenta", 5), ("move", 10), ("negocio", 13))
_LADO_PROTO = {"C": m.COMPRA, "V": m.VENDA}


class Ordem(NamedTuple):
    lado: str
    preco: int        # ticks
    quantidade: int
    prioridade: int


# ---- quadros no formato da Rithmic ------------------------------------------------------------------

def _v(n: int) -> bytes:
    saida = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        saida.append(b | (0x80 if n else 0))
        if not n:
            return bytes(saida)


def _int(numero: int, valor: int) -> bytes:
    return _v(numero << 3) + _v(valor)


def _dbl(numero: int, valor: float) -> bytes:
    return _v(numero << 3 | 1) + struct.pack("<d", valor)


def _txt(numero: int, valor: str) -> bytes:
    bruto = valor.encode("utf-8")
    return _v(numero << 3 | 2) + _v(len(bruto)) + bruto


def _carimbo(carimbo_ns: int) -> bytes:
    return _int(p.F_SSBOE, carimbo_ns // 10 ** 9) + _int(p.F_USECS, (carimbo_ns % 10 ** 9) // 1000)


def quadro_retrato(lado: str, preco: float, ordens: List[Tuple[int, int, str]]) -> bytes:
    """Um nivel do retrato (116): ordens como (quantidade, prioridade, id)."""
    dados = _int(p.F_TEMPLATE, p.RESP_RETRATO_PROFUNDIDADE) + _int(153612, _LADO_PROTO[lado]) + _dbl(154405, preco)
    for quantidade, prioridade, ident in ordens:
        dados += _int(154406, quantidade) + _int(153613, prioridade) + _txt(149238, ident)
    return dados


def quadro_retrato_fim() -> bytes:
    return _int(p.F_TEMPLATE, p.RESP_RETRATO_PROFUNDIDADE) + _txt(p.F_RP_CODE, "0")


def quadro_livro(entradas: List[Tuple[int, str, float, int, int, str]], carimbo_ns: int) -> bytes:
    """Um 160 com uma ou mais atualizacoes: (tipo, lado, preco, quantidade, prioridade, id), alinhadas."""
    dados = _int(p.F_TEMPLATE, p.PROFUNDIDADE_ORDEM)
    for tipo, lado, preco, quantidade, prioridade, ident in entradas:
        dados += (_int(110121, tipo) + _int(153612, _LADO_PROTO[lado]) + _dbl(154405, preco)
                  + _int(154406, quantidade) + _int(153613, prioridade) + _txt(149238, ident))
    return dados + _carimbo(carimbo_ns)


def quadro_negocio(preco: float, quantidade: int, agressor: str, passiva: str, agressora: str, carimbo_ns: int,
                   *, retrato: bool = False) -> bytes:
    return (_int(p.F_TEMPLATE, p.ULTIMO_NEGOCIO) + _int(110121, int(retrato)) + _dbl(100006, preco)
            + _int(100178, quantidade) + _int(112003, _LADO_PROTO[agressor]) + _txt(149238, passiva)
            + _txt(154641, agressora) + _carimbo(carimbo_ns))


# ---- simulacao --------------------------------------------------------------------------------------

def canonico(ordens: Dict[str, Ordem]) -> dict:
    """Livro como {lado: {preco: [(prioridade, id, quantidade), ...]}}, tudo ordenado."""
    livro: dict = {"C": {}, "V": {}}
    for ident, o in ordens.items():
        livro[o.lado].setdefault(o.preco, []).append((o.prioridade, ident, o.quantidade))
    return {lado: {preco: sorted(fila) for preco, fila in sorted(niveis.items())} for lado, niveis in livro.items()}


@dataclass
class Cenario:
    semente: int
    tick: float
    quadros: List[Tuple[int, bytes]] = field(default_factory=list)   # (template, bytes), na ordem
    verdades: List[Tuple[int, dict]] = field(default_factory=list)   # (quadros emitidos ate ali, livro verdadeiro)
    acoes: Dict[str, int] = field(default_factory=dict)

    def livro_final(self) -> dict:
        return self.verdades[-1][1]


class _Simulador:
    def __init__(self, semente: int, tick: float, base: int):
        self.rng, self.tick, self.base = random.Random(semente), tick, base
        self.ordens: Dict[str, Ordem] = {}
        self.prioridade = self.ident = self.agressor = 0
        self.cenario = Cenario(semente, tick)

    def _nova_prioridade(self) -> int:
        self.prioridade += 1
        return self.prioridade

    def _novo_id(self, prefixo: str = "O") -> str:
        self.ident += 1
        return f"{prefixo}{self.ident}"

    def _melhor(self, lado: str) -> Optional[int]:
        precos = [o.preco for o in self.ordens.values() if o.lado == lado]
        if not precos:
            return None
        return max(precos) if lado == "C" else min(precos)

    def _preco_livre(self, lado: str) -> int:
        """Preco que nao cruza o livro: ate 5 ticks atras do melhor oposto."""
        oposto = self._melhor("V" if lado == "C" else "C")
        if lado == "C":
            topo = (oposto if oposto is not None else self.base + 1) - 1
            return topo - self.rng.randint(0, 4)
        fundo = (oposto if oposto is not None else self.base) + 1
        return fundo + self.rng.randint(0, 4)

    def _emitir(self, template: int, dados: bytes) -> None:
        self.cenario.quadros.append((template, dados))

    def _agora(self) -> int:
        return INICIO_NS + len(self.cenario.quadros) * PASSO_NS

    def _pts(self, preco: int) -> float:
        return preco * self.tick

    def _livro(self, entradas) -> None:
        self._emitir(p.PROFUNDIDADE_ORDEM, quadro_livro(
            [(t, o.lado, self._pts(o.preco), q, o.prioridade, ident) for t, ident, o, q in entradas],
            self._agora() - ATRASO_NS))

    def retrato_inicial(self, niveis: int, por_nivel: Tuple[int, int]) -> None:
        for i in range(niveis):
            for lado, preco in (("C", self.base - i), ("V", self.base + 1 + i)):
                for _ in range(self.rng.randint(*por_nivel)):
                    self.ordens[self._novo_id("S")] = Ordem(lado, preco, self.rng.randint(1, 20), self._nova_prioridade())
        for lado, niveis_lado in canonico(self.ordens).items():
            for preco, fila in niveis_lado.items():
                self._emitir(p.RESP_RETRATO_PROFUNDIDADE,
                             quadro_retrato(lado, self._pts(preco), [(q, prio, ident) for prio, ident, q in fila]))
        self._emitir(p.RESP_RETRATO_PROFUNDIDADE, quadro_retrato_fim())

    def passo(self) -> None:
        nomes, pesos = zip(*_ACOES)
        acao = self.rng.choices(nomes, weights=pesos)[0]
        if acao != "novo" and not self.ordens:
            acao = "novo"
        if acao == "negocio" and (self._melhor("C") is None or self._melhor("V") is None):
            acao = "novo"
        if acao in ("reduz",) and not any(o.quantidade > 1 for o in self.ordens.values()):
            acao = "novo"
        getattr(self, "_" + acao)()
        self.cenario.acoes[acao] = self.cenario.acoes.get(acao, 0) + 1

    def _qualquer(self, filtro=lambda o: True) -> str:
        return self.rng.choice(sorted(i for i, o in self.ordens.items() if filtro(o)))

    def _novo(self) -> None:
        lado = self.rng.choice("CV")
        ident = self._novo_id()
        o = self.ordens[ident] = Ordem(lado, self._preco_livre(lado), self.rng.randint(1, 20), self._nova_prioridade())
        self._livro([(m.NOVO, ident, o, o.quantidade)])

    def _cancela(self) -> None:
        ident = self._qualquer()
        o = self.ordens.pop(ident)
        self._livro([(m.REMOCAO, ident, o, 0)])

    def _reduz(self) -> None:
        ident = self._qualquer(lambda o: o.quantidade > 1)
        o = self.ordens[ident] = self.ordens[ident]._replace(quantidade=self.rng.randint(1, self.ordens[ident].quantidade - 1))
        self._livro([(m.ALTERACAO, ident, o, o.quantidade)])

    def _aumenta(self) -> None:
        ident = self._qualquer()
        antiga = self.ordens[ident]
        o = self.ordens[ident] = antiga._replace(quantidade=antiga.quantidade + self.rng.randint(1, 10),
                                                 prioridade=self._nova_prioridade())
        self._livro([(m.ALTERACAO, ident, o, o.quantidade)])

    def _move(self) -> None:
        ident = self._qualquer()
        antiga = self.ordens.pop(ident)  # fora do livro para o preco novo nao se medir contra ela mesma
        o = self.ordens[ident] = antiga._replace(preco=self._preco_livre(antiga.lado), prioridade=self._nova_prioridade())
        self._livro([(m.ALTERACAO, ident, o, o.quantidade)])

    def _negocio(self) -> None:
        agressor = self.rng.choice("CV")
        lado_passivo = "V" if agressor == "C" else "C"
        preco = self._melhor(lado_passivo)
        fila = sorted((o.prioridade, i) for i, o in self.ordens.items() if o.lado == lado_passivo and o.preco == preco)
        restante = self.rng.randint(1, sum(self.ordens[i].quantidade for _, i in fila))
        self.agressor += 1
        agressora = f"A{self.agressor}"
        baixas = []
        for _, ident in fila:
            if restante == 0:
                break
            o = self.ordens[ident]
            executado = min(restante, o.quantidade)
            restante -= executado
            self._emitir(p.ULTIMO_NEGOCIO, quadro_negocio(self._pts(preco), executado, agressor, ident, agressora,
                                                          self._agora() - ATRASO_NS))
            if executado == o.quantidade:
                del self.ordens[ident]
                baixas.append((m.REMOCAO, ident, o, 0))
            else:
                o = self.ordens[ident] = o._replace(quantidade=o.quantidade - executado)
                baixas.append((m.ALTERACAO, ident, o, o.quantidade))
        self._livro(baixas)


def gerar(semente: int, passos: int, *, tick: float = 0.25, base: int = 27400, niveis: int = 5,
          por_nivel: Tuple[int, int] = (1, 4)) -> Cenario:
    """Retrato inicial e `passos` acoes; guarda o livro verdadeiro depois do retrato e de cada passo."""
    if passos < 0 or niveis < 1 or not 1 <= por_nivel[0] <= por_nivel[1]:
        raise ValueError("parametros_invalidos")
    sim = _Simulador(semente, tick, base)
    sim.retrato_inicial(niveis, por_nivel)
    sim.cenario.verdades.append((len(sim.cenario.quadros), canonico(sim.ordens)))
    for _ in range(passos):
        sim.passo()
        sim.cenario.verdades.append((len(sim.cenario.quadros), canonico(sim.ordens)))
    return sim.cenario


def gravar(cenario: Cenario, raiz, *, captura_id: Optional[str] = None) -> Path:
    """Grava o cenario como captura verificavel (mesmo formato do Paper, ambiente declarado sintetico)."""
    gravador = Gravador(raiz, identidade={"system_name": SISTEMA, "requested_symbol": "SINTETICO", "exchange": "QHOS2"},
                        captura_id=captura_id)
    for i, (template, dados) in enumerate(cenario.quadros):
        gravador(dados, template, INICIO_NS + i * PASSO_NS)
    gravador.fechar({"estado": "FECHADA", "referencia": {"min_qprice_change": cenario.tick},
                     "sintetico": {"semente": cenario.semente, "quadros": len(cenario.quadros)}},
                    fechamento_normal=True)
    return gravador.pasta
