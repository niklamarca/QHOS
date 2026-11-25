"""Livro de ofertas por ordem (peca 6b), reconstruido a partir dos eventos normalizados da peca 6a.

Cada ordem viva e guardada pelo id, com lado, preco (ticks), quantidade e prioridade; os niveis de preco sao
mantidos agregados e ordenados para que o melhor preco e os primeiros niveis saiam sem varrer o livro.

Regras, todas contadas e nenhuma silenciosa:
  - fases: VAZIO -> RETRATO (recebendo o retrato) -> PRONTO (depois do RETRATO_FIM);
  - atualizacoes (NOVO, ALTERA, REMOVE) que chegam antes do fim do retrato sao guardadas e aplicadas, na ordem
    recebida, logo depois do RETRATO_FIM. E a regra da Rithmic: a sessao assina as atualizacoes antes de pedir o
    retrato, e o retrato pode nao refletir as que chegaram enquanto ele era enviado. Aplicar na hora deixaria,
    por exemplo, uma ordem fantasma: a remocao chega antes da mensagem do retrato que ainda traz a ordem, e o
    retrato a recoloca (os dados reais de 23/10 tiveram 1 caso candidato);
  - nessa reaplicacao, ALTERA ou REMOVE de ordem desconhecida e NOVO de ordem ja viva sao esperados (o retrato
    ja refletia a atualizacao) e contados a parte, com o prefixo durante_retrato_; mais de `limite_pendentes`
    atualizacoes guardadas deixa o livro SUSPEITO, porque as que passam do limite nao sao aplicadas;
  - ao fim do retrato o livro mede o que a regra errada (aplicar na hora, a 6b ate 25/10) teria deixado, emulando
    so ids e estado: `reaplicadas` traz quantas atualizacoes guardadas foram aplicadas, quantas ordens fantasmas
    existiriam (vivas na emulacao e ausentes do livro correto) e quantas ficariam defasadas (lado, preco ou
    quantidade velhos). Nao sao anomalias do dado: sao a conta do defeito corrigido na etapa 2, exigida pelo plano;
  - depois do retrato, qualquer uma dessas anomalias deixa o livro SUSPEITO ate uma ressincronizacao: o estado
    pode estar errado e nao serve para decidir;
  - livro cruzado (melhor compra >= melhor venda) e julgado ao fim de cada quadro, a menor unidade que chega
    inteira: um quadro pode trazer varias atualizacoes, e no meio dele o livro passa por estados que nao existiram
    no mercado (ex.: a ordem agressiva entra antes das baixas que ela causou). Cruzado no fim do quadro tira a
    qualificacao enquanto durar; cruzado so no meio do quadro e contado a parte e nao tira;
  - NEGOCIO nao muda o livro (as baixas chegam pelos eventos do livro); negocio cuja ordem passiva nao esta viva e
    so contado, porque a ordem entre negocio e baixa no fio nao foi confirmada.

O livro e "qualificado" so quando: PRONTO, nao SUSPEITO e nao cruzado. A hipotese da secao 10 do plano so usa
instantes qualificados.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from qhos2 import eventos as ev

VAZIO, EM_RETRATO, PRONTO = "VAZIO", "RETRATO", "PRONTO"
LIMITE_PENDENTES = 1_000_000           # atualizacoes guardadas ate o fim do retrato
ESQUEMA = "qhos2.livro.v1"


class Ordem(NamedTuple):
    lado: str
    preco: int
    quantidade: int
    prioridade: Optional[int]


def _valida(e: ev.Evento) -> bool:
    """O mesmo crivo de `_ordem`: quantidade inteira positiva, lado conhecido e preco inteiro (bool nao vale)."""
    return (isinstance(e.quantidade, int) and not isinstance(e.quantidade, bool) and e.quantidade > 0
            and e.lado in (ev.COMPRA, ev.VENDA) and isinstance(e.preco, int) and not isinstance(e.preco, bool))


class Livro:
    def __init__(self, limite_pendentes: int = LIMITE_PENDENTES):
        self.ordens: Dict[str, Ordem] = {}
        self._niveis: Dict[str, Dict[int, List[int]]] = {ev.COMPRA: {}, ev.VENDA: {}}  # preco -> [quantidade, ordens]
        self._precos: Dict[str, List[int]] = {ev.COMPRA: [], ev.VENDA: []}            # crescentes
        self.fase = VAZIO
        self.suspeito = False
        self.cruzado = False
        self.eventos = 0
        self.negocios = 0
        self.anomalias: Counter = Counter()
        self.reaplicadas: Counter = Counter()                                         # medido no RETRATO_FIM
        self.limite_pendentes = limite_pendentes
        self._pendentes: List[ev.Evento] = []                                         # antes do fim do retrato
        self._na_hora: Dict[str, Tuple[str, int, int]] = {}                           # emulacao da regra errada

    # ---- consulta ----------------------------------------------------------------------------------

    @property
    def qualificado(self) -> bool:
        return self.fase == PRONTO and not self.suspeito and not self.cruzado

    def melhor(self, lado: str) -> Optional[int]:
        precos = self._precos[lado]
        if not precos:
            return None
        return precos[-1] if lado == ev.COMPRA else precos[0]

    def niveis(self, lado: str, n: int) -> List[Tuple[int, int, int]]:
        """Os `n` melhores niveis do lado, do melhor para o pior: (preco, quantidade, ordens)."""
        precos = self._precos[lado]
        escolhidos = reversed(precos[-n:]) if lado == ev.COMPRA else precos[:n]
        return [(p, *self._niveis[lado][p]) for p in escolhidos]

    def canonico(self) -> dict:
        """Mesmo formato do gabarito do sintetico: {lado: {preco: [(prioridade, id, quantidade), ...]}}."""
        livro: dict = {ev.COMPRA: {}, ev.VENDA: {}}
        for ident, o in self.ordens.items():
            livro[o.lado].setdefault(o.preco, []).append((o.prioridade, ident, o.quantidade))
        return {lado: {preco: sorted(fila) for preco, fila in sorted(niveis.items())} for lado, niveis in livro.items()}

    def assinatura(self) -> str:
        """SHA-256 do estado: o mesmo livro sempre da a mesma assinatura."""
        return hashlib.sha256(repr(self.canonico()).encode("utf-8")).hexdigest()

    def resumo(self) -> dict:
        """So contagens e distancias em ticks: nada de preco absoluto, quantidade ou id."""
        compra, venda = self.melhor(ev.COMPRA), self.melhor(ev.VENDA)
        return {"esquema": ESQUEMA, "fase": self.fase, "qualificado": self.qualificado, "suspeito": self.suspeito,
                "cruzado": self.cruzado, "eventos": self.eventos, "negocios": self.negocios, "ordens": len(self.ordens),
                "pendentes": len(self._pendentes),
                "niveis": {lado: len(self._precos[lado]) for lado in (ev.COMPRA, ev.VENDA)},
                "spread_ticks": None if compra is None or venda is None else venda - compra,
                "anomalias": dict(sorted(self.anomalias.items())),
                "reaplicadas": dict(sorted(self.reaplicadas.items()))}

    # ---- atualizacao --------------------------------------------------------------------------------

    def _adicionar(self, ident: str, o: Ordem) -> None:
        self.ordens[ident] = o
        nivel = self._niveis[o.lado].get(o.preco)
        if nivel is None:
            self._niveis[o.lado][o.preco] = [o.quantidade, 1]
            bisect.insort(self._precos[o.lado], o.preco)
        else:
            nivel[0] += o.quantidade
            nivel[1] += 1

    def _retirar(self, ident: str) -> Ordem:
        o = self.ordens.pop(ident)
        nivel = self._niveis[o.lado][o.preco]
        nivel[0] -= o.quantidade
        nivel[1] -= 1
        if nivel[1] == 0:
            del self._niveis[o.lado][o.preco]
            precos = self._precos[o.lado]
            del precos[bisect.bisect_left(precos, o.preco)]
        return o

    def _anomalia(self, nome: str) -> None:
        if nome in ("sem_id", "quantidade_invalida", "lado_ou_preco_invalido", "evento_desconhecido"):
            self.suspeito = True  # dado invalido nao vira valido ao terminar o retrato
        if self.fase == PRONTO:
            self.anomalias[nome] += 1
            self.suspeito = True
        else:
            self.anomalias["durante_retrato_" + nome] += 1

    def _esta_cruzado(self) -> bool:
        compra, venda = self.melhor(ev.COMPRA), self.melhor(ev.VENDA)
        return compra is not None and venda is not None and compra >= venda

    def aplicar_quadro(self, eventos) -> None:
        """Aplica todos os eventos de um quadro e so entao julga se o livro esta cruzado."""
        eventos = list(eventos)
        for i, e in enumerate(eventos):
            self._aplicar(e)
            if i < len(eventos) - 1 and self._esta_cruzado():
                self.anomalias["cruzado_dentro_do_quadro"] += 1
        self.cruzado = self._esta_cruzado()
        if self.cruzado:
            self.anomalias["cruzado"] += 1

    def aplicar(self, e: ev.Evento) -> None:
        """Um evento que e um quadro inteiro (atalho para testes e quadros de uma atualizacao)."""
        self.aplicar_quadro((e,))

    def _aplicar(self, e: ev.Evento) -> None:
        self.eventos += 1
        if e.tipo == ev.RETRATO:
            if self.fase == PRONTO:
                self._anomalia("retrato_apos_o_fim")
            else:
                self.fase = EM_RETRATO
                self._emular_na_hora(e)
            self._ordem(e)
        elif e.tipo == ev.RETRATO_FIM:
            if self.fase != PRONTO:
                for pendente in self._pendentes:   # ainda antes de PRONTO: anomalias contadas como durante_retrato_
                    self._ordem(pendente)
                self._medir_na_hora()
                self._pendentes = []
                self.fase = PRONTO
        elif e.tipo in (ev.NOVO, ev.ALTERA, ev.REMOVE):
            if self.fase == PRONTO:
                self._ordem(e)
            elif len(self._pendentes) < self.limite_pendentes:
                self._pendentes.append(e)
                self._emular_na_hora(e)
            else:
                self.anomalias["retrato_pendentes_demais"] += 1
                self.suspeito = True
        elif e.tipo == ev.NEGOCIO:
            self.negocios += 1
            if e.ordem and not e.retrato and e.ordem not in self.ordens:
                self.anomalias["negocio_com_passiva_fora_do_livro"] += 1
        elif e.tipo != ev.FIM_EVENTO:
            self._anomalia("evento_desconhecido")

    def _ordem(self, e: ev.Evento) -> None:
        if not isinstance(e.ordem, str) or not e.ordem:
            self._anomalia("sem_id")
            return
        viva = e.ordem in self.ordens
        if e.tipo == ev.REMOVE:
            if viva:
                self._retirar(e.ordem)
            else:
                self._anomalia("remove_desconhecida")
            return
        if e.tipo in (ev.NOVO, ev.RETRATO) and viva:
            self._anomalia("novo_ja_vivo")
        elif e.tipo == ev.ALTERA and not viva:
            self._anomalia("altera_desconhecida")
        if viva:
            self._retirar(e.ordem)
        if (not isinstance(e.quantidade, int) or isinstance(e.quantidade, bool) or e.quantidade <= 0):
            self._anomalia("quantidade_invalida")
            return
        if e.lado not in self._niveis or not isinstance(e.preco, int) or isinstance(e.preco, bool):
            self._anomalia("lado_ou_preco_invalido")
            return
        self._adicionar(e.ordem, Ordem(e.lado, e.preco, e.quantidade, e.prioridade))

    # ---- conta do defeito corrigido (etapa 2) ------------------------------------------------------

    def _emular_na_hora(self, e: ev.Evento) -> None:
        """Faz com `e` o que `_ordem` faria se aplicasse na hora, so em ids e estado: retira a ordem se viva e a
        recoloca se o evento a traz valida. Alimentado por tudo que chega antes do RETRATO_FIM, na ordem do fio."""
        if isinstance(e.ordem, str) and e.ordem:
            self._na_hora.pop(e.ordem, None)
            if e.tipo != ev.REMOVE and _valida(e):
                self._na_hora[e.ordem] = (e.lado, e.preco, e.quantidade)

    def _medir_na_hora(self) -> None:
        """No RETRATO_FIM, compara a emulacao com o livro correto ja reaplicado; sem pendentes os dois sao iguais."""
        self.reaplicadas["aplicadas"] += len(self._pendentes)
        self.reaplicadas["fantasmas"] += 0
        self.reaplicadas["defasadas"] += 0
        if self._pendentes:
            vivas = {ident: (o.lado, o.preco, o.quantidade) for ident, o in self.ordens.items()}
            self.reaplicadas["fantasmas"] += sum(1 for ident in self._na_hora if ident not in vivas)
            self.reaplicadas["defasadas"] += sum(1 for ident, estado in self._na_hora.items()
                                                 if ident in vivas and vivas[ident] != estado)
        self._na_hora = {}


def diferencas(a: dict, b: dict) -> Dict[str, int]:
    """Compara dois livros canonicos ordem a ordem (ex.: o livro montado e um retrato novo)."""
    def por_id(livro):
        return {ident: (lado, preco, prio, qtd) for lado, niveis in livro.items()
                for preco, fila in niveis.items() for prio, ident, qtd in fila}
    ia, ib = por_id(a), por_id(b)
    comuns = ia.keys() & ib.keys()
    return {"so_no_primeiro": len(ia.keys() - ib.keys()), "so_no_segundo": len(ib.keys() - ia.keys()),
            "lado_ou_preco_diferente": sum(ia[i][:2] != ib[i][:2] for i in comuns),
            "prioridade_diferente": sum(ia[i][2] != ib[i][2] for i in comuns),
            "quantidade_diferente": sum(ia[i][3] != ib[i][3] for i in comuns)}


def livro_da_captura(pasta, *, tick: Optional[float] = None) -> Livro:
    """Livro ao fim de uma captura verificada, quadro a quadro (a 6a recusa captura nao verificada ou quadro ruim)."""
    livro = Livro()
    for _, eventos in itertools.groupby(ev.eventos_da_captura(pasta, tick=tick), key=lambda e: e.indice):
        livro.aplicar_quadro(eventos)
    return livro


def _imprimir(nome: str, livro: Livro) -> None:
    r = livro.resumo()
    print(f"captura={nome} fase={r['fase']} qualificado={r['qualificado']} suspeito={r['suspeito']} "
          f"cruzado={r['cruzado']}")
    print(f"  eventos={r['eventos']} negocios={r['negocios']} ordens={r['ordens']} niveis={r['niveis']} "
          f"spread_ticks={r['spread_ticks']}")
    print(f"  anomalias={r['anomalias']}")
    print(f"  reaplicadas={r['reaplicadas']}")
    print(f"  assinatura={livro.assinatura()[:16]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.livro", description=__doc__.splitlines()[0])
    ap.add_argument("capturas", nargs="+", help="pasta(s) de captura; uma que falhe nao impede as outras")
    args = ap.parse_args(argv)
    recusadas = 0
    for caminho in args.capturas:
        if not Path(caminho).is_dir():
            continue  # o curinga do shell pode trazer arquivos soltos
        try:
            _imprimir(Path(caminho).name, livro_da_captura(caminho))
        except ev.EventoError as erro:
            recusadas += 1
            print(f"recusada: {Path(caminho).name}: {erro}")
    return 2 if recusadas else 0


if __name__ == "__main__":
    sys.exit(main())
