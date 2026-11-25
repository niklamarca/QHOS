"""Lake local, imutavel e particionado para eventos de mercado."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json
import os
import re
import shutil
import tempfile
import time

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads
import pyarrow.parquet as pq

from core.config import config
from .event_contracts import (
    EventBudgetExceeded,
    EventContractError,
    EventDataPartial,
    EventIngestResult,
    EventQuality,
    EventRequest,
    ProviderPayload,
    utc_now_iso,
)


def _safe_component(value: Any) -> str:
    text = str(value).strip()
    text = re.sub(r"[^A-Za-z0-9._=-]+", "_", text)
    return text[:160] or "unknown"


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                     dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        last_error: PermissionError | None = None
        for attempt in range(6):
            try:
                os.replace(temp_name, path)
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


class EventLake:
    """Bronze DBN + eventos Parquet sem sobrescrever arquivos existentes."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or (config.data_lake_dir / "events"))
        self.native_root = Path(config.data_lake_dir / "events_native") if root is None else self.root.parent / "events_native"
        self.catalog_root = self.root / "_catalog"
        self.lock_root = self.root / "_locks"
        self.fetch_lock_root = self.root / "_fetch_locks"
        self.staging_root = self.root.parent / "events_staging"
        for directory in (self.root, self.native_root, self.catalog_root, self.lock_root,
                          self.fetch_lock_root, self.staging_root):
            directory.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _request_lock(self, request_id: str, timeout: float = 30.0,
                      root: Path | None = None):
        lock = (root or self.lock_root) / f"{request_id}.lock"
        deadline = time.monotonic() + timeout
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}\n{time.time()}".encode("ascii"))
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > 3600:
                        lock.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"request lock ocupado: {request_id}")
                time.sleep(0.10)
        try:
            yield
        finally:
            try:
                lock.unlink()
            except FileNotFoundError:
                pass

    def _ledger_path(self, request_id: str) -> Path:
        return self.catalog_root / "requests" / f"{request_id}.json"

    def _acquisition_path(self, request_id: str) -> Path:
        return self.catalog_root / "acquisitions" / f"{request_id}.json"

    @contextmanager
    def acquisition_guard(self, request_id: str, timeout: float = 30.0):
        """Serializa a etapa paga separadamente do commit local."""
        with self._request_lock(request_id, timeout=timeout, root=self.fetch_lock_root):
            yield

    def acquisition_state(self, request_id: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._acquisition_path(request_id).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return None

    def mark_acquisition(self, request: EventRequest, status: str, **details: Any) -> None:
        previous = self.acquisition_state(request.request_id) or {}
        _atomic_json(self._acquisition_path(request.request_id), {
            **previous,
            "format": "qhos-event-acquisition-v1",
            "request_id": request.request_id,
            "request": request.canonical(),
            "status": str(status),
            "updated_at": utc_now_iso(),
            **details,
        })

    def staged_native_path(self, request: EventRequest) -> Path:
        directory = self.staging_root / f"request={request.request_id}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{request.request_id}.dbn.zst"

    def storage_capacity(self) -> dict[str, Any]:
        roots = (self.root, self.native_root, self.root.parent / "events_silver",
                 self.root.parent / "events_reference", self.staging_root)
        seen: set[Path] = set()
        used = 0
        for root in roots:
            resolved = root.resolve()
            if resolved in seen or not root.exists():
                continue
            seen.add(resolved)
            used += sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        usage = shutil.disk_usage(self.root)
        return {
            "used_bytes": used,
            "used_gb": round(used / (1024 ** 3), 6),
            "free_gb": round(usage.free / (1024 ** 3), 3),
            "max_storage_gb": float(getattr(config, "event_plane_max_storage_gb", 200.0)),
            "min_free_gb": float(getattr(config, "event_plane_min_free_gb", 50.0)),
        }

    def require_capacity(self) -> None:
        capacity = self.storage_capacity()
        if capacity["used_gb"] >= capacity["max_storage_gb"]:
            raise EventContractError(
                f"event lake atingiu teto de {capacity['max_storage_gb']:.1f} GB"
            )
        if capacity["free_gb"] <= capacity["min_free_gb"]:
            raise EventContractError(
                f"disco livre {capacity['free_gb']:.1f} GB abaixo da reserva "
                f"{capacity['min_free_gb']:.1f} GB"
            )

    def estimated_spend_today(self) -> float:
        today = pd.Timestamp.now(tz="UTC").date()
        total = 0.0
        for path in (self.catalog_root / "acquisitions").glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                stamp = pd.Timestamp(row.get("updated_at"))
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("UTC")
                if stamp.tz_convert("UTC").date() != today:
                    continue
                if row.get("status") in {
                    "FETCHING", "DOWNLOADED", "COMMITTED", "QUARANTINED",
                    "RECOVERY_REQUIRED",
                }:
                    total += float(row.get("estimated_cost_usd") or 0.0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return total

    def require_daily_budget(self, next_estimated_cost: float) -> None:
        spent = self.estimated_spend_today()
        cap = float(getattr(config, "databento_daily_spend_cap_usd", 1.0))
        if spent + float(next_estimated_cost) > cap + 1e-12:
            raise EventBudgetExceeded(
                f"teto diario Databento: estimado US${spent:.4f} + "
                f"pedido US${next_estimated_cost:.4f} > US${cap:.4f}"
            )

    def committed_result(self, request_id: str) -> EventIngestResult | None:
        """Retorna um commit anterior sem consultar o provedor novamente."""
        path = self._ledger_path(request_id)
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("status") != "COMMITTED":
                return None
            return EventIngestResult(
                request_id=str(previous["request_id"]),
                status="COMMITTED",
                rows=int(previous["rows"]),
                partitions=tuple(previous["partitions"]),
                quality=EventQuality(**previous["quality"]),
                native_path=previous.get("native_path"),
                native_sha256=previous.get("native_sha256"),
                estimated_cost_usd=previous.get("estimated_cost_usd"),
                committed_at=previous["committed_at"],
                idempotent_replay=True,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _checkpoint_path(self, request: EventRequest, symbol: str) -> Path:
        return (self.catalog_root / "checkpoints" /
                f"provider={_safe_component(request.provider)}" /
                f"dataset={_safe_component(request.dataset)}" /
                f"schema={_safe_component(request.schema)}" /
                f"symbol={_safe_component(symbol)}.json")

    def _committed_request_ids(self) -> set[str]:
        committed: set[str] = set()
        for path in (self.catalog_root / "requests").glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("status") == "COMMITTED" and payload.get("request_id"):
                    committed.add(str(payload["request_id"]))
            except (OSError, json.JSONDecodeError):
                continue
        return committed

    @staticmethod
    def _normalize_frame(payload: ProviderPayload) -> tuple[pd.DataFrame, EventQuality]:
        frame = payload.frame
        if frame is None or frame.empty:
            raise EventContractError("provedor retornou lote vazio")
        clean = frame.copy()
        rows_input = len(clean)

        if "ts_event" not in clean.columns:
            if isinstance(clean.index, pd.DatetimeIndex):
                clean.insert(0, "ts_event", clean.index)
            elif clean.index.name in {"ts_event", "ts_recv"}:
                clean.insert(0, "ts_event", clean.index)
            else:
                raise EventContractError("payload sem ts_event ou indice temporal")

        event_ts = pd.to_datetime(clean["ts_event"], utc=True, errors="coerce")
        null_ts = int(event_ts.isna().sum())
        if null_ts:
            raise EventContractError(f"payload contem {null_ts} ts_event invalidos")
        clean["ts_event"] = event_ts
        if "ts_recv" in clean.columns:
            clean["ts_recv"] = pd.to_datetime(clean["ts_recv"], utc=True, errors="coerce")

        symbol_column = next((name for name in ("symbol", "raw_symbol", "stype_in_symbol")
                              if name in clean.columns), None)
        if symbol_column:
            clean["symbol"] = clean[symbol_column].astype(str)
        elif len(payload.request.symbols) == 1:
            clean["symbol"] = payload.request.symbols[0]
        else:
            clean["symbol"] = "_mixed"

        clean = clean.reset_index(drop=True)
        before = len(clean)
        clean = clean.drop_duplicates(keep="first")
        duplicates = before - len(clean)
        order_column = "ts_recv" if "ts_recv" in clean.columns and clean["ts_recv"].notna().any() else "ts_event"
        regressions = int(clean.groupby("symbol", dropna=False)[order_column]
                          .apply(lambda values: (values.diff().dropna() < pd.Timedelta(0)).sum())
                          .sum())
        sort_columns = [name for name in ("ts_recv", "ts_event", "sequence") if name in clean.columns]
        if sort_columns:
            clean = clean.sort_values(sort_columns, kind="stable").reset_index(drop=True)

        observed_symbols = tuple(sorted(clean["symbol"].dropna().astype(str).unique()))
        missing_symbols = tuple(sorted(set(payload.request.symbols) - set(observed_symbols)))
        limit_reached = bool(payload.request.limit is not None and rows_input >= int(payload.request.limit))
        partial_reasons = []
        if missing_symbols:
            partial_reasons.append("requested_symbols_without_records")
        if limit_reached:
            partial_reasons.append("request_limit_reached")
        quality = EventQuality(
            status="PARTIAL" if partial_reasons else "APPROVED",
            reason="+".join(partial_reasons) if partial_reasons else "contract_valid",
            rows_input=rows_input,
            rows_output=len(clean),
            duplicate_rows_removed=duplicates,
            null_event_timestamps=null_ts,
            timestamp_regressions=regressions,
            first_event=clean["ts_event"].min().isoformat(),
            last_event=clean["ts_event"].max().isoformat(),
            symbols=observed_symbols,
            missing_symbols=missing_symbols,
            limit_reached=limit_reached,
        )
        return clean, quality

    @staticmethod
    def _content_hash(frame: pd.DataFrame) -> str:
        stable = frame.copy()
        for column in stable.columns:
            if pd.api.types.is_object_dtype(stable[column]):
                stable[column] = stable[column].map(lambda value: repr(value))
        raw = pd.util.hash_pandas_object(stable, index=False).values.tobytes()
        return sha256(raw).hexdigest()

    def _archive_native(self, payload: ProviderPayload) -> tuple[str | None, str | None]:
        source = payload.native_path
        if source is None:
            return None, None
        source = Path(source)
        if not source.is_file():
            raise EventContractError(f"payload nativo ausente: {source}")
        digest = _sha256_file(source)
        suffix = "".join(source.suffixes) or ".dbn"
        target_dir = (self.native_root /
                      f"provider={_safe_component(payload.request.provider)}" /
                      f"dataset={_safe_component(payload.request.dataset)}" /
                      f"schema={_safe_component(payload.request.schema)}" /
                      f"request={payload.request.request_id[:24]}")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"payload-{digest[:16]}{suffix}"
        if not target.exists():
            temp = target.with_name(target.name + ".tmp")
            shutil.copyfile(source, temp)
            if _sha256_file(temp) != digest:
                temp.unlink(missing_ok=True)
                raise EventContractError("checksum do arquivo nativo divergiu durante a copia")
            os.replace(temp, target)
        return str(target), digest

    def commit(self, payload: ProviderPayload) -> EventIngestResult:
        request = payload.request
        ledger_path = self._ledger_path(request.request_id)
        with self._request_lock(request.request_id):
            previous_result = self.committed_result(request.request_id)
            if previous_result is not None:
                return previous_result

            frame, quality = self._normalize_frame(payload)
            native_path, native_sha = self._archive_native(payload)
            if quality.status != "APPROVED" and not bool(
                payload.request.metadata.get("allow_partial", False)
            ):
                quarantined_at = utc_now_iso()
                _atomic_json(ledger_path, {
                    "format": "qhos-event-request-v1",
                    "request_id": request.request_id,
                    "request": request.canonical(),
                    "provider_metadata": dict(payload.provider_metadata),
                    "status": "QUARANTINED",
                    "rows": len(frame),
                    "partitions": [],
                    "quality": quality.to_dict(),
                    "native_path": native_path,
                    "native_sha256": native_sha,
                    "estimated_cost_usd": payload.estimated_cost_usd,
                    "committed_at": quarantined_at,
                })
                raise EventDataPartial(
                    f"lote parcial bloqueado: {quality.reason}; bronze preservado"
                )
            frame["_provider"] = request.provider
            frame["_dataset"] = request.dataset
            frame["_schema"] = request.schema
            frame["_request_id"] = request.request_id
            frame["_event_date"] = frame["ts_event"].dt.strftime("%Y-%m-%d")

            partitions: list[str] = []
            for (symbol, event_date), part in frame.groupby(["symbol", "_event_date"],
                                                            sort=True, dropna=False):
                part = part.reset_index(drop=True)
                digest = self._content_hash(part)
                first_ns = int(part["ts_event"].min().value)
                last_ns = int(part["ts_event"].max().value)
                directory = (self.root /
                             f"provider={_safe_component(request.provider)}" /
                             f"dataset={_safe_component(request.dataset)}" /
                             f"schema={_safe_component(request.schema)}" /
                             f"symbol={_safe_component(symbol)}" /
                             f"date={_safe_component(event_date)}")
                directory.mkdir(parents=True, exist_ok=True)
                target = directory / f"part-{first_ns}-{last_ns}-{digest[:16]}.parquet"
                if not target.exists():
                    temp = target.with_name(target.name + ".tmp")
                    pq.write_table(pa.Table.from_pandas(part, preserve_index=False), temp,
                                   compression="zstd")
                    os.replace(temp, target)
                partitions.append(str(target))

            committed_at = utc_now_iso()
            result = EventIngestResult(
                request_id=request.request_id,
                status="COMMITTED",
                rows=len(frame),
                partitions=tuple(partitions),
                quality=quality,
                native_path=native_path,
                native_sha256=native_sha,
                estimated_cost_usd=payload.estimated_cost_usd,
                committed_at=committed_at,
            )
            ledger = {
                **result.to_dict(),
                "request": request.canonical(),
                "provider_metadata": dict(payload.provider_metadata),
                "format": "qhos-event-request-v1",
            }
            # PREPARED nao e visivel aos leitores. Se o processo cair durante
            # checkpoints, o proximo commit repete de forma idempotente.
            _atomic_json(ledger_path, {**ledger, "status": "PREPARED"})
            for symbol in quality.symbols:
                _atomic_json(self._checkpoint_path(request, symbol), {
                    "format": "qhos-event-checkpoint-v1",
                    "provider": request.provider,
                    "dataset": request.dataset,
                    "schema": request.schema,
                    "symbol": symbol,
                    "last_event": quality.last_event,
                    "request_id": request.request_id,
                    "updated_at": committed_at,
                })
            _atomic_json(ledger_path, ledger)
            return result

    def read_events(
        self,
        *,
        provider: str,
        dataset: str,
        schema: str,
        symbol: str | None = None,
        start: Any | None = None,
        end: Any | None = None,
        deduplicate: bool = True,
    ) -> pd.DataFrame:
        base = (self.root / f"provider={_safe_component(provider)}" /
                f"dataset={_safe_component(dataset)}" /
                f"schema={_safe_component(schema)}")
        if symbol is not None:
            files: Iterable[Path] = (base / f"symbol={_safe_component(symbol)}").glob("date=*/part-*.parquet")
        else:
            files = base.glob("symbol=*/date=*/part-*.parquet")
        paths = sorted(files)
        if not paths:
            return pd.DataFrame()
        # ParquetFile le o arquivo fisico sem inferir as pastas ``symbol=`` e
        # ``date=`` como colunas Hive. ``read_table(path)`` tentaria mesclar a
        # coluna symbol interna com a particao e pode criar tipos incompatíveis.
        frames = [pq.ParquetFile(path).read().to_pandas() for path in paths]
        frame = pd.concat(frames, ignore_index=True)
        committed = self._committed_request_ids()
        if "_request_id" in frame.columns:
            frame = frame.loc[frame["_request_id"].astype(str).isin(committed)]
        if frame.empty:
            return frame
        frame["ts_event"] = pd.to_datetime(frame["ts_event"], utc=True, errors="coerce")
        if start is not None:
            start_ts = pd.Timestamp(start)
            start_ts = start_ts.tz_localize("UTC") if start_ts.tzinfo is None else start_ts.tz_convert("UTC")
            frame = frame.loc[frame["ts_event"] >= start_ts]
        if end is not None:
            end_ts = pd.Timestamp(end)
            end_ts = end_ts.tz_localize("UTC") if end_ts.tzinfo is None else end_ts.tz_convert("UTC")
            frame = frame.loc[frame["ts_event"] < end_ts]
        if deduplicate:
            business_columns = [column for column in frame.columns if not column.startswith("_")]
            frame = frame.drop_duplicates(subset=business_columns, keep="first")
        return frame.sort_values("ts_event", kind="stable").reset_index(drop=True)

    def iter_event_batches(
        self,
        *,
        provider: str,
        dataset: str,
        schema: str,
        symbol: str | None = None,
        start: Any | None = None,
        end: Any | None = None,
        batch_size: int = 65_536,
    ):
        """Scanner out-of-core para trades/quotes; nao concatena todo o lake."""
        base = (self.root / f"provider={_safe_component(provider)}" /
                f"dataset={_safe_component(dataset)}" /
                f"schema={_safe_component(schema)}")
        pattern = (f"symbol={_safe_component(symbol)}/date=*/part-*.parquet"
                   if symbol is not None else "symbol=*/date=*/part-*.parquet")
        paths = [str(path) for path in sorted(base.glob(pattern))]
        if not paths:
            return
        dataset_obj = pads.dataset(paths, format="parquet")
        predicate = None
        def _utc_scalar(value: Any):
            stamp = pd.Timestamp(value)
            stamp = (stamp.tz_localize("UTC") if stamp.tzinfo is None
                     else stamp.tz_convert("UTC"))
            return pa.scalar(stamp.to_pydatetime(), type=pa.timestamp("ns", tz="UTC"))
        if start is not None:
            start_scalar = _utc_scalar(start)
            predicate = pads.field("ts_event") >= start_scalar
        if end is not None:
            end_scalar = _utc_scalar(end)
            clause = pads.field("ts_event") < end_scalar
            predicate = clause if predicate is None else predicate & clause
        committed = self._committed_request_ids()
        for batch in dataset_obj.scanner(filter=predicate, batch_size=batch_size).to_batches():
            frame = batch.to_pandas()
            if "_request_id" in frame:
                frame = frame.loc[frame["_request_id"].astype(str).isin(committed)]
            if not frame.empty:
                yield frame

    def status(self) -> dict[str, Any]:
        ledgers = list((self.catalog_root / "requests").glob("*.json"))
        ledger_states: dict[str, int] = {}
        for path in ledgers:
            try:
                state = str(json.loads(path.read_text(encoding="utf-8")).get("status", "UNKNOWN"))
            except (OSError, json.JSONDecodeError):
                state = "CORRUPT"
            ledger_states[state] = ledger_states.get(state, 0) + 1
        parts = list(self.root.glob("provider=*/dataset=*/schema=*/symbol=*/date=*/part-*.parquet"))
        native = list(self.native_root.glob("provider=*/dataset=*/schema=*/request=*/*"))
        return {
            "format": "qhos-event-plane-v1",
            "enabled": bool(getattr(config, "event_plane_enabled", False)),
            "committed_requests": ledger_states.get("COMMITTED", 0),
            "prepared_requests": ledger_states.get("PREPARED", 0),
            "corrupt_ledgers": ledger_states.get("CORRUPT", 0),
            "parquet_parts": len(parts),
            "native_files": sum(path.is_file() for path in native),
            "root": str(self.root),
            "storage": self.storage_capacity(),
        }
