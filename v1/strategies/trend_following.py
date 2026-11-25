"""
QUANT HPC OS — Trend-Following Paper (braço forward independente)
=================================================================
Implementa EXATAMENTE a regra pré-registrada em HIPOTESE_TREND_spec_falsificavel.md:
TSMOM 3/6/12m sign-blend, inverse-vol, custo 2bps*turnover, vol-target 15%,
TUDO defasado 1 dia (sem look-ahead). Parâmetros CONGELADOS — mudar = novo teste.

Braço SEPARADO do sistema: ledger próprio (trend_nav.csv), estado próprio,
NÃO entra no GA/HRP/broker. Lê parquets direto (sem DuckDB — sem lock).
Recomputa a série determinística do histórico e só APPENDA dias novos ao
ledger (append-only: o passado do forward nunca é reescrito).
"""
from __future__ import annotations
import json
import os
import glob
import numpy as np
import pandas as pd
from core.config import config
from core.logger import get_logger

log = get_logger("TrendPaper")

UNIVERSE = ["SPY", "TLT", "GLD", "IWM", "QQQ"]
LOOKBACKS = [63, 126, 252]
COST = 0.0002          # 2 bps por unidade de turnover
VOL_TARGET = 0.15
LEV_CAP = 3.0
POS_CAP = 0.5
INITIAL = 100_000.0


class TrendFollowingPaper:
    def __init__(self):
        lake = str(config.data_lake_dir)
        self.raw_dir = os.path.join(lake, "raw")
        self.state_path = os.path.join(lake, "trend_state.json")
        self.ledger_path = os.path.join(lake, "track_record", "trend_nav.csv")
        os.makedirs(os.path.dirname(self.ledger_path), exist_ok=True)
        self.nav = INITIAL
        self.last_date = None            # str YYYY-MM-DD do ultimo dia contabilizado
        self._load_state()

    # ── estado ────────────────────────────────────────────────────────
    def _load_state(self):
        try:
            if os.path.exists(self.state_path):
                st = json.load(open(self.state_path))
                self.nav = float(st.get("nav", INITIAL))
                self.last_date = st.get("last_date")
                log.info(f"TrendPaper: estado retomado NAV=${self.nav:,.2f} ate {self.last_date}")
        except Exception as e:
            log.warning(f"TrendPaper: estado corrompido, recomecando: {e}")

    def _save_state(self):
        try:
            tmp = self.state_path + ".tmp"
            json.dump({"nav": self.nav, "last_date": self.last_date}, open(tmp, "w"))
            os.replace(tmp, self.state_path)
        except Exception as e:
            log.warning(f"TrendPaper: falha ao salvar estado: {e}")

    # ── dados ─────────────────────────────────────────────────────────
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
        return px if len(px) > 320 else None

    # ── a regra congelada (identica ao teste de 20 anos) ─────────────
    @staticmethod
    def _strategy_returns(px: pd.DataFrame) -> pd.Series:
        ret = px.pct_change()
        sig = sum(np.sign(px.pct_change(k)) for k in LOOKBACKS) / len(LOOKBACKS)
        sig = sig.shift(1)                                   # decide com dado de ontem
        vol = ret.rolling(63).std().shift(1)
        pos = (sig * (0.01 / vol)).clip(-POS_CAP, POS_CAP)
        turn = pos.diff().abs().sum(axis=1)
        pre = (pos * ret).mean(axis=1) - COST * turn / len(px.columns)
        scale = (VOL_TARGET / (pre.rolling(63).std().shift(1) * np.sqrt(252))).clip(0, LEV_CAP)
        return (pre * scale).dropna()

    # ── passo diario (chamado periodicamente; so age em barra nova) ──
    def step(self) -> bool:
        px = self._prices()
        if px is None:
            log.debug("TrendPaper: dados insuficientes")
            return False
        strat = self._strategy_returns(px)
        if strat.empty:
            return False

        if self.last_date is None:
            # primeira execucao: ancora o ledger no penultimo dia -> contabiliza 1 dia ja
            self.last_date = str(strat.index[-2].date())

        new = strat[strat.index > pd.Timestamp(self.last_date)]
        if new.empty:
            return False

        spy = px["SPY"]
        wrote = 0
        header = not os.path.exists(self.ledger_path)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            if header:
                f.write("date,nav,ret,spy_close\n")
            for dt, r in new.items():
                self.nav *= (1.0 + float(r))
                f.write(f"{dt.date()},{self.nav:.2f},{float(r):.6f},{float(spy.loc[dt]):.4f}\n")
                self.last_date = str(dt.date())
                wrote += 1
        self._save_state()
        log.info(f"TrendPaper: +{wrote} dia(s) -> NAV=${self.nav:,.2f} ({(self.nav/INITIAL-1)*100:+.2f}%)")
        return True

    def status(self) -> dict:
        return {"nav": self.nav, "ret": self.nav / INITIAL - 1.0, "last_date": self.last_date}
