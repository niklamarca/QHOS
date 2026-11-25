"""Features observacionais da V2, sem sinais, ordens ou promessa de retorno.

Microprice = (ask * quantidade_bid + bid * quantidade_ask) / soma_quantidades,
em ticks racionais. CVD conta somente negocios cujo agressor foi informado; o
volume sem agressor fica separado, sem tick-rule ou inferencia de direcao.

A vida observada usa chegada local de NEW ate DELETE do mesmo id. Nao e vida
real na bolsa: ordens do retrato sao censuradas a esquerda; ordens ainda ativas
sao censuradas a direita. DELETE nao informa se houve cancelamento ou execucao,
portanto o modulo nao publica taxa de cancelamento. ALTERA preserva a origem.
Atualizacoes recebidas durante o retrato tambem sao censuradas conservadoramente.

Unidade temporal: quadro recebido, nao atomicidade de evento da bolsa 161.
Estas features sao experimentais, sem sequencia/UTC qualificados. Cada captura
inicia um estado novo; reiniciar zera CVD e nascimentos, sem unir conexoes.
O exportador deve consumir/validar a captura inteira antes de publicar linhas.
"""
from __future__ import annotations

import itertools
from fractions import Fraction
from typing import Iterable, Iterator, Optional

from qhos2 import eventos as ev
from qhos2.desequilibrio import Estado, NIVEIS, desequilibrio
from qhos2.livro import Livro, PRONTO

ESQUEMA = "qhos2.microestrutura.v1"
METODOLOGIA = {
    "esquema": ESQUEMA, "experimental": True,
    "unidade_temporal": "quadro_recebido",
    "sequence_verified": False, "utc_qualificado": False,
    "vida_observada": "NEW_ate_DELETE_por_id_em_recebido_ns",
    "remocao_nao_distingue_cancelamento_de_execucao": True,
    "acumulacao": "desde_inicio_da_captura_apos_retrato",
}
LIMITE_ORDENS = 1_000_000


class MicroestruturaError(ValueError):
    """Entrada incoerente; codigos estaticos, sem dados ou identificadores."""


def microprice(livro: Livro) -> Optional[Fraction]:
    """Valor racional em ticks; None sem os dois lados de um livro qualificado."""
    if not livro.qualificado:
        return None
    compra, venda = livro.niveis(ev.COMPRA, 1), livro.niveis(ev.VENDA, 1)
    if not compra or not venda:
        return None
    bid, qc, _ = compra[0]
    ask, qv, _ = venda[0]
    if qc <= 0 or qv <= 0 or bid >= ask:
        return None
    return Fraction(ask * qc + bid * qv, qc + qv)


class Microestrutura:
    """Estado limitado por ordens ativas; nenhum historico ilimitado de negocios."""

    def __init__(self, limite_ordens: int = LIMITE_ORDENS):
        if type(limite_ordens) is not int or not 1 <= limite_ordens <= LIMITE_ORDENS:
            raise MicroestruturaError("limite_de_ordens_invalido")
        self.limite_ordens = limite_ordens
        self.geracao = -1
        self.reiniciar()

    def reiniciar(self) -> None:
        """Nova conexao/captura, sem atribuir morte ou duracao a ordens anteriores."""
        self.geracao += 1
        self.livro = Livro(limite_pendentes=self.limite_ordens)
        self._nascimentos = {}
        self._indice = self._tempo_ns = None
        self._falha = False
        self._compra = self._venda = self._desconhecido = 0
        self._vidas_n = self._vidas_ns = self._censuradas = 0

    def _recusar(self, motivo):
        self._falha = True
        raise MicroestruturaError(motivo)

    def aplicar_quadro(self, eventos: Iterable[ev.Evento]) -> None:
        if self._falha:
            raise MicroestruturaError("estado_recusado_requer_reinicio")
        grupo = list(eventos)
        if not grupo:
            self._recusar("quadro_vazio")
        indice, recebido_ns = grupo[0].indice, grupo[0].recebido_ns
        if (type(indice) is not int or indice < 0 or type(recebido_ns) is not int or recebido_ns < 0
                or (self._indice is not None and indice <= self._indice)
                or (self._tempo_ns is not None and recebido_ns < self._tempo_ns)):
            self._recusar("quadro_ou_tempo_fora_de_ordem")
        posicao = -1
        for e in grupo:
            if (e.indice != indice or e.recebido_ns != recebido_ns or type(e.posicao) is not int
                    or e.posicao <= posicao):
                self._recusar("quadro_incoerente")
            posicao = e.posicao
            if e.tipo == ev.NEGOCIO and not e.retrato:
                if type(e.quantidade) is not int or e.quantidade <= 0 or e.lado not in (ev.COMPRA, ev.VENDA, None):
                    self._recusar("negocio_invalido")
        pronto_antes = self.livro.fase == PRONTO
        for e in grupo:
            if not pronto_antes:
                continue  # origem durante o retrato e desconhecida, inclusive atualizacao concorrente
            if e.tipo == ev.NOVO and isinstance(e.ordem, str) and e.ordem:
                if e.ordem not in self._nascimentos:
                    self._nascimentos[e.ordem] = recebido_ns
            elif e.tipo == ev.REMOVE:
                nascimento = self._nascimentos.pop(e.ordem, None)
                if nascimento is None:
                    self._censuradas += 1
                else:
                    self._vidas_n += 1
                    self._vidas_ns += recebido_ns - nascimento
            elif e.tipo == ev.NEGOCIO and not e.retrato:
                if e.lado == ev.COMPRA:
                    self._compra += e.quantidade
                elif e.lado == ev.VENDA:
                    self._venda += e.quantidade
                else:
                    self._desconhecido += e.quantidade
        self.livro.aplicar_quadro(grupo)
        if len(self.livro.ordens) > self.limite_ordens or len(self._nascimentos) > self.limite_ordens:
            self._recusar("limite_de_ordens_excedido")
        self._indice, self._tempo_ns = indice, recebido_ns

    def atributos(self) -> dict:
        """Somente fatos acumulados ate o ultimo quadro; contagens em contratos/ns."""
        if self._falha:
            raise MicroestruturaError("estado_recusado_requer_reinicio")
        preco = microprice(self.livro)
        vivas_observadas = len(self._nascimentos.keys() & self.livro.ordens.keys())
        return {
            "microprice_num_ticks": preco.numerator if preco is not None else None,
            "microprice_den": preco.denominator if preco is not None else None,
            "cvd_contratos": self._compra - self._venda,
            "volume_compra_contratos": self._compra,
            "volume_venda_contratos": self._venda,
            "volume_agressor_desconhecido_contratos": self._desconhecido,
            "remocoes_com_inicio_observado": self._vidas_n,
            "remocoes_censuradas": self._censuradas,
            "vida_observada_total_ns": self._vidas_ns,
            "ordens_ativas_com_inicio_observado": vivas_observadas,
            "ordens_ativas_censuradas": len(self.livro.ordens) - vivas_observadas,
        }


def estados(eventos: Iterable[ev.Evento], niveis: int = NIVEIS) -> Iterator[tuple]:
    """Estado original do desequilibrio e features medidas apos o MESMO quadro."""
    motor = Microestrutura()
    for indice, grupo in itertools.groupby(eventos, key=lambda e: e.indice):
        grupo = list(grupo)
        motor.aplicar_quadro(grupo)
        livro = motor.livro
        qc, qv, d = desequilibrio(livro, niveis)
        estado = Estado(indice, grupo[-1].recebido_ns, livro.qualificado, livro.melhor(ev.COMPRA),
                        livro.melhor(ev.VENDA), qc, qv, d)
        yield estado, motor.atributos()


def estados_da_captura(pasta, niveis: int = NIVEIS) -> Iterator[tuple]:
    return estados(ev.eventos_da_captura(pasta), niveis)
