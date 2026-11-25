"""
QUANT HPC OS — Backtest Engine (Core)
Engine vetorizado de backtest para uma única estratégia.
CPU-only, sem GPU, otimizado para execução massiva paralela.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from core.config import config
from evolution.strategy_dna import StrategyDNA


@dataclass
class BacktestResult:
    """Resultado completo de um backtest."""
    strategy_id: str
    sharpe: float = 0.0
    sortino: float = 0.0
    cagr: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    n_trades: int = 0
    avg_trade_duration: float = 0.0
    calmar: float = 0.0
    volatility: float = 0.0
    total_return: float = 0.0
    regime: str = "all"

    # Curvas
    equity_curve: Optional[pd.Series] = None
    drawdown_curve: Optional[pd.Series] = None
    trade_log: Optional[pd.DataFrame] = None

    # Metadata
    is_in_sample: bool = True
    period_start: Optional[str] = None
    period_end: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "strategy_id": self.strategy_id,
            "sharpe": round(self.sharpe, 4),
            "sortino": round(self.sortino, 4),
            "cagr": round(self.cagr, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "n_trades": self.n_trades,
            "avg_trade_duration": round(self.avg_trade_duration, 2),
            "calmar": round(self.calmar, 4),
            "volatility": round(self.volatility, 4),
            "total_return": round(self.total_return, 4),
            "regime": self.regime,
            "is_in_sample": self.is_in_sample,
            "period_start": self.period_start,
            "period_end": self.period_end,
        }

    @property
    def is_valid(self) -> bool:
        return (
            self.n_trades >= config.backtest_min_trades
            and not np.isnan(self.sharpe)
            and not np.isinf(self.sharpe)
        )


class BacktestEngine:
    """
    Engine de backtest vetorizado para uma estratégia.

    Características:
    - 100% NumPy/Pandas (sem loops Python)
    - Slippage e comissão parametrizados (não reproduz microestrutura)
    - Stop-loss, take-profit, trailing stop
    - Métricas institucionais completas
    - Suporte a long/short
    """

    def __init__(
        self,
        slippage_bps: float = None,
        commission_bps: float = None,
    ):
        # is None (nao 'or'): permite custo 0 explicito sem cair no default
        self.slippage_bps = (
            config.backtest_slippage_bps if slippage_bps is None else slippage_bps
        ) / 10000
        self.commission_bps = (
            config.backtest_commission_bps if commission_bps is None else commission_bps
        ) / 10000
        self.total_cost_bps = self.slippage_bps + self.commission_bps

    def run(
        self,
        strategy: StrategyDNA,
        features: pd.DataFrame,
        prices: pd.Series,
        regime: str = "all",
    ) -> BacktestResult:
        """
        Executa backtest completo de uma estratégia.

        Args:
            strategy: DNA da estratégia
            features: DataFrame de features (index = datas)
            prices: Série de preços de fechamento
            regime: 'all' ou nome de regime específico
        """
        if features.empty or len(prices) < 50:
            return BacktestResult(strategy_id=strategy.strategy_id, regime=regime)

        # Alinha índices
        common_idx = features.index.intersection(prices.index)
        if len(common_idx) < 50:
            return BacktestResult(strategy_id=strategy.strategy_id, regime=regime)

        features = features.loc[common_idx]
        prices = prices.loc[common_idx]

        # Gera sinal combinado das regras
        raw_signal = self._generate_signal(strategy, features)

        # Simula execução com custos
        equity, trades = self._simulate(strategy, raw_signal, prices)

        if equity is None or len(equity) < 10:
            return BacktestResult(strategy_id=strategy.strategy_id, regime=regime)

        # Calcula métricas
        result = self._compute_metrics(
            strategy.strategy_id, equity, trades, prices, regime
        )

        return result

    def _generate_signal(
        self, strategy: StrategyDNA, features: pd.DataFrame
    ) -> pd.Series:
        """Combina regras de entrada em sinal normalizado [-1, 0, 1]."""
        if not strategy.entry_rules:
            return pd.Series(0.0, index=features.index)

        signals = []
        for rule in strategy.entry_rules:
            sig = rule.evaluate(features)
            signals.append(sig)

        combined = pd.concat(signals, axis=1).fillna(0).sum(axis=1)

        # Normaliza baseado no tipo de sinal
        if strategy.signal_type.value == "long_only":
            combined = (combined > 0).astype(float)
        elif strategy.signal_type.value == "long_short":
            combined = np.sign(combined)
        else:
            # strength: normaliza [-1, 1]
            max_val = combined.abs().max() + 1e-8
            combined = (combined / max_val).clip(-1, 1)

        return combined.fillna(0)

    def _simulate(
        self,
        strategy: StrategyDNA,
        signal: pd.Series,
        prices: pd.Series,
    ) -> Tuple[Optional[pd.Series], List[Dict]]:
        """
        Simulação vetorizada de execução.

        TODO exit (reversão de sinal, stop-loss, take-profit, trailing,
        max-holding) registra um trade E aplica custo de transação. Sem isso
        win_rate/profit_factor ficam inflados (stops, que sao perdas, somem do
        log) e o equity — logo Sharpe e total_return — ignora o custo das
        saidas de risco. Aqui equity, Sharpe e total_return variam de acordo
        com os trades de fato aplicados.
        """
        n = len(prices)
        if n < 10:
            return None, []

        prices_arr = prices.values.astype(float).copy()
        signal_arr = signal.reindex(prices.index).fillna(0).values.copy()

        c = self.total_cost_bps          # custo por lado (slippage + comissao)
        sl = float(strategy.exit_rule.stop_loss_pct)
        tp = float(strategy.exit_rule.take_profit_pct)
        trail = float(strategy.exit_rule.trailing_stop_pct)
        max_hold = int(strategy.exit_rule.max_holding_days)

        equity = np.ones(n)
        trades = []

        current_pos = 0.0
        ep = 0.0                          # entry price
        entry_day = 0
        peak = 0.0                        # melhor preco favoravel desde a entrada

        def _close_trade(i, exit_px):
            gross = (exit_px / ep - 1.0) * current_pos
            net = gross - 2.0 * c          # custo round-trip refletido no pnl
            trades.append({
                "entry": entry_day,
                "exit": i,
                "direction": float(np.sign(current_pos)),
                "entry_price": ep,
                "exit_price": exit_px,
                "pnl": net,
                "duration": i - entry_day,
            })

        for i in range(1, n):
            prev_pos = current_pos

            # 1) Retorno do periodo sobre a posicao mantida entrando na barra i
            if prev_pos != 0:
                ret = (prices_arr[i] / prices_arr[i - 1] - 1.0) * prev_pos
                equity[i] = equity[i - 1] * (1 + ret)
            else:
                equity[i] = equity[i - 1]

            exited = False

            # 2) Saidas de risco.
            # ASSIMETRIA CORRIGIDA (auditoria 12/08): a ENTRADA usa o sinal da
            # barra i-1 (defasado, correto), mas a SAIDA era avaliada e executada
            # no MESMO fechamento da barra i — decidia e executava com a mesma
            # informacao. Agora a condicao e avaliada na barra i-1 e a execucao
            # ocorre no fechamento da barra i, igual a entrada.
            if current_pos != 0 and ep > 0:
                _px_aval = prices_arr[i - 1]        # avalia com dado de ONTEM
                sgn = np.sign(current_pos)
                if peak == 0.0 or _px_aval * sgn > peak * sgn:
                    peak = _px_aval
                price_change = (_px_aval / ep - 1.0) * sgn
                trail_dd = ((_px_aval - peak) / peak) * sgn if peak > 0 else 0.0

                reason = None
                if sl > 0 and price_change <= -sl:
                    reason = "stop_loss"
                elif tp > 0 and price_change >= tp:
                    reason = "take_profit"
                elif trail > 0 and trail_dd <= -trail:
                    reason = "trailing_stop"
                elif max_hold > 0 and (i - 1 - entry_day) >= max_hold:
                    reason = "max_holding"

                if reason is not None:
                    # executa no fechamento de HOJE (barra i), como a entrada
                    _close_trade(i, prices_arr[i])
                    equity[i] *= (1 - c)          # comissao/slippage de saida no equity
                    current_pos = 0.0
                    ep = 0.0
                    peak = 0.0
                    exited = True

            # 3) Sinal (entrada / saida / inversao) — so se nao saiu por risco
            if not exited:
                target = float(signal_arr[i - 1])
                if abs(target - current_pos) > 0.01:
                    if current_pos != 0 and ep > 0:
                        _close_trade(i, prices_arr[i])
                    # custo proporcional ao notional negociado (fecha + abre)
                    equity[i] *= (1 - c * abs(target - current_pos))
                    current_pos = target
                    if target != 0:
                        ep = prices_arr[i]
                        entry_day = i
                        peak = prices_arr[i]
                    else:
                        ep = 0.0
                        peak = 0.0

        equity_series = pd.Series(equity, index=prices.index)
        return equity_series, trades

    def _compute_metrics(
        self,
        strategy_id: str,
        equity: pd.Series,
        trades: List[Dict],
        prices: pd.Series,
        regime: str,
    ) -> BacktestResult:
        """Calcula métricas institucionais completas."""
        # Retornos diários
        rets = equity.pct_change().dropna()
        if len(rets) < 20:
            return BacktestResult(strategy_id=strategy_id, regime=regime)

        ann_factor = 252
        rf_daily = config.risk_free_rate / ann_factor

        # Sharpe
        excess = rets - rf_daily
        vol = rets.std()
        sharpe = (excess.mean() / (vol + 1e-10)) * np.sqrt(ann_factor)

        # Sortino
        downside = rets[rets < rf_daily]
        downside_vol = downside.std() if len(downside) > 0 else vol
        sortino = (excess.mean() / (downside_vol + 1e-10)) * np.sqrt(ann_factor)

        # CAGR
        total_ret = float(equity.iloc[-1] / equity.iloc[0] - 1)
        n_years = len(equity) / ann_factor
        cagr = (1 + total_ret) ** (1 / max(n_years, 0.01)) - 1

        # Max Drawdown
        roll_max = equity.expanding().max()
        dd = (equity - roll_max) / (roll_max + 1e-8)
        max_dd = float(dd.min())

        # Calmar
        calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0

        # Volatilidade anualizada
        ann_vol = float(vol * np.sqrt(ann_factor))

        # Win rate e profit factor
        if trades:
            pnls = np.array([t["pnl"] for t in trades])
            wins = pnls[pnls > 0]
            losses = pnls[pnls < 0]
            win_rate = len(wins) / len(pnls) if len(pnls) > 0 else 0.0
            profit_factor = (
                abs(wins.sum()) / (abs(losses.sum()) + 1e-8)
                if len(losses) > 0 else 2.0
            )
            avg_duration = np.mean([t["duration"] for t in trades])
        else:
            win_rate = 0.0
            profit_factor = 0.0
            avg_duration = 0.0

        # Drawdown curve
        dd_curve = dd

        return BacktestResult(
            strategy_id=strategy_id,
            sharpe=float(sharpe),
            sortino=float(sortino),
            cagr=float(cagr),
            max_drawdown=float(max_dd),
            win_rate=float(win_rate),
            profit_factor=float(profit_factor),
            n_trades=len(trades),
            avg_trade_duration=float(avg_duration),
            calmar=float(calmar),
            volatility=float(ann_vol),
            total_return=float(total_ret),
            regime=regime,
            equity_curve=equity,
            drawdown_curve=dd_curve,
            period_start=str(equity.index[0].date()),
            period_end=str(equity.index[-1].date()),
        )

    def run_monte_carlo(
        self,
        strategy: StrategyDNA,
        features: pd.DataFrame,
        prices: pd.Series,
        n_trials: int = None,
    ) -> Dict:
        """
        Monte Carlo perturbation test.
        Adiciona ruído às features e re-executa backtest múltiplas vezes.
        Avalia robustez a variações de parâmetros.
        """
        n_trials = n_trials or min(config.backtest_monte_carlo_trials, 200)
        sharpes = []
        dds = []

        # Ruido proporcional ao desvio de CADA feature (escalas heterogeneas:
        # RSI 0-100, retornos ~0.01, volume em milhoes). Ruido absoluto unico
        # seria irrelevante numas e dominante noutras.
        feat_std = features.std(axis=0).replace(0, 1.0).values
        noise_scale = 0.05  # 5% do desvio de cada feature

        for _ in range(n_trials):
            noisy_features = features + (
                np.random.normal(0, 1, size=features.shape) * feat_std * noise_scale
            )
            noisy_prices = prices * (
                1 + np.random.normal(0, 0.001, size=len(prices))
            )

            r = self.run(strategy, noisy_features, noisy_prices.rename("close"))
            if r.is_valid:
                sharpes.append(r.sharpe)
                dds.append(r.max_drawdown)

        if not sharpes:
            return {"stability": 0.0, "sharpe_p5": 0.0, "sharpe_mean": 0.0}

        sharpes_arr = np.array(sharpes)
        mean = float(np.mean(sharpes_arr))
        std = float(np.std(sharpes_arr))
        pct_pos = float(np.mean(sharpes_arr > 0))
        cv = std / (abs(mean) + 1e-8)
        stability = float(pct_pos * (1 - min(cv, 1.0)))  # [0,1] continuo
        return {
            "stability": stability,
            "sharpe_mean": mean,
            "sharpe_std": std,
            "sharpe_p5": float(np.percentile(sharpes_arr, 5)),
            "sharpe_p95": float(np.percentile(sharpes_arr, 95)),
            "dd_mean": float(np.mean(dds)),
            "pct_positive": pct_pos,
            "n_trials": len(sharpes),
        }
