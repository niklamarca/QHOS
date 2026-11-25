#!/usr/bin/env python3
"""
QUANT HPC OS — Scorecard de Consolidacao (Go / No-Go)
=====================================================
Veredito HONESTO sobre se o sistema esta pronto para dinheiro real.
Centro estatistico: Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

Por que DSR: voce evoluiu MILHARES de estrategias e escolheu a melhor. O maior
Sharpe de N tentativas aleatorias parece otimo SEM habilidade nenhuma. O DSR
corrige o Sharpe observado pelo numero de tentativas e pela nao-normalidade
dos retornos, e devolve a PROBABILIDADE de o Sharpe verdadeiro ser > 0.

REGRA DE OURO: edite os GATES abaixo e pre-comprometa-se com eles ANTES de
olhar qualquer resultado. Mover a meta depois de ver o numero recria o
auto-engano que essa ferramenta existe para impedir.

USO:
    # a) sobre um track-record forward (CSV com coluna 'nav' e indice de datas)
    python consolidation_scorecard.py --nav-csv track_record.csv --n-trials 5000

    # b) so a estatistica DSR sobre uma serie de retornos (avancado: importar)
"""
from __future__ import annotations
import argparse
import math
import numpy as np
import pandas as pd
from scipy.stats import norm

# ============================================================
# GATES PRE-COMPROMETIDOS  (edite ANTES de ver resultados)
# ============================================================
GATES = {
    "min_months_forward":      6,      # tempo minimo de track-record forward REAL
    "min_deflated_sharpe":     0.95,   # P(Sharpe verdadeiro > 0) apos deflacao
    "min_annual_sharpe":       1.0,    # Sharpe anualizado forward
    "min_excess_vs_spy_ann":   0.0,    # tem que BATER buy-and-hold SPY (liq. custos)
    "min_trades":              100,    # massa estatistica minima
    "max_drawdown_allowed":   -0.15,   # pior DD tolerado (-15%)
}
PERIODS_PER_YEAR = 252


# ─── Estatistica nucleo ────────────────────────────────────────────────

def annualized_sharpe(returns: np.ndarray, periods: int = PERIODS_PER_YEAR):
    """Sharpe anualizado + erro-padrao (aprox. Lo 2002, ajustado p/ momentos)."""
    r = np.asarray(returns, float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 2 or r.std(ddof=1) == 0:
        return 0.0, 0.0, n
    sr_p = r.mean() / r.std(ddof=1)            # Sharpe por periodo
    sr_ann = sr_p * math.sqrt(periods)
    # erro-padrao do Sharpe por periodo com correcao de skew/kurtose
    g3 = float(pd.Series(r).skew())
    g4 = float(pd.Series(r).kurt()) + 3.0      # kurtose nao-excesso
    var_sr = (1 - g3 * sr_p + (g4 - 1) / 4.0 * sr_p ** 2) / (n - 1)
    se_ann = math.sqrt(max(var_sr, 0.0)) * math.sqrt(periods)
    return sr_ann, se_ann, n


def probabilistic_sharpe_ratio(returns: np.ndarray, sr_benchmark_per_period: float = 0.0):
    """PSR: P(Sharpe verdadeiro > benchmark). Bailey & Lopez de Prado."""
    r = np.asarray(returns, float)
    r = r[~np.isnan(r)]
    n = len(r)
    if n < 3 or r.std(ddof=1) == 0:
        return 0.0
    sr = r.mean() / r.std(ddof=1)              # por periodo
    g3 = float(pd.Series(r).skew())
    g4 = float(pd.Series(r).kurt()) + 3.0
    denom = math.sqrt(max(1 - g3 * sr + (g4 - 1) / 4.0 * sr ** 2, 1e-12))
    z = (sr - sr_benchmark_per_period) * math.sqrt(n - 1) / denom
    return float(norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_trials_std: float) -> float:
    """SR* esperado do MAXIMO de N tentativas independentes (por periodo).
    SR* = std_trials * [(1-g)*Z(1-1/N) + g*Z(1-1/(N*e))],  g = Euler-Mascheroni."""
    if n_trials < 2 or sr_trials_std <= 0:
        return 0.0
    g = 0.5772156649  # Euler-Mascheroni
    z1 = norm.ppf(1 - 1.0 / n_trials)
    z2 = norm.ppf(1 - 1.0 / (n_trials * math.e))
    return float(sr_trials_std * ((1 - g) * z1 + g * z2))


def deflated_sharpe_ratio(returns: np.ndarray, n_trials: int, sr_trials_std_per_period: float):
    """DSR = PSR com benchmark = SR* esperado do maximo de N tentativas.
    sr_trials_std_per_period: desvio-padrao dos Sharpes (POR PERIODO) das N
    estrategias testadas (a dispersao das tentativas)."""
    sr_star = expected_max_sharpe(n_trials, sr_trials_std_per_period)
    return probabilistic_sharpe_ratio(returns, sr_benchmark_per_period=sr_star), sr_star


def max_drawdown(nav: np.ndarray) -> float:
    nav = np.asarray(nav, float)
    peak = np.maximum.accumulate(nav)
    return float((nav / peak - 1.0).min())


# ─── Scorecard ─────────────────────────────────────────────────────────

def evaluate(nav: pd.Series, n_trials: int, sr_trials_std_per_period: float,
             spy_nav: pd.Series = None, n_trades: int = None,
             periods: int = PERIODS_PER_YEAR) -> dict:
    nav = nav.dropna()
    rets = nav.pct_change().dropna().values
    months = len(nav) / (periods / 12.0)
    sr_ann, se_ann, n = annualized_sharpe(rets, periods)
    dsr, sr_star = deflated_sharpe_ratio(rets, n_trials, sr_trials_std_per_period)
    mdd = max_drawdown(nav.values)
    total_ret = float(nav.iloc[-1] / nav.iloc[0] - 1)

    excess = float("nan")
    if spy_nav is not None and len(spy_nav.dropna()) > 2:
        sret = spy_nav.dropna().pct_change().dropna().values
        sr_spy_ann = (np.mean(sret) / (np.std(sret, ddof=1) + 1e-12)) * math.sqrt(periods)
        ann_sys = (np.mean(rets) * periods)
        ann_spy = (np.mean(sret) * periods)
        excess = float(ann_sys - ann_spy)

    rows = []
    def gate(name, value, op, thresh):
        ok = op(value, thresh)
        rows.append((name, value, thresh, ok))
        return ok
    import operator as op
    gate("meses de forward",        months,        op.ge, GATES["min_months_forward"])
    gate("Deflated Sharpe Ratio",   dsr,           op.ge, GATES["min_deflated_sharpe"])
    gate("Sharpe anualizado",       sr_ann,        op.ge, GATES["min_annual_sharpe"])
    if not math.isnan(excess):
        gate("excesso vs SPY (a.a.)", excess,      op.gt, GATES["min_excess_vs_spy_ann"])
    if n_trades is not None:
        gate("nº de trades",        n_trades,      op.ge, GATES["min_trades"])
    gate("max drawdown",            mdd,           op.ge, GATES["max_drawdown_allowed"])

    verdict = all(r[3] for r in rows)
    return {
        "sharpe_ann": sr_ann, "sharpe_se": se_ann, "dsr": dsr, "sr_star": sr_star,
        "max_dd": mdd, "total_return": total_ret, "months": months,
        "excess_vs_spy_ann": excess, "n_obs": n, "rows": rows, "verdict": verdict,
    }


def print_report(res: dict, n_trials: int):
    print("=" * 64)
    print("  SCORECARD DE CONSOLIDACAO — Go / No-Go")
    print("=" * 64)
    print(f"  Sharpe anualizado (forward) : {res['sharpe_ann']:+.2f}  (± {res['sharpe_se']:.2f})")
    print(f"  Deflated Sharpe Ratio       : {res['dsr']:.3f}   <- P(edge real > 0) apos {n_trials} tentativas")
    print(f"  SR* (max esperado por sorte): {res['sr_star']:.3f} por periodo")
    print(f"  Max drawdown                : {res['max_dd']:+.1%}")
    print(f"  Retorno total               : {res['total_return']:+.1%}")
    print(f"  Meses de track-record       : {res['months']:.1f}")
    if not math.isnan(res['excess_vs_spy_ann']):
        print(f"  Excesso vs SPY (a.a.)       : {res['excess_vs_spy_ann']:+.1%}")
    print("-" * 64)
    for name, val, thresh, ok in res["rows"]:
        flag = "PASS" if ok else "FAIL"
        vs = f"{val:+.3f}" if isinstance(val, float) else str(val)
        print(f"  [{flag}]  {name:24} = {vs:>10}   (limite {thresh})")
    print("-" * 64)
    print(f"  VEREDITO: {'>>> GO (todos os portoes passaram)' if res['verdict'] else '>>> NO-GO (ainda nao)'}")
    print("=" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="Scorecard go/no-go com Deflated Sharpe")
    ap.add_argument("--nav-csv", required=True, help="CSV com indice de data e coluna 'nav'")
    ap.add_argument("--spy-csv", default=None, help="CSV opcional com 'nav' do SPY buy-hold")
    ap.add_argument("--n-trials", type=int, required=True, help="nº de estrategias testadas")
    ap.add_argument("--sr-trials-std", type=float, default=None,
                    help="desvio dos Sharpes POR PERIODO das tentativas (se omitido, estima de --n-trials)")
    ap.add_argument("--n-trades", type=int, default=None)
    args = ap.parse_args()

    nav = pd.read_csv(args.nav_csv, index_col=0)["nav"]
    nav.index = pd.to_datetime(nav.index)
    spy = None
    if args.spy_csv:
        spy = pd.read_csv(args.spy_csv, index_col=0)["nav"]; spy.index = pd.to_datetime(spy.index)
    sr_std = args.sr_trials_std if args.sr_trials_std is not None else (1.0 / math.sqrt(PERIODS_PER_YEAR))
    res = evaluate(nav, args.n_trials, sr_std, spy_nav=spy, n_trades=args.n_trades)
    print_report(res, args.n_trials)
    return 0 if res["verdict"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
