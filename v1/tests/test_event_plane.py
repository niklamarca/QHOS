from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import pytest

from data.event_contracts import (
    EventBudgetExceeded,
    EventContractError,
    EventDataPartial,
    EventRecoveryRequired,
    EventRequest,
    ProviderPayload,
)
from data.event_lake import EventLake
from data.event_plane import EventPlane
from data.providers.databento_provider import DatabentoHistoricalProvider
from core.config import config
from governance.epistemics import current_data_fingerprint


def _request() -> EventRequest:
    return EventRequest.create(
        provider="databento",
        dataset="TEST.DATA",
        schema="trades",
        symbols=["ESM6", "NQM6"],
        start="2026-08-17",
        end="2026-08-19",
        limit=1000,
    )


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_event": pd.to_datetime([
            "2026-08-17T14:30:00.000000001Z",
            "2026-08-17T14:30:00.000000001Z",  # duplicata exata
            "2026-08-18T14:30:00.000000002Z",
            "2026-08-18T14:30:00.000000003Z",
        ]),
        "publisher_id": [1, 1, 1, 1],
        "instrument_id": [101, 101, 101, 202],
        "sequence": [10, 10, 11, 12],
        "price": [6500.0, 6500.0, 6510.0, 24000.0],
        "size": [2, 2, 1, 3],
        "symbol": ["ESM6", "ESM6", "ESM6", "NQM6"],
    })


def test_request_is_bounded_utc_and_stable():
    first = _request()
    second = _request()
    assert first.end.endswith("+00:00")
    assert first.request_id == second.request_id
    with pytest.raises(EventContractError):
        EventRequest.create(provider="databento", dataset="X", schema="trades",
                            symbols="ES", start="2026-01-02", end="2026-01-01")


def test_operational_metadata_does_not_create_a_second_paid_request():
    shadow = EventRequest.create(
        provider="databento", dataset="TEST.DATA", schema="trades", symbols="ES",
        start="2026-08-17", end="2026-08-18", limit=100,
        metadata={"profile": "shadow"},
    )
    backfill = EventRequest.create(
        provider="databento", dataset="TEST.DATA", schema="trades", symbols="ES",
        start="2026-08-17", end="2026-08-18", limit=100,
        metadata={"profile": "backfill"},
    )
    assert shadow.request_id == backfill.request_id


def test_event_lake_preserves_native_partitions_and_is_idempotent(tmp_path):
    native = tmp_path / "source.dbn.zst"
    native.write_bytes(b"DBN-test-payload")
    payload = ProviderPayload(_request(), _frame(), native_path=native,
                              estimated_cost_usd=0.25)
    lake = EventLake(tmp_path / "events")

    result = lake.commit(payload)
    assert result.status == "COMMITTED"
    assert result.rows == 3
    assert result.quality.duplicate_rows_removed == 1
    assert len(result.partitions) == 3
    assert result.native_sha256
    assert Path(result.native_path).read_bytes() == native.read_bytes()

    replay = lake.commit(payload)
    assert replay.idempotent_replay is True
    assert replay.partitions == result.partitions
    assert len(list((tmp_path / "events").glob(
        "provider=*/dataset=*/schema=*/symbol=*/date=*/part-*.parquet"
    ))) == 3

    es = lake.read_events(provider="databento", dataset="TEST.DATA",
                          schema="trades", symbol="ESM6",
                          start="2026-08-17T00:00:00Z",
                          end="2026-08-19T00:00:00Z")
    assert len(es) == 2
    assert es["symbol"].unique().tolist() == ["ESM6"]
    checkpoints = list((tmp_path / "events" / "_catalog" / "checkpoints").rglob("*.json"))
    assert len(checkpoints) == 2

    # Um parquet fisico sem ledger COMMITTED nao pertence a visao legivel.
    orphan = es.copy()
    orphan["_request_id"] = "orphan-request"
    orphan_path = Path(result.partitions[0]).with_name("part-orphan.parquet")
    orphan.to_parquet(orphan_path, index=False)
    visible = lake.read_events(provider="databento", dataset="TEST.DATA",
                               schema="trades", symbol="ESM6")
    assert len(visible) == 2


def test_invalid_timestamp_fails_closed_without_commit(tmp_path):
    frame = pd.DataFrame({"price": [1.0], "symbol": ["ESM6"]})
    lake = EventLake(tmp_path / "events")
    with pytest.raises(EventContractError):
        lake.commit(ProviderPayload(_request(), frame))
    assert not list((tmp_path / "events").glob("provider=*"))


def test_partial_payload_is_quarantined_and_native_is_preserved(tmp_path):
    request = EventRequest.create(
        provider="databento", dataset="TEST.DATA", schema="trades",
        symbols=["ESM6", "NQM6"], start="2026-08-17", end="2026-08-18",
        limit=100,
    )
    native = tmp_path / "partial.dbn.zst"
    native.write_bytes(b"paid-native-partial")
    frame = _frame().loc[_frame()["symbol"] == "ESM6"].copy()
    lake = EventLake(tmp_path / "events")
    with pytest.raises(EventDataPartial, match="lote parcial bloqueado"):
        lake.commit(ProviderPayload(request, frame, native_path=native))
    ledger = next((tmp_path / "events" / "_catalog" / "requests").glob("*.json"))
    assert '"status": "QUARANTINED"' in ledger.read_text(encoding="utf-8")
    assert list((tmp_path / "events_native").rglob("*.zst"))
    assert not list((tmp_path / "events").glob("provider=*"))


class _RecoveryProvider:
    configured = True
    max_request_usd = 5.0
    max_records = 1_000_000

    def __init__(self):
        self.fetch_calls = 0
        self.load_calls = 0

    def estimate_cost(self, request):
        return 0.01

    def fetch(self, request, output_dir):
        self.fetch_calls += 1
        raise AssertionError("nao pode repetir compra")

    def load_native(self, request, native_path, estimated_cost_usd=None):
        self.load_calls += 1
        return ProviderPayload(request, _frame(), native_path=native_path,
                               estimated_cost_usd=estimated_cost_usd)


def test_completed_staging_resumes_without_second_download(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    monkeypatch.setattr(config, "event_plane_enabled", True)
    lake = EventLake()
    provider = _RecoveryProvider()
    plane = EventPlane(lake=lake, databento=provider)
    request = _request()
    lake.mark_acquisition(request, "FETCHING", estimated_cost_usd=0.01)
    lake.staged_native_path(request).write_bytes(b"completed-paid-dbn")
    result = plane.ingest_databento(request)
    assert result.status == "COMMITTED"
    assert provider.fetch_calls == 0
    assert provider.load_calls == 1


def test_ambiguous_purchase_without_final_file_blocks_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    monkeypatch.setattr(config, "event_plane_enabled", True)
    lake = EventLake()
    provider = _RecoveryProvider()
    plane = EventPlane(lake=lake, databento=provider)
    request = _request()
    lake.mark_acquisition(request, "RECOVERY_REQUIRED", estimated_cost_usd=0.01)
    with pytest.raises(EventRecoveryRequired, match="repeticao automatica bloqueada"):
        plane.ingest_databento(request)
    assert provider.fetch_calls == 0


def test_daily_spend_cap_blocks_next_request(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "databento_daily_spend_cap_usd", 0.05)
    lake = EventLake(tmp_path / "events")
    lake.mark_acquisition(_request(), "FETCHING", estimated_cost_usd=0.04)
    with pytest.raises(EventBudgetExceeded, match="teto diario"):
        lake.require_daily_budget(0.02)


def test_out_of_core_scanner_filters_committed_events(tmp_path):
    native = tmp_path / "scanner.dbn.zst"
    native.write_bytes(b"DBN-scanner")
    lake = EventLake(tmp_path / "events")
    lake.commit(ProviderPayload(_request(), _frame(), native_path=native))
    batches = list(lake.iter_event_batches(
        provider="databento", dataset="TEST.DATA", schema="trades",
        symbol="ESM6", start="2026-08-18", end="2026-08-19", batch_size=1,
    ))
    assert sum(len(batch) for batch in batches) == 1
    assert all(set(batch["symbol"]) == {"ESM6"} for batch in batches)


class _FakeMetadata:
    def __init__(self, cost: float):
        self.cost = cost

    def get_cost(self, **_kwargs):
        return self.cost


class _FakeTimeseries:
    def __init__(self):
        self.called = False

    def get_range(self, **_kwargs):
        self.called = True
        raise AssertionError("download pago nao deveria ocorrer")


class _FakeClient:
    def __init__(self, cost: float):
        self.metadata = _FakeMetadata(cost)
        self.timeseries = _FakeTimeseries()


def test_databento_budget_blocks_before_download(tmp_path):
    client = _FakeClient(cost=2.50)
    provider = DatabentoHistoricalProvider(client=client, max_request_usd=1.00)
    with pytest.raises(EventBudgetExceeded):
        provider.fetch(_request(), tmp_path)
    assert client.timeseries.called is False


def test_databento_unbounded_fetch_is_blocked_before_download(tmp_path):
    request = EventRequest.create(
        provider="databento", dataset="TEST.DATA", schema="mbo", symbols="ESM6",
        start="2026-08-17", end="2026-08-18",
    )
    client = _FakeClient(cost=0.01)
    provider = DatabentoHistoricalProvider(client=client, max_request_usd=1.00)
    with pytest.raises(EventContractError, match="limit explicito"):
        provider.fetch(request, tmp_path)
    assert client.timeseries.called is False


def test_concurrent_same_request_has_single_logical_commit(tmp_path):
    native = tmp_path / "source.dbn.zst"
    native.write_bytes(b"DBN-concurrent")
    payload = ProviderPayload(_request(), _frame(), native_path=native)
    lake = EventLake(tmp_path / "events")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _item: lake.commit(payload), range(2)))
    assert sum(result.idempotent_replay for result in results) == 1
    assert {result.request_id for result in results} == {_request().request_id}
    assert len(list((tmp_path / "events" / "_catalog" / "requests").glob("*.json"))) == 1


def test_event_bronze_does_not_silently_change_bar_research_lineage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    before = current_data_fingerprint()
    native = tmp_path / "source.dbn.zst"
    native.write_bytes(b"DBN-isolated")
    EventLake().commit(ProviderPayload(_request(), _frame(), native_path=native))
    after = current_data_fingerprint()
    assert after == before


class _ShadowProvider:
    configured = True
    max_request_usd = 5.0
    max_records = 1_000_000

    def __init__(self, fail_doctor: bool = False):
        self.fail_doctor = fail_doctor
        self.fetch_calls = 0

    def doctor(self, dataset, schema):
        if self.fail_doctor:
            raise EventContractError("entitlement ausente")
        return {"authenticated": True, "dataset": dataset, "schema": schema}

    def estimate_cost(self, _request):
        return 0.01

    def fetch(self, request, output_dir):
        self.fetch_calls += 1
        native = Path(output_dir) / "probe.dbn.zst"
        native.write_bytes(b"DBN-shadow-probe")
        stamp = pd.Timestamp(request.start) + pd.Timedelta(hours=14)
        frame = pd.DataFrame({
            "ts_event": [stamp], "publisher_id": [95], "instrument_id": [1],
            "open": [500_000_000_000], "high": [501_000_000_000],
            "low": [499_000_000_000], "close": [500_500_000_000],
            "volume": [100], "symbol": [request.symbols[0]],
        })
        return ProviderPayload(request, frame, native_path=native,
                               estimated_cost_usd=0.01)

    def fetch_reference(self, *, start, end, symbols):
        return {
            "adjustment_factors": pd.DataFrame(),
            "security_master_pit": pd.DataFrame(),
            "security_master_latest": pd.DataFrame({
                "ts_effective": [pd.Timestamp(start)],
                "raw_symbol": [symbols[0]],
                "security_id": ["test-security"],
            }),
        }


def test_shadow_bootstrap_is_end_to_end_and_does_not_pay_twice(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    monkeypatch.setattr(config, "event_plane_enabled", True)
    monkeypatch.setattr(config, "databento_shadow_use_qhos_universe", False)
    monkeypatch.setattr(config, "databento_min_regular_session_coverage", 0.001)
    provider = _ShadowProvider()
    plane = EventPlane(lake=EventLake(), databento=provider)
    session = plane.shadow_request().metadata["session"]
    raw = tmp_path / "raw"
    raw.mkdir()
    pd.DataFrame({"close": [500.5]}, index=pd.to_datetime([session])).to_parquet(
        raw / "SPY_1d.parquet"
    )
    first = plane.bootstrap_shadow()
    second = plane.bootstrap_shadow()
    assert first["status"] == "SHADOW_PROBE_COMMITTED"
    assert second["status"] == "ALREADY_COMMITTED"
    assert provider.fetch_calls == 1
    assert plane.status()["committed_requests"] == 1
    assert plane.status()["last_doctor"]["status"] == "ALREADY_COMMITTED"


def test_shadow_entitlement_failure_is_persisted_without_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    monkeypatch.setattr(config, "event_plane_enabled", True)
    provider = _ShadowProvider(fail_doctor=True)
    plane = EventPlane(lake=EventLake(), databento=provider)
    with pytest.raises(EventContractError, match="entitlement"):
        plane.bootstrap_shadow()
    status = plane.status()
    assert status["committed_requests"] == 0
    assert status["last_doctor"]["status"] == "FAILED"
    assert provider.fetch_calls == 0


class _SdkMetadata:
    def get_cost(self, **_kwargs):
        return 0.02

    def list_datasets(self):
        return ["TEST.DATA"]

    def list_schemas(self, dataset):
        assert dataset == "TEST.DATA"
        return ["trades", "ohlcv-1m"]


class _SdkStore:
    def to_file(self, path, mode, compression):
        assert mode == "x"
        assert compression == "zstd"
        Path(path).write_bytes(b"DBN-sdk-contract")


class _SdkTimeseries:
    def get_range(self, **kwargs):
        assert kwargs["end"] > kwargs["start"]
        assert kwargs["limit"] == 1000
        return _SdkStore()


class _SdkClient:
    metadata = _SdkMetadata()
    timeseries = _SdkTimeseries()


class _DecodedStore:
    price_type = None

    def to_df(self, price_type):
        type(self).price_type = price_type
        return _frame().iloc[:1].copy()


class _FakeDBNStore:
    @staticmethod
    def from_file(path):
        assert Path(path).read_bytes() == b"DBN-sdk-contract"
        return _DecodedStore()


class _FakeDbModule:
    DBNStore = _FakeDBNStore


def test_databento_sdk_contract_creates_native_and_fixed_price_frame(tmp_path):
    provider = DatabentoHistoricalProvider(client=_SdkClient(), max_request_usd=1.0)
    provider._db_module = _FakeDbModule
    doctor = provider.doctor("TEST.DATA", "trades")
    payload = provider.fetch(_request(), tmp_path)
    assert doctor["authenticated"] is True
    assert payload.native_path.read_bytes() == b"DBN-sdk-contract"
    assert _DecodedStore.price_type == "fixed"
    assert payload.provider_metadata["price_encoding"] == "fixed_1e-9"
