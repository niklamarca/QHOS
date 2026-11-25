"""
QUANT HPC OS — Learning Engine (contexto por observação)
========================================================
Acelera o APRENDIZADO POR UNIDADE DE TEMPO: em vez de gravar só o retorno,
grava o CONTEXTO em que ele aconteceu. Com isso, cada dia de forward passa a
alimentar P(resultado | estrategia, contexto) em vez de apenas uma média.

Contexto capturado (tudo já disponível, custo ~zero):
  regime, contango (VIX/VIX3M), nivel de VIX, vol realizada do SPY, dia da semana.

Persistência: data_lake/learning_context.jsonl (append-only).
Consulta: edge_by_context() devolve retorno médio e n por bucket de contexto.
"""
from __future__ import annotations
import glob
import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np

from core.config import config
from core.logger import get_logger

log = get_logger("Learning")


class LearningEngine:
    def __init__(self):
        lake = str(config.data_lake_dir)
        self.path = os.path.join(lake, "learning_context.jsonl")
        self.raw = os.path.join(lake, "raw")
        self._lock = threading.Lock()
        self._ctx_cache: Dict[str, Any] = {}
        self._ctx_ts = 0.0

    # ---------- contexto ----------
    def market_context(self) -> Dict[str, Any]:
        """Snapshot barato do contexto atual (cache de 5 min)."""
        if time.time() - self._ctx_ts < 300 and self._ctx_cache:
            return self._ctx_cache
        ctx: Dict[str, Any] = {"ts": time.time()}
        try:
            import pandas as pd
            def close(sym):
                h = glob.glob(os.path.join(self.raw, f"{sym}_1d.parquet"))
                if not h:
                    return None
                s = pd.read_parquet(h[0])["close"].astype(float)
                return s
            vix, v3m, spy = close("VIX"), close("VIX3M"), close("SPY")
            if vix is not None and len(vix):
                ctx["vix"] = round(float(vix.iloc[-1]), 2)
                ctx["vix_bucket"] = ("baixo" if ctx["vix"] < 15 else
                                     "medio" if ctx["vix"] < 22 else "alto")
            if vix is not None and v3m is not None and len(vix) and len(v3m):
                r = float(vix.iloc[-1]) / float(v3m.iloc[-1])
                ctx["ts_ratio"] = round(r, 3)
                ctx["contango"] = bool(r < 1.0)
            if spy is not None and len(spy) > 21:
                rv = float(spy.pct_change().tail(21).std() * np.sqrt(252))
                ctx["rv21"] = round(rv, 4)
                ctx["rv_bucket"] = ("calmo" if rv < 0.12 else
                                    "normal" if rv < 0.20 else "estressado")
            ctx["dow"] = datetime.now().strftime("%a")
        except Exception as e:
            log.debug(f"contexto falhou: {e}")
        self._ctx_cache, self._ctx_ts = ctx, time.time()
        return ctx

    # ---------- registro ----------
    def observe(self, strategy_id: str, ret: float, regime: str = "unknown",
                extra: Optional[Dict] = None) -> None:
        """Grava UMA observação com o contexto do momento."""
        try:
            rec = {"ts": time.time(),
                   "iso": datetime.now().isoformat(timespec="seconds"),
                   "sid": str(strategy_id)[:16], "ret": float(ret),
                   "regime": regime, "ctx": self.market_context()}
            if extra:
                rec["extra"] = extra
            with self._lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, default=str) + "\n")
        except Exception as e:
            log.debug(f"observe falhou: {e}")

    # ---------- consulta ----------
    def _load(self, limit: int = 20000) -> List[Dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-limit:]
            return [json.loads(x) for x in lines]
        except Exception:
            return []

    def edge_by_context(self, key: str = "regime", min_n: int = 5) -> List[Dict]:
        """
        Retorno médio por bucket de contexto — o começo de P(resultado|contexto).
        key: 'regime' | 'contango' | 'vix_bucket' | 'rv_bucket' | 'dow'
        """
        recs = self._load()
        buckets = defaultdict(list)
        for r in recs:
            if key == "regime":
                k = r.get("regime", "unknown")
            else:
                k = (r.get("ctx") or {}).get(key, "n/d")
            buckets[str(k)].append(float(r.get("ret", 0.0)))
        out = []
        for k, v in buckets.items():
            if len(v) < min_n:
                continue
            a = np.asarray(v, float)
            sd = a.std()
            out.append({"bucket": k, "n": len(v),
                        "mean_pct": round(float(a.mean()) * 100, 4),
                        "t": round(float(a.mean() / (sd / np.sqrt(len(a)))), 2) if sd > 0 else 0.0,
                        "win_rate": round(float((a > 0).mean()) * 100, 1)})
        return sorted(out, key=lambda x: -x["n"])

    def summary(self) -> Dict[str, Any]:
        recs = self._load()
        return {"n_obs": len(recs),
                "by_regime": self.edge_by_context("regime"),
                "by_contango": self.edge_by_context("contango"),
                "by_vix": self.edge_by_context("vix_bucket"),
                "by_rv": self.edge_by_context("rv_bucket")}


_engine: Optional[LearningEngine] = None


def get_learning_engine() -> LearningEngine:
    global _engine
    if _engine is None:
        _engine = LearningEngine()
    return _engine
