#!/usr/bin/env python3
"""
QUANT HPC OS — Monitor de Aprendizado (read-only)
=================================================
Mostra, sem tocar no sistema rodando, a cobertura das marcações simuladas e
como esta o desempenho PAPER-FORWARD. Le apenas arquivos (live_performance.json
e track_record/nav.csv) — nao abre o DuckDB (que fica travado pelo sistema).

USO:  python monitor_learning.py

REGRA: mede paper-forward. Nunca conclui "aprendeu" ou "lucrativo".
"""
from __future__ import annotations
import json, os, math
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE = os.path.join(ROOT, "data_lake", "live_performance.json")
NAV = os.path.join(ROOT, "data_lake", "track_record", "nav.csv")
ACTIVATION = 10          # nº de registros p/ o feedback ativar (compute_fitness)
PPY = 252


def _sharpe(a):
    a = np.asarray(a, float)
    return float(a.mean() / (a.std() + 1e-9) * math.sqrt(PPY)) if len(a) > 1 and a.std() > 0 else 0.0


def feedback_status():
    print("=" * 66)
    print("  1. COBERTURA DE MARCAÇÕES PAPER (não é aprendizado)")
    print("=" * 66)
    if not os.path.exists(LIVE):
        print("  live_performance.json NAO existe — feedback ainda sem dados.")
        print("  (nenhuma posicao foi marcada/fechada ainda)")
        return
    d = json.load(open(LIVE))
    if not d:
        print("  vazio."); return
    rows = []
    for sid, rets in d.items():
        n = len(rets); a = np.asarray(rets, float)
        rows.append((sid, n, float(a.mean()) * 100, _sharpe(a)))
    rows.sort(key=lambda r: -r[1])
    n_total = len(rows); n_active = sum(1 for r in rows if r[1] >= ACTIVATION)
    n_recs = sum(r[1] for r in rows)
    print(f"  IDs com marcações simuladas    : {n_total}")
    print(f"  registros de retorno totais   : {n_recs}")
    print(f"  feedback JA ativo (>={ACTIVATION} regs)   : {n_active}   "
          f"({'ligado' if n_active else 'ainda acumulando — precisa de mais dias'})")
    print(f"\n  {'strategy_id':16} {'regs':>5} {'ret medio':>10} {'sharpe_vivo':>12}  status")
    print("  " + "-" * 62)
    for sid, n, meanp, sh in rows[:15]:
        prog = f"{n}/{ACTIVATION}" if n < ACTIVATION else "ATIVO"
        verdict = "perdendo" if sh < -0.2 else ("ganhando" if sh > 0.2 else "neutro")
        print(f"  {sid[:16]:16} {n:>5} {meanp:>+9.3f}% {sh:>+11.1f}  {prog:>6} {verdict}")


def _segments(df):
    df = df.copy(); df["date"] = pd.to_datetime(df["date"])
    segs = []; cur = [0]
    for i in range(1, len(df)):
        reset = df["n_fills_cum"].iloc[i] < df["n_fills_cum"].iloc[i-1]
        gap = (df["date"].iloc[i] - df["date"].iloc[i-1]).days > 4
        if reset or gap:
            segs.append(cur); cur = [i]
        else:
            cur.append(i)
    segs.append(cur)
    return df, segs


def forward_status():
    print("\n" + "=" * 66)
    print("  2. DESEMPENHO PAPER-FORWARD (NAV simulado)")
    print("=" * 66)
    if not os.path.exists(NAV):
        print("  nav.csv nao existe ainda."); return
    df = pd.read_csv(NAV)
    if df.empty:
        print("  vazio."); return
    df, segs = _segments(df)
    print(f"  {'periodo':26} {'dias':>4} {'retorno':>9}")
    print("  " + "-" * 46)
    for s in segs:
        sub = df.iloc[s]
        ret = (sub["nav"].iloc[-1] / 100000 - 1) * 100
        print(f"  {str(sub['date'].iloc[0].date())} a {str(sub['date'].iloc[-1].date())} "
              f"{len(s):>4} {ret:>+8.2f}%")
    last = df.iloc[-1]
    print(f"\n  NAV atual: ${last['nav']:,.0f}   ({(last['nav']/100000-1)*100:+.2f}% vs inicio)")
    n_boots = len(segs)
    if n_boots > 1:
        print(f"  ATENCAO: {n_boots} segmentos = o sistema reiniciou {n_boots-1}x "
              f"(fragmenta o track-record e orfana o feedback).")

    # vs SPY
    sp = df.dropna(subset=["spy_close"])
    if len(sp) > 1:
        s0, s1 = sp["spy_close"].iloc[0], sp["spy_close"].iloc[-1]
        spy_ret = (s1 / s0 - 1) * 100
        sys_ret = (df["nav"].iloc[-1] / 100000 - 1) * 100
        print(f"\n  vs BENCHMARK: SPY {spy_ret:+.2f}%  |  sistema {sys_ret:+.2f}%  "
              f"|  excesso {sys_ret - spy_ret:+.2f}%")
        if sys_ret < spy_ret:
            print("  -> perde para simplesmente segurar o SPY (sem valor ate aqui).")


def verdict():
    print("\n" + "=" * 66)
    print("  3. VEREDITO HONESTO (so forward, sem falsa esperanca)")
    print("=" * 66)
    if not os.path.exists(NAV):
        print("  🟡 sem dados suficientes ainda."); return
    df = pd.read_csv(NAV)
    df, segs = _segments(df)
    last_ret = (df["nav"].iloc[-1] / 100000 - 1) * 100
    n_active = 0
    if os.path.exists(LIVE):
        d = json.load(open(LIVE)); n_active = sum(1 for v in d.values() if len(v) >= ACTIVATION)
    if last_ret < -1.0:
        print("  🔴 SANGRANDO — forward negativo. Verificar custo/edge.")
    elif last_ret > 0.5:
        print("  🟢 SINAL POSITIVO PRELIMINAR — MAS amostra insuficiente. Rodar")
        print("     consolidation_scorecard.py; NUNCA operar com dinheiro real ainda.")
    else:
        print("  🟡 DE LADO — forward ~flat. Custo controlado, SEM edge demonstrado.")
        print("     'De lado' nao e lucro.")
    print(f"\n  feedback ativo em {n_active} estrategias | precisa de semanas + DSR")
    print("  antes de qualquer conclusao. Lucro garantido nao existe.")


if __name__ == "__main__":
    feedback_status()
    forward_status()
    verdict()
    print()
