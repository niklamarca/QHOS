"""Contrato de proveniência e qualidade para séries de mercado.

O raw pode conter uma barra ainda em formação para fins de observabilidade,
mas pesquisa, features e execução só podem ler observações finalizadas e
aprovadas por este módulo. A ausência de dado confiável é preferível a uma
observação inventada ou atrasada.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Tuple
import json
import os
import tempfile
import time

import pandas as pd

from core.config import config
from core import market_clock
from core.logger import get_logger

log = get_logger("DataProvenance")


@dataclass(frozen=True)
class DataQuality:
    asset: str
    timeframe: str
    source: str
    status: str                 # approved | partial | quarantined
    reason: str
    rows: int
    first_observation: str | None
    last_observation: str | None
    final_observation: str | None
    retrieved_at: str
    data_hash: str                 # arquivo fisico, pode incluir barra parcial
    final_data_hash: str = "unavailable"  # visao realmente entregue a pesquisa

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _safe_name(asset: str, timeframe: str) -> str:
    return asset.replace("/", "_").replace(":", "_").replace("^", "") + f"_{timeframe}"


def _market(asset: str) -> str:
    text = asset.upper()
    return "crypto" if "/" in text or text.endswith("USDT") or text.endswith("-USD") else "stocks"


def _hash_frame(frame: pd.DataFrame) -> str:
    """Hash determinístico, suficiente para detectar alteração de insumo."""
    try:
        raw = pd.util.hash_pandas_object(frame, index=True).values.tobytes()
        return sha256(raw).hexdigest()
    except Exception:
        return "unavailable"


def assess(asset: str, frame: pd.DataFrame, timeframe: str = "1d",
           source: str = "unknown") -> Tuple[DataQuality, pd.DataFrame]:
    """Classifica uma série e retorna a versão segura para pesquisa.

    Para barras diárias de ações, a vela do pregão atual só é utilizável após
    a liquidação oficial. Para cripto, a vela UTC corrente também é removida.
    Séries stale e fontes sintéticas são quarentenadas; não existe fallback
    silencioso para o motor de pesquisa.
    """
    if frame is None or frame.empty:
        quality = DataQuality(asset, timeframe, source, "quarantined", "empty_series",
                              0, None, None, None,
                              datetime.now(timezone.utc).isoformat(), "empty", "empty")
        return quality, pd.DataFrame()

    clean = frame.copy()
    clean.index = pd.to_datetime(clean.index, errors="coerce")
    clean = clean[~clean.index.isna()].sort_index()
    if clean.empty:
        quality = DataQuality(asset, timeframe, source, "quarantined", "invalid_index",
                              0, None, None, None,
                              datetime.now(timezone.utc).isoformat(), "invalid", "invalid")
        return quality, clean

    if source.lower() == "synthetic":
        quality = DataQuality(asset, timeframe, source, "quarantined", "synthetic_source",
                              len(clean), str(clean.index.min().date()), str(clean.index.max().date()), None,
                              datetime.now(timezone.utc).isoformat(), _hash_frame(clean), "unavailable")
        return quality, clean.iloc[0:0]

    last_date = clean.index.max().date()
    first_date = clean.index.min().date()
    market = _market(asset)
    partial = False
    if timeframe == "1d":
        if market == "stocks":
            settled = market_clock.last_settled_session()
            partial = last_date > settled
            # Em um dia de pregão aberto, um timestamp de hoje ainda é parcial.
            safe = clean.loc[clean.index.date <= settled]
            max_lag = config.data_max_stale_equity_sessions
            stale_cut = settled
            for _ in range(max_lag):
                stale_cut -= timedelta(days=1)
                while not market_clock.is_trading_day(stale_cut):
                    stale_cut -= timedelta(days=1)
        else:
            utc_today = datetime.now(timezone.utc).date()
            partial = last_date >= utc_today
            safe = clean.loc[clean.index.date < utc_today] if partial else clean
            stale_cut = utc_today - pd.Timedelta(days=config.data_max_stale_crypto_days)
    else:
        safe = clean
        stale_cut = last_date

    if safe.empty:
        quality = DataQuality(asset, timeframe, source, "partial", "no_final_bar_yet",
                              len(clean), str(first_date), str(last_date), None,
                              datetime.now(timezone.utc).isoformat(), _hash_frame(clean), "empty")
        return quality, safe

    final_date = safe.index.max().date()
    if final_date < stale_cut:
        quality = DataQuality(asset, timeframe, source, "quarantined",
                              f"stale_final_bar:{final_date}", len(clean), str(first_date),
                              str(last_date), str(final_date), datetime.now(timezone.utc).isoformat(),
                              _hash_frame(clean), "unavailable")
        return quality, safe.iloc[0:0]

    status = "partial" if partial else "approved"
    reason = "current_bar_excluded" if partial else "final_and_fresh"
    quality = DataQuality(asset, timeframe, source, status, reason, len(clean),
                          str(first_date), str(last_date), str(final_date),
                          datetime.now(timezone.utc).isoformat(), _hash_frame(clean),
                          _hash_frame(safe))
    return quality, safe


def manifest_path(lake: Path, asset: str, timeframe: str = "1d") -> Path:
    return lake / "provenance" / f"{_safe_name(asset, timeframe)}.json"


def write_manifest(lake: Path, quality: DataQuality) -> Path:
    """Escreve proveniência via replace atômico para não produzir meia verdade."""
    target = manifest_path(lake, quality.asset, quality.timeframe)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(quality.to_dict(), fh, ensure_ascii=False, indent=2)
        last_error = None
        for attempt in range(6):
            try:
                os.replace(temp_name, target)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.10 * (attempt + 1))
        if last_error is not None:
            raise last_error
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target


def read_manifest(lake: Path, asset: str, timeframe: str = "1d") -> Dict[str, Any] | None:
    path = manifest_path(lake, asset, timeframe)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
