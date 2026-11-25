#!/usr/bin/env python3
"""
QUANT LAB v3 — Contas comendo distribuicoes de trade do BACKTEST
================================================================
ATENCAO: backtest = FICCAO conhecida deste sistema (ele mentiu ao vivo). Isto
NAO certifica edge. Serve para (a) ver o MOTOR do laboratorio girando em
distribuicoes ricas e (b) CONTRASTAR o que o backtest promete vs. o forward real.

Pipeline (roda so com o sistema DESLIGADO, senao o DuckDB fica travado):
  1. le strategy_catalog (read-only) -> StrategyDNA.from_dict
  2. reroda BacktestEngine por estrategia, junta o pnl de CADA trade
  3. bootstrap desses trades atraves das barreiras ($11.5k passa / $9k liquida),
     com PERSONALIDADE = alavancagem sobre a distribuicao
  4. imprime stats do backtest + resultado por personalidade + contraste forward
"""
from __future__ import annotations
import os, sys, json, glob
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import duckdb
from evolution.strategy_dna import StrategyDNA
from backtest.engine import BacktestEngine

DB   = os.path.join(ROOT, "data_lake", "quant_hpc.db")
FEAT = os.path.join(ROOT, "data_lake", "features")
LIVE = os.path.join(ROOT, "data_lake", "live_performance.json")

CAP0, TARGET, FLOOR = 10_000.0, 11_500.0, 9_000.0
MAX_TRADES = 150
N_PATHS    = 20_000
COST_BPS   = 0.0001
SEED = 13
MAX_STRATS = 40      # limita para velocidade


def load_features(asset):
    # tenta variacoes de nome (crypto usa '/')
    for cand in [asset, asset.replace("/", ""), asset.replace("/", "_"), asset.split("/")[0]]:
        hits = glob.glob(os.path.join(FEAT, f"{cand}_*features.parquet")) or \
               glob.glob(os.path.join(FEAT, f"{cand}*.parquet"))
        if hits:
            try:
                df = pd.read_parquet(hits[0])
                if "close" in df.columns and len(df) > 60:
                    return df
            except Exception:
                pass
    return None


def trade_pnls(engine, dna):
    """Reroda o backtest nos target_assets e junta o pnl de cada trade."""
    pnls = []
    for asset in (dna.target_assets or []):
        df = load_features(asset)
        if df is None:
            continue
        try:
            sig = engine._generate_signal(dna, df)
            _eq, trades = engine._simulate(dna, sig, df["close"])
            pnls += [float(t["pnl"]) for t in trades]
        except Exception:
            continue
    return np.array(pnls, float)


def lab(returns, L, rng):
    eq = np.full(N_PATHS, CAP0); resolved = np.zeros(N_PATHS, bool)
    out = np.zeros(N_PATHS, np.int8); m = len(returns)
    for _ in range(MAX_TRADES):
        r = returns[rng.integers(0, m, N_PATHS)]
        act = ~resolved
        eq = np.where(act, eq * (1.0 + L * r - COST_BPS * L), eq)
        pas = act & (eq >= TARGET); liq = act & (eq <= FLOOR)
        out[pas] = 1; resolved[pas] = True
        out[liq] = 2; resolved[liq] = True
    out[~resolved] = 3
    return out, eq


def main():
    rng = np.random.default_rng(SEED)
    con = duckdb.connect(DB, read_only=True)
    rows = con.execute(
        "SELECT strategy_id, name, fitness, oos_sharpe, metadata "
        "FROM strategy_catalog ORDER BY fitness DESC LIMIT ?", [MAX_STRATS]
    ).fetchdf()
    con.close()
    fwd = json.load(open(LIVE)) if os.path.exists(LIVE) else {}
    fwd_pref = {k[:11]: np.asarray(v, float) for k, v in fwd.items()}

    engine = BacktestEngine()
    print("="*100)
    print(f"  QUANT LAB v3 — distribuicoes de TRADE do BACKTEST (FICCAO) | {len(rows)} estrategias | "
          f"alvo ${TARGET:,.0f} piso ${FLOOR:,.0f}")
    print("="*100)

    done = 0
    for _, s in rows.iterrows():
        try:
            dna = StrategyDNA.from_dict(json.loads(s["metadata"]))
        except Exception as e:
            continue
        p = trade_pnls(engine, dna)
        if len(p) < 20:
            continue
        n, mu, sd = len(p), p.mean(), p.std(ddof=1)
        ts = mu/(sd/np.sqrt(n)) if sd > 0 else 0.0
        kel = mu/(sd**2) if sd > 0 else 0.0
        sid = s["strategy_id"][:11]
        fline = ""
        if sid in fwd_pref:
            fa = fwd_pref[sid]; fline = f" | FORWARD real: n={len(fa)} media/dia={fa.mean()*100:+.3f}%"
        print(f"\n  {sid}  fit={s['fitness']:.2f} bt_sharpe={s['oos_sharpe']:.2f}  "
              f"trades={n} pnl_medio={mu*100:+.3f}% std={sd*100:.2f}% t={ts:+.1f} Kelly~{kel:.1f}x{fline}")
        persons = [("Conservadora L=1", 1.0), ("Equilibrada L=3", 3.0),
                   ("Agressiva L=8", 8.0), ("Kelly-otima", max(0.0, min(kel, 20.0))),
                   ("Meia-Kelly", max(0.0, min(kel/2, 20.0)))]
        print(f"    {'personalidade':22} {'PASSA':>7} {'LIQUIDA':>8} {'timeout':>8}")
        for nome, L in persons:
            out, eq = lab(p, L, rng)
            print(f"    {nome:22} {np.mean(out==1):6.1%} {np.mean(out==2):7.1%} {np.mean(out==3):7.1%}")
        done += 1
        if done >= 12:
            break

    print("\n" + "="*100)
    print("  LEMBRETE: PASSA alto aqui = promessa do BACKTEST, nao edge. Compare com a linha FORWARD:")
    print("  onde o backtest promete e o forward entrega ~0, isso e a FICCAO medida. So o forward manda.")
    print("="*100)


if __name__ == "__main__":
    main()
