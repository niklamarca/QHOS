"""Adaptador historico Databento, carregado apenas quando solicitado."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import os

from core.config import config
from data.event_contracts import (
    EventBudgetExceeded,
    EventContractError,
    EventRequest,
    ProviderPayload,
)


class DatabentoHistoricalProvider:
    """Converte uma requisicao limitada em DBN nativo + DataFrame.

    A dependencia e o cliente sao lazy. Assim, manter a camada desativada nao
    muda o boot atual e uma chave ausente nunca produz fallback para outra
    fonte com o rotulo Databento.
    """

    name = "databento"

    def __init__(self, api_key: str | None = None,
                 max_request_usd: float | None = None,
                 max_records: int | None = None,
                 client: Any | None = None):
        self.api_key = api_key or getattr(config, "databento_api_key", "") or os.getenv("DATABENTO_API_KEY", "")
        self.max_request_usd = float(
            max_request_usd if max_request_usd is not None
            else getattr(config, "databento_max_request_usd", 5.0)
        )
        self.max_records = int(
            max_records if max_records is not None
            else getattr(config, "event_plane_max_records_per_request", 1_000_000)
        )
        self._client = client
        self._db_module: Any | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key and str(self.api_key).startswith("db-"))

    def _load_client(self):
        if self._client is not None:
            return self._client
        if not self.configured:
            raise EventContractError("DATABENTO_API_KEY ausente ou invalida")
        try:
            import databento as db
        except ImportError as exc:
            raise EventContractError(
                "pacote databento nao instalado; instale as dependencias do projeto"
            ) from exc
        self._db_module = db
        self._client = db.Historical(self.api_key)
        return self._client

    @staticmethod
    def _kwargs(request: EventRequest) -> dict[str, Any]:
        if request.provider != "databento":
            raise EventContractError(f"provider incompativel: {request.provider}")
        kwargs: dict[str, Any] = {
            "dataset": request.dataset,
            "schema": request.schema,
            "symbols": list(request.symbols),
            "start": request.start,
            "end": request.end,
            "stype_in": request.stype_in,
        }
        if request.limit is not None:
            kwargs["limit"] = int(request.limit)
        return kwargs

    def estimate_cost(self, request: EventRequest) -> float:
        client = self._load_client()
        cost = float(client.metadata.get_cost(**self._kwargs(request)))
        if cost < 0:
            raise EventContractError("Databento retornou custo negativo")
        return cost

    def doctor(self, dataset: str, schema: str) -> dict[str, Any]:
        """Confirma autenticacao e compatibilidade sem baixar eventos."""
        client = self._load_client()
        datasets = list(client.metadata.list_datasets())
        if dataset not in datasets:
            raise EventContractError(f"dataset indisponivel para a API: {dataset}")
        schemas = list(client.metadata.list_schemas(dataset=dataset))
        if schema not in schemas:
            raise EventContractError(f"schema {schema} indisponivel em {dataset}")
        return {
            "authenticated": True,
            "dataset": dataset,
            "dataset_available": True,
            "schema": schema,
            "schema_available": True,
            "available_schema_count": len(schemas),
        }

    def fetch(self, request: EventRequest, output_dir: Path) -> ProviderPayload:
        """Baixa um intervalo somente apos o gate de custo."""
        client = self._load_client()
        if request.limit is None:
            raise EventContractError(
                "fase shadow exige limit explicito; leitor out-of-core ainda nao promovido"
            )
        if int(request.limit) > self.max_records:
            raise EventContractError(
                f"limit={request.limit} excede o teto em memoria {self.max_records}"
            )
        cost = self.estimate_cost(request)
        if cost > self.max_request_usd:
            raise EventBudgetExceeded(
                f"custo estimado US${cost:.4f} excede o limite "
                f"US${self.max_request_usd:.4f}"
            )
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        native_path = output_dir / f"{request.request_id}.dbn.zst"
        partial_path = native_path.with_name(native_path.name + ".partial")
        if native_path.exists():
            return self.load_native(request, native_path, estimated_cost_usd=cost)
        if partial_path.exists():
            raise EventContractError(
                f"download parcial encontrado: {partial_path}; repeticao automatica bloqueada"
            )
        store = client.timeseries.get_range(**self._kwargs(request))
        store.to_file(partial_path, mode="x", compression="zstd")
        os.replace(partial_path, native_path)

        return self.load_native(request, native_path, estimated_cost_usd=cost)

    def load_native(self, request: EventRequest, native_path: Path,
                    estimated_cost_usd: float | None = None) -> ProviderPayload:
        """Reconstrói o payload a partir do DBN local sem nova chamada paga."""
        native_path = Path(native_path)
        if not native_path.is_file() or native_path.stat().st_size <= 0:
            raise EventContractError(f"DBN nativo ausente ou vazio: {native_path}")

        # Ler o arquivo acabado, em vez de depender do estado interno do stream,
        # garante que bronze e silver nasceram dos mesmos bytes.
        db = self._db_module
        if db is None:
            try:
                import databento as db
            except ImportError as exc:
                raise EventContractError("pacote databento indisponivel") from exc
        # Preco fixed preserva o inteiro original em unidades de 1e-9. O DBN
        # continua sendo a verdade; nao introduzimos float antes da camada de
        # features explicitamente escolher sua escala.
        frame = db.DBNStore.from_file(native_path).to_df(price_type="fixed")
        return ProviderPayload(
            request=request,
            frame=frame,
            native_path=native_path,
            estimated_cost_usd=estimated_cost_usd,
            provider_metadata={
                "provider": "databento",
                "encoding": "dbn",
                "compression": "zstd",
                "price_encoding": "fixed_1e-9",
                "end_semantics": "exclusive",
            },
        )

    def fetch_reference(self, *, start: Any, end: Any,
                        symbols: tuple[str, ...]) -> dict[str, Any]:
        """Busca ajustes e security master PIT para a mesma janela do backfill."""
        if not self.configured:
            raise EventContractError("DATABENTO_API_KEY ausente ou invalida")
        try:
            import databento as db
        except ImportError as exc:
            raise EventContractError("pacote databento indisponivel") from exc
        client = db.Reference(self.api_key)
        kwargs = {
            "start": start,
            "end": end,
            "symbols": list(symbols),
            "stype_in": "raw_symbol",
        }
        adjustments = client.adjustment_factors.get_range(**kwargs)
        security_master_pit = client.security_master.get_range(
            **kwargs, index="ts_effective"
        )
        security_master_latest = client.security_master.get_last(
            symbols=list(symbols), stype_in="raw_symbol", countries=["US"]
        )
        return {
            "adjustment_factors": adjustments,
            "security_master_pit": security_master_pit,
            "security_master_latest": security_master_latest,
        }
