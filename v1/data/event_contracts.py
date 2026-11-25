"""Contratos do data plane de eventos do Quant HPC OS.

O plano de eventos e separado do lake de barras diarias. O payload nativo do
provedor e preservado como bronze; uma representacao Parquet, ainda fiel ao
evento, e criada para consultas locais. Nenhum consumidor de pesquisa recebe
esses dados automaticamente.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence
import json

import pandas as pd


class EventPlaneError(RuntimeError):
    """Erro base do data plane."""


class EventContractError(EventPlaneError):
    """A requisicao ou o payload viola o contrato de eventos."""


class EventBudgetExceeded(EventPlaneError):
    """O custo estimado ultrapassa o limite local autorizado."""


class EventRecoveryRequired(EventPlaneError):
    """Uma compra ficou ambigua e nao pode ser repetida automaticamente."""


class EventDataPartial(EventPlaneError):
    """O lote foi preservado como bronze, mas reprovou o gate de completude."""


def _utc_iso(value: Any) -> str:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize("UTC")
    else:
        stamp = stamp.tz_convert("UTC")
    return stamp.isoformat()


@dataclass(frozen=True)
class EventRequest:
    """Requisicao historica reproduzivel e de intervalo limitado.

    ``end`` e obrigatorio e exclusivo. Impedir consultas sem limite e uma
    protecao cientifica e financeira: o mesmo pedido sempre descreve o mesmo
    intervalo e nunca cresce silenciosamente com o calendario.
    """

    provider: str
    dataset: str
    schema: str
    symbols: tuple[str, ...]
    start: str
    end: str
    stype_in: str = "raw_symbol"
    limit: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        symbols = tuple(str(symbol).strip() for symbol in self.symbols if str(symbol).strip())
        object.__setattr__(self, "provider", str(self.provider).strip().lower())
        object.__setattr__(self, "dataset", str(self.dataset).strip())
        object.__setattr__(self, "schema", str(self.schema).strip().lower())
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "start", _utc_iso(self.start))
        object.__setattr__(self, "end", _utc_iso(self.end))
        object.__setattr__(self, "stype_in", str(self.stype_in).strip())
        object.__setattr__(self, "metadata", dict(self.metadata))
        if not self.provider or not self.dataset or not self.schema:
            raise EventContractError("provider, dataset e schema sao obrigatorios")
        if not symbols:
            raise EventContractError("ao menos um simbolo e obrigatorio")
        if pd.Timestamp(self.end) <= pd.Timestamp(self.start):
            raise EventContractError("end deve ser posterior a start")
        if self.limit is not None and int(self.limit) <= 0:
            raise EventContractError("limit deve ser positivo")

    @classmethod
    def create(
        cls,
        *,
        provider: str,
        dataset: str,
        schema: str,
        symbols: Sequence[str] | str,
        start: Any,
        end: Any,
        stype_in: str = "raw_symbol",
        limit: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "EventRequest":
        normalized = (symbols,) if isinstance(symbols, str) else tuple(symbols)
        return cls(provider, dataset, schema, normalized, start, end, stype_in,
                   limit, metadata or {})

    def canonical(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "schema": self.schema,
            "symbols": list(self.symbols),
            "start": self.start,
            "end": self.end,
            "stype_in": self.stype_in,
            "limit": self.limit,
            "metadata": dict(sorted(self.metadata.items())),
        }

    @property
    def request_id(self) -> str:
        # Metadata descreve o uso local (shadow/backfill), nao a consulta paga.
        # Exclui-la impede que a mesma janela seja comprada de novo so porque
        # recebeu outro rotulo operacional.
        billable = self.canonical()
        billable.pop("metadata", None)
        raw = json.dumps(billable, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True).encode("utf-8")
        return sha256(raw).hexdigest()


@dataclass(frozen=True)
class EventQuality:
    status: str
    reason: str
    rows_input: int
    rows_output: int
    duplicate_rows_removed: int
    null_event_timestamps: int
    timestamp_regressions: int
    first_event: str | None
    last_event: str | None
    symbols: tuple[str, ...]
    missing_symbols: tuple[str, ...] = ()
    limit_reached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderPayload:
    """Resultado temporario de um provedor antes do commit no event lake."""

    request: EventRequest
    frame: pd.DataFrame
    native_path: Path | None = None
    estimated_cost_usd: float | None = None
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EventIngestResult:
    request_id: str
    status: str
    rows: int
    partitions: tuple[str, ...]
    quality: EventQuality
    native_path: str | None
    native_sha256: str | None
    estimated_cost_usd: float | None
    committed_at: str
    idempotent_replay: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["quality"] = self.quality.to_dict()
        return result


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
