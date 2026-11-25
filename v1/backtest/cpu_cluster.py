"""
QUANT HPC OS — CPU Backtest Cluster
32 threads paralelas para backtest massivo de estratégias.
Cada thread é independente, sem compartilhamento de estado.
"""
from __future__ import annotations
import asyncio
import time
import numpy as np
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Dict, List, Optional
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from data.storage import StorageEngine
from data.features import FeatureEngine
from evolution.strategy_dna import StrategyDNA
from .engine import BacktestEngine, BacktestResult

log = get_logger("BacktestCluster")


def _run_single_backtest(args: tuple) -> Dict:
    """
    Função standalone para execução em processo separado.
    Executa backtest + Monte Carlo para uma estratégia.
    """
    strategy_dict, features_dict, prices_dict, slippage, commission = args

    try:
        strategy = StrategyDNA.from_dict(strategy_dict)
        engine = BacktestEngine(slippage_bps=slippage, commission_bps=commission)

        # Seleciona asset principal da estratégia
        target = strategy.target_assets[0] if strategy.target_assets else "SPY"

        # Usa o primeiro asset disponível
        features = None
        prices = None
        for asset in strategy.target_assets + ["SPY"]:
            if asset in features_dict and asset in prices_dict:
                _f = features_dict[asset]; _p = prices_dict[asset]
                # Sem copia quando ja vem como DataFrame/Series com indice pronto.
                # NUNCA escrever no objeto recebido: ele e COMPARTILHADO entre as
                # 32 threads do pool. A versao anterior fazia
                # `features.index = pd.to_datetime(...)` — mutacao no objeto de
                # todo mundo. Funcionava por acidente (o produtor ja converte),
                # nao por desenho. Se precisar converter, converte numa COPIA.
                if isinstance(_f, pd.DataFrame) and isinstance(_f.index, pd.DatetimeIndex):
                    features = _f
                else:
                    features = pd.DataFrame(_f).copy()
                    features.index = pd.to_datetime(features.index)
                if isinstance(_p, pd.Series) and isinstance(_p.index, pd.DatetimeIndex):
                    prices = _p
                else:
                    prices = pd.Series(_p).copy()
                    prices.index = pd.to_datetime(prices.index)
                break

        if features is None or prices is None:
            return {"strategy_id": strategy.strategy_id, "error": "no_data"}

        # Embargo: o GA NUNCA enxerga os últimos `embargo_fraction` do histórico
        # (zona de auditoria held-out). Seleção opera só na janela de pesquisa.
        embargo = getattr(config, "embargo_fraction", 0.20)
        research_end = int(len(features) * (1.0 - embargo))
        features = features.iloc[:research_end]
        prices = prices.iloc[:research_end]

        # Split IS/OOS (70/30) — dentro da janela de pesquisa (0–80%)
        n = len(features)
        split_idx = int(n * 0.7)

        is_features = features.iloc[:split_idx]
        is_prices = prices.iloc[:split_idx]
        oos_features = features.iloc[split_idx:]
        oos_prices = prices.iloc[split_idx:]

        # IS backtest
        is_result = engine.run(strategy, is_features, is_prices)

        # OOS backtest (mais importante)
        oos_result = engine.run(strategy, oos_features, oos_prices)
        oos_result.is_in_sample = False

        # Monte Carlo (amostras reduzidas para velocidade)
        mc_result = engine.run_monte_carlo(
            strategy, oos_features, oos_prices, n_trials=50
        )

        return {
            "strategy_id": strategy.strategy_id,
            "is_sharpe": is_result.sharpe,
            "oos_sharpe": oos_result.sharpe,
            "oos_sortino": oos_result.sortino,
            "oos_cagr": oos_result.cagr,
            "max_drawdown": oos_result.max_drawdown,
            "win_rate": oos_result.win_rate,
            "profit_factor": oos_result.profit_factor,
            "n_trades": oos_result.n_trades,
            "calmar": oos_result.calmar,
            "volatility": oos_result.volatility,
            "total_return": oos_result.total_return,
            "monte_carlo": mc_result,
            "stability_score": mc_result.get("stability", 0.0),
            "sharpe_p5": mc_result.get("sharpe_p5", 0.0),
        }

    except Exception as e:
        return {"strategy_id": strategy_dict.get("strategy_id", "?"), "error": str(e)}


class BacktestCluster:
    """
    Cluster de backtest paralelo com ProcessPoolExecutor.
    Explora os 32 cores da CPU para maximizar throughput.
    """

    def __init__(self, storage: StorageEngine, feature_engine: FeatureEngine):
        self.storage = storage
        self.feature_engine = feature_engine
        self.bus = event_bus
        self._n_workers = min(config.max_cpu_threads, 32)
        self._executor: Optional[ThreadPoolExecutor] = None
        self._process_pool: Optional[ProcessPoolExecutor] = None

    async def start(self):
        """Inicializa o pool de threads."""
        self._executor = ThreadPoolExecutor(max_workers=self._n_workers)
        log.info(f"BacktestCluster iniciado: {self._n_workers} workers")

    async def stop(self):
        """Para o cluster."""
        if self._executor:
            self._executor.shutdown(wait=False)
        log.info("BacktestCluster parado")

    async def run_population(
        self,
        population: List[StrategyDNA],
        chunk_size: int = 8,
    ) -> List[StrategyDNA]:
        """
        Executa backtest para toda a população em paralelo.
        Atualiza as métricas de fitness diretamente nos objetos.
        """
        if not population:
            return population

        log.info(f"Iniciando backtest de {len(population)} estratégias...")
        t_start = time.time()

        # Pré-carrega features (uma vez, compartilhadas)
        features_dict, prices_dict = await self._load_data_for_strategies(population)

        if not features_dict:
            log.warning("Nenhum dado disponível para backtest")
            return population

        # Prepara argumentos
        args_list = [
            (
                s.to_dict(),
                features_dict,
                prices_dict,
                config.backtest_slippage_bps,
                config.backtest_commission_bps,
            )
            for s in population
        ]

        # Executa em chunks para evitar overload
        results_map: Dict[str, Dict] = {}
        loop = asyncio.get_running_loop()

        for chunk_start in range(0, len(args_list), chunk_size):
            chunk = args_list[chunk_start:chunk_start + chunk_size]

            futures = [
                loop.run_in_executor(self._executor, _run_single_backtest, args)
                for args in chunk
            ]
            # Timeout por chunk: 60s para 8 estratégias (~7.5s/estratégia)
            # Se travar: recria executor (abandona threads presas) e aborta a geração
            done_set, pending_set = await asyncio.wait(futures, timeout=60)
            if pending_set:
                n_pending = len(pending_set)
                for f in pending_set:
                    f.cancel()
                log.warning(f"Backtest chunk timeout: {n_pending} estratégias canceladas — recriando executor")
                # Recria o executor para liberar threads travadas
                old_executor = self._executor
                self._executor = ThreadPoolExecutor(max_workers=self._n_workers)
                old_executor.shutdown(wait=False)
                break  # Aborta chunks restantes — geração incompleta mas não trava
            chunk_results = [f.result() if f.done() and f.exception() is None
                             else (f.exception() if f.done() else Exception('timeout'))
                             for f in done_set]

            for r in chunk_results:
                if isinstance(r, Exception):
                    log.debug(f"Backtest falhou: {r}")
                    continue
                if isinstance(r, dict) and "error" not in r:
                    results_map[r["strategy_id"]] = r

            # Status update
            done = min(chunk_start + chunk_size, len(population))
            log.debug(f"  Backtest: {done}/{len(population)}")

        # Atualiza métricas nas estratégias
        for strategy in population:
            r = results_map.get(strategy.strategy_id)
            if r:
                strategy.is_sharpe = float(r.get("is_sharpe", 0))
                strategy.oos_sharpe = float(r.get("oos_sharpe", 0))
                strategy.max_drawdown = float(r.get("max_drawdown", 0))
                strategy.win_rate = float(r.get("win_rate", 0))
                strategy.n_trades = int(r.get("n_trades", 0))
                strategy.stability_score = float(r.get("stability_score", 0))
                strategy.regime_robustness = float(
                    r.get("monte_carlo", {}).get("pct_positive", 0)
                )

                # save_backtest movido para executor abaixo (não bloqueia o event loop)
                pass

        # Salva backtests em batch via executor — não bloqueia o event loop
        _storage_ref = self.storage
        _results_snapshot = dict(results_map)
        _strats_snapshot = [(s.strategy_id, {
            "sharpe": s.oos_sharpe,
            "sortino": results_map.get(s.strategy_id, {}).get("oos_sortino", 0),
            "max_drawdown": s.max_drawdown,
            "win_rate": s.win_rate,
            "n_trades": s.n_trades,
            "cagr": results_map.get(s.strategy_id, {}).get("oos_cagr", 0),
            "regime": "oos",
        }) for s in population if results_map.get(s.strategy_id)]

        def _batch_save():
            for sid, data in _strats_snapshot:
                try:
                    _storage_ref.save_backtest(sid, data)
                except Exception:
                    pass

        loop.run_in_executor(None, _batch_save)  # fire-and-forget, não awaita

        elapsed = time.time() - t_start
        valid = sum(1 for s in population if s.n_trades >= config.backtest_min_trades)

        log.info(
            f"Backtest completo: {valid}/{len(population)} válidos "
            f"em {elapsed:.1f}s "
            f"({len(population)/elapsed:.1f} strats/s)"
        )

        await self.bus.publish(Event(
            type=EventType.BACKTEST_COMPLETE,
            source="backtest_cluster",
            payload={
                "n_strategies": len(population),
                "n_valid": valid,
                "elapsed": elapsed,
            }
        ))

        return population

    async def run_single(
        self,
        strategy: StrategyDNA,
        detailed: bool = True,
    ) -> BacktestResult:
        """Executa backtest detalhado de uma única estratégia."""
        features_dict, prices_dict = await self._load_data_for_strategies([strategy])

        if not features_dict:
            return BacktestResult(strategy_id=strategy.strategy_id)

        loop = asyncio.get_running_loop()
        result_dict = await loop.run_in_executor(
            self._executor,
            _run_single_backtest,
            (
                strategy.to_dict(),
                features_dict,
                prices_dict,
                config.backtest_slippage_bps,
                config.backtest_commission_bps,
            ),
        )

        if "error" in result_dict:
            return BacktestResult(strategy_id=strategy.strategy_id)

        r = BacktestResult(
            strategy_id=strategy.strategy_id,
            sharpe=result_dict.get("oos_sharpe", 0),
            sortino=result_dict.get("oos_sortino", 0),
            cagr=result_dict.get("oos_cagr", 0),
            max_drawdown=result_dict.get("max_drawdown", 0),
            win_rate=result_dict.get("win_rate", 0),
            n_trades=result_dict.get("n_trades", 0),
            calmar=result_dict.get("calmar", 0),
            volatility=result_dict.get("volatility", 0),
            total_return=result_dict.get("total_return", 0),
        )
        return r

    async def _load_data_for_strategies(
        self, population: List[StrategyDNA]
    ) -> tuple:
        """Carrega features e preços para todos os assets necessários."""
        all_assets = set()
        for s in population:
            all_assets.update(s.target_assets)
        all_assets.update(["SPY"])  # benchmark sempre disponível

        features_dict = {}
        prices_dict = {}

        for asset in all_assets:
            # Carrega features do data lake
            df_feat = self.storage.load_features(asset)
            if df_feat is None:
                # Tenta gerar features
                df_feat = await self.feature_engine.compute_for_asset(asset)

            if df_feat is not None and len(df_feat) > 100:
                # DataFrame/Series DIRETO — nao .to_dict().
                # O executor e ThreadPool: threads compartilham memoria, entao
                # nao ha serializacao a fazer. Converter para dict Python custava
                # 11x mais RAM (1.25 GB contra 0.11 GB com o historico completo),
                # porque cada valor virava um objeto float com overhead de dict.
                # Medido em 2026-08-13, depois do dia em que o servidor sumiu.
                df_feat = df_feat.copy()
                df_feat.index = pd.to_datetime(df_feat.index)
                features_dict[asset] = df_feat
                if "close" in df_feat.columns:
                    prices_dict[asset] = df_feat["close"]
                else:
                    df_raw = self.storage.load_raw(asset)
                    if df_raw is not None:
                        _p = df_raw["close"].copy()
                        _p.index = pd.to_datetime(_p.index)
                        prices_dict[asset] = _p

        return features_dict, prices_dict
