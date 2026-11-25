#!/usr/bin/env python3
"""
FUNDING EXTREME — HIPOTESE_FUNDING_EXTREME_spec_falsificavel.md

Short quando z-score do funding (270 eventos) > 2; cobre quando z < 0.
Parâmetros congelados. Usa o cache de funding já baixado pelo lab CRYPTO.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data_cache")
ASSETS = ["BTCUSDT", "ETHUSDT"]
Z_WINDOW = 270
Z_ENTER = 2.0
Z_EXIT = 0.0
COST_RT = 0.0008          # 8 bps round-trip
FAMILY = 2
ALPHA = 0.05 / FAMILY
N_SHIFTS = 2000


def circular_shift_pvalue(signal: np.ndarray, target: np.ndarray,
                          n_shifts: int = N_SHIFTS, seed: int = 11) -> tuple:
    mask = ~(np.isnan(signal) | np.isnan(target))
    s, t = signal[mask], target[mask]
    ic = float(np.corrcoef(s, t)[0, 1])
    rng = np.random.default_rng(seed)
    n = len(s)
    shifts = rng.integers(int(n * 0.05), int(n * 0.95), n_shifts)
    null = np.array([np.corrcoef(np.roll(s, k), t)[0, 1] for k in shifts])
    p = float((np.count_nonzero(np.abs(null) >= abs(ic)) + 1) / (n_shifts + 1))
    return ic, p


def block_bootstrap_tstat(x: np.ndarray, block: int = 30, n_boot: int = 4000,
                          seed: int = 7) -> float:
    rng = np.random.default_rng(seed)
    n = len(x)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block, size=(n_boot, n_blocks))
    means = np.array([
        np.concatenate([x[s:s + block] for s in row])[:n].mean()
        for row in starts
    ])
    se = means.std(ddof=1)
    return float(x.mean() / se) if se > 0 else 0.0


def main() -> None:
    print("HIPOTESE_FUNDING_EXTREME — bateria (parâmetros congelados)")
    print("=" * 92)
    daily = {}
    print(f"  {'ativo':8} {'IC':>8} {'p':>8} {'signif':>7} {'SharpeLIQ':>10} "
          f"{'t':>7} {'piso':>6} {'E-2':>8}")
    for symbol in ASSETS:
        funding = pd.read_parquet(os.path.join(CACHE, f"funding_{symbol}.parquet"))
        klines = pd.read_parquet(os.path.join(CACHE, f"klines1h_{symbol}.parquet"))
        rate = funding["rate"].astype(float)
        # retorno do perp no intervalo de 8h (close a close nas horas 0/8/16)
        close = klines["close"].astype(float)
        close_8h = close.resample("8h").last().dropna()
        ret = close_8h.pct_change()
        aligned = pd.DataFrame({"rate": rate, "ret": ret}).dropna()
        z = ((aligned["rate"] - aligned["rate"].rolling(Z_WINDOW).mean())
             / aligned["rate"].rolling(Z_WINDOW).std())
        fwd = aligned["ret"].shift(-1)
        ic, p = circular_shift_pvalue(z.to_numpy(), fwd.to_numpy())
        significant = p < ALPHA

        position = pd.Series(0.0, index=aligned.index)
        short = False
        for ts, value in z.items():
            if pd.isna(value):
                position.loc[ts] = 0.0
                continue
            if not short and value > Z_ENTER:
                short = True
            elif short and value < Z_EXIT:
                short = False
            position.loc[ts] = -1.0 if short else 0.0
        strategy = (position.shift(1) * aligned["ret"]
                    - COST_RT * position.diff().abs() / 2.0)
        strategy = strategy.dropna()
        hours_per_year = 3 * 365
        years = len(strategy) / hours_per_year
        sharpe = (strategy.mean() / strategy.std() * np.sqrt(hours_per_year)
                  if strategy.std() > 0 else 0.0)
        t_s = sharpe * np.sqrt(years)
        floor = float(np.sqrt(2 * np.log(FAMILY)) / np.sqrt(max(years, 1e-9)))
        operable = (t_s > 2) and (sharpe > floor)
        print(f"  {symbol:8} {ic:+8.4f} {p:8.4f} {'SIM' if significant else 'nao':>7} "
              f"{sharpe:+10.2f} {t_s:+7.2f} {floor:6.2f} "
              f"{'PASSA' if operable else 'MORRE':>8}")
        daily[symbol] = strategy.resample("1D").sum()

    aggregate = pd.DataFrame(daily).mean(axis=1).dropna()
    t_agg = block_bootstrap_tstat(aggregate.to_numpy(), block=10)
    net_pa = float(aggregate.mean() * 365)
    years = len(aggregate) / 365
    sharpe_agg = (aggregate.mean() / aggregate.std() * np.sqrt(365)
                  if aggregate.std() > 0 else 0.0)
    floor = float(np.sqrt(2 * np.log(FAMILY)) / np.sqrt(max(years, 1e-9)))
    print(f"\n  AGREGADO: {net_pa:+.2%}/ano | Sharpe {sharpe_agg:+.2f} | "
          f"t {t_agg:+.2f} | piso {floor:.2f}")
    print(f"  E-2 agregado t>2: {'PASSA' if t_agg > 2 else 'MORRE'}")
    print(f"  E-3 Sharpe > piso: {'PASSA' if sharpe_agg > floor else 'MORRE'}")
    try:
        fund = pd.concat(
            [pd.read_parquet(os.path.join(CACHE, f"funding_{s}.parquet"))["rate"].resample("1D").sum()
             for s in ASSETS], axis=1).mean(axis=1)
        j = pd.DataFrame({"ext": aggregate, "carry": fund}).dropna()
        print(f"  Qualificação corr c/ funding carry {float(j['ext'].corr(j['carry'])):+.3f} "
              f"(≥0,5 = timing disfarçado)")
    except Exception as exc:
        print(f"  Qualificação indisponível: {exc}")
    print("=" * 92)


if __name__ == "__main__":
    main()
