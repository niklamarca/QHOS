"""
QUANT HPC OS — Portfolio Engine
Construção e rebalanceamento do portfólio final.
Combina candidatos aprovados internamente e autorizados para PAPER usando HRP.
"""
from __future__ import annotations
import asyncio
import time
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from data.storage import StorageEngine
from evolution.strategy_dna import StrategyDNA
from .hrp import HierarchicalRiskParity

log = get_logger("PortfolioEngine")


class PortfolioEngine:
    """
    Engine local de construção de portfólio paper.

    Responsabilidades:
    - Seleciona as melhores estratégias validadas
    - Calcula pesos via HRP (Hierarchical Risk Parity)
    - Ajusta dinamicamente por regime de mercado
    - Minimiza correlação entre estratégias selecionadas
    - Publica pesos finais para o Execution Engine
    - Mantém histórico de NAV e métricas do portfólio
    """

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self.bus = event_bus
        self.hrp = HierarchicalRiskParity(
            min_weight=config.portfolio_min_strategy_weight,
            max_weight=0.30,
        )

        self._active_strategies: List[StrategyDNA] = []
        self._weights: Dict[str, float] = {}           # strategy_id → weight
        self._positions: Dict[str, float] = {}         # asset → weight
        self._nav: float = 100_000.0      # capital inicial padrão do paper broker
        self._peak_nav: float = 100_000.0
        self._nav_history: List[Tuple[float, float]] = []  # (timestamp, nav)
        self._current_regime: str = "mean_reversion"
        self._last_rebalance: float = 0

        # Risk engine (injetado)
        self.risk_engine = None

    def inject_dependencies(self, risk_engine, regime_model=None):
        self.risk_engine = risk_engine
        self._regime_model = regime_model

    # ─── Strategy Selection ──────────────────────────────────────────

    def select_strategies(
        self,
        candidates: List[StrategyDNA],
        max_n: int = None,
        current_regime: Optional[str] = None,
    ) -> List[StrategyDNA]:
        """
        Seleciona as melhores estratégias para o portfólio.
        Filtra por: validação, regime compatível, correlação.
        """
        max_n = max_n or config.portfolio_max_strategies
        regime = current_regime or self._current_regime

        # Filtra validadas
        validated = [
            s for s in candidates
            if s.status == "validated"
            and s.oos_sharpe >= config.validation_min_oos_sharpe
        ]

        if not validated:
            log.warning("Nenhuma estratégia validada disponível")
            return []

        # Filtra por regime compatível
        regime_compatible = [
            s for s in validated
            if regime in s.allowed_regimes or not s.allowed_regimes
        ]

        if not regime_compatible:
            regime_compatible = validated  # fallback: usa todas validadas

        # Ordena por fitness
        regime_compatible.sort(key=lambda s: s.fitness, reverse=True)

        # Seleciona top N com diversidade (reduz correlação)
        selected = self._select_diverse(regime_compatible, max_n)
        log.info(
            f"Portfolio: {len(selected)}/{len(validated)} estratégias selecionadas "
            f"(regime={regime})"
        )
        return selected

    def _select_diverse(
        self, strategies: List[StrategyDNA], max_n: int
    ) -> List[StrategyDNA]:
        """
        Greedy selection para maximizar diversidade.
        Evita estratégias com tipos de regras muito similares.
        """
        selected = [strategies[0]]  # Melhor sempre incluída

        rule_type_counts: Dict[str, int] = {}

        for s in strategies[1:]:
            if len(selected) >= max_n:
                break

            # Limita estratégias do mesmo tipo de regra
            rt = s.rule_type.value
            if rule_type_counts.get(rt, 0) >= max(2, max_n // 4):
                continue

            selected.append(s)
            rule_type_counts[rt] = rule_type_counts.get(rt, 0) + 1

        return selected

    # ─── Weight Calculation ──────────────────────────────────────────

    async def rebalance(self) -> Dict[str, float]:
        """Rebalanceia o portfólio com pesos HRP atualizados."""
        log.info("Iniciando rebalanceamento do portfólio...")
        loop = asyncio.get_running_loop()

        # Carrega estratégias validadas — run_in_executor para não bloquear event loop
        strats_df = await loop.run_in_executor(
            None,
            lambda: self.storage.get_validated_strategies(
                min_sharpe=config.validation_min_oos_sharpe,
                limit=100,   # top-100 por fitness — HRP só precisa de ~20
            )
        )
        # ``aprovada nos testes internos`` é pesquisa, não autorização de
        # capital. Só estratégias promovidas explicitamente após shadow/forward
        # entram no portfolio.
        internal_pass_count = len(strats_df)
        governance_available = True
        _promotion_error: Optional[Exception] = None
        try:
            from governance.promotion import promotion
            approved_ids = promotion.paper_approved_ids()
            if not strats_df.empty:
                strats_df = strats_df[strats_df["strategy_id"].isin(approved_ids)].copy()
            log.info(f"Promoção: {internal_pass_count} aprovadas nos testes internos, "
                     f"{len(strats_df)} autorizadas para paper")
        except Exception as exc:
            # Falhar fechado: uma indisponibilidade de governança não vira
            # promoção automática.
            governance_available = False
            _promotion_error = exc
            strats_df = strats_df.iloc[0:0].copy()

        if strats_df.empty:
            # Três causas distintas compartilhavam uma única mensagem WARNING,
            # e foi por isso que a falha de governança de 18/08 23:27 passou
            # despercebida: ela se parecia com o estado normal de pesquisa.
            if not governance_available:
                log.error(
                    "Portfolio vazio por INDISPONIBILIDADE DE GOVERNANÇA "
                    f"({type(_promotion_error).__name__}: {_promotion_error}). "
                    "Nenhuma promoção foi avaliada; o portfólio anterior foi "
                    "mantido por precaução, não por decisão."
                )
            elif internal_pass_count == 0:
                try:
                    _epoch = self.storage._scientific_epoch(fresh=False)[:48]
                except Exception:
                    _epoch = "indisponível"
                log.warning(
                    "Zero estratégias aprovadas nos testes internos na época "
                    f"corrente ({_epoch}...). Se este estado persistir por mais "
                    "de uma época de dados, suspeite do pipeline de pesquisa, "
                    "não do mercado."
                )
            else:
                log.info(
                    f"{internal_pass_count} aprovadas nos testes internos, "
                    "0 autorizadas para paper — portfólio mantido. Estado "
                    "esperado enquanto nenhuma candidata completou o forward "
                    "pré-registrado."
                )
            return self._weights

        # Obtém regime atual — também via executor
        try:
            regime_df = await loop.run_in_executor(None, self.storage.load_regime)
            if regime_df is not None and not regime_df.empty:
                self._current_regime = str(regime_df.iloc[-1].get("regime", "mean_reversion"))
        except Exception as _e:
            log.debug(f"portfolio/constructor.py L154: falha silenciosa suprimida: {type(_e).__name__}: {_e}")

        # Reconstrói objetos StrategyDNA
        candidates = []
        for _, row in strats_df.iterrows():
            try:
                import json
                meta = json.loads(row.get("metadata", "{}"))
                s = StrategyDNA.from_dict(meta)
                candidates.append(s)
            except Exception as _e:
                log.debug(f"portfolio/constructor.py L165: falha silenciosa suprimida: {type(_e).__name__}: {_e}")

        if not candidates:
            return self._weights

        # Seleciona estratégias para o portfólio
        selected = self.select_strategies(candidates, current_regime=self._current_regime)

        if not selected:
            return self._weights

        self._active_strategies = selected

        # Cria matriz de retornos sintética para HRP
        returns_matrix = self._build_returns_matrix(selected)

        if returns_matrix.empty:
            # Fallback: equal weight
            n = len(selected)
            self._weights = {s.strategy_id: 1.0 / n for s in selected}
        else:
            # HRP com ajuste por regime
            _regime_snap = self._current_regime
            _rm_snap = returns_matrix
            self._weights = await loop.run_in_executor(
                None,
                lambda: self.hrp.fit_with_regime(_rm_snap, regime=_regime_snap),
            )

        self._last_rebalance = time.time()

        # Translada pesos de estratégia → pesos de ativo (para execução paper)
        asset_weights: Dict[str, float] = {}
        for s in self._active_strategies:
            strat_w = self._weights.get(s.strategy_id, 0.0)
            n_assets = len(s.target_assets)
            if n_assets == 0 or strat_w == 0.0:
                continue
            per_asset = strat_w / n_assets
            for asset in s.target_assets:
                asset_weights[asset] = asset_weights.get(asset, 0.0) + per_asset
        _total_w = sum(asset_weights.values())
        if _total_w > 0:
            asset_weights = {k: v / _total_w for k, v in asset_weights.items()}
        # Cap por ATIVO em max_position_size com water-filling: capa e redistribui
        # o excedente para os demais ativos. Sem isso, normalizar-para-1 re-
        # concentra (ex: 62.8% num ativo) e dispara o breach toda hora. Se tudo
        # capar (poucos ativos), o excedente vira caixa (soma < 1).
        _mps = getattr(config, "max_position_size", 0.5)
        for _ in range(10):
            _over = {k: v for k, v in asset_weights.items() if v > _mps + 1e-9}
            if not _over:
                break
            _excess = sum(v - _mps for v in _over.values())
            for k in _over:
                asset_weights[k] = _mps
            _under = {k: v for k, v in asset_weights.items() if v < _mps - 1e-9}
            _tot_under = sum(_under.values())
            if _tot_under <= 1e-12:
                break  # nada para redistribuir -> excedente vira caixa
            for k in _under:
                asset_weights[k] += _excess * (_under[k] / _tot_under)
        self._asset_weights = asset_weights

        # Atualiza posicoes no risk engine com pesos por ATIVO (nao por estrategia).
        # O risk engine indexa _portfolio_positions por ATIVO (concentracao,
        # pre_trade_check). Antes recebia self._weights (por strategy_id), entao
        # a checagem de concentracao/exposicao lia chaves que nunca batiam.
        if self.risk_engine:
            self.risk_engine.update_positions(asset_weights)

        await self.bus.publish(Event(
            type=EventType.WEIGHTS_REBALANCED,
            source="portfolio_engine",
            payload={
                "weights": self._weights,
                "asset_weights": asset_weights,
                "regime": self._current_regime,
                "n_strategies": len(selected),
                "timestamp": time.time(),
            }
        ))

        log.info(
            f"Rebalanceamento completo: {len(self._weights)} estratégias, "
            f"regime={self._current_regime}"
        )
        return self._weights

    def _build_returns_matrix(self, strategies: List[StrategyDNA]) -> pd.DataFrame:
        """
        Cria matriz de retornos REAIS para o HRP.

        Roda o backtest de cada estrategia na sua serie de precos e usa os
        retornos diarios do equity curve, alinhados por data. Isso e o que
        permite ao HRP enxergar a correlacao VERDADEIRA entre estrategias
        (ex: 13 estrategias no mesmo ticker -> correlacao ~1 -> HRP reduz o
        cluster). A versao antiga usava np.random.normal independente, dando
        correlacao ~0 ficticia e diversificacao de fachada.

        Respeita o embargo: usa apenas a janela de pesquisa (holdout intocado).
        """
        from backtest.engine import BacktestEngine
        from core.embargo import split_embargo

        engine = BacktestEngine()
        returns_data: Dict[str, pd.Series] = {}
        for s in strategies:
            feats = None
            prices = None
            for asset in (list(getattr(s, "target_assets", []) or []) + ["SPY"]):
                df = self.storage.load_features(asset)
                if df is not None and len(df) >= 150 and "close" in df.columns:
                    feats = df
                    prices = df["close"]
                    break
            if feats is None:
                continue
            try:
                (rf, rp), _audit = split_embargo(feats, prices)
                res = engine.run(s, rf, rp)
            except Exception:
                continue
            eq = res.equity_curve
            if eq is None or len(eq) < 30:
                continue
            r = eq.pct_change().dropna()
            if len(r) >= 30 and float(r.std()) > 0:
                returns_data[s.strategy_id] = r

        if not returns_data:
            return pd.DataFrame()

        # Alinhamento por data (union); o HRP faz dropna -> interseccao
        return pd.DataFrame(returns_data)

    # ─── NAV Tracking ────────────────────────────────────────────────

    def update_nav(self, nav: float):
        """Atualiza NAV do portfólio."""
        self._nav = nav
        if nav > self._peak_nav:
            self._peak_nav = nav
        self._nav_history.append((time.time(), nav))
        if len(self._nav_history) > 252 * 10:
            self._nav_history = self._nav_history[-252:]

        if self.risk_engine:
            self.risk_engine.update_nav(nav)

    def get_metrics(self) -> Dict:
        """Métricas atuais do portfólio. Sempre retorna dados válidos."""
        n = len(self._nav_history)
        # Calcula métricas quando há histórico suficiente
        if n >= 2:
            navs = np.array([x[1] for x in self._nav_history[-252:]])
            rets = np.diff(navs) / navs[:-1]
            sharpe = float(np.mean(rets) / (np.std(rets) + 1e-10) * np.sqrt(252)) if len(rets) > 0 else 0.0
            dd = float((self._nav / self._peak_nav - 1)) if self._peak_nav > 0 else 0.0
            total_ret = float(self._nav / self._nav_history[0][1] - 1) if self._nav_history else 0.0
        else:
            # Sem histórico suficiente — usa valores atuais sem cálculo de retorno
            sharpe = 0.0
            dd = 0.0
            total_ret = 0.0

        return {
            "nav": self._nav,
            "peak_nav": self._peak_nav,
            "drawdown": dd,
            "sharpe_rolling": sharpe,
            "total_return": total_ret,
            "n_strategies": len(self._active_strategies),
            "regime": self._current_regime,
            "weights": self._weights,
            "last_rebalance": self._last_rebalance,
        }

    async def start(self):
        log.info("PortfolioEngine iniciado")

    async def stop(self):
        log.info("PortfolioEngine parado")
