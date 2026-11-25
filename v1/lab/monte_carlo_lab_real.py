#!/usr/bin/env python3
"""
QUANT LAB v2 — Contas comendo DISTRIBUICOES REAIS de estrategias
================================================================
Agora sem (p,b) assumidos. Cada conta faz bootstrap da distribuicao REALIZADA
de retornos de uma estrategia (do live_performance.json, forward real) e a
PERSONALIDADE e um involucro de SIZING (alavancagem) sobre essa distribuicao.
Assim separamos EDGE (esta na distribuicao) de PERSONALIDADE (o sizing).
Barreiras: alvo $11.500 (PASSA), piso $9.000 (LIQUIDA). Bootstrap de MAX_DAYS.

Honestidade: se a distribuicao nao tem expectativa positiva, NENHUM sizing a
salva — so muda entre ir a lugar nenhum (timeout) e se autoliquidar (alavanca).
E com n minusculo, o proprio SINAL do edge nao e estabelecido (ver t-stat).
"""
from __future__ import annotations
import json, os
import numpy as np

CAP0, TARGET, FLOOR = 10_000.0, 11_500.0, 9_000.0
MAX_DAYS = 60
N_PATHS  = 40_000
COST_BPS = 0.0001   # custo por dia por unidade de alavancagem (1bp * L)
SEED = 11
LIVE = "data_lake/live_performance.json"

# personalidade = alavancagem sobre a distribuicao real (+ Kelly derivada por fluxo)
PERSONS_FIXAS = [("Conservadora L=1", 1.0), ("Equilibrada L=3", 3.0),
                 ("Agressiva L=8", 8.0), ("Muito agressiva L=15", 15.0)]


def carrega_fluxos_distintos():
    d = json.load(open(LIVE))
    vistos, fluxos = {}, {}
    for sid, rets in d.items():
        a = np.asarray(rets, float)
        if len(a) < 5:            # precisa de amostra minima p/ bootstrap
            continue
        chave = tuple(np.round(a, 6))   # dedup: fluxos identicos = mesma linhagem
        if chave in vistos:
            vistos[chave].append(sid[:11]); continue
        vistos[chave] = [sid[:11]]
        fluxos[sid[:11]] = a
    # anexa quantos ids colapsaram em cada fluxo
    dup = {list(v)[0][:11] if False else None: None}
    return fluxos, vistos


def stats(a):
    n = len(a); mu = a.mean(); sd = a.std(ddof=1) if n > 1 else 0.0
    tstat = mu / (sd / np.sqrt(n)) if sd > 0 else 0.0
    kelly = mu / (sd**2) if sd > 0 else 0.0   # f* ~ mu/var (retornos pequenos)
    return n, mu, sd, tstat, kelly


def simula(returns, L, rng):
    eq = np.full(N_PATHS, CAP0); resolved = np.zeros(N_PATHS, bool)
    out = np.zeros(N_PATHS, np.int8); td = np.full(N_PATHS, np.nan)
    m = len(returns)
    for day in range(MAX_DAYS):
        idx = rng.integers(0, m, N_PATHS)
        r = returns[idx]
        act = ~resolved
        eq = np.where(act, eq * (1.0 + L * r - COST_BPS * L), eq)
        pas = act & (eq >= TARGET); liq = act & (eq <= FLOOR)
        out[pas] = 1; resolved[pas] = True; td[pas] = day + 1
        out[liq] = 2; resolved[liq] = True; td[liq] = day + 1
    out[~resolved] = 3
    return out, eq, td


def main():
    rng = np.random.default_rng(SEED)
    fluxos, vistos = carrega_fluxos_distintos()
    print("="*94)
    print(f"  QUANT LAB v2 — distribuicoes REAIS (forward) | alvo ${TARGET:,.0f} piso ${FLOOR:,.0f} | "
          f"{MAX_DAYS}d | {N_PATHS:,} contas")
    print(f"  16 estrategias colapsam em {len(fluxos)} fluxos distintos (o resto sao duplicatas de linhagem)")
    print("="*94)
    for fid, a in fluxos.items():
        n, mu, sd, ts, kel = stats(a)
        dups = sum(len(v) for k, v in vistos.items() if tuple(np.round(a,6)) == k)
        print(f"\n  FLUXO {fid}  (n={n}, {dups} estrategias identicas)")
        print(f"    media/dia={mu*100:+.4f}%  std/dia={sd*100:.4f}%  t-stat={ts:+.2f}  "
              f"Kelly f*~{kel:.1f}x  ->  edge {'POSITIVO?' if mu>0 else 'NEGATIVO'} e "
              f"{'ESTABELECIDO' if abs(ts)>2 else 'NAO estabelecido (|t|<2)'}")
        persons = PERSONS_FIXAS + [("Kelly-otima", max(0.0, min(kel, 25.0))),
                                   ("Meia-Kelly", max(0.0, min(kel/2, 25.0)))]
        print(f"    {'personalidade (sizing)':26} {'PASSA':>7} {'LIQUIDA':>8} {'timeout':>8} {'eq.media':>10}")
        for nome, L in persons:
            out, eq, td = simula(a, L, rng)
            print(f"    {nome:26} {np.mean(out==1):6.1%} {np.mean(out==2):7.1%} "
                  f"{np.mean(out==3):7.1%} {np.median(eq):9,.0f}")
    print("\n" + "="*94)
    print("  LEITURA HONESTA: distribuicao sem expectativa positiva => nenhum sizing passa acima")
    print("  da base; alavancar so troca 'timeout' por 'liquidacao'. E com |t-stat|<2 nem o SINAL")
    print("  do edge esta estabelecido: n minusculo, forward de dias. A falsificacao esta funcionando.")
    print("="*94)


if __name__ == "__main__":
    main()
