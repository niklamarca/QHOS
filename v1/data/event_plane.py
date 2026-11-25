"""Fachada operacional do data plane de eventos."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import os
from datetime import date, datetime, time, timedelta, timezone

import pandas as pd

from core.config import config
from .event_contracts import (
    EventBudgetExceeded,
    EventContractError,
    EventDataPartial,
    EventIngestResult,
    EventRecoveryRequired,
    EventRequest,
)
from .event_lake import EventLake
from .event_silver import EventReferenceStore, EventSilverBuilder
from .providers import DatabentoHistoricalProvider


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    last_error: PermissionError | None = None
    import time as _time
    for attempt in range(6):
        try:
            os.replace(temp, path)
            last_error = None
            break
        except PermissionError as exc:
            last_error = exc
            _time.sleep(0.10 * (attempt + 1))
    if last_error is not None:
        raise last_error


class EventPlane:
    """Coordena provider, gate financeiro e commit imutavel."""

    def __init__(self, lake: EventLake | None = None,
                 databento: DatabentoHistoricalProvider | None = None):
        self.lake = lake or EventLake()
        self.databento = databento or DatabentoHistoricalProvider()
        self.silver = EventSilverBuilder(self.lake.root.parent / "events_silver")
        self.reference = EventReferenceStore(self.lake.root.parent / "events_reference")

    def estimate_databento(self, request: EventRequest) -> float:
        return self.databento.estimate_cost(request)

    def ingest_databento(self, request: EventRequest) -> EventIngestResult:
        if not bool(getattr(config, "event_plane_enabled", False)):
            raise EventContractError(
                "event plane desativado; habilitacao exige teste shadow e chave configurada"
            )
        if request.provider != "databento":
            raise EventContractError("ingest_databento recebeu outro provider")
        with self.lake.acquisition_guard(request.request_id):
            existing = self.lake.committed_result(request.request_id)
            if existing is not None:
                self._ensure_silver(existing, request)
                return existing
            self.lake.require_capacity()
            staged_native = self.lake.staged_native_path(request)
            acquisition = self.lake.acquisition_state(request.request_id)
            recovery_native = staged_native
            if acquisition:
                for key in ("staged_native", "native_archive"):
                    candidate = acquisition.get(key)
                    if candidate and Path(candidate).is_file():
                        recovery_native = Path(candidate)
                        break
            if recovery_native.exists():
                estimated = (acquisition or {}).get("estimated_cost_usd")
                payload = self.databento.load_native(
                    request, recovery_native, estimated_cost_usd=estimated
                )
            else:
                if acquisition:
                    raise EventRecoveryRequired(
                        "compra anterior sem DBN final; repeticao automatica bloqueada "
                        f"({acquisition.get('status', 'UNKNOWN')})"
                    )
                estimated = self.databento.estimate_cost(request)
                request_cap = min(
                    float(self.databento.max_request_usd),
                    float(request.metadata.get("max_cost_usd", self.databento.max_request_usd)),
                )
                if estimated > request_cap:
                    raise EventBudgetExceeded(
                        f"custo estimado US${estimated:.4f} excede teto "
                        f"US${request_cap:.4f}"
                    )
                self.lake.require_daily_budget(estimated)
                self.lake.mark_acquisition(
                    request, "FETCHING", estimated_cost_usd=estimated,
                    note="nao repetir automaticamente se este processo cair",
                )
                try:
                    payload = self.databento.fetch(request, staged_native.parent)
                except Exception as exc:
                    self.lake.mark_acquisition(
                        request, "RECOVERY_REQUIRED",
                        estimated_cost_usd=estimated,
                        error_type=type(exc).__name__, reason=str(exc)[:500],
                    )
                    raise
                self.lake.mark_acquisition(
                    request, "DOWNLOADED", estimated_cost_usd=estimated,
                    staged_native=str(payload.native_path),
                    staged_bytes=Path(payload.native_path).stat().st_size,
                )
            try:
                result = self.lake.commit(payload)
            except EventDataPartial as exc:
                self.lake.mark_acquisition(
                    request, "QUARANTINED", reason=str(exc)[:500],
                    staged_native=str(staged_native),
                )
                raise
            self.lake.mark_acquisition(
                request, "COMMITTED", estimated_cost_usd=result.estimated_cost_usd,
                native_archive=result.native_path, committed_at=result.committed_at,
            )
            try:
                if staged_native.is_file() and str(staged_native) != str(result.native_path):
                    staged_native.unlink()
                    staged_native.parent.rmdir()
            except OSError:
                pass
            self._ensure_silver(result, request)
            return result

    def _ensure_silver(self, result: EventIngestResult,
                       request: EventRequest, force: bool = False) -> dict:
        if request.schema != "ohlcv-1m":
            return {"status": "NOT_APPLICABLE", "research_eligible": False}
        report_path = self.silver.catalog / "requests" / f"{request.request_id}.json"
        if report_path.exists():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                report = {"status": "CORRUPT", "research_eligible": False}
            if report.get("status") == "RECONCILED" and not force:
                return {**report, "global": self.silver.refresh_global_status()}
            # A fonte diaria pode chegar depois do bronze. Reavalia localmente,
            # sem consultar nem cobrar o provedor novamente.
        return self.silver.build(result, request)

    def _request_for_session(self, session: date, profile: str) -> EventRequest:
        start = datetime.combine(session, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        symbols = (
            tuple(config.data_assets_stocks)
            if bool(getattr(config, "databento_shadow_use_qhos_universe", True))
            else (str(config.databento_shadow_symbol),)
        )
        return EventRequest.create(
            provider="databento",
            dataset=str(config.databento_shadow_dataset),
            schema=str(config.databento_shadow_schema),
            symbols=symbols,
            start=start,
            end=end,
            limit=int(config.databento_shadow_limit),
            metadata={"profile": profile, "session": str(session),
                      "max_cost_usd": float(config.databento_bootstrap_max_usd)},
        )

    def shadow_request(self) -> EventRequest:
        """Probe de uma sessao encerrada; nunca usa o pregao corrente."""
        from core import market_clock
        session = market_clock.last_settled_session()
        return self._request_for_session(session, "qhos-bootstrap-shadow-v2")

    def _sync_reference_window(self, request: EventRequest) -> dict:
        previous = self.reference.status()
        target_start = (pd.Timestamp(request.start) - pd.Timedelta(days=45)).date()
        target_end = pd.Timestamp(request.end).date()
        if (previous.get("research_ready") and previous.get("start")
                and pd.Timestamp(previous["start"]).date() <= target_start
                and pd.Timestamp(previous.get("end")).date() >= target_end):
            try:
                age_hours = ((pd.Timestamp.now(tz="UTC")
                              - pd.Timestamp(previous["updated_at"])).total_seconds() / 3600)
            except (KeyError, TypeError, ValueError):
                age_hours = 999.0
            if age_hours < 20.0:
                return previous
        return self.reference.sync(
            self.databento, start=str(target_start), end=str(target_end),
            symbols=request.symbols,
        )

    def bootstrap_shadow(self) -> dict:
        """Doctor + custo + um commit real pequeno, idempotente por sessao."""
        request = self.shadow_request()
        existing = self.lake.committed_result(request.request_id)
        if existing is not None:
            silver = self._ensure_silver(existing, request)
            reference = self._sync_reference_window(request)
            silver = self._ensure_silver(existing, request, force=True)
            if (silver.get("status") != "RECONCILED"
                    or not silver.get("adjustments_applied")
                    or not reference.get("research_ready")):
                state = {
                    "status": "FAILED",
                    "reason": "commit bronze existe, mas silver/reference nao passou",
                    "result": existing.to_dict(), "silver": silver,
                    "reference": reference,
                }
                _atomic_json(self.lake.catalog_root / "doctor.json", state)
                raise EventContractError(state["reason"])
            state = {"status": "ALREADY_COMMITTED", "result": existing.to_dict(),
                     "silver": silver, "reference": reference}
            _atomic_json(self.lake.catalog_root / "doctor.json", state)
            return state
        try:
            doctor = self.databento.doctor(request.dataset, request.schema)
            estimated = self.databento.estimate_cost(request)
            bootstrap_cap = float(config.databento_bootstrap_max_usd)
            if estimated > bootstrap_cap:
                from .event_contracts import EventBudgetExceeded
                raise EventBudgetExceeded(
                    f"probe estimado US${estimated:.4f} excede teto bootstrap US${bootstrap_cap:.4f}"
                )
            result = self.ingest_databento(request)
            silver = self._ensure_silver(result, request)
            reference = self._sync_reference_window(request)
            silver = self._ensure_silver(result, request, force=True)
            if silver.get("status") == "QUARANTINED" or not silver.get("adjustments_applied"):
                raise EventContractError("silver reprovado na reconciliacao shadow")
            if not reference.get("research_ready"):
                raise EventContractError(
                    f"reference data nao pronta: {reference.get('status')}"
                )
            state = {
                "status": "SHADOW_PROBE_COMMITTED",
                "doctor": doctor,
                "estimated_cost_usd": estimated,
                "result": result.to_dict(),
                "silver": silver,
                "reference": reference,
            }
            _atomic_json(self.lake.catalog_root / "doctor.json", state)
            return state
        except Exception as exc:
            _atomic_json(self.lake.catalog_root / "doctor.json", {
                "status": "FAILED",
                "error_type": type(exc).__name__,
                "reason": str(exc)[:500],
                "request": request.canonical(),
            })
            raise

    def backfill_step(self) -> dict:
        """Preenche poucas sessoes por ciclo, retomavel e com teto por pedido."""
        if not bool(getattr(config, "databento_backfill_enabled", True)):
            return {"status": "DISABLED"}
        from core import market_clock
        last = market_clock.last_settled_session()
        wanted: list[date] = []
        cursor = last
        while len(wanted) < max(1, int(config.databento_backfill_sessions)):
            if market_clock.is_trading_day(cursor):
                wanted.append(cursor)
            cursor -= timedelta(days=1)
        missing = []
        for session in reversed(wanted):
            request = self._request_for_session(session, "qhos-backfill-v1")
            existing = self.lake.committed_result(request.request_id)
            if existing is None:
                missing.append((session, request))
            else:
                self._ensure_silver(existing, request)
        completed = []
        limit = max(1, int(config.databento_backfill_max_sessions_per_cycle))
        for session, request in missing[:limit]:
            result = self.ingest_databento(request)
            completed.append({"session": str(session), "request_id": result.request_id,
                              "rows": result.rows})
        return {
            "status": "COMPLETE" if len(missing) <= limit else "IN_PROGRESS",
            "target_sessions": len(wanted),
            "remaining_before_cycle": len(missing),
            "completed_this_cycle": completed,
            "silver": self.silver.status(),
        }

    def status(self) -> dict:
        state = self.lake.status()
        package_installed = importlib.util.find_spec("databento") is not None
        if not package_installed:
            readiness = "DEPENDENCY_MISSING"
        elif not self.databento.configured:
            readiness = "WAITING_FOR_KEY"
        elif not bool(getattr(config, "event_plane_enabled", False)):
            readiness = "READY_DISABLED"
        else:
            readiness = "SHADOW_ENABLED"
        state.update({
            "databento_configured": self.databento.configured,
            "databento_package_installed": package_installed,
            "readiness": readiness,
            "max_request_usd": self.databento.max_request_usd,
            "max_records_per_request": self.databento.max_records,
            "automatic_ingestion": True,
            "automatic_research_consumers": 0,
            "research_isolation": True,
            "silver": self.silver.status(),
            "reference": self.reference.status(),
            "historical_profile_ready": self.silver.status().get("status")
            in {"READY_FOR_PROMOTION"},
            "research_eligible": bool(self.silver.status().get("research_eligible")),
            "live_mbo_ready": False,
        })
        doctor_path = self.lake.catalog_root / "doctor.json"
        try:
            state["last_doctor"] = json.loads(doctor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state["last_doctor"] = None
        return state


__all__ = ["EventPlane", "EventRequest"]
