"""Checklist 1 da etapa 3 (secao 4.2 do plano): o movimento em 10 s paga o custo?

Mede, nos quadros que contam da janela de calibracao (os mesmos da calibracao: dentro da janela, pregao regular de
Nova York, fora da faixa do FOMC, livro qualificado com os dois lados, ate a lacuna de entrega), o movimento do preco
medio no horizonte da operacao SEM condicionar a nenhum sinal (secao 10, item 3, excecao de 25/10):

  - uma janela por vez, como a operacao (uma posicao por vez): a janela abre num quadro que conta em t, toma o meio
    e o spread do livro vigente em t + 0,65 s (sinal, ordem e latencia, como no avaliador) e em t + 10,65 s (saida),
    e so entao a proxima janela pode abrir. As janelas nao se sobrepoem, por isso a dependencia entre amostras e a
    mesma que existiria entre operacoes;
  - "livro vigente" num instante e o ultimo quadro recebido ate aquele instante (avaliador.py); se ele nao esta
    qualificado, a janela e descartada e contada;
  - movimento = meio na saida menos meio na entrada, em ticks (multiplo de 0,5); custo de cruzar = meio spread na
    entrada mais meio spread na saida, em ticks: e o que uma compra a mercado seguida de uma venda a mercado paga
    alem da taxa, com o movimento do meio a favor ou contra.

Nenhum sinal, nenhum P5/P95 e nenhum resultado de operacao entram aqui. A saida traz o histograma do movimento (em
meios ticks), a media do movimento absoluto, o desvio, a fracao parada, o custo medio de cruzar, as contagens por dia
e os acumuladores por bloco de 5 minutos que a conta de poder (qhos2.poder) usa. Com a taxa de ida e volta (regra B
do plano: a da tabela documentada da corretora, registrada antes de qualquer avaliacao) o checklist responde: custo
total em ticks, acerto necessario e abandono acima de 65 % (secao 4 do plano).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from qhos2 import avaliador as av
from qhos2 import desequilibrio as de
from qhos2 import eventos as ev

ESQUEMA = "qhos2.custo.v1"
ESQUEMA_CHECKLIST = "qhos2.checklist1.v1"
ENTRADA_NS = av.ATRASO_ENTRADA_NS + av.LATENCIA_NS     # 0,65 s: sinal, ordem e latencia
SAIDA_NS = ENTRADA_NS + av.SEGURA_NS                   # 10,65 s: saida preenchida 10 s depois da entrada
ACERTO_MAX = 0.65                                      # abandono da etapa 3 acima disso (secao 4 do plano)
BLOCO_S = av.BLOCO_S


class CustoError(ValueError):
    """Medicao ou checklist impossivel, com codigo curto e estatico."""


class _Acumulador:
    """Somas que bastam para o resumo e para a conta de poder; nada por amostra fica em memoria."""

    def __init__(self):
        self.histograma: Counter = Counter()          # movimento em meios ticks -> contagem
        self.n, self.parados, self.quadros = 0, 0, 0
        self.soma = self.soma2 = self.soma_abs = self.spread = 0.0
        self.blocos: Dict[str, List[float]] = {}      # "data/bloco" -> [n, soma, soma2, soma_abs]
        self.por_dia: Counter = Counter()
        self.descartados: Counter = Counter()

    def amostra(self, momento: datetime, delta: float, custo_spread: float) -> None:
        self.n += 1
        self.histograma[int(round(delta * 2))] += 1
        self.soma += delta
        self.soma2 += delta * delta
        self.soma_abs += abs(delta)
        self.parados += delta == 0
        self.spread += custo_spread
        chave = f"{momento.date().isoformat()}/{(momento.hour * 3600 + momento.minute * 60 + momento.second) // BLOCO_S}"
        bloco = self.blocos.setdefault(chave, [0, 0.0, 0.0, 0.0])
        bloco[0] += 1
        bloco[1] += delta
        bloco[2] += delta * delta
        bloco[3] += abs(delta)
        self.por_dia[momento.date().isoformat()] += 1

    def juntar(self, outro: "_Acumulador") -> None:
        self.histograma.update(outro.histograma)
        self.n += outro.n
        self.parados += outro.parados
        self.quadros += outro.quadros
        self.soma += outro.soma
        self.soma2 += outro.soma2
        self.soma_abs += outro.soma_abs
        self.spread += outro.spread
        for chave, b in outro.blocos.items():
            meu = self.blocos.setdefault(chave, [0, 0.0, 0.0, 0.0])
            for i in range(4):
                meu[i] += b[i]
        self.por_dia.update(outro.por_dia)
        self.descartados.update(outro.descartados)


def _negociavel(e: Optional[de.Estado]) -> bool:
    return e is not None and e.qualificado and e.compra is not None and e.venda is not None


def _medir_captura(estados: Iterable[de.Estado], inicio: date, fim: date, fomc: Tuple[date, ...]) -> _Acumulador:
    acc = _Acumulador()
    anterior: Optional[de.Estado] = None
    dentro_anterior, cortado = False, False
    pendente: Optional[list] = None                   # [alvo_ns, fase, t_sinal, momento, meio_entrada, spread_entrada]

    def resolver(vigente: Optional[de.Estado]) -> None:
        nonlocal pendente
        alvo, fase, t_sinal, momento, meio_entrada, spread_entrada = pendente
        if not _negociavel(vigente):
            acc.descartados["livro_nao_qualificado_no_instante"] += 1
            pendente = None
            return
        meio, spread = (vigente.compra + vigente.venda) / 2, vigente.venda - vigente.compra
        if fase == "entrada":
            pendente = [t_sinal + SAIDA_NS, "saida", t_sinal, momento, meio, spread]
        else:
            acc.amostra(momento, meio - meio_entrada, (spread_entrada + spread) / 2)
            pendente = None

    for e in estados:
        t = e.recebido_ns
        if type(t) is not int or t <= 0 or (anterior is not None and t < anterior.recebido_ns):
            raise ev.EventoError("estados_fora_de_ordem")
        momento = de.hora_de_nova_york(t)
        dentro = de.no_pregao(momento)
        if (not cortado and anterior is not None and (dentro or dentro_anterior)
                and t - anterior.recebido_ns > de.LACUNA_MAX_NS):
            cortado = True                             # lacuna de entrega: a captura vale ate o quadro anterior (N6)
            if pendente is not None:
                acc.descartados["janela_cortada_pela_lacuna"] += 1
                pendente = None
        while pendente is not None and pendente[0] < t:
            resolver(anterior)                         # o alvo passou: o vigente e o ultimo quadro antes deste
        anterior, dentro_anterior = e, dentro
        if cortado:
            acc.descartados["depois_da_lacuna"] += 1
        elif not inicio <= momento.date() <= fim:
            acc.descartados["fora_da_janela"] += 1
        elif not dentro:
            acc.descartados["fora_do_pregao"] += 1
        elif de.na_faixa_do_fomc(momento, fomc):
            acc.descartados["fomc"] += 1
        elif not e.qualificado:
            acc.descartados["nao_qualificado"] += 1
        elif e.compra is None or e.venda is None:
            acc.descartados["sem_os_dois_lados"] += 1
        else:
            acc.quadros += 1
            if pendente is None:
                pendente = [t + ENTRADA_NS, "entrada", t, momento, None, None]
    if pendente is not None:
        acc.descartados["janela_sem_fim"] += 1
    return acc


def medir(pastas, *, janela: Tuple[date, date] = de.JANELA_CALIBRACAO, niveis: int = de.NIVEIS,
          fomc: Tuple[date, ...] = de.FOMC) -> dict:
    """Movimento do meio em 10 s sem sinal, nas capturas da janela de calibracao. Captura recusada e listada."""
    inicio, fim = janela
    if type(inicio) is not date or type(fim) is not date or inicio > fim:
        raise CustoError("janela_invalida")
    if not isinstance(fomc, tuple) or any(type(d) is not date for d in fomc):
        raise CustoError("fomc_invalido")
    total, capturas, recusadas = _Acumulador(), [], []
    for pasta in pastas:
        nome = Path(pasta).name
        try:
            acc = _medir_captura(de.estados_da_captura(pasta, niveis), inicio, fim, fomc)
        except ev.EventoError as erro:
            recusadas.append(f"{nome}: {erro}")
            continue
        total.juntar(acc)
        capturas.append(nome)
    n = total.n
    media = total.soma / n if n else None
    variancia = max(0.0, total.soma2 / n - media * media) if n else None
    return {"esquema": ESQUEMA, "janela": [inicio.isoformat(), fim.isoformat()], "niveis": niveis,
            "fomc": [d.isoformat() for d in fomc], "horizonte_ns": av.SEGURA_NS, "entrada_ns": ENTRADA_NS,
            "quadros_que_contam": total.quadros, "amostras": n,
            "movimento_medio_abs_ticks": total.soma_abs / n if n else None,
            "movimento_medio_ticks": media, "desvio_ticks": math.sqrt(variancia) if n else None,
            "parado": total.parados / n if n else None,
            "custo_spread_medio_ticks": total.spread / n if n else None,
            "histograma_meios_ticks": {str(k): v for k, v in sorted(total.histograma.items())},
            "blocos": dict(sorted(total.blocos.items())), "por_dia": dict(sorted(total.por_dia.items())),
            "descartados": dict(sorted(total.descartados.items())), "capturas": capturas, "recusadas": recusadas}


def acerto_necessario(custo_ticks: float, movimento_ticks: float) -> Optional[float]:
    """Fracao de acertos de direcao que zera o resultado liquido, com o movimento medio a favor ou contra.

    Esperanca por operacao = (2p - 1) * m - c, logo p* = 1/2 + c / (2m); sem movimento nao ha acerto que pague.
    """
    if any(not (type(v) in (int, float) and math.isfinite(v)) or v < 0 for v in (custo_ticks, movimento_ticks)):
        raise CustoError("parametros_invalidos")
    return None if movimento_ticks == 0 else 0.5 + custo_ticks / (2 * movimento_ticks)


def checklist(medicao: dict, taxa_usd: float) -> dict:
    """Checklist 1: custo total (taxa + cruzar o spread) contra o movimento medio; abandono acima de ACERTO_MAX."""
    if type(taxa_usd) not in (int, float) or not math.isfinite(taxa_usd) or taxa_usd < 0:
        raise CustoError("taxa_invalida")
    try:
        n, m, s = medicao["amostras"], medicao["movimento_medio_abs_ticks"], medicao["custo_spread_medio_ticks"]
    except (KeyError, TypeError):
        raise CustoError("medicao_invalida") from None
    taxa_ticks = taxa_usd / av.TICK_USD
    if not n:
        return {"esquema": ESQUEMA_CHECKLIST, "taxa_usd": taxa_usd, "taxa_ticks": taxa_ticks, "amostras": 0,
                "veredito": "SEM_AMOSTRA"}
    custo = taxa_ticks + s
    acerto = acerto_necessario(custo, m)
    abandono = acerto is None or acerto > ACERTO_MAX
    return {"esquema": ESQUEMA_CHECKLIST, "taxa_usd": taxa_usd, "taxa_ticks": taxa_ticks,
            "custo_spread_ticks": s, "custo_total_ticks": custo, "movimento_medio_abs_ticks": m, "amostras": n,
            "movimento_paga_o_custo": m > custo, "acerto_necessario": acerto, "acerto_maximo": ACERTO_MAX,
            "veredito": "ABANDONO_MICROESTRUTURA" if abandono else "SEGUE"}


def _imprimir(r: dict) -> None:
    print(f"movimento janela={r['janela'][0]}..{r['janela'][1]} fomc={r['fomc']} quadros_que_contam={r['quadros_que_contam']} "
          f"amostras={r['amostras']}")
    print(f"  movimento_medio_abs_ticks={r['movimento_medio_abs_ticks']} desvio_ticks={r['desvio_ticks']} "
          f"parado={r['parado']} custo_spread_medio_ticks={r['custo_spread_medio_ticks']}")
    print(f"  por_dia={r['por_dia']}")
    print(f"  descartados={r['descartados']}")
    print(f"  capturas={len(r['capturas'])} recusadas={r['recusadas']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.custo", description=__doc__.splitlines()[0])
    ap.add_argument("capturas", nargs="+", help="pastas de captura; so os quadros da janela de calibracao contam")
    ap.add_argument("--taxa-usd", type=float, default=None,
                    help="taxa de ida e volta por contrato, da tabela documentada da corretora (regra B); sem ela "
                         "so a medicao e impressa e o checklist espera")
    ap.add_argument("--json", action="store_true", help="imprime a medicao (e o checklist, se houver taxa) em JSON")
    args = ap.parse_args(argv)
    try:
        r = medir([c for c in args.capturas if Path(c).is_dir()])
        c = checklist(r, args.taxa_usd) if args.taxa_usd is not None else None
    except (CustoError, de.DesequilibrioError) as erro:
        print(f"medicao impossivel: {erro}")
        return 2
    if args.json:
        print(json.dumps({"medicao": r, "checklist": c}, indent=2, sort_keys=True))
    else:
        _imprimir(r)
        if c is None:
            print("  checklist: aguarda a taxa de ida e volta (regra B)")
        else:
            print(f"  checklist: custo_total_ticks={c.get('custo_total_ticks')} acerto_necessario={c.get('acerto_necessario')} "
                  f"veredito={c['veredito']}")
    return 2 if r["recusadas"] else 0


if __name__ == "__main__":
    sys.exit(main())
