#!/usr/bin/env python3
"""
FUNDING DISPERSION — HIPOTESE_FUNDING_DISPERSION_spec_falsificavel.md

Spread de funding entre Binance, Bybit e OKX. Parâmetros congelados na spec.
Cache em lab/data_cache, fora do lake.
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

ASSETS = ["BTCUSDT", "ETHUSDT"]
START_MS = int(pd.Timestamp("2020-01-01").timestamp() * 1000)
COST_PA = 0.03
CASH_PROXY_PA = 0.04
CORR_CAP = 0.30


def _get(url: str) -> dict | list:
    request = urllib.request.Request(url, headers={"User-Agent": "qhos-lab/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_binance(symbol: str) -> pd.Series:
    path = os.path.join(CACHE, f"funding_{symbol}.parquet")
    if os.path.exists(path):
        frame = pd.read_parquet(path)
        return frame["rate"]
    rows, start = [], START_MS
    while True:
        batch = _get("https://fapi.binance.com/fapi/v1/fundingRate"
                     f"?symbol={symbol}&startTime={start}&limit=1000")
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
    return frame["rate"]


def fetch_bybit(symbol: str) -> pd.Series:
    path = os.path.join(CACHE, f"funding_bybit_{symbol}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)["rate"]
    rows, end = [], int(time.time() * 1000)
    while True:
        payload = _get("https://api.bybit.com/v5/market/funding/history"
                       f"?category=linear&symbol={symbol}&endTime={end}&limit=200")
        batch = payload.get("result", {}).get("list", []) if isinstance(payload, dict) else []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(x["fundingRateTimestamp"]) for x in batch)
        if oldest <= START_MS or len(batch) < 200:
            break
        end = oldest - 1
        time.sleep(0.15)
    if not rows:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["fundingRateTimestamp"].astype(np.int64), unit="ms")
    frame["rate"] = frame["fundingRate"].astype(float)
    frame = frame[["ts", "rate"]].drop_duplicates("ts").set_index("ts").sort_index()
    frame = frame[frame.index >= pd.Timestamp("2020-01-01")]
    frame.to_parquet(path)
    return frame["rate"]


def fetch_okx(inst: str) -> pd.Series:
    path = os.path.join(CACHE, f"funding_okx_{inst}.parquet")
    if os.path.exists(path):
        return pd.read_parquet(path)["rate"]
    rows, after = [], ""
    while True:
        url = f"https://www.okx.com/api/v5/public/funding-rate-history?instId={inst}&limit=100"
        if after:
            url += f"&after={after}"
        payload = _get(url)
        batch = payload.get("data", []) if isinstance(payload, dict) else []
        if not batch:
            break
        rows.extend(batch)
        after = batch[-1]["fundingTime"]
        oldest = int(batch[-1]["fundingTime"])
        if oldest <= START_MS or len(batch) < 100:
            break
        time.sleep(0.15)
    if not rows:
        return pd.Series(dtype=float)
    frame = pd.DataFrame(rows)
    frame["ts"] = pd.to_datetime(frame["fundingTime"].astype(np.int64), unit="ms")
    frame["rate"] = frame["fundingRate"].astype(float)
    frame = frame[["ts", "rate"]].drop_duplicates("ts").set_index("ts").sort_index()
    frame = frame[frame.index >= pd.Timestamp("2020-01-01")]
    frame.to_parquet(path)
    return frame["rate"]


def block_bootstrap_tstat(x: np.ndarray, block: int = 30, n_boot: int = 4000,
                          seed: int = 7) -> float:
    rng = np.random.default_rng(seed)
    n = len(x)
    if n < block * 2:
        return 0.0
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block, size=(n_boot, n_blocks))
    means = np.array([
        np.concatenate([x[s:s + block] for s in row])[:n].mean()
        for row in starts
    ])
    se = means.std(ddof=1)
    return float(x.mean() / se) if se > 0 else 0.0


def align_8h(*series: pd.Series) -> pd.DataFrame:
    """Alinha por hora (arredonda para a hora do evento) e junta venues."""
    frames = []
    for i, s in enumerate(series):
        if s is None or s.empty:
            continue
        idx = s.index.round("h")
        frames.append(pd.Series(s.to_numpy(), index=idx, name=f"v{i}"))
    if not frames:
        return pd.DataFrame()
    joined = pd.concat(frames, axis=1)
    joined = joined[~joined.index.duplicated(keep="last")].sort_index()
    return joined.dropna(how="any") if joined.shape[1] >= 2 else pd.DataFrame()


def main() -> None:
    print("HIPOTESE_FUNDING_DISPERSION — bateria (parâmetros congelados)")
    print("=" * 92)
    daily = {}
    cost_event = COST_PA / (3 * 365)
    for symbol, okx_id in (("BTCUSDT", "BTC-USDT-SWAP"), ("ETHUSDT", "ETH-USDT-SWAP")):
        b = fetch_binance(symbol)
        y = fetch_bybit(symbol)
        o = fetch_okx(okx_id)
        print(f"  {symbol}: binance {len(b)} | bybit {len(y)} | okx {len(o)}")
        joined = align_8h(b, y, o)
        if joined.empty or len(joined) < 200:
            print(f"  {symbol}: cobertura insuficiente após alinhamento")
            continue
        spread = joined.max(axis=1) - joined.min(axis=1)
        net = spread - cost_event
        daily[symbol] = net.resample("1D").sum()
        gross_pa = float(spread.mean() * 3 * 365)
        net_pa = gross_pa - COST_PA
        t = block_bootstrap_tstat(net.to_numpy())
        print(f"  {symbol}: n={len(joined)} eventos alinhados | "
              f"spread bruto {gross_pa:+.2%}/ano | líquido {net_pa:+.2%} | t {t:+.2f}")

    if not daily:
        print("  SEM DADOS — bateria inconclusiva (não passa, não morre por t)")
        return
    aggregate = pd.DataFrame(daily).mean(axis=1).dropna()
    net_pa = float(aggregate.mean() * 365)
    t_net = block_bootstrap_tstat(aggregate.to_numpy(), block=10)
    print(f"\n  AGREGADO líquido de {COST_PA:.1%}/ano: {net_pa:+.2%}/ano | t {t_net:+.2f}")
    print(f"  D-1 (t>2): {'PASSA' if t_net > 2 else 'MORRE'}")
    print(f"  D-2 (>{CASH_PROXY_PA:.0%} caixa): {'PASSA' if net_pa > CASH_PROXY_PA else 'MORRE'}")
    try:
        btc = pd.read_parquet("data_lake/raw/BTC_USDT_1d.parquet")["close"].pct_change()
        btc.index = pd.to_datetime(btc.index).date
        spy = pd.read_parquet("data_lake/raw/SPY_1d.parquet")["close"].pct_change()
        spy.index = pd.to_datetime(spy.index).date
        agg = aggregate.copy()
        agg.index = agg.index.date
        j = pd.DataFrame({"x": agg, "btc": btc, "spy": spy}).dropna()
        c_btc, c_spy = float(j["x"].corr(j["btc"])), float(j["x"].corr(j["spy"]))
        print(f"  D-3 corr BTC {c_btc:+.3f} | SPY {c_spy:+.3f} -> "
              f"{'PASSA' if max(abs(c_btc), abs(c_spy)) < CORR_CAP else 'MORRE'}")
    except Exception as exc:
        print(f"  D-3 indisponível: {exc}")
    try:
        fund = pd.concat(
            [pd.read_parquet(os.path.join(CACHE, f"funding_{s}.parquet"))["rate"].resample("1D").sum()
             for s in ASSETS], axis=1).mean(axis=1)
        fund.index = fund.index.date
        agg = aggregate.copy(); agg.index = agg.index.date
        j = pd.DataFrame({"disp": agg, "fund": fund}).dropna()
        print(f"  Concentração corr diária c/ funding carry {float(j['disp'].corr(j['fund'])):+.3f}")
    except Exception as exc:
        print(f"  Concentração indisponível: {exc}")
    print("=" * 92)


if __name__ == "__main__":
    main()
