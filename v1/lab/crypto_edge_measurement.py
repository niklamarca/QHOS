#!/usr/bin/env python3
"""
CRYPTO — Bateria de medição da HIPOTESE_CRYPTO_spec_falsificavel.md

Teste A: funding carry delta-neutro (BTC, ETH, SOL, BNB perp, Binance).
Teste B: imbalance de agressor (taker_buy_volume) em klines 1h de BTC/ETH.

Parâmetros CONGELADOS na spec ANTES da primeira execução. Não ajustar aqui.
Dados públicos (sem chave), cache em lab/data_cache/ — fora do data_lake,
para não tocar a linhagem científica.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data_cache")
os.makedirs(CACHE, exist_ok=True)

FAPI = "https://fapi.binance.com"
FUNDING_ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
IMBALANCE_ASSETS = ["BTCUSDT", "ETHUSDT"]
START_MS = int(pd.Timestamp("2020-01-01").timestamp() * 1000)

# Custos e limiares — copiados da spec, não recalibrar.
CARRY_COST_PA = 0.015          # A: 1,5%/ano all-in
CASH_PROXY_PA = 0.04           # A-2
CORR_CAP = 0.30                # A-3
Z_WINDOW_H = 720               # B: 30 dias
FAMILY_B = 6                   # 2 ativos x 3 horizontes
ALPHA_B = 0.05 / FAMILY_B
COST_PER_REBAL = 0.0008        # B: 8 bps
HORIZONS_H = [1, 4, 24]
N_SHIFTS = 2000


def _get(url: str) -> list:
    request = urllib.request.Request(url, headers={"User-Agent": "qhos-lab/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_funding(symbol: str) -> pd.DataFrame:
    path = os.path.join(CACHE, f"funding_{symbol}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    rows, start = [], START_MS
    while True:
        batch = _get(f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}"
                     f"&startTime={start}&limit=1000")
        if not batch:
            break
        rows.extend(batch)
        start = int(batch[-1]["fundingTime"]) + 1
        time.sleep(0.15)
        if len(batch) < 1000:
            break
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["fundingTime"].astype(np.int64), unit="ms")
    frame["rate"] = frame["fundingRate"].astype(float)
    frame = frame[["ts", "rate"]].drop_duplicates("ts").set_index("ts").sort_index()
    frame.to_parquet(path)
    return frame


def fetch_klines_1h(symbol: str) -> pd.DataFrame:
    path = os.path.join(CACHE, f"klines1h_{symbol}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)
    rows, start = [], START_MS
    while True:
        batch = _get(f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1h"
                     f"&startTime={start}&limit=1500")
        if not batch:
            break
        rows.extend(batch)
        start = int(batch[-1][0]) + 1
        time.sleep(0.15)
        if len(batch) < 1500:
            break
    frame = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_vol", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
    frame["ts"] = pd.to_datetime(frame["open_time"].astype(np.int64), unit="ms")
    for column in ("close", "volume", "taker_buy_base"):
        frame[column] = frame[column].astype(float)
    frame = (frame[["ts", "close", "volume", "taker_buy_base"]]
             .drop_duplicates("ts").set_index("ts").sort_index())
    # Descarta a barra corrente (inacabada): regra da casa, barra parcial
    # não é observação.
    frame = frame.iloc[:-1]
    frame.to_parquet(path)
    return frame


def block_bootstrap_tstat(x: np.ndarray, block: int, n_boot: int = 4000,
                          seed: int = 7) -> float:
    """t da média com erro-padrão por bootstrap em blocos (autocorrelação)."""
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


def circular_shift_pvalue(signal: np.ndarray, target: np.ndarray,
                          n_shifts: int = N_SHIFTS, seed: int = 11) -> tuple:
    """p-valor do IC por deslocamento circular: preserva as duas autocorrelações
    e destrói só o alinhamento — o nulo honesto para séries dependentes."""
    mask = ~(np.isnan(signal) | np.isnan(target))
    s, t = signal[mask], target[mask]
    ic = float(np.corrcoef(s, t)[0, 1])
    rng = np.random.default_rng(seed)
    n = len(s)
    shifts = rng.integers(int(n * 0.05), int(n * 0.95), n_shifts)
    null = np.array([np.corrcoef(np.roll(s, k), t)[0, 1] for k in shifts])
    p = float((np.count_nonzero(np.abs(null) >= abs(ic)) + 1) / (n_shifts + 1))
    return ic, p


# ─── TESTE A — funding carry ─────────────────────────────────────────────
def test_a() -> None:
    print("=" * 92)
    print("  TESTE A — FUNDING CARRY DELTA-NEUTRO (custos pré-registrados: "
          f"{CARRY_COST_PA:.1%}/ano)")
    print("=" * 92)
    daily_pnl = {}
    print(f"\n  {'ativo':8} {'desde':>10} {'n(8h)':>7} {'%positivo':>10} "
          f"{'bruto/ano':>10} {'líquido':>9} {'t(bloco)':>9} {'veredito A-1/A-2':>18}")
    for symbol in FUNDING_ASSETS:
        funding = fetch_funding(symbol)
        rate = funding["rate"].to_numpy()
        gross_pa = rate.mean() * 3 * 365
        net_pa = gross_pa - CARRY_COST_PA
        # t da média líquida por período: subtrai custo por período de 8h.
        per_period_cost = CARRY_COST_PA / (3 * 365)
        t_net = block_bootstrap_tstat(rate - per_period_cost, block=30)
        ok = (t_net > 2) and (net_pa > CASH_PROXY_PA)
        print(f"  {symbol:8} {str(funding.index.min().date()):>10} {len(rate):7d} "
              f"{(rate > 0).mean():9.1%} {gross_pa:10.2%} {net_pa:9.2%} "
              f"{t_net:9.2f} {'PASSA' if ok else 'MORRE':>18}")
        daily_pnl[symbol] = funding["rate"].resample("1D").sum()

    aggregate = pd.DataFrame(daily_pnl).mean(axis=1).dropna()
    agg_gross = aggregate.mean() * 365
    agg_net = agg_gross - CARRY_COST_PA
    t_agg = block_bootstrap_tstat(
        aggregate.to_numpy() - CARRY_COST_PA / 365, block=10)
    print(f"\n  AGREGADO equal-weight: bruto {agg_gross:+.2%}/ano | "
          f"líquido {agg_net:+.2%}/ano | t {t_agg:+.2f}")

    # A-3: correlação com BTC e SPY (dados diários do lake, só leitura).
    try:
        btc = pd.read_parquet("data_lake/raw/BTC_USDT_1d.parquet")["close"]
        btc.index = pd.to_datetime(btc.index)
        spy = pd.read_parquet("data_lake/raw/SPY_1d.parquet")["close"]
        spy.index = pd.to_datetime(spy.index)
        joined = pd.DataFrame({
            "carry": aggregate,
            "btc": btc.pct_change(),
            "spy": spy.pct_change(),
        }).dropna()
        c_btc = joined["carry"].corr(joined["btc"])
        c_spy = joined["carry"].corr(joined["spy"])
        print(f"  A-3 correlação do carry: BTC {c_btc:+.3f} | SPY {c_spy:+.3f} "
              f"(teto {CORR_CAP}) -> {'PASSA' if max(abs(c_btc), abs(c_spy)) < CORR_CAP else 'MORRE'}")
    except Exception as exc:
        print(f"  A-3 indisponível: {exc}")

    equity = (1 + aggregate - CARRY_COST_PA / 365).cumprod()
    drawdown = float((equity / equity.cummax() - 1).min())
    negative_90d = aggregate.rolling(90).mean() * 365 - CARRY_COST_PA
    print(f"  MDD do carry líquido: {drawdown:+.2%} | pior janela 90d "
          f"anualizada líquida: {negative_90d.min():+.2%}")
    print(f"  gatilho estrutural (funding 90d <= 0): "
          f"{(aggregate.rolling(90).mean() <= 0).mean():.1%} do tempo")


# ─── TESTE B — imbalance de agressor ─────────────────────────────────────
def test_b() -> None:
    print("\n" + "=" * 92)
    print(f"  TESTE B — IMBALANCE DE AGRESSOR (família={FAMILY_B}, "
          f"alfa={ALPHA_B:.4f}, custo={COST_PER_REBAL*1e4:.0f} bps)")
    print("=" * 92)
    print(f"\n  {'ativo':8} {'h':>4} {'IC':>8} {'p(shift)':>9} {'signif.':>8} "
          f"{'SharpeLIQ':>10} {'t':>6} {'piso':>6} {'B-2/B-3':>8}")
    for symbol in IMBALANCE_ASSETS:
        klines = fetch_klines_1h(symbol)
        ratio = (klines["taker_buy_base"] / klines["volume"]).clip(0, 1) - 0.5
        z = ((ratio - ratio.rolling(Z_WINDOW_H).mean())
             / ratio.rolling(Z_WINDOW_H).std())
        pct = ratio.rolling(Z_WINDOW_H).rank(pct=True)
        ret_1h = klines["close"].pct_change()
        for horizon in HORIZONS_H:
            fwd = klines["close"].pct_change(horizon).shift(-horizon)
            ic, p = circular_shift_pvalue(z.to_numpy(), fwd.to_numpy())
            significant = p < ALPHA_B
            # Regra de quintis na grade do horizonte (parâmetros da spec).
            grid = slice(None, None, horizon)
            position = pd.Series(0.0, index=klines.index)
            position[pct > 0.8] = 1.0
            position[pct < 0.2] = -1.0
            position = position.iloc[grid].reindex(klines.index).ffill()
            strategy = (position.shift(1) * ret_1h
                        - COST_PER_REBAL * position.diff().abs())
            strategy = strategy.dropna()
            hours_per_year = 24 * 365
            sharpe = (strategy.mean() / strategy.std()
                      * np.sqrt(hours_per_year)) if strategy.std() > 0 else 0.0
            years = len(strategy) / hours_per_year
            t_strategy = sharpe * np.sqrt(years)
            floor = float(np.sqrt(2 * np.log(FAMILY_B)) / np.sqrt(years))
            operable = (t_strategy > 2) and (sharpe > floor)
            print(f"  {symbol:8} {horizon:3d}h {ic:+8.4f} {p:9.4f} "
                  f"{'SIM' if significant else 'nao':>8} {sharpe:+10.2f} "
                  f"{t_strategy:+6.2f} {floor:5.2f} "
                  f"{'PASSA' if operable else 'MORRE':>8}")


if __name__ == "__main__":
    print("HIPOTESE_CRYPTO — bateria de medição (parâmetros congelados na spec)")
    test_a()
    test_b()
    print("\n" + "=" * 92)
