"""
QUANT HPC OS — Walk-Forward Validator
Validação robusta com walk-forward e regime-split testing.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from core.config import config
from core.logger import get_logger
from evolution.strategy_dna import StrategyDNA
from .engine import BacktestEngine, BacktestResult

log = get_logger("WalkForward")


@dataclass
class WalkForwardResult:
    """Resultado de validação walk-forward."""
    strategy_id: str
    n_folds: int
    fold_sharpes: List[float] = field(default_factory=list)
    fold_dds: List[float] = field(default_factory=list)
    mean_sharpe: float = 0.0
    std_sharpe: float = 0.0
    min_sharpe: float = 0.0
    pct_positive_folds: float = 0.0
    stability_score: float = 0.0
    is_anchor_sharpe: float = 0.0
    degradation_ratio: float = 1.0
    overall_valid: bool = False

    def compute(self):
        if not self.fold_sharpes:
            return
        arr = np.array(self.fold_sharpes)
        self.mean_sharpe = float(np.mean(arr))
        self.std_sharpe = float(np.std(arr))
        self.min_sharpe = float(np.min(arr))
        self.pct_positive_folds = float(np.mean(arr > 0))
        # Estabilidade: baixo CV (coef. de variação)
        cv = self.std_sharpe / (abs(self.mean_sharpe) + 1e-8)
        self.stability_score = float(np.clip(1.0 - cv, 0, 1))

        # Thresholds (config-driven, com fallback seguro)
        min_mean   = getattr(config, "validation_min_oos_sharpe", 0.5)
        min_pos    = getattr(config, "validation_wf_min_positive_folds", 0.6)
        min_worst  = getattr(config, "validation_wf_min_worst_fold", 0.0)
        min_stab   = getattr(config, "validation_wf_min_stability", 0.2)
        max_degrad = getattr(config, "validation_wf_max_degradation", 0.4)
        ref_min    = getattr(config, "validation_wf_degradation_ref_min", 0.5)

        # Degradação IS->OOS: se há âncora in-sample confiável, exige que o
        # walk-forward não colapse em relação a ela. Captura overfit clássico
        # (ótimo no período de calibração, despenca fora dele).
        if self.is_anchor_sharpe > ref_min:
            self.degradation_ratio = self.mean_sharpe / self.is_anchor_sharpe
        else:
            self.degradation_ratio = 1.0  # sem referência confiável -> não penaliza

        self.overall_valid = (
            self.mean_sharpe >= min_mean
            and self.pct_positive_folds >= min_pos
            and self.min_sharpe >= min_worst            # nenhum fold colapsa
            and self.stability_score >= min_stab        # consistência entre folds
            and self.degradation_ratio >= max_degrad    # não degrada vs in-sample
        )


class WalkForwardValidator:
    """
    Validação walk-forward com janelas deslizantes.
    Garante que a estratégia funciona fora do período de calibração.
    """

    def __init__(self):
        self.engine = BacktestEngine()

    def validate(
        self,
        strategy: StrategyDNA,
        features: pd.DataFrame,
        prices: pd.Series,
        n_folds: int = None,
    ) -> WalkForwardResult:
        """
        Executa walk-forward com n_folds janelas.
        Cada fold: treina nos 80% anteriores, testa nos 20% seguintes.
        """
        n_folds = n_folds or config.backtest_walk_forward_folds
        result = WalkForwardResult(strategy_id=strategy.strategy_id, n_folds=n_folds)

        n = len(features)
        if n < 200:
            log.debug(f"Dados insuficientes para walk-forward: {n} rows")
            return result

        # Tamanho de cada fold
        fold_size = n // (n_folds + 1)
        min_train = fold_size * 2  # mínimo 2 folds de treino

        # Janela-âncora in-sample: trecho inicial que os folds não cobrem.
        # Referência para medir degradação out-of-sample (anti-overfit).
        first_oos_start = n - n_folds * fold_size
        if first_oos_start >= 30:
            anchor = self.engine.run(
                strategy,
                features.iloc[:first_oos_start],
                prices.iloc[:first_oos_start],
            )
            if anchor.is_valid and not np.isnan(anchor.sharpe):
                result.is_anchor_sharpe = float(anchor.sharpe)

        for fold in range(n_folds):
            # Define janela OOS
            oos_start = n - (n_folds - fold) * fold_size
            oos_end   = oos_start + fold_size
            train_end = oos_start

            if train_end < min_train or oos_end > n:
                continue

            oos_features = features.iloc[oos_start:oos_end]
            oos_prices   = prices.iloc[oos_start:oos_end]

            if len(oos_features) < 30:
                continue

            r = self.engine.run(strategy, oos_features, oos_prices)

            result.fold_sharpes.append(r.sharpe if not np.isnan(r.sharpe) else 0.0)
            result.fold_dds.append(r.max_drawdown)

        result.compute()
        return result

    def regime_split_test(
        self,
        strategy: StrategyDNA,
        features: pd.DataFrame,
        prices: pd.Series,
        regime_series: Optional[pd.Series] = None,
    ) -> Dict[str, BacktestResult]:
        """
        Testa estratégia separadamente em cada regime de mercado.
        Verifica robustez cross-regime.
        """
        results = {}

        if regime_series is not None:
            regime_series = regime_series.reindex(features.index, method="ffill")
            for regime_name in regime_series.unique():
                mask = regime_series == regime_name
                feat_reg = features[mask]
                price_reg = prices[mask]
                if len(feat_reg) >= 30:
                    r = self.engine.run(strategy, feat_reg, price_reg, regime=regime_name)
                    results[regime_name] = r
        else:
            # Sem regime labels — usa heurística baseada em volatilidade
            n = len(features)
            vol = prices.pct_change().rolling(20).std()
            vol_median = vol.median()

            # High vol (aproxima vol_expansion)
            hv_mask = vol > vol_median * 1.5
            lv_mask = vol <= vol_median

            if hv_mask.sum() >= 30:
                results["high_vol"] = self.engine.run(
                    strategy,
                    features[hv_mask],
                    prices[hv_mask],
                    regime="vol_expansion",
                )
            if lv_mask.sum() >= 30:
                results["low_vol"] = self.engine.run(
                    strategy,
                    features[lv_mask],
                    prices[lv_mask],
                    regime="trend_mr",
                )

        return results

    def compute_regime_robustness(
        self, regime_results: Dict[str, BacktestResult]
    ) -> float:
        """
        Score de robustez cross-regime [0, 1].
        Alta robustez = funciona bem em múltiplos regimes.
        """
        if not regime_results:
            return 0.5

        sharpes = [r.sharpe for r in regime_results.values()
                   if r.is_valid and not np.isnan(r.sharpe)]

        if not sharpes:
            return 0.0

        pct_positive = np.mean([s > 0 for s in sharpes])
        min_sharpe = min(sharpes)
        mean_sharpe = np.mean(sharpes)

        # Penaliza estratégias que só funcionam em um regime
        diversity_score = min(len(sharpes) / 4.0, 1.0)

        robustness = (pct_positive * 0.4 +
                      float(min_sharpe > -0.5) * 0.3 +
                      diversity_score * 0.3)

        return float(np.clip(robustness, 0, 1))
