"""
QUANT HPC OS — Strategy DNA
Representação genética de estratégias financeiras.
Cada estratégia é um cromossomo de regras combinadas.
"""
from __future__ import annotations
import json
import uuid
import copy
import hashlib
import numpy as np
from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
from core.config import config


class RuleType(Enum):
    """Tipos de regras de negociação."""
    MOMENTUM      = "momentum"
    MEAN_REVERT   = "mean_revert"
    BREAKOUT      = "breakout"
    VOLATILITY    = "volatility"
    VOLUME        = "volume"
    MULTI_FACTOR  = "multi_factor"
    REGIME_FILTER = "regime_filter"
    PATTERN       = "pattern"


class SignalType(Enum):
    """Tipos de sinal de saída."""
    LONG_SHORT    = "long_short"      # -1, 0, +1
    LONG_ONLY     = "long_only"       # 0, +1
    STRENGTH      = "strength"        # contínuo [-1, +1]


@dataclass
class TradingRule:
    """Uma regra individual de negociação (gene)."""
    rule_type: RuleType
    feature: str                    # nome da feature usada
    operator: str                   # ">", "<", "cross_up", "cross_down", "between"
    threshold: float = 0.0
    threshold2: float = 0.0         # para "between"
    lookback: int = 20
    weight: float = 1.0             # peso desta regra no sinal combinado

    def to_dict(self) -> Dict:
        return {
            "rule_type": self.rule_type.value,
            "feature": self.feature,
            "operator": self.operator,
            "threshold": self.threshold,
            "threshold2": self.threshold2,
            "lookback": self.lookback,
            "weight": self.weight,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "TradingRule":
        d = d.copy()
        d["rule_type"] = RuleType(d["rule_type"])
        return cls(**d)

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Avalia a regra sobre um DataFrame de features. Retorna sinal (-1, 0, 1)."""
        if self.feature not in df.columns:
            return pd.Series(0.0, index=df.index)

        val = df[self.feature]
        signal = pd.Series(0.0, index=df.index)

        if self.operator == ">":
            signal = (val > self.threshold).astype(float)
        elif self.operator == "<":
            signal = -(val < self.threshold).astype(float)
        elif self.operator == ">|<":
            signal = np.sign(val - self.threshold)
        elif self.operator == "cross_up":
            signal = ((val > self.threshold) & (val.shift(1) <= self.threshold)).astype(float)
        elif self.operator == "cross_down":
            signal = -((val < self.threshold) & (val.shift(1) >= self.threshold)).astype(float)
        elif self.operator == "between":
            signal = ((val > self.threshold) & (val < self.threshold2)).astype(float)
        elif self.operator == "rolling_z":
            mu = val.rolling(self.lookback).mean()
            sd = val.rolling(self.lookback).std() + 1e-8
            z = (val - mu) / sd
            signal = np.tanh(z)  # suavizado em [-1, 1]
        elif self.operator == "percentile_rank":
            signal = val.rolling(self.lookback).rank(pct=True) - 0.5

        return signal * self.weight


@dataclass
class ExitRule:
    """Regra de saída de posição."""
    stop_loss_pct: float = 0.02         # 2% stop
    take_profit_pct: float = 0.05       # 5% target
    max_holding_days: int = 20
    trailing_stop_pct: float = 0.0      # 0 = desabilitado
    regime_exit: Optional[str] = None  # sai se regime mudar


@dataclass
class StrategyDNA:
    """
    Cromossomo completo de uma estratégia quantitativa.
    Contém todas as regras, parâmetros de risco e metadados evolutivos.
    """
    # Identidade
    strategy_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = "unnamed"
    generation: int = 0
    parent_ids: List[str] = field(default_factory=list)

    # Configuração
    rule_type: RuleType = RuleType.MOMENTUM
    signal_type: SignalType = SignalType.LONG_SHORT
    target_assets: List[str] = field(default_factory=list)
    allowed_regimes: List[str] = field(default_factory=lambda: [
        "trend", "mean_reversion", "vol_expansion", "liq_stress"
    ])

    # Regras de entrada (genes primários)
    entry_rules: List[TradingRule] = field(default_factory=list)
    exit_rule: ExitRule = field(default_factory=ExitRule)

    # Parâmetros de risco
    position_size: float = 0.05            # 5% do portfólio
    max_leverage: float = 1.0
    rebalance_frequency: str = "daily"     # daily | weekly | monthly

    # Métricas de fitness (preenchidas pelo backtest)
    fitness: float = 0.0
    oos_sharpe: float = 0.0
    is_sharpe: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    n_trades: int = 0
    stability_score: float = 0.0
    regime_robustness: float = 0.0
    complexity_score: float = 0.0         # penalidade

    # Walk-forward results (escritos pelo validator)
    wf_mean_sharpe: float = 0.0           # sharpe médio nos folds OOS
    walk_forward_ok: bool = False         # passou validação WF
    noise_stability: float = -1.0         # -1=nunca testado, 0-1=resultado noise injection
    # Estatistica descritiva paper — mantem nomes legados na serializacao.
    live_sharpe: float = 0.0
    live_trades: int = 0                  # observacoes, nao necessariamente trades

    # Status
    status: str = "pending"               # pending | validated | rejected | live
    rejection_reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: pd.Timestamp.now(tz="UTC").isoformat())

    # ─── Fitness Function ─────────────────────────────────────────────

    def compute_fitness(self) -> float:
        """
        Fitness = sharpe_score × stability × robustness × dd_factor
                  × complexity_factor × trade_factor   (tudo em [0,1]).
        Penalidades sao MULTIPLICATIVAS (escalam o score), nao subtrativas
        (que zeravam o gradiente com drawdowns honestos). Gates duros
        (Sharpe<=0, trades<20, noise<0.3) continuam zerando direto.
        """
        if str(self.status).lower() in {"rejected", "falsified"}:
            self.fitness = 0.0
            return 0.0

        if self.oos_sharpe <= 0 or self.n_trades < 20:
            self.fitness = 0.0
            return 0.0

        # Penalidade severa por overfitting: walk-forward negativo
        # Usa wf_mean_sharpe se disponível (mais confiável que is_sharpe)
        if self.walk_forward_ok and self.wf_mean_sharpe > 0:
            # Passou walk-forward: usa média de in-sample e OOS real
            effective_sharpe = self.oos_sharpe * 0.4 + self.wf_mean_sharpe * 0.6
        elif self.wf_mean_sharpe < 0:
            # Walk-forward negativo = overfitting confirmado — penaliza fortemente
            effective_sharpe = self.oos_sharpe * 0.2 + self.wf_mean_sharpe * 0.8
        else:
            # Ainda não validado: usa só in-sample com desconto
            effective_sharpe = self.oos_sharpe * 0.5

        if effective_sharpe <= 0:
            self.fitness = 0.0
            return 0.0

        # Penalidade noise sensitivity (escrita pelo validator após noise injection test)
        # noise_stability=-1 = nunca testado; <0.5 = parameter-sensitive (overfitting)
        # IMPORTANTE: aplicada ANTES de normalizar o sharpe — antes estava depois
        # (sharpe_score ja calculado), entao o noise_mult era codigo morto.
        if self.noise_stability >= 0:  # já foi testado
            if self.noise_stability < 0.3:
                self.fitness = 0.0
                return 0.0
            elif self.noise_stability < 0.5:
                # Penaliza proporcionalmente (0.3→0x, 0.5→1x)
                noise_mult = (self.noise_stability - 0.3) / 0.2
                effective_sharpe *= noise_mult

        # OOS Sharpe normalizado [0, 3]
        sharpe_score = min(max(effective_sharpe, 0), 3.0) / 3.0

        # Estabilidade [0, 1]
        stability = min(max(self.stability_score, 0), 1.0)

        # Robustez de regime [0, 1]
        robustness = min(max(self.regime_robustness, 0), 1.0)

        # ─── Penalidades como FATORES multiplicativos em (0, 1] ──────────
        # Antes eram SUBTRATIVAS: a base (sharpe×stab×robust ~0.5) menos um
        # dd_penalty que, com DD honesto de ~35%, chegava a 0.5 -> fitness
        # clampava em 0 e o gradiente do GA morria (selecao virava aleatoria).
        # Agora ESCALAM o score: penalizam sem aniquilar, preservando o ranking.
        n_rules = len(self.entry_rules)

        # Drawdown: decai suave acima de 10% (DD 35% -> ~0.50; 60% -> ~0.33)
        dd_excess = max(0.0, abs(self.max_drawdown) - 0.10)
        dd_factor = 1.0 / (1.0 + dd_excess * 4.0)

        # Complexidade: cada regra acima de 3 reduz um pouco (6 regras -> ~0.77)
        complexity_factor = 1.0 / (1.0 + max(0, n_rules - 3) * 0.10)

        # Trades: rampa ate 50 (significancia). >=50 -> 1.0; 20 trades -> 0.40
        trade_factor = min(1.0, self.n_trades / 50.0)

        fitness = (
            sharpe_score * stability * robustness
            * dd_factor * complexity_factor * trade_factor
        )

        # Adaptacao online e DESLIGADA por padrao. Reusar o proprio periodo
        # confirmatorio para selecionar descendentes converte paper-forward em
        # treino e apaga sua funcao de teste. O campo so entra no fitness em um
        # experimento explicitamente habilitado e pre-registrado.
        if config.online_feedback_affects_selection and self.live_trades >= 10:
            live_factor = float(np.clip(1.0 + self.live_sharpe * 0.2, 0.1, 1.4))
            fitness *= live_factor

        self.fitness = max(min(fitness, 1.4), 0.0)
        return self.fitness

    # ─── Serialização ─────────────────────────────────────────────────

    def to_dict(self) -> Dict:
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "generation": self.generation,
            "parent_ids": self.parent_ids,
            "rule_type": self.rule_type.value,
            "signal_type": self.signal_type.value,
            "target_assets": self.target_assets,
            "allowed_regimes": self.allowed_regimes,
            "entry_rules": [r.to_dict() for r in self.entry_rules],
            "exit_rule": asdict(self.exit_rule),
            "position_size": self.position_size,
            "max_leverage": self.max_leverage,
            "rebalance_frequency": self.rebalance_frequency,
            "fitness": self.fitness,
            "oos_sharpe": self.oos_sharpe,
            "is_sharpe": self.is_sharpe,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "n_trades": self.n_trades,
            "stability_score": self.stability_score,
            "noise_stability": self.noise_stability,
            "live_sharpe": self.live_sharpe,
            "live_trades": self.live_trades,
            "regime_robustness": self.regime_robustness,
            "complexity_score": self.complexity_score,
            "status": self.status,
            "rejection_reason": self.rejection_reason,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "StrategyDNA":
        d = d.copy()
        d["rule_type"] = RuleType(d.get("rule_type", "momentum"))
        d["signal_type"] = SignalType(d.get("signal_type", "long_short"))
        d["entry_rules"] = [TradingRule.from_dict(r) for r in d.get("entry_rules", [])]
        d["exit_rule"] = ExitRule(**d.get("exit_rule", {}))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "StrategyDNA":
        return cls.from_dict(json.loads(s))

    def fingerprint(self) -> str:
        """Identidade canonica de tudo que pode alterar sinal ou execucao.

        O hash antigo ignorava signal_type, regimes, alavancagem, horizonte e
        trailing stop; DNAs diferentes podiam compartilhar um julgamento.
        """
        dna_repr = {
            "schema": "strategy-dna-v2",
            "rules": sorted(
                [r.to_dict() for r in self.entry_rules],
                key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
            ),
            "rule_type": self.rule_type.value if hasattr(self.rule_type, "value") else str(self.rule_type),
            "signal_type": self.signal_type.value if hasattr(self.signal_type, "value") else str(self.signal_type),
            "target_assets": list(self.target_assets),
            "allowed_regimes": sorted(self.allowed_regimes),
            "exit_rule": asdict(self.exit_rule) if self.exit_rule else None,
            "position_size": self.position_size,
            "max_leverage": self.max_leverage,
            "rebalance_frequency": self.rebalance_frequency,
        }
        fp_str = json.dumps(dna_repr, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(fp_str.encode()).hexdigest()[:16]

    @property
    def complexity(self) -> int:
        return len(self.entry_rules)

    def clone(self) -> "StrategyDNA":
        """Cópia profunda da estratégia."""
        d = copy.deepcopy(self.to_dict())
        d["strategy_id"] = str(uuid.uuid4())[:12]
        d["parent_ids"] = [self.strategy_id]
        d["generation"] = self.generation + 1
        d["status"] = "pending"
        d["fitness"] = 0.0
        d["oos_sharpe"] = 0.0
        return StrategyDNA.from_dict(d)


# ─── Strategy Factory ─────────────────────────────────────────────────────────

class StrategyFactory:
    """Cria estratégias aleatórias válidas para a população inicial."""

    OPERATORS = [">", "<", ">|<", "rolling_z", "percentile_rank"]
    # Pesos: favorecer operadores invariantes à escala (rolling_z, percentile_rank)
    OPERATOR_WEIGHTS = [0.15, 0.15, 0.20, 0.25, 0.25]

    MOMENTUM_FEATURES = ["mom_5", "mom_21", "mom_63", "rsi_14", "macd_hist", "stoch_k"]
    REVERT_FEATURES   = ["z_score_20", "z_score_5", "bb_pct_20", "autocorr_1"]
    VOL_FEATURES      = ["vol_20", "vol_5", "vol_regime", "atr_pct_14", "bb_width_20"]
    VOLUME_FEATURES   = ["vol_ratio_ma_20", "obv_trend", "vwap_dist"]
    TREND_FEATURES    = ["trend_5_20", "trend_20_50", "price_to_sma_50", "golden_cross"]

    FEATURE_POOLS = {
        RuleType.MOMENTUM:    MOMENTUM_FEATURES,
        RuleType.MEAN_REVERT: REVERT_FEATURES,
        RuleType.BREAKOUT:    TREND_FEATURES,
        RuleType.VOLATILITY:  VOL_FEATURES,
        RuleType.VOLUME:      VOLUME_FEATURES,
        RuleType.MULTI_FACTOR: (
            MOMENTUM_FEATURES + REVERT_FEATURES + TREND_FEATURES
        ),
    }

    # Ranges de threshold calibrados ao range real de cada feature
    FEATURE_THRESHOLD_RANGES = {
        "mom_5":           (-0.05,  0.05),
        "mom_21":          (-0.08,  0.08),
        "mom_63":          (-0.15,  0.15),
        "rsi_14":          (25.0,   75.0),
        "macd_hist":       (-0.002, 0.002),
        "stoch_k":         (20.0,   80.0),
        "z_score_5":       (-2.0,   2.0),
        "z_score_20":      (-2.0,   2.0),
        "bb_pct_20":       (0.1,    0.9),
        "autocorr_1":      (-0.3,   0.3),
        "vol_20":          (0.10,   0.40),
        "vol_5":           (0.10,   0.60),
        "vol_regime":      (0.5,    2.0),
        "atr_pct_14":      (0.005,  0.025),
        "bb_width_20":     (0.01,   0.08),
        "vol_ratio_ma_20": (0.5,    2.0),
        "obv_trend":       (-0.3,   0.3),
        "vwap_dist":       (-0.015, 0.015),
        "trend_5_20":      (-0.02,  0.02),
        "trend_20_50":     (-0.05,  0.05),
        "price_to_sma_50": (-0.10,  0.10),
        "golden_cross":    (0.0,    1.0),
    }
    DEFAULT_THRESHOLD_RANGE = (-1.0, 1.0)

    @classmethod
    def random(
        cls,
        available_assets: Optional[List[str]] = None,
        rng: Optional[np.random.Generator] = None,
    ) -> StrategyDNA:
        """Gera uma estratégia aleatória válida."""
        if rng is None:
            rng = np.random.default_rng()

        rule_type = rng.choice(list(RuleType))
        signal_type = rng.choice(list(SignalType))

        # 1-4 regras de entrada
        n_rules = int(rng.integers(1, 5))
        feature_pool = cls.FEATURE_POOLS.get(rule_type, cls.MOMENTUM_FEATURES)
        n_rules = min(n_rules, len(feature_pool))
        features = rng.choice(feature_pool, size=n_rules, replace=False).tolist()

        entry_rules = []
        for feat in features:
            op = rng.choice(cls.OPERATORS, p=cls.OPERATOR_WEIGHTS)
            lo, hi = cls.FEATURE_THRESHOLD_RANGES.get(feat, cls.DEFAULT_THRESHOLD_RANGE)
            threshold = float(rng.uniform(lo, hi))
            threshold2 = float(threshold + abs(rng.uniform((hi - lo) * 0.1, (hi - lo) * 0.5)))
            weight = float(rng.uniform(0.5, 2.0))
            entry_rules.append(TradingRule(
                rule_type=rule_type,
                feature=feat,
                operator=op,
                threshold=threshold,
                threshold2=threshold2,
                lookback=int(rng.choice([5, 10, 20, 50])),
                weight=weight,
            ))

        exit_rule = ExitRule(
            stop_loss_pct=float(rng.uniform(0.01, 0.05)),
            take_profit_pct=float(rng.uniform(0.02, 0.15)),
            max_holding_days=int(rng.integers(3, 60)),
            trailing_stop_pct=float(rng.choice([0.0, 0.0, 0.01, 0.02])),
        )

        assets = available_assets or ["SPY", "QQQ"]
        # O backtest atual avalia um unico ativo por DNA. Ate existir um
        # backtest multi-asset verdadeiro, gerar varios ativos criaria uma
        # promessa que a pesquisa nao mede e a execucao replicaria cegamente.
        target_assets = [str(rng.choice(assets))]

        return StrategyDNA(
            name=f"{rule_type.value}_{str(uuid.uuid4())[:6]}",
            rule_type=rule_type,
            signal_type=signal_type,
            target_assets=target_assets,
            entry_rules=entry_rules,
            exit_rule=exit_rule,
            position_size=float(rng.uniform(0.02, 0.10)),
            max_leverage=float(rng.choice([1.0, 1.0, 1.5, 2.0])),
        )
