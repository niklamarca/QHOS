"""
QUANT HPC OS — Signal Executor
Executa sinais de candidatos autorizados no broker simulado.

Em vez de comprar uma cesta estática de ações (abordagem anterior),
este módulo roda a lógica de cada estratégia sobre dados recentes,
gera sinais de entrada/saída e executa ordens no broker.

Ciclo diário (após data refresh):
  1. Carrega features mais recentes do storage para cada ativo alvo
  2. Roda strategy.entry_rules.evaluate(features) → sinal da barra atual
  3. Verifica condições de saída (stop-loss, take-profit, max_holding_days)
  4. Agrega sinais ponderados pelo peso HRP de cada estratégia
  5. Submete ordens líquidas ao ExecutionEngine
"""
from __future__ import annotations
import asyncio
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING
from core.config import config
from core.logger import get_logger

if TYPE_CHECKING:
    from data.storage import StorageEngine
    from execution.execution_engine import ExecutionEngine
    from evolution.strategy_dna import StrategyDNA

log = get_logger("SignalExecutor")


@dataclass
class StrategyPositionState:
    """Estado de posição de uma estratégia no paper trading."""
    strategy_id: str
    current_position: float = 0.0      # -1=short, 0=flat, +1=long
    entry_price: float = 0.0
    entry_ts: float = 0.0              # timestamp de entrada
    days_in_position: int = 0
    last_signal: float = 0.0
    last_mark_price: float = 0.0        # ultimo preco marcado (feedback diario mark-to-market)


class SignalExecutor:
    """
    Executor de sinais reais para paper trading.

    Mantém estado de posição por estratégia e executa ordens
    baseadas nos sinais gerados pelas regras de entrada/saída do DNA.
    """

    def __init__(self, storage: "StorageEngine", execution_engine: "ExecutionEngine",
                 track_record=None):
        self._storage = storage
        self._execution = execution_engine
        # Track-record forward (auto-instancia => funciona sem tocar main.py)
        if track_record is None:
            try:
                from core.track_record import TrackRecord
                track_record = TrackRecord()
            except Exception as _tr_e:
                log.warning(f"TrackRecord indisponivel: {_tr_e}")
                track_record = None
        self._track_record = track_record

        # Estado de posição por estratégia
        self._positions: Dict[str, StrategyPositionState] = {}

        # Portfolio atual (atualizado pelo portfolio engine)
        self._active_strategies: List["StrategyDNA"] = []
        self._strategy_weights: Dict[str, float] = {}   # strategy_id → weight

        self._last_cycle: float = 0.0
        self._cycle_count: int = 0

    # ─── Portfolio Update ────────────────────────────────────────────

    def update_portfolio(
        self,
        strategies: List["StrategyDNA"],
        weights: Dict[str, float],
    ):
        """
        Atualiza o portfólio ativo com novas estratégias e pesos HRP.
        Chamado pelo orchestrator após cada rebalanceamento.
        """
        self._active_strategies = strategies
        self._strategy_weights = weights

        # Remove estados de estratégias que saíram do portfólio
        active_ids = {s.strategy_id for s in strategies}
        stale = [sid for sid in self._positions if sid not in active_ids]
        for sid in stale:
            del self._positions[sid]

        log.info(
            f"Portfolio atualizado: {len(strategies)} estratégias, "
            f"{len(self._strategy_weights)} pesos"
        )

    # ─── Signal Generation ───────────────────────────────────────────

    def _get_current_position_signal(
        self,
        strategy: "StrategyDNA",
        features: pd.DataFrame,
        lookback: int = 756,
    ) -> float:
        """
        Determina a posição atual da estratégia simulando os últimos `lookback` bars.

        Usa todo o histórico disponível (até 756 bars = ~3 anos) para obter o estado
        correto de posição. Lookback curto (60) causava falso-flat: a estratégia pode
        ter entrado e saído várias vezes dentro da janela e estar flat no bar final,
        enquanto com mais histórico o estado real é "em posição".
        """
        if not strategy.entry_rules or features.empty or len(features) < 20:
            return 0.0

        # Usa histórico completo (até `lookback` bars)
        window = features.iloc[-lookback:] if len(features) >= lookback else features

        if "close" not in window.columns:
            return 0.0

        prices_arr = window["close"].astype(float).values

        # Gera série de sinais combinados para toda a janela
        raw_sigs = []
        rule_errors = []
        for rule in strategy.entry_rules:
            try:
                sig = rule.evaluate(window)
                if isinstance(sig, pd.Series) and len(sig) > 0:
                    raw_sigs.append(sig.reindex(window.index).fillna(0))
            except Exception as _re:
                rule_errors.append(f"{type(rule).__name__}:{_re}")

        if rule_errors:
            log.warning(
                f"{strategy.name}: {len(rule_errors)} regras falharam: {rule_errors[:3]}"
            )

        if not raw_sigs:
            log.warning(
                f"{strategy.name}: nenhum sinal gerado "
                f"(cols disponíveis: {list(window.columns[:10])})"
            )
            return 0.0

        combined = pd.concat(raw_sigs, axis=1).fillna(0).sum(axis=1)
        sig_type = (
            strategy.signal_type.value
            if hasattr(strategy.signal_type, "value")
            else str(strategy.signal_type)
        )

        if sig_type == "long_only":
            signal_series = (combined > 0).astype(float).values
        elif sig_type == "long_short":
            signal_series = np.sign(combined).values
        else:
            max_val = combined.abs().max() + 1e-8
            signal_series = combined.clip(-max_val, max_val).div(max_val).values

        # Mini-simulação: percorre a janela para determinar a posição ao final
        exit_rule = strategy.exit_rule
        current_pos = 0.0
        entry_price = 0.0
        entry_day = 0

        for i in range(1, len(prices_arr)):
            target = max(0.0, float(signal_series[i - 1]))  # long-only

            # Aplica stop-loss / take-profit / max_holding_days
            if current_pos != 0.0 and entry_price > 0.0:
                price_chg = (prices_arr[i] / entry_price - 1.0) * np.sign(current_pos)
                days_held = i - entry_day
                if (price_chg <= -exit_rule.stop_loss_pct
                        or price_chg >= exit_rule.take_profit_pct
                        or days_held >= exit_rule.max_holding_days):
                    current_pos = 0.0
                    entry_price = 0.0

            # Transição de posição
            if abs(target - current_pos) > 0.01:
                current_pos = target
                if target != 0.0:
                    entry_price = prices_arr[i]
                    entry_day = i

        return float(current_pos)

    def _check_exit_conditions(
        self,
        state: StrategyPositionState,
        strategy: "StrategyDNA",
        current_price: float,
    ) -> bool:
        """Verifica se a posição deve ser encerrada por stop/target/tempo."""
        if state.current_position == 0 or state.entry_price <= 0:
            return False

        price_change = (
            (current_price / state.entry_price - 1.0)
            * np.sign(state.current_position)
        )
        exit_rule = strategy.exit_rule

        if price_change <= -exit_rule.stop_loss_pct:
            log.debug(f"{strategy.name}: stop-loss atingido ({price_change:.2%})")
            return True
        if price_change >= exit_rule.take_profit_pct:
            log.debug(f"{strategy.name}: take-profit atingido ({price_change:.2%})")
            return True
        if state.days_in_position >= exit_rule.max_holding_days:
            log.debug(f"{strategy.name}: max holding days ({state.days_in_position}d)")
            return True

        return False

    # ─── Main Cycle ──────────────────────────────────────────────────

    async def run_daily_cycle(self, current_nav: float) -> Dict[str, float]:
        """
        Ciclo principal: gera sinais e executa ordens.
        Deve ser chamado após cada data refresh.
        Retorna dicionário de pesos alvo por ativo.
        """
        # Track-record forward: registra o NAV do dia ANTES de operar, para uma
        # equity curve continua (mesmo em dias sem ordens). Fonte limpa e
        # separada do backtest, consumida pelo consolidation_scorecard.py.
        if self._track_record is not None:
            try:
                _bk = getattr(self._execution, "_default_broker", None)
                # SPY para o benchmark: le o close mais recente do storage SEMPRE
                # (mesmo sem posicao em SPY). Antes vinha de broker.prices["SPY"],
                # que so existia se SPY fosse negociado -> spy_close ficava NaN e o
                # benchmark cegava. Fallback: preco do broker, se houver.
                _spy = (getattr(_bk, "prices", {}) or {}).get("SPY")
                try:
                    _spy_df = self._storage.load_features("SPY")
                    if _spy_df is not None and not _spy_df.empty and "close" in _spy_df.columns:
                        _v = float(_spy_df["close"].iloc[-1])
                        if _v > 0:
                            _spy = _v
                except Exception as _e:
                    log.debug(f"execution/signal_executor.py L249: falha silenciosa suprimida: {type(_e).__name__}: {_e}")
                self._track_record.log_nav(
                    nav=current_nav,
                    cash=getattr(_bk, "cash", None),
                    n_positions=len(getattr(_bk, "positions", {}) or {}),
                    spy_close=_spy,
                    n_fills=int(getattr(_bk, "_fills_cum", 0)) or len(getattr(_bk, "fills", []) or []),
                )
            except Exception as _tr_err:
                log.warning(f"track_record: falha ao registrar NAV: {_tr_err}")

        if not self._active_strategies:
            log.debug("SignalExecutor: sem estratégias ativas — pulando ciclo")
            return {}

        loop = asyncio.get_running_loop()
        self._cycle_count += 1
        t0 = time.time()

        # Acumula pesos alvo por ativo (agrega múltiplas estratégias no mesmo ativo)
        asset_target: Dict[str, float] = {}
        executed_count = 0
        flat_count = 0

        error_count = 0
        skip_no_features = 0
        for strategy in self._active_strategies:
            try:
                strat_weight = self._strategy_weights.get(strategy.strategy_id, 0.0)
                if strat_weight <= 0:
                    continue

                primary_asset = strategy.target_assets[0] if strategy.target_assets else None
                if not primary_asset:
                    continue

                # Carrega features (via executor para não bloquear event loop)
                try:
                    features = await loop.run_in_executor(
                        None, lambda a=primary_asset: self._storage.load_features(a)
                    )
                except Exception as e:
                    log.warning(f"Erro ao carregar features de {primary_asset}: {e}")
                    skip_no_features += 1
                    continue

                if features is None or len(features) < 50 or "close" not in features.columns:
                    skip_no_features += 1
                    continue

                current_price = float(features["close"].iloc[-1])

                # Estado de posição atual
                state = self._positions.get(
                    strategy.strategy_id,
                    StrategyPositionState(strategy_id=strategy.strategy_id)
                )

                # Mini-simulação dos últimos 60 bars → posição atual
                # (cobre restart: estratégia pode estar "em posição" há dias)
                signal = self._get_current_position_signal(strategy, features)
                log.debug(
                    f"  {strategy.name} [{strategy.rule_type.value if hasattr(strategy.rule_type,'value') else strategy.rule_type}]"
                    f" → signal={signal:.1f} (w={strat_weight:.3f})"
                )

                # Atualiza estado de posição
                prev_pos = state.current_position

                # FEEDBACK DIARIO (mark-to-market): registra o retorno do periodo
                # sobre a posicao MANTIDA, sempre que o preco mudou — nao so quando
                # o round-trip fecha. Assim o laco de aprendizado recebe dado
                # CONTINUO e funciona mesmo com a banda cortando o giro (antes
                # ficava faminto: sem fechar posicao, nunca acumulava os >=10
                # registros pra ligar). Substitui a marcacao so-no-exit.
                if prev_pos != 0.0 and state.last_mark_price > 0.0:
                    if current_price != state.last_mark_price:
                        mark_ret = (current_price / state.last_mark_price - 1.0) * float(np.sign(prev_pos))
                        # CUSTO DE GIRO: sem isto o fitness live e CEGO a rotatividade —
                        # uma estrategia que vira posicao todo dia parecia tao boa quanto
                        # uma que segura. Cobra os mesmos bps do backtest.
                        try:
                            _dpos = abs(float(signal) - float(prev_pos))
                            if _dpos > 0.01:
                                _bps = (config.backtest_slippage_bps + config.backtest_commission_bps) / 10000.0
                                mark_ret -= _bps * _dpos
                        except Exception as _e:
                            log.debug(f"execution/signal_executor.py L336: falha silenciosa suprimida: {type(_e).__name__}: {_e}")
                        try:
                            from core.live_performance import get_live_performance
                            get_live_performance().record(
                                strategy.strategy_id, mark_ret,
                                ctx={"asset": str(primary_asset),
                                     "p_de": float(state.last_mark_price),
                                     "p_para": float(current_price),
                                     "pos": float(prev_pos), "sinal": float(signal)})
                            # Learning Engine: mesma observacao, agora COM CONTEXTO

                            try:

                                from core.learning_engine import get_learning_engine

                                get_learning_engine().observe(strategy.strategy_id if hasattr(strategy,"strategy_id") else "unknown",

                                                              float(mark_ret), regime=str(getattr(self, "_last_regime", "unknown")))

                            except Exception as _e:

                                log.debug(f"execution/signal_executor.py L356: falha silenciosa suprimida: {type(_e).__name__}: {_e}")
                        except Exception as _lp:
                            log.debug(f"live mark record falhou: {_lp}")
                    state.last_mark_price = current_price  # atualiza a marca (posicao mantida)

                state.current_position = signal
                state.last_signal = signal

                if signal != 0.0 and prev_pos == 0.0:
                    state.entry_price = current_price
                    state.last_mark_price = current_price   # inicia marcacao na entrada
                    state.entry_ts = time.time()
                    state.days_in_position = 0
                    log.info(f"ENTER {strategy.name} @ ${current_price:.2f}")
                elif signal == 0.0 and prev_pos != 0.0:
                    state.entry_price = 0.0
                    state.last_mark_price = 0.0
                    state.days_in_position = 0
                    log.info(f"EXIT {strategy.name}")
                    flat_count += 1
                elif signal != 0.0 and prev_pos != 0.0:
                    state.days_in_position += 1

                self._positions[strategy.strategy_id] = state

                # Alocação alvo: peso HRP × sinal × position_size da estratégia
                target_alloc = strat_weight * signal
                if target_alloc == 0:
                    continue

                # Distribui igualmente pelos ativos alvo da estratégia
                n_assets = len(strategy.target_assets)
                per_asset = target_alloc / n_assets
                for asset in strategy.target_assets:
                    asset_target[asset] = asset_target.get(asset, 0.0) + per_asset

                executed_count += 1

            except Exception as _strat_err:
                error_count += 1
                log.warning(f"Estratégia {getattr(strategy,'name','?')} pulada: {_strat_err}")

        if not asset_target:
            # Ainda chama o rebalanceador: alvo vazio significa fechar qualquer
            # posicao herdada. Retornar aqui deixava exposicao orfa para sempre.
            log.info(f"SignalExecutor ciclo {self._cycle_count}: todos flat — zerando exposicao residual | skip={skip_no_features} erros={error_count}")

        # Cap por ATIVO em max_position_size (ex: 50%): sem isso, com 1 estrategia
        # o alvo seria 100% num ativo e o risk gate rejeitaria a ordem inteira
        # (sistema nao operaria). Com o cap, opera 50% e segura caixa.
        # Cap com FOLGA (97% do limite) para absorver drift de preco/NAV entre
        # o calculo do alvo e o gate pre-trade. Sem a folga, capar em exatamente
        # max_position_size faz o gate rejeitar por 0.01% (ex: 50.01% > 50.0%).
        _mps = getattr(config, "max_position_size", 0.5) * 0.97
        asset_target = {k: max(-_mps, min(v, _mps)) for k, v in asset_target.items()}

        # Normaliza pela exposicao bruta; preserva o sinal long/short.
        gross = sum(abs(v) for v in asset_target.values())
        if gross > 1.0:
            asset_target = {k: v / gross for k, v in asset_target.items()}

        # Alimenta preços reais ao broker
        broker = self._execution._default_broker
        for asset in list(asset_target.keys()):
            try:
                features = await loop.run_in_executor(
                    None, lambda a=asset: self._storage.load_features(a)
                )
                if features is not None and "close" in features.columns:
                    price = float(features["close"].iloc[-1])
                    if price > 0 and hasattr(broker, "update_price"):
                        broker.update_price(asset, price)
            except Exception as _e:
                log.debug(f"execution/signal_executor.py L429: falha silenciosa suprimida: {type(_e).__name__}: {_e}")

        # Executa rebalanceamento
        try:
            await self._execution.rebalance_to_weights(asset_target, current_nav)
        except Exception as e:
            log.error(f"SignalExecutor: erro na execução: {e}")
            return asset_target

        elapsed = time.time() - t0
        self._last_cycle = time.time()
        log.info(
            f"SignalExecutor ciclo {self._cycle_count}: "
            f"{executed_count} com sinal, {flat_count} saídas, {skip_no_features} sem features, {error_count} erros | "
            f"{len(asset_target)} ordens enviadas (NAV=${current_nav:,.0f}, {elapsed:.1f}s)"
        )
        return asset_target

    def get_status(self) -> dict:
        """Status para dashboard."""
        long_count = sum(1 for s in self._positions.values() if s.current_position > 0)
        flat_count = sum(1 for s in self._positions.values() if s.current_position == 0)
        return {
            "active_strategies": len(self._active_strategies),
            "long_positions": long_count,
            "flat_positions": flat_count,
            "cycle_count": self._cycle_count,
            "last_cycle_ts": self._last_cycle,
        }
