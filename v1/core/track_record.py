"""
QUANT HPC OS — Track-Record Paper-Forward
==================================================
Registro do NAV por sessao liquidada do portfolio no broker simulado,
separado do DB de backtest. Ele mede comportamento paper-forward; nao e
execucao real nem substitui extrato de broker.

- Upsert por data (re-rodar o mesmo dia atualiza a linha, nao duplica).
- Escrita atomica (crash-safe): nunca corrompe o historico acumulado.
- Exporta CSV pronto para o consolidation_scorecard.py.

Grava em: <data_lake>/track_record/nav.csv
Colunas: date, nav, cash, n_positions, spy_close, n_fills_cum, regime
"""
from __future__ import annotations
import os
import shutil
import time
from pathlib import Path
from datetime import datetime, date as _date
import numpy as np
import pandas as pd
from core.config import config
from core.logger import get_logger

log = get_logger("TrackRecord")

_COLS = ["date", "nav", "cash", "n_positions", "spy_close", "n_fills_cum", "regime"]


class TrackRecord:
    def __init__(self, path: str | None = None):
        self.path = Path(path) if path else (config.data_lake_dir / "track_record" / "nav.csv")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._df = self._load()

    def _load(self) -> pd.DataFrame:
        if self.path.exists():
            try:
                df = pd.read_csv(self.path)
                for c in _COLS:
                    if c not in df.columns:
                        df[c] = np.nan
                df = df[_COLS]
                parsed = pd.to_datetime(df["date"], errors="coerce")
                from core.market_clock import is_trading_day
                valid = parsed.notna() & parsed.dt.date.map(is_trading_day)
                valid &= pd.to_numeric(df["nav"], errors="coerce").map(np.isfinite)
                cleaned = df.loc[valid].copy()
                cleaned["date"] = parsed.loc[valid].dt.date.astype(str)
                cleaned = cleaned.drop_duplicates(subset=["date"], keep="last")
                cleaned.sort_values("date", inplace=True, ignore_index=True)
                if len(cleaned) != len(df):
                    # Limpeza recuperavel: conserva exatamente o arquivo antigo.
                    stamp = time.strftime("%Y%m%d_%H%M%S")
                    backup = self.path.with_suffix(self.path.suffix + f".calendar_cleanup_{stamp}.bak")
                    shutil.copy2(self.path, backup)
                    tmp = str(self.path) + ".tmp"
                    cleaned.to_csv(tmp, index=False)
                    os.replace(tmp, self.path)
                    log.warning(
                        f"TrackRecord: {len(df)-len(cleaned)} linhas fora do calendario/invalidas "
                        f"removidas; original preservado em {backup.name}"
                    )
                return cleaned
            except Exception as e:
                log.warning(f"track_record corrompido, recomeçando registro em memória: {e}")
        return pd.DataFrame(columns=_COLS)

    def _atomic_write(self):
        tmp = str(self.path) + ".tmp"
        self._df.to_csv(tmp, index=False)
        os.replace(tmp, str(self.path))  # rename atomico

    def log_nav(self, nav: float, cash=None, n_positions=None, spy_close=None,
                n_fills=None, regime: str = "unknown", date=None):
        """Registra (ou atualiza) o NAV de um dia. Upsert por data."""
        if nav is None or not np.isfinite(float(nav)):
            return
        if date is None:
            from core.market_clock import last_settled_session
            d = last_settled_session().isoformat()
        elif isinstance(date, (_date, datetime)):
            d = date.isoformat()[:10]
        else:
            d = str(date)[:10]

        row = {
            "date": d,
            "nav": float(nav),
            "cash": float(cash) if cash is not None and np.isfinite(float(cash)) else np.nan,
            "n_positions": int(n_positions) if n_positions is not None else 0,
            "spy_close": float(spy_close) if spy_close is not None and np.isfinite(float(spy_close)) else np.nan,
            "n_fills_cum": int(n_fills) if n_fills is not None else 0,
            "regime": str(regime),
        }
        if not self._df.empty and (self._df["date"] == d).any():
            for k, v in row.items():
                self._df.loc[self._df["date"] == d, k] = v
        elif self._df.empty:
            self._df = pd.DataFrame([row], columns=_COLS)
        else:
            self._df = pd.concat([self._df, pd.DataFrame([row])], ignore_index=True)
        self._df.sort_values("date", inplace=True, ignore_index=True)
        self._atomic_write()

    # ─── Exportacao para o scorecard ──────────────────────────────────

    def export_scorecard_csv(self, out=None) -> str:
        """NAV indexado por data (formato que o consolidation_scorecard.py le)."""
        out = Path(out) if out else (self.path.parent / "scorecard_nav.csv")
        self._df[["date", "nav"]].set_index("date").to_csv(out)
        return str(out)

    def export_spy_csv(self, out=None):
        """Buy-and-hold de SPY, normalizado ao NAV inicial (benchmark)."""
        df = self._df.dropna(subset=["spy_close"])
        if len(df) < 2 or self._df.empty:
            return None
        start_nav = float(self._df["nav"].iloc[0])
        spy0 = float(df["spy_close"].iloc[0])
        spy_nav = start_nav * (df["spy_close"].astype(float) / spy0)
        out = Path(out) if out else (self.path.parent / "scorecard_spy.csv")
        pd.DataFrame({"nav": spy_nav.values}, index=df["date"].values).to_csv(out, index_label="date")
        return str(out)

    # ─── Helpers ──────────────────────────────────────────────────────

    @property
    def n_days(self) -> int:
        return len(self._df)

    @property
    def n_sessions(self) -> int:
        return len(self._df)

    @property
    def n_trades(self) -> int:
        if self._df.empty:
            return 0
        value = pd.to_numeric(self._df["n_fills_cum"], errors="coerce").max()
        return int(value) if pd.notna(value) else 0

    def summary(self) -> dict:
        if self._df.empty:
            return {"sessions": 0, "days": 0, "nav": None, "trades": 0,
                    "execution_evidence": "SIMULATED_CLOSE_FILL"}
        return {
            "sessions": len(self._df),
            "days": len(self._df),  # compatibilidade de API
            "first_date": self._df["date"].iloc[0],
            "last_date": self._df["date"].iloc[-1],
            "nav": float(self._df["nav"].iloc[-1]),
            "trades": self.n_trades,
            "execution_evidence": "SIMULATED_CLOSE_FILL",
        }
