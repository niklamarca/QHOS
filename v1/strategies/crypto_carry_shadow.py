"""
QUANT HPC OS — CRYPTO-CARRY Shadow (sobreviveu à bateria pré-registrada de 2026-08-19)
======================================================================================
Protocolo CONGELADO (HIPOTESE_CRYPTO_spec_falsificavel.md §E):
  short perp hedgeado recebe funding; 4 ativos com peso fixo 1/4; líquido de
  0,015/1095 por evento de 8h; gate por ativo = média dos 270 eventos anteriores
  <= 0 desliga; sessão = dia UTC completo; zero crédito retroativo.
SHADOW: observação por sessão liquidada, sem ordem, sem fill, sem capital.
Braço independente: ingere o próprio funding (fapi pública, sem chave) com
manifest de proveniência, ledger append-only próprio, lê parquet direto (sem
DuckDB). Mudar qualquer parâmetro = novo teste (relógio zera).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from core.config import config
from core.logger import get_logger

log = get_logger("CryptoCarryShadow")

ASSETS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
WEIGHT = 0.25                          # fixo; ativo desligado = caixa a 0%
COST_PER_EVENT = 0.015 / (3 * 365)     # 1,5%/ano amortizado por evento de 8h
GATE_WINDOW_EVENTS = 270               # 90 dias x 3 eventos
BOOTSTRAP_DAYS = 120                   # aquece o gate; não vira sessão
INITIAL = 100_000.0
FAPI = "https://fapi.binance.com"
_FETCH_INTERVAL_S = 7200.0             # funding muda a cada 8h; 2h de folga


class CryptoCarryShadow:
    def __init__(self):
        lake = Path(config.data_lake_dir)
        self.raw_dir = lake / "raw"
        self.state_path = lake / "crypto_carry_state.json"
        self.ledger_path = lake / "track_record" / "crypto_carry_nav.csv"
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_ledger_header()
        self.nav = INITIAL
        self.last_date: str | None = None
        self._last_fetch = 0.0
        self._load_state()

    def _ensure_ledger_header(self) -> None:
        """Cria o CSV com cabeçalho para a trilha de evidência existir no boot.

        Não grava sessão: zero crédito retroativo permanece. Só evita
        FileNotFoundError no epistemics/dashboard enquanto o primeiro dia UTC
        completo após a âncora ainda não fechou.
        """
        if self.ledger_path.exists():
            return
        try:
            tmp = str(self.ledger_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("date,nav,ret,active_assets,btc_close\n")
            os.replace(tmp, self.ledger_path)
        except Exception as e:
            log.warning(f"CryptoCarryShadow: nao criou ledger vazio: {e}")

    # ─── estado ──────────────────────────────────────────────────────
    def _load_state(self):
        try:
            if self.state_path.exists():
                st = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.nav = float(st.get("nav", INITIAL))
                self.last_date = st.get("last_date")
                log.info(f"CryptoCarryShadow: retomado NAV=${self.nav:,.2f} ate {self.last_date}")
        except Exception as e:
            log.warning(f"CryptoCarryShadow: estado corrompido, recomecando: {e}")

    def _save_state(self):
        try:
            tmp = str(self.state_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"nav": self.nav, "last_date": self.last_date}, fh)
            os.replace(tmp, self.state_path)
        except Exception as e:
            log.warning(f"CryptoCarryShadow: falha ao salvar estado: {e}")

    # ─── ingestão de funding (fonte pública, com proveniência) ───────
    def _fetch_funding_rows(self, symbol: str, start_ms: int) -> list:
        rows, cursor = [], start_ms
        while True:
            url = (f"{FAPI}/fapi/v1/fundingRate?symbol={symbol}"
                   f"&startTime={cursor}&limit=1000")
            request = urllib.request.Request(url, headers={"User-Agent": "qhos/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                batch = json.loads(response.read().decode("utf-8"))
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1]["fundingTime"]) + 1
            if len(batch) < 1000:
                break
            time.sleep(0.15)
        return rows

    def _funding_path(self, symbol: str) -> Path:
        return self.raw_dir / f"FUNDING_{symbol}_8h.parquet"

    def _refresh_funding(self) -> bool:
        """Atualização incremental. Cada evento de funding já pago é fato
        liquidado — não existe barra parcial aqui."""
        missing = any(not self._funding_path(symbol).exists() for symbol in ASSETS)
        if not missing and time.time() - self._last_fetch < _FETCH_INTERVAL_S:
            return True
        ok = True
        for symbol in ASSETS:
            path = self._funding_path(symbol)
            try:
                current = pd.read_parquet(path) if path.exists() else None
                if current is not None and len(current):
                    start_ms = int(current.index.max().timestamp() * 1000) + 1
                else:
                    current = None
                    start_ms = int((datetime.now(timezone.utc)
                                    - timedelta(days=BOOTSTRAP_DAYS)).timestamp() * 1000)
                rows = self._fetch_funding_rows(symbol, start_ms)
                if rows:
                    new = pd.DataFrame(rows)
                    new["ts"] = pd.to_datetime(new["fundingTime"].astype(np.int64), unit="ms")
                    new["rate"] = new["fundingRate"].astype(float)
                    new = new[["ts", "rate"]].set_index("ts").sort_index()
                    merged = pd.concat([current, new]) if current is not None else new
                    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    tmp = str(path) + ".tmp"
                    merged.to_parquet(tmp)
                    os.replace(tmp, path)
                    self._write_provenance(symbol, merged)
                elif current is None or not len(current):
                    ok = False
            except Exception as e:
                log.warning(f"CryptoCarryShadow: funding {symbol} indisponivel: "
                            f"{type(e).__name__}: {e}")
                ok = False
        if ok:
            self._last_fetch = time.time()
        return ok

    def _write_provenance(self, symbol: str, frame: pd.DataFrame):
        try:
            from data.provenance import assess, write_manifest
            quality, _ = assess(f"FUNDING_{symbol}", frame, timeframe="8h",
                                source="binance.fapi.fundingRate")
            write_manifest(Path(config.data_lake_dir), quality)
        except Exception as e:
            log.debug(f"CryptoCarryShadow: manifest {symbol}: {e}")

    # ─── regra congelada ─────────────────────────────────────────────
    def _event_returns(self) -> pd.DataFrame | None:
        """Retorno líquido por evento e por ativo, com o gate SEM lookahead."""
        columns = {}
        for symbol in ASSETS:
            path = self._funding_path(symbol)
            if not path.exists():
                return None
            try:
                rate = pd.read_parquet(path)["rate"].astype(float)
            except Exception:
                return None
            gate_on = rate.shift(1).rolling(GATE_WINDOW_EVENTS).mean() > 0
            columns[symbol] = (rate - COST_PER_EVENT) * gate_on.astype(float)
        events = pd.DataFrame(columns).dropna(how="all")
        return events if len(events) else None

    def step(self) -> bool:
        self._refresh_funding()
        events = self._event_returns()
        if events is None or events.empty:
            return False
        utc_today = datetime.now(timezone.utc).date()
        if self.last_date is None:
            # Zero crédito retroativo (§E.5): a primeira sessão contada é o
            # primeiro dia UTC completo APÓS o congelamento do protocolo.
            self.last_date = str(utc_today)
            self._save_state()
            log.info(f"CryptoCarryShadow: ancorado em {self.last_date}; "
                     f"primeira sessao sera {utc_today + timedelta(days=1)}")
            return False
        # peso fixo 1/4; NaN (ativo sem historico p/ gate) = desligado
        portfolio = (events.fillna(0.0) * WEIGHT).sum(axis=1)
        active = (events.fillna(0.0) != 0.0).sum(axis=1)
        daily = portfolio.groupby(portfolio.index.date).sum()
        active_daily = active.groupby(active.index.date).max()
        new_days = [d for d in daily.index
                    if str(d) > self.last_date and d < utc_today]
        if not new_days:
            return False
        btc = self._btc_close()
        header = not self.ledger_path.exists() or self.ledger_path.stat().st_size == 0
        wrote = 0
        with open(self.ledger_path, "a", encoding="utf-8") as fh:
            if header:
                fh.write("date,nav,ret,active_assets,btc_close\n")
            for day in sorted(new_days):
                r = float(daily.loc[day])
                self.nav *= (1.0 + r)
                close = btc.get(str(day), float("nan")) if btc else float("nan")
                fh.write(f"{day},{self.nav:.2f},{r:.8f},"
                         f"{int(active_daily.loc[day])},{close:.2f}\n")
                self.last_date = str(day)
                wrote += 1
        self._save_state()
        log.info(f"CryptoCarryShadow: +{wrote} sessao(oes) -> NAV=${self.nav:,.2f} "
                 f"({(self.nav / INITIAL - 1) * 100:+.3f}%)")
        return True

    def _btc_close(self) -> dict:
        """Referência de contexto no ledger (como spy_close no SYMB-1)."""
        try:
            px = pd.read_parquet(self.raw_dir / "BTC_USDT_1d.parquet")["close"]
            px.index = pd.to_datetime(px.index)
            return {str(ts.date()): float(v) for ts, v in px.items()}
        except Exception:
            return {}

    def status(self) -> dict:
        return {"nav": self.nav, "ret": self.nav / INITIAL - 1.0,
                "last_date": self.last_date}
