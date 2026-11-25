"""Checklist 2 da etapa 3 (secao 4.2 do plano): qual e o menor efeito que o teste consegue detectar?

Regra 15: nenhum teste e registrado sem a conta do menor efeito detectavel (MDE) com a amostra disponivel. O teste da
hipotese 1 tem dois criterios (avaliador.py, C3 de 28/10) e os dois tem de passar; o MDE do teste e o maior dos dois,
em ticks liquidos por operacao:

  - por operacao (bootstrap em blocos de 5 minutos): a media liquida precisa superar o erro padrao vezes
    z(1 - alfa/2) + z(poder). O erro padrao usa o desvio do movimento sem sinal (qhos2.custo), o numero de operacoes
    previsto e o efeito de desenho dos blocos, deff = 1 + (b - 1) * rho, com rho a correlacao intraclasse do
    movimento dentro do bloco de 5 minutos (ANOVA de um fator sobre os acumuladores por bloco da medicao; vale o maior
    entre o rho do movimento com sinal e o do movimento absoluto) e b as operacoes por bloco;
  - por pregao (permutacao por inversao de sinal): com 7 a 10 pregoes nao ha formula fechada. O poder e simulado com
    somas diarias normais de desvio sigma_dia = sigma_op * raiz(operacoes por pregao * deff), e o MDE e achado por
    bissecao, com os mesmos sorteios em todos os passos.

So entra aqui o que a calibracao pode olhar: o movimento sem sinal e a contagem de sinais (D contra P5/P95, sem preco,
com rearme e uma posicao por vez). Nenhum resultado de operacao entra. Abandono (secao 4): MDE maior que o custo.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import NormalDist
from typing import Dict, Iterable, Optional, Tuple

from qhos2 import avaliador as av
from qhos2 import custo as cu
from qhos2 import desequilibrio as de
from qhos2 import eventos as ev

ESQUEMA = "qhos2.checklist2.v1"
PODER = 0.80
DIAS_PREVISTOS = 10                                    # pregoes de 9 a 20/11
SIMULACOES = 1000
TOLERANCIA = 0.02                                      # bissecao do MDE por pregao, em fracao de sigma_dia
BLOCOS_POR_PREGAO = (16 * 60 - 9 * 60 - 30) // (av.BLOCO_S // 60)    # 78 blocos de 5 min das 9h30 as 16h


class PoderError(ValueError):
    """Conta de poder impossivel, com codigo curto e estatico."""


def _finito(v) -> bool:
    return type(v) in (int, float) and math.isfinite(v)


def z(p: float) -> float:
    if not _finito(p) or not 0 < p < 1:
        raise PoderError("probabilidade_invalida")
    return NormalDist().inv_cdf(p)


def mde_por_operacao(sigma_op: float, n_ops: int, alfa: float, *, poder: float = PODER, deff: float = 1.0) -> float:
    """Menor media liquida por operacao que o intervalo bicaudal 1-alfa detecta com o poder dado."""
    if (not _finito(sigma_op) or sigma_op <= 0 or type(n_ops) is not int or n_ops < 1
            or not _finito(alfa) or not 0 < alfa < 1 or not _finito(deff) or deff < 1):
        raise PoderError("parametros_invalidos")
    return (z(1 - alfa / 2) + z(poder)) * sigma_op * math.sqrt(deff / n_ops)


def _icc(blocos: Dict[str, list], coluna_soma: int) -> Optional[float]:
    """Correlacao intraclasse por ANOVA de um fator; os acumuladores sao [n, soma, soma2, soma_abs] por bloco."""
    usados = [b for b in blocos.values() if b[0] > 0]
    k, total = len(usados), sum(b[0] for b in usados)
    if k < 2 or total <= k:
        return None
    geral = sum(b[coluna_soma] for b in usados) / total
    entre = sum(b[0] * (b[coluna_soma] / b[0] - geral) ** 2 for b in usados)
    dentro = sum(b[2] - b[coluna_soma] ** 2 / b[0] for b in usados)
    ms_entre, ms_dentro = entre / (k - 1), max(0.0, dentro) / (total - k)
    n0 = (total - sum(b[0] ** 2 for b in usados) / total) / (k - 1)
    denominador = ms_entre + (n0 - 1) * ms_dentro
    if denominador <= 0:
        return 0.0
    return min(1.0, max(0.0, (ms_entre - ms_dentro) / denominador))


def efeito_de_desenho(blocos: Dict[str, list], ops_por_bloco: float) -> dict:
    """deff = 1 + (b - 1) * rho, com o maior rho entre o movimento com sinal e o absoluto."""
    if not isinstance(blocos, dict) or not _finito(ops_por_bloco) or ops_por_bloco <= 0:
        raise PoderError("parametros_invalidos")
    try:
        rho, rho_abs = _icc(blocos, 1), _icc(blocos, 3)
    except (TypeError, IndexError, ZeroDivisionError):
        raise PoderError("blocos_invalidos") from None
    usado = max(r for r in (rho, rho_abs, 0.0) if r is not None)
    return {"rho": rho, "rho_abs": rho_abs, "rho_usado": usado, "blocos": sum(1 for b in blocos.values() if b[0] > 0),
            "amostras": sum(b[0] for b in blocos.values()), "ops_por_bloco": ops_por_bloco,
            "deff": 1 + max(0.0, ops_por_bloco - 1) * usado}


def contar_sinais(estados: Iterable[de.Estado], p5: float, p95: float, *, janela: Tuple[date, date] = de.JANELA_CALIBRACAO,
                  fomc: Tuple[date, ...] = de.FOMC) -> dict:
    """Sinais que a regra registrada daria (D alem de P5/P95, com rearme e uma posicao por vez), so olhando D.

    Mesmo filtro da calibracao e do custo; a posicao ocupa ate t + 10,65 s; sinal cuja saida passaria das 16h e
    contado como perto do fechamento. Nenhum preco e lido.
    """
    if not (_finito(p5) and _finito(p95) and -1 <= p5 < p95 <= 1):
        raise PoderError("parametros_invalidos")
    inicio, fim = janela
    por_dia, contagens = Counter(), Counter()
    anterior_ns, dentro_anterior, cortado, armado, ocupado_ate = None, False, False, False, 0
    for e in estados:
        t = e.recebido_ns
        if type(t) is not int or t <= 0 or (anterior_ns is not None and t < anterior_ns):
            raise ev.EventoError("estados_fora_de_ordem")
        momento = de.hora_de_nova_york(t)
        dentro = de.no_pregao(momento)
        if not cortado and anterior_ns is not None and (dentro or dentro_anterior) and t - anterior_ns > de.LACUNA_MAX_NS:
            cortado = True
        anterior_ns, dentro_anterior = t, dentro
        if (cortado or not inicio <= momento.date() <= fim or not dentro or de.na_faixa_do_fomc(momento, fomc)
                or not e.qualificado or e.d is None):
            continue
        fora = e.d > p95 or e.d < p5
        if t < ocupado_ate:
            contagens["sinal_ignorado_posicao_aberta"] += fora
            continue
        if not fora:
            armado = True
            continue
        if not armado:
            contagens["fora_sem_rearme"] += 1
            continue
        armado = False
        saida = de.hora_de_nova_york(t + cu.SAIDA_NS)
        if saida.date() != momento.date() or saida.time() >= de.FECHAMENTO:
            contagens["sinal_perto_do_fechamento"] += 1
            continue
        por_dia[momento.date().isoformat()] += 1
        ocupado_ate = t + cu.SAIDA_NS
    sinais, pregoes = sum(por_dia.values()), len(por_dia)
    return {"sinais": sinais, "pregoes": pregoes, "sinais_por_pregao": sinais / pregoes if pregoes else None,
            "por_dia": dict(sorted(por_dia.items())), "contagens": dict(sorted(contagens.items()))}


def contar_sinais_das_capturas(pastas, p5: float, p95: float, *, niveis: int = de.NIVEIS, **opcoes) -> dict:
    total, por_dia, contagens, capturas, recusadas = Counter(), Counter(), Counter(), [], []
    for pasta in pastas:
        nome = Path(pasta).name
        try:
            r = contar_sinais(de.estados_da_captura(pasta, niveis), p5, p95, **opcoes)
        except ev.EventoError as erro:
            recusadas.append(f"{nome}: {erro}")
            continue
        por_dia.update(r["por_dia"])
        contagens.update(r["contagens"])
        capturas.append(nome)
    sinais, pregoes = sum(por_dia.values()), len(por_dia)
    return {"sinais": sinais, "pregoes": pregoes, "sinais_por_pregao": sinais / pregoes if pregoes else None,
            "por_dia": dict(sorted(por_dia.items())), "contagens": dict(sorted(contagens.items())),
            "capturas": capturas, "recusadas": recusadas}


def _poder_por_pregao(delta: float, sigma: float, n: int, alfa: float, simulacoes: int, reamostras: int,
                      semente: int) -> float:
    sorteio = random.Random(semente)                  # os mesmos sorteios para todo delta: poder monotono em delta
    rejeicoes = 0
    for _ in range(simulacoes):
        somas = [delta + sigma * sorteio.gauss(0.0, 1.0) for _ in range(n)]
        media, p, _ = av.p_inversao_de_sinal(somas, reamostras=reamostras, semente=semente)
        rejeicoes += media > 0 and p <= alfa / 2
    return rejeicoes / simulacoes


def mde_por_pregao(sigma_dia: float, n_dias: int, alfa: float, *, poder: float = PODER, simulacoes: int = SIMULACOES,
                   reamostras: int = av.REAMOSTRAS, semente: int = av.SEMENTE, tolerancia: float = TOLERANCIA) -> dict:
    """Menor media por pregao que o criterio por pregao rejeita com o poder dado; None se n_dias nao basta."""
    if (not _finito(sigma_dia) or sigma_dia <= 0 or type(n_dias) is not int or n_dias < 1 or not _finito(alfa)
            or not 0 < alfa < 1 or not _finito(poder) or not 0 < poder < 1 or type(simulacoes) is not int
            or not 1 <= simulacoes <= 1_000_000 or not _finito(tolerancia) or not 0 < tolerancia <= 1):
        raise PoderError("parametros_invalidos")
    exato = 2 ** n_dias <= reamostras
    base = {"n_dias": n_dias, "sigma_dia": sigma_dia, "alfa": alfa, "poder": poder, "simulacoes": simulacoes,
            "exato": exato, "p_minimo": 2.0 ** -n_dias if exato else 1 / (reamostras + 1)}
    if base["p_minimo"] > alfa / 2:
        return {**base, "mde_ticks": None, "poder_alcancado": 0.0, "motivo": "n_dias_insuficiente_para_rejeitar"}

    def poder_em(delta):
        return _poder_por_pregao(delta, sigma_dia, n_dias, alfa, simulacoes, reamostras, semente)

    baixo, alto = 0.0, sigma_dia
    while poder_em(alto) < poder:
        alto *= 2
        if alto > 64 * sigma_dia:
            return {**base, "mde_ticks": None, "poder_alcancado": poder_em(alto / 2), "motivo": "poder_inalcancavel"}
    while alto - baixo > tolerancia * sigma_dia:
        meio = (baixo + alto) / 2
        if poder_em(meio) >= poder:
            alto = meio
        else:
            baixo = meio
    return {**base, "mde_ticks": alto, "poder_alcancado": poder_em(alto), "motivo": None}


def checklist(medicao: dict, *, ops_por_pregao: float, n_dias: int, alfa: float, custo_ticks: float,
              poder: float = PODER, simulacoes: int = SIMULACOES, reamostras: int = av.REAMOSTRAS,
              semente: int = av.SEMENTE) -> dict:
    """Checklist 2: MDE dos dois criterios, em ticks liquidos por operacao, contra o custo; abandono se maior."""
    if (not _finito(ops_por_pregao) or ops_por_pregao <= 0 or type(n_dias) is not int or n_dias < 1
            or not _finito(custo_ticks) or custo_ticks < 0):
        raise PoderError("parametros_invalidos")
    try:
        sigma_op, blocos = medicao["desvio_ticks"], medicao["blocos"]
    except (KeyError, TypeError):
        raise PoderError("medicao_invalida") from None
    if not _finito(sigma_op) or sigma_op <= 0:
        raise PoderError("medicao_sem_desvio")
    desenho = efeito_de_desenho(blocos, ops_por_pregao / BLOCOS_POR_PREGAO)
    n_ops = int(ops_por_pregao * n_dias)
    por_op = mde_por_operacao(sigma_op, n_ops, alfa, poder=poder, deff=desenho["deff"])
    sigma_dia = sigma_op * math.sqrt(ops_por_pregao * desenho["deff"])
    dia = mde_por_pregao(sigma_dia, n_dias, alfa, poder=poder, simulacoes=simulacoes, reamostras=reamostras,
                         semente=semente)
    dia_por_op = dia["mde_ticks"] / ops_por_pregao if dia["mde_ticks"] is not None else None
    mde = max(por_op, dia_por_op) if dia_por_op is not None else None
    return {"esquema": ESQUEMA, "alfa": alfa, "poder": poder, "n_dias": n_dias, "ops_por_pregao": ops_por_pregao,
            "n_ops": n_ops, "sigma_op_ticks": sigma_op, "desenho": desenho,
            "mde_por_operacao_ticks": por_op, "por_pregao": dia, "mde_por_pregao_em_ticks_por_operacao": dia_por_op,
            "mde_ticks": mde, "custo_ticks": custo_ticks, "minimo_de_pregoes": av.MIN_PREGOES,
            "veredito": "ABANDONO_MICROESTRUTURA" if mde is None or mde > custo_ticks else "SEGUE"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="qhos2.poder", description=__doc__.splitlines()[0])
    ap.add_argument("--medicao", type=Path, required=True, help="JSON de `qhos2.custo --json` (medicao do movimento)")
    ap.add_argument("--ops-por-pregao", type=float, required=True,
                    help="operacoes previstas por pregao (contar_sinais com P5/P95 congelados, na calibracao)")
    ap.add_argument("--n-dias", type=int, default=DIAS_PREVISTOS, help="pregoes previstos na janela de teste")
    ap.add_argument("--alfa", type=float, default=0.05 / 3, help="alfa bicaudal do teste (regra 14: 5 %%/3)")
    ap.add_argument("--taxa-usd", type=float, required=True, help="taxa de ida e volta por contrato (regra B)")
    ap.add_argument("--simulacoes", type=int, default=SIMULACOES)
    args = ap.parse_args(argv)
    try:
        if args.medicao.stat().st_size > 64 * 1024 * 1024:
            raise PoderError("medicao_muito_grande")
        bruto = json.loads(args.medicao.read_text(encoding="utf-8"))
        medicao = bruto.get("medicao", bruto) if isinstance(bruto, dict) else None
        c1 = cu.checklist(medicao, args.taxa_usd)
        if c1["veredito"] == "SEM_AMOSTRA":
            raise PoderError("medicao_sem_amostra")
        r = checklist(medicao, ops_por_pregao=args.ops_por_pregao, n_dias=args.n_dias, alfa=args.alfa,
                      custo_ticks=c1["custo_total_ticks"], simulacoes=args.simulacoes)
    except (PoderError, cu.CustoError, OSError, ValueError) as erro:
        print(f"conta de poder impossivel: {erro}")
        return 2
    print(json.dumps({"checklist1": c1, "checklist2": r}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
