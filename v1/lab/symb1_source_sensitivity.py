#!/usr/bin/env python3
"""
SYMB-1 — SENSIBILIDADE A FONTE E A AJUSTE (decide a compra do Databento)

Pergunta falsificavel: a estrutura do SYMB-1 depende da CAMADA DE AJUSTE da
fonte, ou sobrevive a ela?

Motivo de existir: o SYMB-1 e reversao em retorno diario. Erro de fator de
ajuste produz exatamente isso — um dia anomalo seguido de "reversao". A classe
de defeito da fonte coincide com a classe de sinal da hipotese, e a fonte atual
(yfinance) ajusta RETROATIVAMENTE e nao tem vintage. Enquanto essa coincidencia
nao for medida, nao se pode descartar que parte do resultado seja artefato.

Consequencia da decisao:
  - se a estrutura MUDA muito -> ajuste e load-bearing -> fator point-in-time
    (Databento adjustment factors, US$225/mes) tem trabalho real a fazer;
  - se a estrutura NAO muda -> ajuste nao e o confound -> comprar dado impecavel
    nao muda a conclusao, e a compra nao se justifica por este motivo.

Regra CONGELADA, copiada de strategies/symb1_reversal.py (nao re-derivar):
  pos_i = -sign( (r - sig63) * ((sig5 - r) + (sig5 - sig21)) ), defasado 1 dia
  portfolio = media das 5 pernas - 2bps * turnover

NAO escreve nada no data_lake. Somente leitura + rede para a serie de controle.
"""
from __future__ import annotations

import glob
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

UNIVERSE = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
COST = 0.0002
RAW_DIR = os.path.join("data_lake", "raw")
# A pesquisa original comeca em 2004-11 porque o GLD nao existia antes.
RESEARCH_START = "2004-11-01"


# ─── Regra congelada ──────────────────────────────────────────────────────
def symb1_returns(px: pd.DataFrame) -> pd.Series:
    ret = px.pct_change()
    s5 = ret.rolling(5).std()
    s21 = ret.rolling(21).std()
    s63 = ret.rolling(63).std()
    f = (ret - s63) * ((s5 - ret) + (s5 - s21))
    pos = (-np.sign(f)).shift(1)
    turn = pos.diff().abs().sum(axis=1)
    port = (pos * ret).mean(axis=1) - COST * turn / len(px.columns)
    return port.dropna()


def stats(r: pd.Series, name: str) -> dict:
    r = r.dropna()
    if len(r) < 252:
        print(f"  {name:34} amostra curta ({len(r)}) — nao avaliado")
        return {}
    years = len(r) / 252.0
    sd = r.std()
    sharpe = float(r.mean() / sd * np.sqrt(252)) if sd > 1e-12 else 0.0
    # t-stat do Sharpe anualizado: Sharpe * sqrt(anos).
    tstat = sharpe * np.sqrt(years)
    equity = (1 + r).cumprod()
    mdd = float((equity / equity.cummax() - 1).min() * 100)
    total = float((equity.iloc[-1] - 1) * 100)
    print(f"  {name:34} Sharpe {sharpe:+.3f}  t {tstat:+.2f}  "
          f"ret_total {total:+8.1f}%  MDD {mdd:+6.1f}%  n {len(r)}")
    return {"sharpe": sharpe, "t": tstat, "total": total, "mdd": mdd, "n": len(r)}


# ─── Carregadores ─────────────────────────────────────────────────────────
def load_from_disk() -> pd.DataFrame:
    """Exatamente o que o braco paper consome hoje."""
    cols = {}
    for symbol in UNIVERSE:
        hit = glob.glob(os.path.join(RAW_DIR, f"{symbol}_1d.parquet"))
        if not hit:
            raise FileNotFoundError(f"parquet ausente: {symbol}")
        frame = pd.read_parquet(hit[0])
        frame.index = pd.to_datetime(frame.index)
        cols[symbol] = frame["close"].astype(float)
    return pd.DataFrame(cols).dropna()


def load_from_yahoo(auto_adjust: bool) -> pd.DataFrame:
    import yfinance as yf

    cols = {}
    for symbol in UNIVERSE:
        history = yf.Ticker(symbol).history(
            start="1993-01-01", auto_adjust=auto_adjust, actions=False,
        )
        if history.empty:
            raise RuntimeError(f"Yahoo devolveu vazio para {symbol}")
        series = history["Close"].astype(float)
        series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
        cols[symbol] = series
    return pd.DataFrame(cols).dropna()


def ex_dates() -> dict:
    import yfinance as yf

    out = {}
    for symbol in UNIVERSE:
        try:
            dividends = yf.Ticker(symbol).dividends
            index = pd.to_datetime(dividends.index).tz_localize(None).normalize()
            out[symbol] = pd.Series(dividends.values, index=index)
        except Exception as exc:
            print(f"  [aviso] dividendos de {symbol} indisponiveis: {exc}")
            out[symbol] = pd.Series(dtype=float)
    return out


# ─── Testes ───────────────────────────────────────────────────────────────
def test_adjustment_layer(disk: pd.DataFrame) -> None:
    print("\n" + "=" * 86)
    print("  TESTE 1 — A ESTRUTURA DEPENDE DA CAMADA DE AJUSTE?")
    print("=" * 86)
    print("  Mesma regra, mesmo periodo, tres tratamentos de ajuste da MESMA fonte.")
    print("  Se o Sharpe muda materialmente, o ajuste e load-bearing.\n")

    try:
        adjusted = load_from_yahoo(auto_adjust=True)
        unadjusted = load_from_yahoo(auto_adjust=False)
    except Exception as exc:
        print(f"  FALHOU (rede/fonte): {type(exc).__name__}: {exc}")
        return

    common = disk.index.intersection(adjusted.index).intersection(unadjusted.index)
    common = common[common >= pd.Timestamp(RESEARCH_START)]
    print(f"  janela comum: {common.min().date()} .. {common.max().date()} "
          f"({len(common)} sessoes)\n")

    results = {}
    results["disco"] = stats(symb1_returns(disk.loc[common]), "disco (ajustado, em uso)")
    results["ajustado"] = stats(symb1_returns(adjusted.loc[common]), "Yahoo ajustado (refetch)")
    results["bruto"] = stats(symb1_returns(unadjusted.loc[common]), "Yahoo NAO ajustado")

    if results["disco"] and results["bruto"]:
        delta = results["bruto"]["sharpe"] - results["disco"]["sharpe"]
        print(f"\n  DELTA Sharpe (nao ajustado - ajustado): {delta:+.3f}")
        if abs(delta) < 0.10:
            print("  LEITURA: a estrutura NAO depende da camada de ajuste.")
            print("           Fator point-in-time nao e o confound aqui.")
        else:
            print("  LEITURA: a estrutura DEPENDE do ajuste. Fator versionado importa.")

    if results["disco"] and results["ajustado"]:
        drift = results["ajustado"]["sharpe"] - results["disco"]["sharpe"]
        print(f"\n  DERIVA da mesma fonte (refetch hoje - disco): {drift:+.3f}")
        print("  (mede reajuste retroativo do Yahoo entre a ingestao e agora)")


def test_ex_date_concentration(disk: pd.DataFrame) -> None:
    print("\n" + "=" * 86)
    print("  TESTE 2 — O LUCRO SE CONCENTRA EM DIAS DE EX-DIVIDENDO?")
    print("=" * 86)
    print("  Ex-date e onde erro de ajuste nasce. Se o P&L se concentra ali,")
    print("  o 'sinal' pode ser artefato de ajuste, nao reversao economica.\n")

    dividends = ex_dates()
    window = disk.loc[disk.index >= pd.Timestamp(RESEARCH_START)]
    returns = symb1_returns(window)

    all_ex = set()
    for symbol, series in dividends.items():
        all_ex.update(d for d in series.index if d in returns.index)
    # Contamina o dia do ex-date e o seguinte (a "reversao" cairia no dia+1).
    contaminated = set()
    index = list(returns.index)
    position = {d: i for i, d in enumerate(index)}
    for date in all_ex:
        i = position.get(date)
        if i is None:
            continue
        contaminated.add(index[i])
        if i + 1 < len(index):
            contaminated.add(index[i + 1])

    mask = returns.index.isin(contaminated)
    near = returns[mask]
    far = returns[~mask]
    share_days = mask.mean()
    total_pnl = returns.sum()
    share_pnl = near.sum() / total_pnl if abs(total_pnl) > 1e-12 else float("nan")

    print(f"  dias proximos de ex-date: {mask.sum()} de {len(returns)} ({share_days:.1%})")
    print(f"  fatia do P&L total nesses dias: {share_pnl:.1%}")
    stats(near, "so perto de ex-date")
    stats(far, "LONGE de ex-date")
    if len(far) > 252:
        sharpe_far = far.mean() / far.std() * np.sqrt(252)
        print(f"\n  LEITURA: removendo dias de ex-date, Sharpe = {sharpe_far:+.3f}")
        if share_pnl > 2 * share_days:
            print("           CONCENTRACAO DESPROPORCIONAL — suspeite de artefato de ajuste.")
        else:
            print("           Sem concentracao desproporcional. O sinal nao vive do ex-date.")


def test_error_injection(disk: pd.DataFrame, trials: int = 200) -> None:
    print("\n" + "=" * 86)
    print("  TESTE 3 — QUANTO ERRO DE DADO A ESTRUTURA TOLERA?")
    print("=" * 86)
    print("  Injeta erro de ajuste trimestral (sinal aleatorio) e remede.")
    print("  Mede a DERIVADA: se pequenos erros movem muito, dado limpo importa.\n")

    window = disk.loc[disk.index >= pd.Timestamp(RESEARCH_START)]
    base = symb1_returns(window)
    base_sharpe = base.mean() / base.std() * np.sqrt(252)
    print(f"  Sharpe base: {base_sharpe:+.3f}\n")
    rng = np.random.default_rng(11)

    print(f"  {'erro por ex-date':>18} {'Sharpe medio':>14} {'p5':>8} {'p95':>8} {'delta':>8}")
    for magnitude in (0.001, 0.0025, 0.005, 0.01):
        sharpes = []
        for _ in range(trials):
            perturbed = window.copy()
            for symbol in perturbed.columns:
                # ~4 ex-dates por ano, posicionados ao acaso.
                n_events = max(1, len(perturbed) // 63)
                rows = rng.integers(0, len(perturbed), n_events)
                signs = rng.choice([-1.0, 1.0], n_events)
                factor = np.ones(len(perturbed))
                factor[rows] = 1.0 + signs * magnitude
                perturbed[symbol] = perturbed[symbol].to_numpy() * factor
            series = symb1_returns(perturbed)
            sharpes.append(series.mean() / series.std() * np.sqrt(252))
        arr = np.array(sharpes)
        print(f"  {magnitude*100:16.2f}% {arr.mean():+14.3f} {np.percentile(arr,5):+8.3f} "
              f"{np.percentile(arr,95):+8.3f} {arr.mean()-base_sharpe:+8.3f}")

    print("\n  LEITURA: compare o delta com a margem do SYMB-1 sobre buy&hold (+0.17).")
    print("           Se erro de 0,25% ja consome a margem, a fonte e load-bearing.")


def main() -> None:
    print("=" * 86)
    print("  SYMB-1 — SENSIBILIDADE A FONTE E A AJUSTE")
    print("  Objetivo: decidir a compra de adjustment factors SEM comprar nada.")
    print("=" * 86)
    disk = load_from_disk()
    print(f"\n  disco: {disk.index.min().date()} .. {disk.index.max().date()} "
          f"({len(disk)} sessoes, {len(disk.columns)} ativos)")

    test_adjustment_layer(disk)
    test_ex_date_concentration(disk)
    test_error_injection(disk)
    print("\n" + "=" * 86)


if __name__ == "__main__":
    main()
