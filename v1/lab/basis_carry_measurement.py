#!/usr/bin/env python3
"""
BASIS — Bateria de medição da HIPOTESE_BASIS_spec_falsificavel.md

Cash-and-carry em futuros trimestrais (BTC, ETH, Binance): long spot + short
CURRENT_QUARTER, rolando na entrega. Parâmetros CONGELADOS na spec antes da
primeira execução. Dados públicos, cache em lab/data_cache (fora do lake).
"""
from __future__ import annotations

import calendar
import json
import os
import time
import urllib.request
from datetime import date, timedelta

import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "data_cache")
os.makedirs(CACHE, exist_ok=True)

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
ASSETS = ["BTCUSDT", "ETHUSDT"]
START_MS = int(pd.Timestamp("2020-01-01").timestamp() * 1000)

# Congelados na spec — não recalibrar.
COST_PA = 0.015
CASH_PROXY_PA = 0.04
CORR_CAP = 0.30
GATE_WINDOW_D = 90
MIN_DTE_FOR_LEVEL = 7


def _get(url: str) -> list:
    request = urllib.request.Request(url, headers={"User-Agent": "qhos-lab/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _paginate_klines(path_url: str, extra: dict, cache_name: str) -> pd.DataFrame:
    path = os.path.join(CACHE, cache_name)
    if os.path.exists(path):
        return pd.read_parquet(path)
    rows, cursor = [], START_MS
    while True:
        qs = "&".join(f"{k}={v}" for k, v in extra.items())
        batch = _get(f"{path_url}?{qs}&interval=1d&startTime={cursor}&limit=1000")
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) + 1
        time.sleep(0.15)
        if len(batch) < 1000:
            break
    frame = pd.DataFrame(rows).iloc[:, [0, 4]]
    frame.columns = ["open_time", "close"]
    frame["ts"] = pd.to_datetime(frame["open_time"].astype(np.int64), unit="ms")
    frame["close"] = frame["close"].astype(float)
    frame = (frame[["ts", "close"]].drop_duplicates("ts")
             .set_index("ts").sort_index().iloc[:-1])   # barra corrente fora
    frame.to_parquet(path)
    return frame


def fetch_quarterly(symbol: str) -> pd.DataFrame:
    return _paginate_klines(
        f"{FAPI}/fapi/v1/continuousKlines",
        {"pair": symbol, "contractType": "CURRENT_QUARTER"},
        f"quarterly_{symbol}.parquet")


def fetch_spot(symbol: str) -> pd.DataFrame:
    return _paginate_klines(
        f"{SPOT}/api/v3/klines", {"symbol": symbol},
        f"spot1d_{symbol}.parquet")


def expiry_calendar(first_year: int, last_year: int) -> list[date]:
    """Últimas sextas de mar/jun/set/dez (entrega 08:00 UTC)."""
    expiries = []
    for year in range(first_year, last_year + 1):
        for month in (3, 6, 9, 12):
            last_day = calendar.monthrange(year, month)[1]
            d = date(year, month, last_day)
            while d.weekday() != 4:      # sexta
                d -= timedelta(days=1)
            expiries.append(d)
    return expiries


def days_to_expiry(index: pd.DatetimeIndex) -> pd.Series:
    expiries = expiry_calendar(index.min().year, index.max().year + 1)
    exp_arr = np.array([pd.Timestamp(e) for e in expiries])
    dte = []
    for ts in index:
        future = exp_arr[exp_arr >= ts]
        dte.append((future[0] - ts).days if len(future) else np.nan)
    return pd.Series(dte, index=index, dtype=float)


def block_bootstrap_tstat(x: np.ndarray, block: int = 10, n_boot: int = 4000,
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
    print("HIPOTESE_BASIS — bateria (parâmetros congelados na spec)")
    print("=" * 92)
    daily_pnl, basis_levels = {}, {}
    for symbol in ASSETS:
        fut = fetch_quarterly(symbol)["close"]
        spot = fetch_spot(symbol)["close"]
        joined = pd.DataFrame({"fut": fut, "spot": spot}).dropna()
        dte = days_to_expiry(joined.index)
        basis_ann = ((joined["fut"] / joined["spot"] - 1.0)
                     * 365.0 / dte.clip(lower=1))
        basis_level = basis_ann[dte >= MIN_DTE_FOR_LEVEL]
        basis_levels[symbol] = basis_level

        # P&L delta-neutro: ret_spot - ret_fut, zerando o salto da rolagem
        # (dia da entrega e o seguinte) — conservador por construção.
        ret_spot = joined["spot"].pct_change()
        ret_fut = joined["fut"].pct_change()
        expiries = set(expiry_calendar(joined.index.min().year,
                                       joined.index.max().year))
        roll_days = {d for e in expiries for d in (e, e + timedelta(days=1))}
        ret_fut = ret_fut.where(~pd.Index(joined.index.date).isin(roll_days), 0.0)
        ret_spot = ret_spot.where(~pd.Index(joined.index.date).isin(roll_days), 0.0)
        carry = ret_spot - ret_fut

        # Gate 90d sem lookahead sobre a basis anualizada.
        gate = (basis_ann.shift(1).rolling(GATE_WINDOW_D).mean() > 0)
        net = (carry * gate.astype(float)) - COST_PA / 365.0
        daily_pnl[symbol] = net.dropna()

        gross_pa = float(carry[gate].mean() * 365) if gate.any() else float("nan")
        print(f"  {symbol}: {len(joined)} dias desde {joined.index.min().date()} | "
              f"basis anualizada média {basis_level.mean():+.2%} "
              f"(mediana {basis_level.median():+.2%}) | "
              f"% dias em contango {float((basis_level > 0).mean()):.1%} | "
              f"carry bruto (gate on) {gross_pa:+.2%}/ano")

    aggregate = pd.DataFrame(daily_pnl).mean(axis=1).dropna()
    net_pa = float(aggregate.mean() * 365)
    t_net = block_bootstrap_tstat(aggregate.to_numpy())
    print(f"\n  AGREGADO BTC+ETH líquido de {COST_PA:.1%}/ano: "
          f"{net_pa:+.2%}/ano | t {t_net:+.2f}")
    print(f"  B-1 (t > 2): {'PASSA' if t_net > 2 else 'MORRE'}")
    print(f"  B-2 (> {CASH_PROXY_PA:.0%} caixa): "
          f"{'PASSA' if net_pa > CASH_PROXY_PA else 'MORRE'}")

    # B-3: correlações com BTC e SPY.
    try:
        btc_ret = fetch_spot("BTCUSDT")["close"].pct_change()
        btc_ret.index = btc_ret.index.date
        spy = pd.read_parquet("data_lake/raw/SPY_1d.parquet")["close"]
        spy_ret = spy.pct_change()
        spy_ret.index = pd.to_datetime(spy.index).date
        agg = aggregate.copy()
        agg.index = agg.index.date
        j = pd.DataFrame({"carry": agg, "btc": btc_ret}).dropna()
        c_btc = float(j["carry"].corr(j["btc"]))
        j2 = pd.DataFrame({"carry": agg, "spy": spy_ret}).dropna()
        c_spy = float(j2["carry"].corr(j2["spy"]))
        verdict = "PASSA" if max(abs(c_btc), abs(c_spy)) < CORR_CAP else "MORRE"
        print(f"  B-3 corr: BTC {c_btc:+.3f} | SPY {c_spy:+.3f} -> {verdict}")
    except Exception as exc:
        print(f"  B-3 indisponível: {exc}")

    # Concentração: correlação com o funding carry (cache do lab, 6,6 anos).
    try:
        funding = {}
        for symbol in ASSETS:
            f = pd.read_parquet(os.path.join(CACHE, f"funding_{symbol}.parquet"))
            funding[symbol] = f["rate"].resample("1D").sum()
        fund_daily = pd.DataFrame(funding).mean(axis=1)
        fund_daily.index = fund_daily.index.date
        agg = aggregate.copy()
        agg.index = agg.index.date
        j3 = pd.DataFrame({"basis": agg, "funding": fund_daily}).dropna()
        c_fund = float(j3["basis"].corr(j3["funding"]))
        # correlação em janelas mensais também (níveis do prêmio, não ruído diário)
        m = j3.groupby(pd.to_datetime(j3.index).to_period("M")).sum()
        c_fund_m = float(m["basis"].corr(m["funding"]))
        print(f"  Concentração: corr diária c/ funding {c_fund:+.3f} | "
              f"mensal {c_fund_m:+.3f} (>=0,8 = mesmo prêmio)")
    except Exception as exc:
        print(f"  Concentração indisponível: {exc}")

    equity = (1 + aggregate).cumprod()
    mdd = float((equity / equity.cummax() - 1).min())
    off = pd.concat([(basis_levels[s].rolling(GATE_WINDOW_D).mean() <= 0)
                     for s in ASSETS], axis=1).mean(axis=1)
    print(f"  MDD líquido: {mdd:+.2%} | gate desligado (média dos ativos): "
          f"{float(off.mean()):.1%} do tempo")
    print("=" * 92)


if __name__ == "__main__":
    main()
