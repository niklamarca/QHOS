#!/usr/bin/env python3
"""
QUANT LAB — Laboratorio de Contas Competindo (Monte Carlo)
==========================================================
Cada conta comeca com $10.000, alvo $11.500 (PASSA), piso $9.000 (LIQUIDADA).
Cada personalidade = uma parametrizacao de (assimetria b, agressividade f,
frequencia). O EDGE e um INPUT (nao descoberto): expectativa aritmetica por
trade, em unidades de risco R. edge=0 => jogo justo (null). O laboratorio
mede a GEOMETRIA DE SOBREVIVENCIA: quantas contas passam vs. liquidam, e como
vol-drag + custo matam personalidades sozinhos, e quanto edge e preciso para
virar o jogo.

REGRA DE OURO: edge=0 e a linha de base honesta. Nenhuma personalidade deveria
passar acima da ruina do apostador (~1000/2500=40% no jogo justo idealizado);
o custo e o vol-drag empurram para baixo. So EDGE (>0) move acima disso.
"""
from __future__ import annotations
import numpy as np

CAP0    = 10_000.0
TARGET  = 11_500.0
FLOOR   =  9_000.0
DAILY_CAP = 0.05     # stop diario: para de operar no dia se cair 5% do equity do dia
MAX_DAYS  = 60
N_PATHS   = 40_000
COST_BPS_RT   = 0.0010   # 10 bps round-trip sobre o nocional
NOTIONAL_MULT = 5.0      # nocional ~ 5x o risco por trade (stop ~20%)
SEED = 7

# personalidade: (nome, b=payoff win/loss, f=fracao de equity arriscada/trade, trades/dia)
PERSONALIDADES = [
    ("Conservadora",     1.0, 0.02, 3),
    ("Equilibrada",      1.0, 0.05, 3),
    ("Agressiva",        1.0, 0.12, 3),
    ("Assimetrica+ (deixa correr)", 2.5, 0.03, 3),  # p baixo, ganhos grandes
    ("Assimetrica- (scalper)",      0.5, 0.03, 6),  # p alto, ganhos pequenos
    ("Cacadora HF",      1.0, 0.03, 10),
]
EDGES = [0.00, 0.03, 0.06, 0.10]   # expectativa aritmetica por trade (R)


def simula(b, f, tpd, edge, rng):
    # p tal que expectativa aritmetica = p*b-(1-p) = edge  =>  p=(1+edge)/(1+b)
    p = (1.0 + edge) / (1.0 + b)
    k = COST_BPS_RT * NOTIONAL_MULT * f        # custo por trade (fracao de equity)
    eq = np.full(N_PATHS, CAP0)
    resolved = np.zeros(N_PATHS, bool)
    outcome  = np.zeros(N_PATHS, np.int8)      # 1=passa 2=liquida 3=timeout
    tdays    = np.full(N_PATHS, np.nan)
    for day in range(MAX_DAYS):
        day_start = eq.copy()
        for t in range(tpd):
            active = ~resolved & (eq > day_start * (1 - DAILY_CAP))
            if not active.any():
                continue
            u = rng.random(N_PATHS)
            win = u < p
            ret = np.where(win, b * f, -f)      # retorno bruto do trade (fracao equity)
            neweq = eq * (1.0 + ret - k)
            eq = np.where(active, neweq, eq)
            frac_day = day + (t + 1) / tpd
            passou = active & (eq >= TARGET)
            liq    = active & (eq <= FLOOR)
            outcome[passou] = 1; resolved[passou] = True; tdays[passou] = frac_day
            outcome[liq]    = 2; resolved[liq]    = True; tdays[liq]    = frac_day
    outcome[~resolved] = 3
    return outcome, eq, tdays


def main():
    rng = np.random.default_rng(SEED)
    print("="*92)
    print(f"  QUANT LAB — {N_PATHS:,} contas/personalidade | alvo ${TARGET:,.0f} | piso ${FLOOR:,.0f} | "
          f"stop diario {DAILY_CAP:.0%} | {MAX_DAYS}d")
    print(f"  custo {COST_BPS_RT*1e4:.0f}bps rt x nocional {NOTIONAL_MULT:.0f}x | ruina do apostador (jogo justo) = "
          f"{(CAP0-FLOOR)/(TARGET-FLOOR):.0%}")
    print("="*92)
    for edge in EDGES:
        tag = "NULL (sem edge)" if edge == 0 else f"edge = +{edge:.2f}R/trade"
        print(f"\n  ----- {tag} -----")
        print(f"  {'personalidade':32} {'PASSA':>7} {'LIQUIDA':>8} {'timeout':>8} {'d.medio':>8} {'equity med':>11}")
        for nome, b, f, tpd in PERSONALIDADES:
            out, eq, td = simula(b, f, tpd, edge, rng)
            npass = np.mean(out == 1); nliq = np.mean(out == 2); ntmo = np.mean(out == 3)
            dmed = np.nanmedian(td[out == 1]) if (out == 1).any() else float("nan")
            print(f"  {nome:32} {npass:6.1%} {nliq:7.1%} {ntmo:7.1%} {dmed:7.1f} {np.median(eq):10,.0f}")
    print("\n" + "="*92)
    print("  LEITURA: edge=0 => nenhuma personalidade passa acima da base por MERITO; agressividade")
    print("  (vol-drag) + custo DERRUBAM a taxa de passagem sozinhos. So edge>0 vira o jogo — e note")
    print("  quem CONVERTE edge melhor (nem sempre a mais agressiva: super-aposta se autoliquida).")
    print("="*92)


if __name__ == "__main__":
    main()
