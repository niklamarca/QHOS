"""
QUANT HPC OS — Data Ingestion Engine
Ingestão multi-asset: Stocks (yfinance), Crypto (ccxt/Binance), Futuros (IBKR/yfinance).
"""
from __future__ import annotations
import asyncio
import pandas as pd
import numpy as np
from typing import List, Optional, Dict
from datetime import datetime, timedelta
import time
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from .storage import StorageEngine

log = get_logger("DataIngestion")


class DataIngestionEngine:
    """
    Engine de ingestão de dados multi-asset.
    Suporta: yfinance (stocks/ETFs), ccxt (crypto), alpaca (stocks US real-time).
    Salva automaticamente em Parquet no data lake.
    """

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self.bus = event_bus
        self._last_refresh: Dict[str, float] = {}
        # Um boot pode disparar refresh pelo bootstrap e pelo orquestrador quase
        # ao mesmo tempo. Duas escritas concorrentes no mesmo parquet/manifest
        # nao sao "mais atualizacao"; sao uma corrida de I/O e de proveniencia.
        self._refresh_task: Optional[asyncio.Task] = None

    # ─── Public API ──────────────────────────────────────────────────

    async def refresh_all(self):
        """Atualiza todos os assets, coalescendo chamadas concorrentes.

        Quem chega enquanto um refresh ja esta em curso aguarda e reutiliza o
        mesmo resultado. Isso preserva um unico escritor para raw, manifests e
        eventos DATA_UPDATED sem descartar a solicitacao do chamador.
        """
        active = self._refresh_task
        if active is not None and not active.done():
            log.info("Refresh ja em andamento — solicitacao agrupada ao ciclo ativo")
            return await asyncio.shield(active)

        task = asyncio.create_task(self._refresh_all_once())
        self._refresh_task = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._refresh_task is task:
                self._refresh_task = None

    async def _refresh_all_once(self):
        """Executa um unico ciclo fisico de atualizacao de dados."""
        log.info("Iniciando refresh de dados multi-asset...")

        # Stocks/ETFs/indices: UM download em LOTE (incremental + deteccao de
        # drift de ajuste). Antes: 1 request "period=max" POR ticker a cada
        # refresh (decadas re-baixadas 4x/dia). Agora: 1-2 requests no total,
        # baixando so os dias novos.
        stock_tickers = list(config.data_assets_stocks) + list(getattr(config, "data_assets_reference", []))
        results = await asyncio.gather(
            self._ingest_stocks_batch(stock_tickers), return_exceptions=True
        )
        batch_ok = results[0] if results and not isinstance(results[0], Exception) else 0
        stock_errors = [str(r) for r in results if isinstance(r, Exception)]

        # Crypto (ccxt Binance spot) — sequencial: um cliente compartilhado
        # com rate-limit; gather paralelo só gerava timeout em exchangeInfo.
        crypto_ok = 0
        crypto_errors = []
        for asset in config.data_assets_crypto:
            try:
                await self._ingest_crypto(asset)
                crypto_ok += 1
            except Exception as exc:
                crypto_errors.append(str(exc))

        success = int(batch_ok) + crypto_ok
        errors = stock_errors + crypto_errors
        tasks_total = len(stock_tickers) + len(config.data_assets_crypto)

        log.info(f"Refresh completo: {success}/{tasks_total} assets atualizados")
        if errors:
            log.warning(f"Erros: {errors[:3]}")

        await self.bus.publish(Event(
            type=EventType.DATA_UPDATED,
            source="data_ingestion",
            payload={
                "assets_updated": success,
                "total": tasks_total,
                "timestamp": time.time(),
            }
        ))

    async def get_ohlcv(
        self,
        asset: str,
        timeframe: str = "1d",
        periods: Optional[int] = None,
        force_refresh: bool = False,
    ) -> Optional[pd.DataFrame]:
        """
        Retorna OHLCV para um asset.

        periods=None (padrão) -> HISTÓRICO COMPLETO do parquet.

        Antes o padrão era 500. Como o motor de features chamava sem passar
        `periods`, TODA a pesquisa rodava sobre ~487 barras enquanto o disco
        guardava até 13.476 (94% dos dados descartados). Com população de 500
        indivíduos x 40+ gerações, a razão candidatos:observações passava de
        8:1 — território de memorização de ruído, não de otimização. Foi a
        causa aritmética do catálogo com Sharpe 5,25 que virou expectativa
        negativa por trade.
        """
        # Check cache
        if not force_refresh:
            df = self.storage.load_raw(asset, timeframe)
            if df is not None and len(df) > 100:
                last = self._last_refresh.get(asset, 0)
                # last == 0 → sessão reiniciada, nunca baixamos nesta sessão → usa parquet
                # last > 0 e < 6h → ainda fresco → usa parquet
                # Só baixa de novo se EXPLICITAMENTE refreshado E mais de 6h atrás
                if last == 0 or time.time() - last < 21600:
                    return df if periods is None else df.tail(periods)

        # Fetch fresh
        market = self.storage._detect_market(asset)
        df = None

        _n = periods if periods is not None else 100_000   # None = tudo que o provider der
        if market == "crypto":
            df = await self._fetch_crypto(asset, timeframe, _n)
        else:
            df = await self._fetch_stock(asset, _n)

        if df is not None and len(df) > 10:
            source = str(getattr(df, "attrs", {}).get("data_source", ""))
            if not source:
                source = "ccxt.binance" if market == "crypto" else "yfinance"
            self.storage.save_raw(asset, df, timeframe, source=source)
            self._last_refresh[asset] = time.time()
            # Nunca entrega a vela diária aberta para features/evolução.
            return self.storage.load_raw(asset, timeframe)

        return None

    # ─── Batch/Incremental yfinance engine ───────────────────────────

    _OVERLAP_DAYS = 16      # ~10 pregoes: janela p/ checar drift de ajuste
    _DRIFT_TOL = 0.002      # 0.2%: acima disto no overlap = dividendo/split -> full

    async def _ingest_stocks_batch(self, tickers) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._refresh_stocks_batch_sync, list(tickers))

    def _normalize_yf(self, df, ticker: str):
        """Normaliza um frame do yfinance: tz, colunas, filtro de volume."""
        if df is None or df.empty:
            return None
        df = df.copy()
        try:
            df.index = pd.to_datetime(df.index.tz_localize(None))
        except (TypeError, AttributeError):
            df.index = pd.to_datetime(df.index)
        df.columns = [str(c).lower() for c in df.columns]
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        if "close" not in cols:
            return None
        df = df[cols].dropna(subset=["close"])
        if "volume" in df.columns and not ticker.startswith("^"):
            df = df[df["volume"] > 0]
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df if len(df) else None

    @staticmethod
    def _extract_batch(batch, ticker: str, many: bool):
        """Extrai o frame de um ticker do resultado do yf.download em lote."""
        try:
            if many:
                if ticker not in set(batch.columns.get_level_values(0)):
                    return None
                return batch[ticker].dropna(how="all")
            return batch.dropna(how="all")
        except Exception:
            return None

    def _refresh_stocks_batch_sync(self, tickers) -> int:
        """
        Refresh em LOTE com atualizacao INCREMENTAL e deteccao de drift.

        Por que o overlap-check e obrigatorio (o erro classico do append):
        com auto_adjust=True os precos ajustados MUDAM RETROATIVAMENTE a cada
        dividendo/split. Append cego de incremento cria serie inconsistente
        (passado com fator velho). Se o close no overlap divergir > _DRIFT_TOL,
        houve evento corporativo -> re-baixa o historico COMPLETO do ticker.
        """
        import yfinance as yf
        from datetime import datetime, timedelta

        ok = 0
        full, incr, existing = [], [], {}
        for t in tickers:
            try:
                cur = self.storage.load_raw(t, "1d")
            except Exception:
                cur = None
            if cur is None or len(cur) < 200:
                full.append(t)
            else:
                existing[t] = cur
                incr.append(t)

        # ---- FULL: historia completa, UMA chamada em lote ----
        if full:
            try:
                batch = yf.download(full, period="max", auto_adjust=True,
                                    group_by="ticker", threads=True, progress=False)
                many = len(full) > 1
                for t in full:
                    df = self._normalize_yf(self._extract_batch(batch, t, many), t)
                    if df is not None and len(df) > 50:
                        quality = self.storage.save_raw(t, df, "1d", source="yfinance")
                        if quality.status == "quarantined":
                            log.error(f"[DADOS] {t} recusado: {quality.reason}")
                            continue
                        self._last_refresh[t] = time.time()
                        ok += 1
                        log.info(f"[DADOS] full: {t} {len(df)} candles ({df.index.min().date()}->{df.index.max().date()})")
            except Exception as e:
                log.warning(f"[DADOS] batch FULL falhou: {type(e).__name__}: {e}")

        # ---- INCREMENTAL: so os dias novos + janela de overlap ----
        drift_full = []
        if incr:
            start = min(existing[t].index.max() for t in incr) - timedelta(days=self._OVERLAP_DAYS)
            try:
                batch = yf.download(incr, start=start.strftime("%Y-%m-%d"), auto_adjust=True,
                                    group_by="ticker", threads=True, progress=False)
                many = len(incr) > 1
                for t in incr:
                    new = self._normalize_yf(self._extract_batch(batch, t, many), t)
                    if new is None or new.empty:
                        # sem dado novo (feriado/fds/Yahoo mudo) — parquet local segue
                        self._last_refresh[t] = time.time()
                        ok += 1
                        log.debug(f"[DADOS] incremental vazio para {t}; mantido local")
                        continue
                    cur = existing[t]
                    common = cur.index.intersection(new.index)
                    if len(common) >= 2:
                        a = cur.loc[common, "close"].astype(float)
                        b = new.loc[common, "close"].astype(float)
                        drift = float((a - b).abs().div(b.abs().clip(lower=1e-9)).max())
                        if drift > self._DRIFT_TOL:
                            log.info(f"[DADOS] drift de ajuste em {t} ({drift:.2%}) -> re-download full")
                            drift_full.append(t)
                            continue
                    merged = pd.concat([cur[cur.index < new.index.min()], new])
                    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    quality = self.storage.save_raw(t, merged, "1d", source="yfinance")
                    if quality.status == "quarantined":
                        log.error(f"[DADOS] {t} recusado: {quality.reason}")
                        continue
                    self._last_refresh[t] = time.time()
                    ok += 1
            except Exception as e:
                log.warning(f"[DADOS] batch INCR falhou: {type(e).__name__}: {e}")

        # ---- re-download full dos tickers com drift (dividendo/split) ----
        if drift_full:
            try:
                batch = yf.download(drift_full, period="max", auto_adjust=True,
                                    group_by="ticker", threads=True, progress=False)
                many = len(drift_full) > 1
                for t in drift_full:
                    df = self._normalize_yf(self._extract_batch(batch, t, many), t)
                    if df is not None and len(df) > 50:
                        quality = self.storage.save_raw(t, df, "1d", source="yfinance")
                        if quality.status == "quarantined":
                            log.error(f"[DADOS] {t} recusado: {quality.reason}")
                            continue
                        self._last_refresh[t] = time.time()
                        ok += 1
            except Exception as e:
                log.warning(f"[DADOS] re-download drift falhou: {type(e).__name__}: {e}")

        return ok

    # ─── Stock/ETF Ingestion (yfinance) ──────────────────────────────

    async def _ingest_stock(self, ticker: str):
        """Ingere dados de um stock/ETF via yfinance."""
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, self._fetch_stock_sync, ticker, 20000)
        if df is not None and len(df) > 50:
            quality = self.storage.save_raw(ticker, df, "1d",
                                            source=str(df.attrs.get("data_source", "yfinance")))
            if quality.status == "quarantined":
                raise ValueError(f"dados quarentenados: {quality.reason}")
            self._last_refresh[ticker] = time.time()
            return True
        raise ValueError(f"Dados insuficientes para {ticker}")

    def _fetch_stock_sync(self, ticker: str, periods: int) -> Optional[pd.DataFrame]:
        """Fetch síncrono de stock (executa em thread pool)."""
        try:
            import yfinance as yf
            ticker_obj = yf.Ticker(ticker)
            # Historico COMPLETO (decadas). Antes limitava a ~5 anos (periods*1.4 dias),
            # cegando qualquer teste cross-regime (2008/2020/2022 — onde diversificadores
            # como trend-following se provam). O tail(periods) abaixo controla quanto cada
            # chamador guarda; a ingestao (_ingest_stock) pede um periods grande p/ manter tudo.
            df = ticker_obj.history(period="max", auto_adjust=True)
            if df.empty:
                return None
            df.index = pd.to_datetime(df.index.tz_localize(None))
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            # Indices (^VIX, ^VIX3M) nao tem volume — nao filtra por volume neles
            if not ticker.startswith("^"):
                df = df[df["volume"] > 0]
            log.debug(f"[DADOS] yfinance OK {ticker}: {len(df)} candles reais")
            return df.tail(periods)
        except Exception as e:
            log.error(f"[DADOS] yfinance FALHOU {ticker}: {type(e).__name__}: {e} — fonte recusada")
            return None

    async def _fetch_stock(self, ticker: str, periods: int) -> Optional[pd.DataFrame]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._fetch_stock_sync, ticker, periods)

    # ─── Crypto Ingestion (ccxt) ─────────────────────────────────────
    # Um cliente spot compartilhado: criar 7x ccxt.binance em paralelo fazia
    # cada um chamar exchangeInfo (incluindo dapi) e estourar RequestTimeout.

    _binance_lock = None
    _binance_exchange = None

    def _binance_client(self):
        """Cliente spot único, thread-safe, sem carregar delivery/dapi."""
        import threading
        import ccxt
        if DataIngestionEngine._binance_lock is None:
            DataIngestionEngine._binance_lock = threading.RLock()
        with DataIngestionEngine._binance_lock:
            if DataIngestionEngine._binance_exchange is None:
                exchange = ccxt.binance({
                    "apiKey": config.binance_api_key or None,
                    "secret": config.binance_secret_key or None,
                    "enableRateLimit": True,
                    "timeout": 30_000,
                    "options": {
                        "defaultType": "spot",
                        "fetchMarkets": ["spot"],
                        "recvWindow": 60_000,
                    },
                })
                try:
                    exchange.load_time_difference()
                except Exception as exc:
                    log.warning(f"[DADOS] Binance time sync falhou: {exc}")
                DataIngestionEngine._binance_exchange = exchange
            return DataIngestionEngine._binance_exchange

    async def _ingest_crypto(self, symbol: str):
        """Ingere dados de crypto via ccxt Binance (spot)."""
        df = await self._fetch_crypto(symbol, "1d", 1000)
        if df is not None and len(df) > 50:
            quality = self.storage.save_raw(symbol, df, "1d",
                                            source=str(df.attrs.get("data_source", "ccxt.binance")))
            if quality.status == "quarantined":
                raise ValueError(f"dados quarentenados: {quality.reason}")
            self._last_refresh[symbol] = time.time()
            return True
        # Timeout/rede nao pode apagar um historico local ja finalizado.
        existing = self.storage.load_raw(symbol, "1d")
        if existing is not None and len(existing) > 50:
            self._last_refresh[symbol] = time.time()
            log.warning(
                f"[DADOS] Binance indisponivel para {symbol}; "
                f"mantido parquet local ({len(existing)} barras)"
            )
            return True
        raise ValueError(f"Dados insuficientes para {symbol}")

    async def _fetch_crypto(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 500,
    ) -> Optional[pd.DataFrame]:
        """Fetch de crypto via ccxt."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._fetch_crypto_sync, symbol, timeframe, limit
        )

    def _fetch_crypto_sync(
        self, symbol: str, timeframe: str, limit: int
    ) -> Optional[pd.DataFrame]:
        try:
            exchange = self._binance_client()
            with DataIngestionEngine._binance_lock:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv:
                return None
            df = pd.DataFrame(
                ohlcv,
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df = df.set_index("timestamp")
            df.index.name = "Date"
            df.attrs["data_source"] = "ccxt.binance"
            log.debug(f"[DADOS] Binance OK {symbol}: {len(df)} candles reais")
            return df.dropna()
        except Exception as e:
            # Reseta o cliente se o endpoint ficou em mau estado (timeout dapi etc.)
            DataIngestionEngine._binance_exchange = None
            log.error(f"[DADOS] Binance FALHOU {symbol}: {type(e).__name__}: {e} — fonte recusada")
            return None

    # ─── Synthetic Data (fallback) ────────────────────────────────────

    def _generate_synthetic(self, asset: str, periods: int) -> pd.DataFrame:
        """
        Gera dados sintéticos realistas para desenvolvimento/teste.
        Usa GBM (Geometric Brownian Motion) com fat tails.
        """
        log.debug(f"Gerando dados sintéticos para {asset}")
        np.random.seed(hash(asset) % 2**32)

        dates = pd.date_range(
            end=datetime.utcnow().date(),
            periods=periods,
            freq="B"  # Business days
        )

        # GBM com regime switching
        mu = 0.0003      # drift diário ~7.8% ao ano
        sigma = 0.015    # vol diária ~24% ao ano
        S0 = 100.0

        returns = np.random.normal(mu, sigma, periods)
        # Fat tails: adiciona eventos extremos ocasionais
        jumps = np.random.choice(
            [0, -0.03, 0.03, -0.06, 0.06],
            size=periods,
            p=[0.97, 0.01, 0.01, 0.005, 0.005]
        )
        returns = returns + jumps
        prices = S0 * np.exp(np.cumsum(returns))

        # OHLCV realista
        intraday_range = np.abs(np.random.normal(0, sigma * 1.5, periods))
        open_p = prices * (1 + np.random.normal(0, 0.003, periods))
        high_p = prices * (1 + intraday_range)
        low_p  = prices * (1 - intraday_range)
        close_p = prices
        volume = np.random.lognormal(15, 1, periods).astype(int)

        frame = pd.DataFrame({
            "open": np.round(open_p, 4),
            "high": np.round(np.maximum(high_p, np.maximum(open_p, close_p)), 4),
            "low":  np.round(np.minimum(low_p, np.minimum(open_p, close_p)), 4),
            "close": np.round(close_p, 4),
            "volume": volume,
        }, index=dates)
        frame.attrs["data_source"] = "synthetic"
        return frame

    # ─── Multi-asset Matrix ───────────────────────────────────────────

    async def get_returns_matrix(
        self,
        assets: Optional[List[str]] = None,
        periods: int = 252,
    ) -> pd.DataFrame:
        """Retorna matriz de retornos para todos os assets (usado pelo portfolio engine)."""
        if assets is None:
            assets = config.data_assets_stocks[:10]  # top 10 por padrão

        dfs = {}
        for asset in assets:
            df = await self.get_ohlcv(asset, periods=periods)
            if df is not None and len(df) > 50:
                dfs[asset] = df["close"].pct_change().dropna()

        if not dfs:
            return pd.DataFrame()

        matrix = pd.DataFrame(dfs)
        matrix = matrix.dropna()
        return matrix

    async def start(self):
        """Valida chaves de API e loga status de cada fonte de dados."""
        log.info("DataIngestion iniciado")

        anthropic_ok = bool(config.anthropic_api_key and "COLE" not in config.anthropic_api_key)
        alpaca_ok    = bool(config.alpaca_api_key and config.alpaca_api_key not in ("", "PK...", "your_key"))
        binance_ok   = bool(config.binance_api_key and len(config.binance_api_key) > 20)

        log.info(f"[KEYS] Anthropic : {'OK (' + config.anthropic_api_key[:12] + '...)' if anthropic_ok else 'NAO CONFIGURADA'}")
        log.info(f"[KEYS] Alpaca    : {'OK (' + config.alpaca_api_key[:8] + '...)' if alpaca_ok else 'NAO CONFIGURADA - yfinance'}")
        log.info(f"[KEYS] Binance   : {'OK (' + config.binance_api_key[:8] + '...)' if binance_ok else 'NAO CONFIGURADA - sintetico'}")

        # Teste de conexão Binance em background (não bloqueia startup)
        if binance_ok:
            asyncio.create_task(self._check_binance_connection())

    async def _check_binance_connection(self):
        """Testa conexão Binance spot em background — não bloqueia o startup."""
        try:
            loop = asyncio.get_running_loop()

            def _probe():
                with DataIngestionEngine._binance_lock:
                    client = self._binance_client()
                    return client.load_markets()

            markets = await loop.run_in_executor(None, _probe)
            log.info(f"[KEYS] Binance spot conectado OK: {len(markets)} pares disponíveis")
        except Exception as e:
            DataIngestionEngine._binance_exchange = None
            log.warning(f"[KEYS] Binance conexao FALHOU: {type(e).__name__}: {e}")

    async def stop(self):
        log.info("DataIngestion parado")
