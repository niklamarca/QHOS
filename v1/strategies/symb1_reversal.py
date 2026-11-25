"""
QUANT HPC OS — SYMB-1 Paper (estrutura aprovada em dois holdouts históricos)
==============================================================================
Regra CONGELADA (HIPOTESE_SYMB1_spec_falsificavel.md):
  pos_i = -sign( (r - sig63) * ((sig5 - r) + (sig5 - sig21)) ), defasado 1 dia
  portfolio = media das 5 pernas - 2bps * turnover. Sem vol-target, sem alavanca.
Braço independente: ledger append-only proprio, estado persistido, le parquets
direto (sem DuckDB). Mudar qualquer parametro = novo teste (relogio zera).
"""
from __future__ import annotations
import json, os, glob
import numpy as np
import pandas as pd
from core.config import config
from core.logger import get_logger

log = get_logger("Symb1Paper")
UNIVERSE = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
COST = 0.0002
INITIAL = 100_000.0


class Symb1Paper:
    def __init__(self):
        lake = str(config.data_lake_dir)
        self.raw_dir = os.path.join(lake, "raw")
        self.state_path = os.path.join(lake, "symb1_state.json")
        self.ledger_path = os.path.join(lake, "track_record", "symb1_nav.csv")
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        self.nav = INITIAL
        self.last_date = None
        self._load_state()

    def _load_state(self):
        try:
            if os.path.exists(self.state_path):
                st = json.load(open(self.state_path))
                self.nav = float(st.get("nav", INITIAL))
                self.last_date = st.get("last_date")
                log.info(f"Symb1Paper: retomado NAV=${self.nav:,.2f} ate {self.last_date}")
        except Exception as e:
            log.warning(f"Symb1Paper: estado corrompido, recomecando: {e}")

    def _save_state(self):
        try:
            tmp = self.state_path + ".tmp"
            json.dump({"nav": self.nav, "last_date": self.last_date}, open(tmp, "w"))
            os.replace(tmp, self.state_path)
        except Exception as e:
            log.warning(f"Symb1Paper: falha ao salvar estado: {e}")

    def _prices(self):
        cols = {}
        for s in UNIVERSE:
            hit = glob.glob(os.path.join(self.raw_dir, f"{s}_1d.parquet"))
            if not hit:
                return None
            try:
                d = pd.read_parquet(hit[0])["close"].astype(float)
                d.index = pd.to_datetime(d.index)
                cols[s] = d
            except Exception:
                return None
        px = pd.DataFrame(cols).sort_index().ffill().dropna()
        # BARRA LIQUIDADA: descarta qualquer vela cuja sessao ainda nao
        # encerrou (+folga). Sem isto o braco grava no ledger um fechamento
        # que ainda vai mudar — ocorreu em 12/08/2026 (772.687 vs 772.490).
        try:
            from core.market_clock import last_settled_session
            px = px[px.index.date <= last_settled_session()]
        except Exception:
            pass
        return px if len(px) > 100 else None

    @staticmethod
    def _strategy_returns(px: pd.DataFrame) -> pd.Series:
        ret = px.pct_change()
        s5 = ret.rolling(5).std()
        s21 = ret.rolling(21).std()
        s63 = ret.rolling(63).std()
        f = (ret - s63) * ((s5 - ret) + (s5 - s21))
        pos = (-np.sign(f)).shift(1)                      # defasado: decide com ontem
        turn = pos.diff().abs().sum(axis=1)
        port = (pos * ret).mean(axis=1) - COST * turn / len(px.columns)
        return port.dropna()

    def step(self) -> bool:
        px = self._prices()
        if px is None:
            return False
        strat = self._strategy_returns(px)
        if strat.empty:
            return False
        if self.last_date is None:
            self.last_date = str(strat.index[-2].date())
        new = strat[strat.index > pd.Timestamp(self.last_date)]
        if new.empty:
            return False
        spy = px["SPY"]
        header = not os.path.exists(self.ledger_path)
        wrote = 0
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            if header:
                fh.write("date,nav,ret,spy_close\n")
            for dt, r in new.items():
                self.nav *= (1.0 + float(r))
                fh.write(f"{dt.date()},{self.nav:.2f},{float(r):.6f},{float(spy.loc[dt]):.4f}\n")
                self.last_date = str(dt.date())
                wrote += 1
        self._save_state()
        log.info(f"Symb1Paper: +{wrote} dia(s) -> NAV=${self.nav:,.2f} ({(self.nav/INITIAL-1)*100:+.2f}%)")
        return True

    def status(self) -> dict:
        return {"nav": self.nav, "ret": self.nav / INITIAL - 1.0, "last_date": self.last_date}
