"""Passo 8 da etapa 3 (secao 4.2 do plano): o avaliador provado em dado sintetico, com semente fixa.

Duas camadas:

  - mecanica: um pregao sintetico de estados do livro (desequilibrio.Estado), com o meio em passeio aleatorio, spread
    de 1 tick, sinais plantados (D alem de P5/P95) e, se pedido, um deslocamento do meio na direcao do sinal 2 s
    depois dele (o "edge"). Passa por `avaliador.simular` como o dado real passaria: sem edge o resultado bruto medio
    e o custo de cruzar o spread (-1 tick); com edge E e E - 1; a entrada e tomada no livro vigente ate 0,65 s depois
    do sinal e a saida 10 s depois da entrada; em dia de FOMC nada dispara das 13h55 as 16h; a taxa sai do liquido;
    a mesma entrada da o mesmo relatorio, byte a byte;
  - decisao: pregoes com operacoes de resultado normal (media e desvio dados), uma por instante, espalhadas pelos
    blocos de 5 min, e a mesma decisao do teste real (intervalo em blocos E permutacao por pregao, `avaliador.julgar`).
    Sem edge, a fracao de PASSOU e o falso positivo e tem de ficar na taxa do alfa (alfa/2, porque os criterios sao
    unilaterais); com edge do tamanho do menor efeito detectavel (qhos2.poder) a fracao de PASSOU e o poder. O poder
    conjunto dos dois criterios no MDE e menor ou igual ao de cada um: este modulo mede quanto.

Nada aqui le captura real. A CLI imprime a conta de decisao para os parametros dados.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import date, datetime, time, timedelta
from typing import List, Optional

from qhos2 import avaliador as av
from qhos2 import desequilibrio as de
from qhos2 import eventos as ev
from qhos2 import poder as po

ESQUEMA = "qhos2.avaliador_sintetico.v1"
S = 10 ** 9
MEIO_INICIAL = 27_400                  # ES perto de 6850 pontos, em ticks de 0,25
PRIMEIRO_DIA = date(2026, 11, 9)       # inicio da janela de teste


class SinteticoError(ValueError):
    """Parametro impossivel, com codigo curto e estatico."""


def instante(data: date, hora: time) -> int:
    """Instante em ns de uma hora de Nova York, pela base de fusos do sistema."""
    return int(datetime.combine(data, hora, de.fuso()).timestamp()) * S


def dias_uteis(n: int, primeiro: date = PRIMEIRO_DIA) -> List[date]:
    datas, d = [], primeiro
    while len(datas) < n:
        if d.weekday() < 5:
            datas.append(d)
        d += timedelta(days=1)
    return datas


# ---- mecanica: estados do livro --------------------------------------------------------------------

def gerar_estados(semente: int, data: date, *, p5: float = -0.5, p95: float = 0.5, passo_ns: int = S,
                  prob_sinal: float = 0.003, edge_ticks: int = 0, prob_passo: float = 0.2,
                  inicio: time = time(9, 20), fim: time = time(16, 5)) -> List[de.Estado]:
    """Um pregao de estados, um a cada `passo_ns`: meio em passeio aleatorio, spread de 1 tick, D dentro de
    [P5, P95] salvo nos sinais plantados; com `edge_ticks`, o meio salta nessa direcao 2 s depois do sinal."""
    if (type(passo_ns) is not int or passo_ns <= 0 or not 0 <= prob_sinal <= 1 or not 0 <= prob_passo <= 1
            or type(edge_ticks) is not int or not -1 <= p5 < p95 <= 1):
        raise SinteticoError("parametros_invalidos")
    rng = random.Random(semente)
    meio, saltos, estados = MEIO_INICIAL, [], []
    for i, t in enumerate(range(instante(data, inicio), instante(data, fim) + 1, passo_ns)):
        while saltos and saltos[0][0] <= t:
            meio += saltos.pop(0)[1]
        if rng.random() < prob_passo:
            meio += rng.choice((-1, 1))
        d = rng.uniform(p5 * 0.6, p95 * 0.6)
        if rng.random() < prob_sinal:
            lado = rng.choice((1, -1))
            d = p95 + (1 - p95) / 2 if lado > 0 else p5 - (1 + p5) / 2
            if edge_ticks:
                saltos.append((t + 2 * S, lado * edge_ticks))
                saltos.sort()
        estados.append(de.Estado(i, t, True, meio, meio + 1, 10, 10, d))
    return estados


# ---- decisao: operacoes de resultado normal --------------------------------------------------------

def gerar_dia(rng: random.Random, data: date, *, ops_por_pregao: int, media_bruta_ticks: float,
              sigma_ticks: float) -> av.Dia:
    """Um pregao completo com `ops_por_pregao` operacoes de resultado normal, igualmente espacadas no pregao."""
    inicio = instante(data, de.ABERTURA)
    duracao = instante(data, de.FECHAMENTO) - inicio
    ops = []
    for k in range(ops_por_pregao):
        t = inicio + int((k + 0.5) * duracao / ops_por_pregao)
        ops.append(av.Operacao(ev.COMPRA, t, t, 100, t + av.SEGURA_NS, 100, rng.gauss(media_bruta_ticks, sigma_ticks),
                               None))
    return av.Dia(data, True, "completo", tuple(ops), {})


def ensaio_decisao(*, media_liquida_ticks: float, sigma_ticks: float, ops_por_pregao: int, n_dias: int, alfa: float,
                   taxa_usd: float = 0.0, corridas: int = 200, reamostras: int = 500,
                   semente: int = av.SEMENTE) -> dict:
    """Fracao de PASSOU (e de cada criterio) em `corridas` testes sinteticos com a media liquida dada."""
    if (type(ops_por_pregao) is not int or ops_por_pregao < 1 or type(n_dias) is not int or n_dias < 1
            or type(corridas) is not int or not 1 <= corridas <= 100_000 or not po._finito(media_liquida_ticks)
            or not po._finito(sigma_ticks) or sigma_ticks <= 0 or not po._finito(taxa_usd) or taxa_usd < 0):
        raise SinteticoError("parametros_invalidos")
    rng = random.Random(semente)
    datas = dias_uteis(n_dias)
    media_bruta = media_liquida_ticks + taxa_usd / av.TICK_USD
    # O teste real usa REAMOSTRAS no criterio por pregao, o que o torna exato ate 13 pregoes; `reamostras` menor so
    # encurta o bootstrap em blocos, senao o ensaio mediria um teste por pregao mais fraco que o real.
    reamostras_pregao = max(reamostras, min(2 ** n_dias, av.REAMOSTRAS))
    vereditos, bloco_ok, pregao_ok = Counter(), 0, 0
    for _ in range(corridas):
        dias = [gerar_dia(rng, d, ops_por_pregao=ops_por_pregao, media_bruta_ticks=media_bruta,
                          sigma_ticks=sigma_ticks) for d in datas]
        ops = [o for d in dias for o in d.operacoes]
        _, baixo, _ = av.intervalo(ops, taxa_usd, alfa, reamostras=reamostras, semente=semente)
        pregao = av.por_pregao(dias, taxa_usd, reamostras=reamostras_pregao, semente=semente)
        vereditos[av.julgar(len(dias), baixo, pregao["p_valor"], alfa)] += 1
        bloco_ok += baixo is not None and baixo > 0
        pregao_ok += pregao["p_valor"] is not None and pregao["p_valor"] <= alfa / 2 and pregao["media_por_pregao"] > 0
    return {"esquema": ESQUEMA, "media_liquida_ticks": media_liquida_ticks, "sigma_ticks": sigma_ticks,
            "ops_por_pregao": ops_por_pregao, "n_dias": n_dias, "alfa": alfa, "taxa_usd": taxa_usd,
            "corridas": corridas, "reamostras": reamostras, "semente": semente,
            "vereditos": dict(sorted(vereditos.items())), "fracao_passou": vereditos["PASSOU"] / corridas,
            "fracao_bloco": bloco_ok / corridas, "fracao_pregao": pregao_ok / corridas}


def mde_dos_dois_criterios(sigma_ticks: float, ops_por_pregao: int, n_dias: int, alfa: float, *,
                           simulacoes: int = po.SIMULACOES, reamostras: int = av.REAMOSTRAS) -> dict:
    """MDE de cada criterio com operacoes independentes (deff = 1), em ticks liquidos por operacao."""
    por_op = po.mde_por_operacao(sigma_ticks, ops_por_pregao * n_dias, alfa)
    dia = po.mde_por_pregao(sigma_ticks * ops_por_pregao ** 0.5, n_dias, alfa, simulacoes=simulacoes,
                            reamostras=reamostras)
    por_dia = dia["mde_ticks"] / ops_por_pregao if dia["mde_ticks"] is not None else None
    return {"mde_por_operacao_ticks": por_op, "mde_por_pregao_em_ticks_por_operacao": por_dia,
            "mde_ticks": max(por_op, por_dia) if por_dia is not None else None}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.avaliador_sintetico", description=__doc__.splitlines()[0])
    ap.add_argument("--sigma", type=float, required=True, help="desvio do resultado por operacao, em ticks")
    ap.add_argument("--ops-por-pregao", type=int, required=True)
    ap.add_argument("--n-dias", type=int, default=po.DIAS_PREVISTOS)
    ap.add_argument("--alfa", type=float, default=0.05 / 3)
    ap.add_argument("--taxa-usd", type=float, default=0.0)
    ap.add_argument("--media-liquida", type=float, default=None,
                    help="media liquida por operacao a plantar; sem ela, usa o MDE dos dois criterios (poder)")
    ap.add_argument("--corridas", type=int, default=200)
    ap.add_argument("--reamostras", type=int, default=500)
    args = ap.parse_args(argv)
    try:
        mde = None
        media = args.media_liquida
        if media is None:
            mde = mde_dos_dois_criterios(args.sigma, args.ops_por_pregao, args.n_dias, args.alfa)
            media = mde["mde_ticks"]
            if media is None:
                raise SinteticoError("n_dias_insuficiente_para_rejeitar")
        nulo = ensaio_decisao(media_liquida_ticks=0.0, sigma_ticks=args.sigma, ops_por_pregao=args.ops_por_pregao,
                              n_dias=args.n_dias, alfa=args.alfa, taxa_usd=args.taxa_usd, corridas=args.corridas,
                              reamostras=args.reamostras)
        com_edge = ensaio_decisao(media_liquida_ticks=media, sigma_ticks=args.sigma, ops_por_pregao=args.ops_por_pregao,
                                  n_dias=args.n_dias, alfa=args.alfa, taxa_usd=args.taxa_usd, corridas=args.corridas,
                                  reamostras=args.reamostras)
    except (SinteticoError, po.PoderError, de.DesequilibrioError) as erro:
        print(f"ensaio impossivel: {erro}")
        return 2
    print(json.dumps({"mde": mde, "sem_edge": nulo, "com_edge": com_edge}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
