"""
QUANT HPC OS — Feature Engineering Engine
Pipeline de features técnicas, estatísticas e de microestrutura para todos os assets.
"""
from __future__ import annotations
import asyncio
import numpy as np
import pandas as pd
from typing import Optional, List, Dict
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from .storage import StorageEngine
from .ingestion import DataIngestionEngine

log = get_logger("Features")


class FeatureEngine:
    """
    Engine local de feature engineering.
    Calcula 80+ features por asset em paralelo.
    Categorias: Trend, Momentum, Volatility, Volume, Mean Reversion, Regime, Microstructure.
    """

    def __init__(self, storage: StorageEngine, ingestion: DataIngestionEngine):
        self.storage = storage
        self.ingestion = ingestion
        self.bus = event_bus

    async def compute_all(self):
        """Computa features para todos os assets."""
        assets = config.data_assets_stocks + config.data_assets_crypto

        tasks = [self.compute_for_asset(a) for a in assets]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # ``None`` significa que o raw foi recusado/quarentenado. Contá-lo como
        # sucesso esconderia exatamente a falha que esta camada deve propagar.
        ok = sum(1 for r in results if not isinstance(r, Exception) and r is not None)
        log.info(f"Features computadas: {ok}/{len(assets)} assets")

        await self.bus.publish(Event(
            type=EventType.FEATURES_COMPUTED,
            source="features",
            payload={"assets": ok, "total": len(assets)}
        ))

    async def compute_for_asset(self, asset: str) -> Optional[pd.DataFrame]:
        """Computa todas as features para um asset específico."""
        # Janela vinda do CONFIG (data_history_years). 0 = tudo.
        # Explicito de proposito: se algum dia o padrao de get_ohlcv voltar a
        # ser truncado, esta chamada continua correta — e agora a decisao mora
        # na configuracao, nao num argumento default escondido.
        _anos = int(getattr(config, "data_history_years", 0) or 0)
        _periods = None if _anos <= 0 else _anos * 252
        df_raw = await self.ingestion.get_ohlcv(asset, periods=_periods)
        if df_raw is None or len(df_raw) < 100:
            return None

        loop = asyncio.get_running_loop()
        df_features = await loop.run_in_executor(
            None, self._compute_features_sync, df_raw.copy()
        )

        if df_features is not None:
            self.storage.save_features(asset, df_features)
        return df_features

    def _compute_features_sync(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computa todas as features (executa em thread pool — CPU-bound)."""
        features = pd.DataFrame(index=df.index)

        o = df["open"]
        h = df["high"]
        l = df["low"]
        c = df["close"]
        v = df["volume"]

        # Inclui OHLCV no features para que o backtest tenha preços mesmo sem raw
        features["close"] = c
        features["open"] = o
        features["high"] = h
        features["low"] = l
        features["volume"] = v

        ret = c.pct_change()
        log_ret = np.log(c / c.shift(1))

        # ── Trend Features ──────────────────────────────────────────
        for n in [5, 10, 20, 50, 100, 200]:
            features[f"sma_{n}"] = c.rolling(n).mean()
            features[f"ema_{n}"] = c.ewm(span=n).mean()
            features[f"price_to_sma_{n}"] = c / features[f"sma_{n}"] - 1

        features["trend_5_20"] = features["sma_5"] / features["sma_20"] - 1
        features["trend_20_50"] = features["sma_20"] / features["sma_50"] - 1
        features["trend_50_200"] = features["sma_50"] / features["sma_200"] - 1

        # Golden/Death Cross
        features["golden_cross"] = (
            (features["sma_50"] > features["sma_200"]).astype(float)
        )

        # ── Momentum Features ────────────────────────────────────────
        for n in [1, 3, 5, 10, 21, 63, 126, 252]:
            features[f"mom_{n}"] = c.pct_change(n)

        # RSI
        for period in [7, 14, 21]:
            features[f"rsi_{period}"] = self._rsi(c, period)

        # MACD
        ema12 = c.ewm(span=12).mean()
        ema26 = c.ewm(span=26).mean()
        features["macd"] = ema12 - ema26
        features["macd_signal"] = features["macd"].ewm(span=9).mean()
        features["macd_hist"] = features["macd"] - features["macd_signal"]

        # Stochastic
        low14  = l.rolling(14).min()
        high14 = h.rolling(14).max()
        features["stoch_k"] = 100 * (c - low14) / (high14 - low14 + 1e-8)
        features["stoch_d"] = features["stoch_k"].rolling(3).mean()

        # ── Volatility Features ──────────────────────────────────────
        for n in [5, 10, 20, 63]:
            features[f"vol_{n}"] = log_ret.rolling(n).std() * np.sqrt(252)
        for n in [5, 10, 63]:
            features[f"vol_ratio_{n}"] = features["vol_20"] / (features[f"vol_{n}"] + 1e-8)

        # ATR
        for period in [14, 21]:
            features[f"atr_{period}"] = self._atr(h, l, c, period)
            features[f"atr_pct_{period}"] = features[f"atr_{period}"] / c

        # Bollinger Bands
        for n in [20, 50]:
            mid = c.rolling(n).mean()
            std = c.rolling(n).std()
            features[f"bb_upper_{n}"] = mid + 2 * std
            features[f"bb_lower_{n}"] = mid - 2 * std
            features[f"bb_pct_{n}"] = (c - features[f"bb_lower_{n}"]) / (
                features[f"bb_upper_{n}"] - features[f"bb_lower_{n}"] + 1e-8
            )
            features[f"bb_width_{n}"] = (features[f"bb_upper_{n}"] - features[f"bb_lower_{n}"]) / mid

        # Realized vol ratio (short/long)
        features["vol_regime"] = features["vol_5"] / features["vol_63"]

        # ── Volume Features ──────────────────────────────────────────
        for n in [5, 10, 20]:
            features[f"vol_ma_{n}"] = v.rolling(n).mean()
            features[f"vol_ratio_ma_{n}"] = v / (features[f"vol_ma_{n}"] + 1)

        # OBV
        obv = (np.sign(ret) * v).fillna(0).cumsum()
        features["obv"] = obv
        features["obv_ma20"] = obv.rolling(20).mean()
        features["obv_trend"] = obv / (obv.rolling(20).mean() + 1e-8) - 1

        # VWAP approximation
        features["vwap_dist"] = (c - (c * v).rolling(20).sum() / v.rolling(20).sum()) / c

        # ── Mean Reversion Features ──────────────────────────────────
        for n in [5, 10, 20, 63]:
            features[f"z_score_{n}"] = (
                (c - c.rolling(n).mean()) / (c.rolling(n).std() + 1e-8)
            )

        # Half-life reversion (Ornstein-Uhlenbeck proxy)
        features["ou_lag"] = c.shift(1)
        # Simple proxy: negative autocorrelation
        features["autocorr_1"] = ret.rolling(20).apply(
            lambda x: x.autocorr(lag=1) if len(x) > 5 else np.nan
        )
        features["autocorr_5"] = ret.rolling(60).apply(
            lambda x: x.autocorr(lag=5) if len(x) > 10 else np.nan
        )

        # ── Regime Features ─────────────────────────────────────────
        # Trend strength (ADX proxy)
        features["trend_strength"] = abs(features["trend_5_20"]) + abs(features["trend_20_50"])

        # Hurst exponent (rolling, approximation)
        features["hurst_approx"] = ret.rolling(100).apply(
            self._hurst_approx, raw=True
        )

        # Drawdown from peak
        roll_max = c.expanding().max()
        features["drawdown"] = (c - roll_max) / (roll_max + 1e-8)
        features["roll_dd_20"] = (c - c.rolling(20).max()) / (c.rolling(20).max() + 1e-8)

        # ── Microstructure ───────────────────────────────────────────
        # High-low spread proxy
        features["hl_spread"] = (h - l) / (c + 1e-8)
        # Candle body
        features["body_size"] = abs(c - o) / (h - l + 1e-8)
        # Upper/lower wicks
        features["upper_wick"] = (h - np.maximum(o, c)) / (h - l + 1e-8)
        features["lower_wick"] = (np.minimum(o, c) - l) / (h - l + 1e-8)
        # Gap
        features["gap"] = (o - c.shift(1)) / (c.shift(1) + 1e-8)

        # ── Returns ──────────────────────────────────────────────────
        features["ret_1d"] = ret
        features["log_ret_1d"] = log_ret
        features["close"] = c  # preço de referência


        # ── Kyle λ Microestrutura (Kalman + GLRT-CUSUM) ──────────────
        try:
            features['kyle_lambda'], features['kyle_innov_std'],             features['cusum_stat'], features['cusum_alarm'] =                 self._kyle_kalman_cusum(c, v)
        except Exception:
            features['kyle_lambda']    = np.nan
            features['kyle_innov_std'] = np.nan
            features['cusum_stat']     = np.nan
            features['cusum_alarm']    = 0.0

        # Drop NaN rows (primeiras N linhas sem dados suficientes)
        features = features.replace([np.inf, -np.inf], np.nan)
        features = features.dropna(thresh=int(len(features.columns) * 0.5))
        return features

    # ─── Indicadores Auxiliares ───────────────────────────────────────


    @staticmethod
    def _kyle_kalman_cusum(
        close: pd.Series,
        volume: pd.Series,
        s_proc: float = 0.02,
        k: float = 0.5,
        h: float = 12.0,
        warm_pct: float = 0.3,
    ):
        """
        Estima Kyle λ via Kalman Filter e detecta mudanças via GLRT-CUSUM.

        Kyle (1985): ΔP_t = λ_t · Q_t + ε_t
        Q_t = signed_volume = volume * sign(ret)   (proxy de order-flow)
        Inovação normalizada: z_t = (ΔP_t - λ̂_{t-1}·Q_t) / √S_t ~ N(0,1)
        GLRT-CUSUM: C_t = max(0, C_{t-1} + z_t² - 1 - k)  →  alarme se C_t > h

        Returns 4 Series: kyle_lambda, innov_std, cusum_stat, cusum_alarm
        """
        T = len(close)
        dP = close.diff().values          # ΔP
        Q  = (volume * np.sign(dP)).values  # signed volume proxy

        # --- Seed/escala CAUSAL: estimados SOMENTE no warm-up (primeiros
        # warm_pct), NUNCA no futuro. O codigo antigo usava nanstd/cov/var
        # sobre a serie INTEIRA -> vazava info futura para kyle_lambda em
        # TODAS as datas (look-ahead via normalizacao global). ---
        valid = ~(np.isnan(dP) | np.isnan(Q))
        if valid.sum() < 30:
            nan_s = pd.Series(np.nan, index=close.index)
            return nan_s, nan_s, nan_s, pd.Series(0.0, index=close.index)

        valid_idx = np.where(valid)[0]
        T_seed = max(30, int(T * warm_pct))
        seed_idx = valid_idx[valid_idx < T_seed]
        if len(seed_idx) < 30:                  # fallback: primeiros 30 validos
            seed_idx = valid_idx[:30]

        # Escala de Q a partir SO do warm-up (constante derivada do passado)
        Q_scale = float(np.nanstd(Q[seed_idx]) + 1e-10)
        Q = Q / Q_scale          # λ_raw = λ_normalized / Q_scale

        # Seed OLS λ e R a partir SO do warm-up
        dP_s, Q_s = dP[seed_idx], Q[seed_idx]
        vQ = float(np.var(Q_s) + 1e-10)
        lam_ols = float(np.cov(dP_s, Q_s)[0, 1] / vQ)
        R = max(float(np.var(dP_s - lam_ols * Q_s)), 1e-6)

        lam  = lam_ols
        Pv   = 1.0
        Q_p  = s_proc ** 2

        lam_arr   = np.full(T, np.nan)
        innov_arr = np.full(T, np.nan)

        for t in range(T):
            if np.isnan(dP[t]) or np.isnan(Q[t]):
                continue
            Pv += Q_p
            H   = Q[t]
            S   = H ** 2 * Pv + R
            v_t = dP[t] - lam * H
            z_t = v_t / np.sqrt(max(S, 1e-12))
            if abs(S) > 1e-12:
                K   = Pv * H / S
                lam += K * v_t
                Pv  = (1 - K * H) * Pv
            lam_arr[t]   = lam
            innov_arr[t] = z_t

        # --- GLRT-CUSUM on innovations (auto-calibrated: E[z²]=1) ---
        cusum_arr = np.zeros(T)
        alarm_arr = np.zeros(T)
        C = 0.0
        T_warm = int(T * warm_pct)     # não acionar alarme durante warm-up
        for t in range(T):
            z = innov_arr[t]
            if np.isnan(z):
                cusum_arr[t] = C
                continue
            C = max(0.0, C + z * z - 1.0 - k)
            cusum_arr[t] = C
            if t >= T_warm and C > h:
                alarm_arr[t] = 1.0
                C = 0.0     # reset após alarme (persistência de janela)

        idx = close.index
        return (
            pd.Series(lam_arr,   index=idx),
            pd.Series(innov_arr, index=idx),
            pd.Series(cusum_arr, index=idx),
            pd.Series(alarm_arr, index=idx),
        )

    @staticmethod
    def _rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
        avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
        rs = avg_gain / (avg_loss + 1e-8)
        return 100 - 100 / (1 + rs)

    @staticmethod
    def _atr(h: pd.Series, l: pd.Series, c: pd.Series, period: int = 14) -> pd.Series:
        tr1 = h - l
        tr2 = abs(h - c.shift(1))
        tr3 = abs(l - c.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.ewm(span=period, min_periods=period).mean()

    @staticmethod
    def _hurst_approx(returns: np.ndarray) -> float:
        """Aproximação do expoente de Hurst via R/S analysis."""
        if len(returns) < 20:
            return 0.5
        try:
            n = len(returns)
            mean_r = np.mean(returns)
            devs = np.cumsum(returns - mean_r)
            R = np.max(devs) - np.min(devs)
            S = np.std(returns, ddof=1)
            if S == 0:
                return 0.5
            rs = R / S
            return np.log(rs) / np.log(n / 2) if n > 0 else 0.5
        except Exception:
            return 0.5

    async def get_feature_matrix(
        self,
        assets: List[str],
        feature_cols: Optional[List[str]] = None
    ) -> Dict[str, pd.DataFrame]:
        """Retorna matrix de features para múltiplos assets."""
        result = {}
        for asset in assets:
            df = self.storage.load_features(asset)
            if df is None:
                df = await self.compute_for_asset(asset)
            if df is not None:
                if feature_cols:
                    cols = [c for c in feature_cols if c in df.columns]
                    result[asset] = df[cols]
                else:
                    result[asset] = df
        return result

    async def start(self):
        log.info("FeatureEngine iniciado")

    async def stop(self):
        log.info("FeatureEngine parado")
