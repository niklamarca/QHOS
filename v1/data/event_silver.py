"""Camada silver e reconciliacao do Event Plane.

Bronze pago nunca entra diretamente em features. OHLCV intradiario e agregado
em barras regulares, comparado com a fonte diaria atual e publicado com um gate
explicito. A fonte de pesquisa nao e trocada por este modulo.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import tempfile

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from core.config import config
from .event_contracts import EventContractError, EventIngestResult, EventRequest, utc_now_iso


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True,
                      default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), temporary,
                   compression="zstd")
    os.replace(temporary, path)


class EventReferenceStore:
    """Versoes imutaveis de adjustment factors e security master PIT."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or (config.data_lake_dir / "events_reference"))
        self.catalog = self.root / "_catalog" / "status.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def status(self) -> dict[str, Any]:
        try:
            return json.loads(self.catalog.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "NOT_SYNCED", "research_ready": False}

    def sync(self, provider: Any, *, start: Any, end: Any,
             symbols: tuple[str, ...]) -> dict[str, Any]:
        if not hasattr(provider, "fetch_reference"):
            state = {"status": "PROVIDER_CAPABILITY_MISSING", "research_ready": False,
                     "updated_at": utc_now_iso()}
            _atomic_json(self.catalog, state)
            return state
        try:
            frames = provider.fetch_reference(start=start, end=end, symbols=symbols)
            files: dict[str, str] = {}
            rows: dict[str, int] = {}
            for name in ("adjustment_factors", "security_master_pit",
                         "security_master_latest"):
                frame = frames.get(name)
                if frame is None:
                    frame = pd.DataFrame()
                frame = frame.reset_index()
                rows[name] = len(frame)
                canonical = frame.to_json(orient="split", date_format="iso", default_handler=str)
                digest = sha256(canonical.encode("utf-8")).hexdigest()
                path = self.root / name / f"part-{digest[:24]}.parquet"
                if not path.exists():
                    _atomic_parquet(frame, path)
                files[name] = str(path)
            # Ausencia de fator/PIT na janela e valida: pode nao ter ocorrido
            # mudanca. O snapshot latest, por outro lado, precisa mapear o
            # universo atual.
            latest = frames.get("security_master_latest")
            latest = latest.reset_index() if latest is not None else pd.DataFrame()
            observed: set[str] = set()
            for column in ("symbol", "raw_symbol", "nasdaq_symbol"):
                if column in latest:
                    observed.update(latest[column].dropna().astype(str))
            requested = set(map(str, symbols))
            symbol_coverage = (len(requested & observed) / max(len(requested), 1)
                               if observed else 0.0)
            ready = symbol_coverage >= float(
                config.databento_reconciliation_min_symbol_coverage
            )
            state = {
                "status": "SYNCED" if ready else "INCOMPLETE",
                "research_ready": ready,
                "start": str(start),
                "end": str(end),
                "symbols_requested": len(symbols),
                "rows": rows,
                "symbol_coverage": symbol_coverage,
                "files": files,
                "updated_at": utc_now_iso(),
            }
            _atomic_json(self.catalog, state)
            return state
        except Exception as exc:
            state = {"status": "FAILED", "research_ready": False,
                     "error_type": type(exc).__name__, "reason": str(exc)[:500],
                     "updated_at": utc_now_iso()}
            _atomic_json(self.catalog, state)
            return state


class EventSilverBuilder:
    """Agrega OHLCV-1m e mede concordancia antes de qualquer consumo."""

    PRICE_COLUMNS = ("open", "high", "low", "close")

    def __init__(self, root: Path | None = None):
        self.root = Path(root or (config.data_lake_dir / "events_silver"))
        self.catalog = self.root / "_catalog"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _daily_from_parts(result: EventIngestResult) -> pd.DataFrame:
        frames = [pq.ParquetFile(path).read().to_pandas() for path in result.partitions]
        if not frames:
            raise EventContractError("commit sem particoes para camada silver")
        frame = pd.concat(frames, ignore_index=True)
        required = {"ts_event", "symbol", "open", "high", "low", "close", "volume"}
        missing = required - set(frame.columns)
        if missing:
            raise EventContractError(f"OHLCV sem colunas silver: {sorted(missing)}")
        stamps = pd.to_datetime(frame["ts_event"], utc=True, errors="coerce")
        local = stamps.dt.tz_convert("America/New_York")
        minutes = local.dt.hour * 60 + local.dt.minute
        regular = frame.loc[(minutes >= 9 * 60 + 30) & (minutes < 16 * 60)].copy()
        if regular.empty:
            raise EventContractError("OHLCV sem barras da sessao regular")
        regular["session"] = local.loc[regular.index].dt.date.astype(str)
        for column in EventSilverBuilder.PRICE_COLUMNS:
            # O provider preserva fixed-price em unidades de 1e-9.
            regular[column] = pd.to_numeric(regular[column], errors="coerce") * 1e-9
        regular["volume"] = pd.to_numeric(regular["volume"], errors="coerce")
        regular = regular.dropna(subset=[*EventSilverBuilder.PRICE_COLUMNS, "volume"])
        daily = (regular.sort_values("ts_event")
                 .groupby(["symbol", "session"], as_index=False)
                 .agg(open=("open", "first"), high=("high", "max"),
                      low=("low", "min"), close=("close", "last"),
                      volume=("volume", "sum"), minute_bars=("close", "size")))
        return daily

    @staticmethod
    def _reference_close(symbol: str, session: str) -> float | None:
        safe = symbol.replace("/", "_").replace(":", "_").replace("^", "")
        path = Path(config.data_lake_dir) / "raw" / f"{safe}_1d.parquet"
        try:
            frame = pd.read_parquet(path)
            if "Date" in frame:
                frame = frame.set_index("Date")
            elif "date" in frame:
                frame = frame.set_index("date")
            frame.index = pd.to_datetime(frame.index).normalize()
            row = frame.loc[pd.Timestamp(session)]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            return float(row["close"])
        except (OSError, KeyError, TypeError, ValueError, IndexError):
            return None

    def _apply_adjustments(self, daily: pd.DataFrame) -> tuple[pd.DataFrame, bool, str]:
        reference = EventReferenceStore(self.root.parent / "events_reference").status()
        if not reference.get("research_ready"):
            return daily, False, "reference_not_ready"
        path = (reference.get("files") or {}).get("adjustment_factors")
        if not path or not Path(path).is_file():
            return daily, False, "adjustment_file_missing"
        factors = pd.read_parquet(path)
        if factors.empty:
            return daily, True, "no_adjustments_in_window"
        symbol_column = next((name for name in ("symbol", "raw_symbol", "nasdaq_symbol")
                              if name in factors), None)
        if symbol_column is None or "factor" not in factors or "ex_date" not in factors:
            return daily, False, "adjustment_contract_incomplete"
        factors = factors.copy()
        factors["_symbol"] = factors[symbol_column].astype(str)
        factors["_ex_date"] = pd.to_datetime(factors["ex_date"], errors="coerce").dt.date
        factors["_factor"] = pd.to_numeric(factors["factor"], errors="coerce")
        factors = factors.dropna(subset=["_ex_date", "_factor"])
        order = next((name for name in ("ts_record", "ts_created", "updated_at")
                      if name in factors), None)
        if order:
            factors = factors.sort_values(order, kind="stable")
        identity = [name for name in ("event_id", "ex_date") if name in factors]
        if identity and "option" in factors:
            choices = factors.groupby(identity, dropna=False)["option"].nunique(dropna=True)
            if (choices > 1).any():
                return daily, False, "ambiguous_adjustment_options"
        if identity:
            factors = factors.groupby(identity, as_index=False, dropna=False).tail(1)
        if "status" in factors:
            statuses = set(factors["status"].dropna().astype(str).str.upper())
            if "P" in statuses:
                return daily, False, "pending_adjustment_factor"
            factors = factors.loc[factors["status"].astype(str).str.upper() == "A"]
        if ((factors["_factor"] <= 0) | ~factors["_factor"].map(pd.notna)).any():
            return daily, False, "invalid_adjustment_factor"
        adjusted = daily.copy()
        for index, row in adjusted.iterrows():
            session = pd.Timestamp(row["session"]).date()
            applicable = factors.loc[
                (factors["_symbol"] == str(row["symbol"]))
                & (factors["_ex_date"] > session)
            ]
            multiplier = float(applicable["_factor"].prod()) if len(applicable) else 1.0
            for column in self.PRICE_COLUMNS:
                adjusted.at[index, column] = float(row[column]) * multiplier
        return adjusted, True, "adjustments_applied"

    def build(self, result: EventIngestResult, request: EventRequest) -> dict[str, Any]:
        if request.schema != "ohlcv-1m":
            return {"status": "NOT_APPLICABLE", "schema": request.schema,
                    "research_eligible": False}
        daily = self._daily_from_parts(result)
        daily, adjustments_applied, adjustment_reason = self._apply_adjustments(daily)
        rows = []
        for row in daily.itertuples(index=False):
            reference = self._reference_close(str(row.symbol), str(row.session))
            error = (abs(float(row.close) - reference) / max(abs(reference), 1e-12)
                     if reference is not None else None)
            rows.append({"symbol": str(row.symbol), "session": str(row.session),
                         "databento_close": float(row.close),
                         "reference_close": reference, "close_relative_error": error})
        reconciliation = pd.DataFrame(rows)
        matched = reconciliation["close_relative_error"].notna()
        compared = int(matched.sum())
        passed = int((reconciliation.loc[matched, "close_relative_error"] <=
                      float(config.databento_reconciliation_max_close_error)).sum())
        coverage = compared / max(len(request.symbols), 1)
        pass_rate = passed / max(compared, 1)
        expected_minutes = []
        for session in daily["session"]:
            from core.market_clock import _close_time
            close = _close_time(pd.Timestamp(session).date())
            expected_minutes.append((close.hour * 60 + close.minute) - (9 * 60 + 30))
        daily["expected_minute_bars"] = expected_minutes
        daily["minute_coverage"] = daily["minute_bars"] / daily["expected_minute_bars"]
        min_minute_coverage = float(daily["minute_coverage"].min())
        approved = (
            coverage >= float(config.databento_reconciliation_min_symbol_coverage)
            and compared > 0 and pass_rate >= 0.95
            and min_minute_coverage >= float(config.databento_min_regular_session_coverage)
        )
        for row in daily.itertuples(index=False):
            directory = (self.root / "provider=databento" /
                         f"dataset={request.dataset}" / "schema=ohlcv-1d" /
                         f"symbol={row.symbol}" / f"date={row.session}")
            path = directory / f"part-{request.request_id[:24]}.parquet"
            if not path.exists():
                _atomic_parquet(pd.DataFrame([row._asdict()]), path)
        state = {
            "format": "qhos-event-silver-v1",
            "request_id": request.request_id,
            "status": "RECONCILED" if approved else "QUARANTINED",
            "research_eligible": False,
            "reason": ("shadow_only_until_global_gate" if approved
                       else "reconciliation_or_session_coverage_failed"),
            "sessions": sorted(daily["session"].unique().tolist()),
            "symbols_requested": len(request.symbols),
            "symbols_compared": compared,
            "symbol_coverage": coverage,
            "close_pass_rate": pass_rate,
            "minimum_minute_coverage": min_minute_coverage,
            "adjustments_applied": adjustments_applied,
            "adjustment_reason": adjustment_reason,
            "max_close_error": (float(reconciliation["close_relative_error"].max())
                                if compared else None),
            "created_at": utc_now_iso(),
        }
        _atomic_json(self.catalog / "requests" / f"{request.request_id}.json", state)
        return {**state, "global": self.refresh_global_status()}

    def refresh_global_status(self) -> dict[str, Any]:
        reports = []
        for path in (self.catalog / "requests").glob("*.json"):
            try:
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        sessions = sorted({session for report in reports
                           for session in report.get("sessions", [])})
        reconciled = sum(report.get("status") == "RECONCILED" for report in reports)
        reference = EventReferenceStore(self.root.parent / "events_reference").status()
        enough = len(sessions) >= int(config.databento_reconciliation_min_sessions)
        all_good = bool(reports) and reconciled == len(reports)
        all_adjusted = bool(reports) and all(
            report.get("adjustments_applied") for report in reports
        )
        gate = (enough and all_good and all_adjusted
                and bool(reference.get("research_ready")))
        state = {
            "status": "READY_FOR_PROMOTION" if gate else "ACCUMULATING_SHADOW",
            "research_eligible": bool(gate and config.databento_promote_to_research),
            "promotion_switch": bool(config.databento_promote_to_research),
            "sessions": len(sessions),
            "minimum_sessions": int(config.databento_reconciliation_min_sessions),
            "reconciled_requests": reconciled,
            "total_requests": len(reports),
            "all_adjustments_applied": all_adjusted,
            "reference_status": reference.get("status"),
            "updated_at": utc_now_iso(),
        }
        _atomic_json(self.catalog / "status.json", state)
        return state

    def status(self) -> dict[str, Any]:
        try:
            return json.loads((self.catalog / "status.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "NO_SILVER_DATA", "research_eligible": False,
                    "sessions": 0}


__all__ = ["EventReferenceStore", "EventSilverBuilder"]
