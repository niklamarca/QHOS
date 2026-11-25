"""Desequilibrio do livro ao fim de cada quadro e calibracao dos percentis (peca 7, secao 10 do plano).

O estado e medido ao fim de cada quadro (a menor unidade que chega inteira): melhor compra e venda em ticks, soma das
quantidades nos `niveis` melhores precos de cada lado e o desequilibrio

    D = (Qc - Qv) / (Qc + Qv)

So conta para a calibracao o quadro que termina com o livro qualificado, com os dois lados presentes, dentro do pregao
regular (9h30 as 16h de Nova York, dias uteis), fora da faixa do FOMC (13h55 as 16h nos dias de decisao; emenda de
25/10, codigo de 28/10) e dentro da janela de calibracao (26/10 a 6/11/2026, pela data de Nova York). Quadro fora da
janela e ignorado e contado: a calibracao nunca usa dado da janela de teste. Lacuna de entrega (intervalo maior que
LACUNA_MAX_NS entre quadros recebidos que toque o pregao regular) encerra a captura ali: o que vem depois e contado em
`depois_da_lacuna` e nao entra (N6, 28/10; o avaliador aplica o mesmo corte).

Percentil pelo metodo do posto mais proximo sobre D arredondado a 4 casas (um histograma de no maximo 20001 valores,
para caber um mes de pregoes em memoria). A calibracao devolve so P5 e P95 e contagens: nenhum resultado de operacao
e calculado aqui.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterable, Iterator, NamedTuple, Optional, Tuple

from qhos2 import eventos as ev
from qhos2.livro import Livro

ESQUEMA = "qhos2.calibracao.v1"
NIVEIS = 3
ESCALA = 10_000                       # D arredondado a 4 casas
JANELA_CALIBRACAO = (date(2026, 10, 26), date(2026, 11, 6))
ABERTURA, FECHAMENTO = time(9, 30), time(16, 0)
LACUNA_MAX_NS = 60 * 10 ** 9          # lacuna de entrega: nenhum intervalo maior entre quadros que toque o pregao
FOMC_INICIO = time(13, 55)
# Dias de decisao do FOMC nas janelas de calibracao e de teste (calendario de 2026 do Federal Reserve; datas
# declaradas pelo autor na auditoria de 28/10). O avaliador recebe as datas pelo plano registrado; a calibracao usa estas.
FOMC = (date(2026, 10, 28), date(2026, 12, 9))
_FUSO = None


class DesequilibrioError(ValueError):
    """Calibracao impossivel, com codigo curto e estatico."""


class Estado(NamedTuple):
    indice: int                 # quadro ao fim do qual o estado foi medido
    recebido_ns: int
    qualificado: bool
    compra: Optional[int]       # melhor preco de compra, em ticks
    venda: Optional[int]
    qc: int                     # soma das quantidades nos `niveis` melhores precos de compra
    qv: int
    d: Optional[float]          # desequilibrio; None se faltar um lado


def fuso():
    global _FUSO
    if _FUSO is None:
        try:
            from zoneinfo import ZoneInfo
            _FUSO = ZoneInfo("America/New_York")
        except Exception:
            raise DesequilibrioError("fuso_horario_indisponivel") from None
    return _FUSO


def hora_de_nova_york(recebido_ns: int) -> datetime:
    return datetime.fromtimestamp(recebido_ns // 10 ** 9, fuso())


def no_pregao(momento: datetime) -> bool:
    return momento.weekday() < 5 and ABERTURA <= momento.time() < FECHAMENTO


def na_faixa_do_fomc(momento: datetime, fomc: Tuple[date, ...] = FOMC) -> bool:
    """Das 13h55 ate o fechamento, nos dias de decisao: nem calibra nem dispara (secao 10, item 1)."""
    return momento.date() in fomc and FOMC_INICIO <= momento.time() < FECHAMENTO


def desequilibrio(livro: Livro, niveis: int = NIVEIS) -> Tuple[int, int, Optional[float]]:
    if type(niveis) is not int or not 1 <= niveis <= 1000:
        raise DesequilibrioError("niveis_invalidos")
    qc = sum(q for _, q, _ in livro.niveis(ev.COMPRA, niveis))
    qv = sum(q for _, q, _ in livro.niveis(ev.VENDA, niveis))
    return qc, qv, (qc - qv) / (qc + qv) if qc and qv else None


def estados(eventos: Iterable[ev.Evento], niveis: int = NIVEIS) -> Iterator[Estado]:
    """Um estado ao fim de cada quadro, na ordem da captura."""
    livro = Livro()
    for indice, grupo in itertools.groupby(eventos, key=lambda e: e.indice):
        grupo = list(grupo)
        livro.aplicar_quadro(grupo)
        qc, qv, d = desequilibrio(livro, niveis)
        yield Estado(indice, grupo[-1].recebido_ns, livro.qualificado, livro.melhor(ev.COMPRA),
                     livro.melhor(ev.VENDA), qc, qv, d)


def estados_da_captura(pasta, niveis: int = NIVEIS) -> Iterator[Estado]:
    return estados(ev.eventos_da_captura(pasta), niveis)


class Histograma:
    """Contagem de D arredondado a 4 casas; percentil pelo posto mais proximo."""

    def __init__(self):
        self.contagem: Counter = Counter()
        self.n = 0

    def adicionar(self, d: float) -> None:
        if type(d) not in (int, float) or not -1 <= d <= 1 or not math.isfinite(d):
            raise DesequilibrioError("desequilibrio_invalido")
        self.contagem[round(d * ESCALA)] += 1
        self.n += 1

    def juntar(self, outro: "Histograma") -> None:
        self.contagem.update(outro.contagem)
        self.n += outro.n

    def percentil(self, p: float) -> Optional[float]:
        if type(p) not in (int, float) or not 0 <= p <= 100 or not math.isfinite(p):
            raise DesequilibrioError("percentil_invalido")
        if not self.n:
            return None
        posto, acumulado = max(1, math.ceil(p / 100 * self.n)), 0
        for chave in sorted(self.contagem):
            acumulado += self.contagem[chave]
            if acumulado >= posto:
                return chave / ESCALA
        raise AssertionError("inalcancavel")


def calibrar(pastas, *, janela: Tuple[date, date] = JANELA_CALIBRACAO, niveis: int = NIVEIS,
             fomc: Tuple[date, ...] = FOMC) -> dict:
    """P5 e P95 de D nos quadros que contam (ver o topo do modulo). Captura recusada e listada, nao interrompe.

    O erro de um quadro pode aparecer no meio da captura: cada captura e contada a parte e so entra no total se for
    lida ate o fim, para que uma captura recusada nao deixe metade dos seus quadros na calibracao.
    """
    inicio, fim = janela
    if type(inicio) is not date or type(fim) is not date or inicio > fim:
        raise DesequilibrioError("janela_invalida")
    if type(niveis) is not int or not 1 <= niveis <= 1000:
        raise DesequilibrioError("niveis_invalidos")
    if not isinstance(fomc, tuple) or any(type(d) is not date for d in fomc):
        raise DesequilibrioError("fomc_invalido")
    histograma, por_dia, descartados = Histograma(), Counter(), Counter()
    capturas, recusadas = [], []
    for pasta in pastas:
        nome = Path(pasta).name
        h, dias, fora = Histograma(), Counter(), Counter()
        anterior_ns, dentro_anterior, cortado = None, False, False
        try:
            for e in estados_da_captura(pasta, niveis):
                momento = hora_de_nova_york(e.recebido_ns)
                dentro = no_pregao(momento)
                if (not cortado and anterior_ns is not None and (dentro or dentro_anterior)
                        and e.recebido_ns - anterior_ns > LACUNA_MAX_NS):
                    cortado = True                     # lacuna de entrega: a captura vale ate o quadro anterior
                anterior_ns, dentro_anterior = e.recebido_ns, dentro
                if cortado:
                    fora["depois_da_lacuna"] += 1
                elif not inicio <= momento.date() <= fim:
                    fora["fora_da_janela"] += 1
                elif not dentro:
                    fora["fora_do_pregao"] += 1
                elif na_faixa_do_fomc(momento, fomc):
                    fora["fomc"] += 1
                elif not e.qualificado:
                    fora["nao_qualificado"] += 1
                elif e.d is None:
                    fora["sem_os_dois_lados"] += 1
                else:
                    h.adicionar(e.d)
                    dias[momento.date().isoformat()] += 1
        except ev.EventoError as erro:
            recusadas.append(f"{nome}: {erro}")
            continue
        histograma.juntar(h)
        por_dia.update(dias)
        descartados.update(fora)
        capturas.append(nome)
    return {"esquema": ESQUEMA, "janela": [inicio.isoformat(), fim.isoformat()], "niveis": niveis,
            "fomc": [d.isoformat() for d in fomc],
            "quadros_usados": histograma.n, "p5": histograma.percentil(5), "p95": histograma.percentil(95),
            "por_dia": dict(sorted(por_dia.items())), "descartados": dict(sorted(descartados.items())),
            "capturas": capturas, "recusadas": recusadas}


def _imprimir(r: dict) -> None:
    print(f"calibracao janela={r['janela'][0]}..{r['janela'][1]} niveis={r['niveis']} fomc={r['fomc']} "
          f"quadros_usados={r['quadros_usados']}")
    print(f"  P5={r['p5']} P95={r['p95']}")
    print(f"  por_dia={r['por_dia']}")
    print(f"  descartados={r['descartados']}")
    print(f"  capturas={len(r['capturas'])} recusadas={r['recusadas']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.desequilibrio", description=__doc__.splitlines()[0])
    ap.add_argument("capturas", nargs="+", help="pastas de captura; so os quadros da janela de calibracao contam")
    ap.add_argument("--json", action="store_true", help="imprime o relatorio completo em JSON")
    args = ap.parse_args(argv)
    try:
        r = calibrar([c for c in args.capturas if Path(c).is_dir()])
    except DesequilibrioError as erro:
        print(f"calibracao impossivel: {erro}")
        return 2
    if args.json:
        print(json.dumps(r, indent=2, sort_keys=True))
    else:
        _imprimir(r)
    return 2 if r["recusadas"] else 0


if __name__ == "__main__":
    sys.exit(main())
