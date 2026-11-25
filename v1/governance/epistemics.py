"""Contratos epistemologicos e fonte factual do QHOS.

Este modulo separa cinco coisas que a linguagem antiga misturava:

* processo responsivo;
* integridade dos dados;
* estagio de um candidato;
* modo de execucao;
* forca da evidencia.

Nenhum componente deve inferir ``edge``, ``aprendizado`` ou ``execucao real``
a partir de nomes livres. Dashboard, Discord e agentes recebem o mesmo snapshot
atomico em ``data_lake/governance/CURRENT_STATE.json``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from core.config import config
from core.logger import get_logger

log = get_logger("Epistemics")


class CandidateStage(str, Enum):
    GENERATED = "GENERATED"
    BACKTESTED = "BACKTESTED"
    INTERNAL_TESTS_PASSED = "INTERNAL_TESTS_PASSED"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE_CAPITAL = "LIVE_CAPITAL"
    FALSIFIED = "FALSIFIED"


class DataState(str, Enum):
    FINALIZED = "FINALIZED"
    PARTIAL = "PARTIAL"
    STALE = "STALE"
    QUARANTINED = "QUARANTINED"
    UNAVAILABLE = "UNAVAILABLE"


class InferenceSource(str, Enum):
    MODEL = "MODEL"
    BASELINE = "BASELINE"
    FALLBACK = "FALLBACK"
    UNAVAILABLE = "UNAVAILABLE"


class ExecutionEvidence(str, Enum):
    BACKTEST = "BACKTEST"
    SHADOW_MARK = "SHADOW_MARK"
    SIMULATED_CLOSE_FILL = "SIMULATED_CLOSE_FILL"
    LIVE_BROKER_FILL = "LIVE_BROKER_FILL"
    NONE = "NONE"


class EvidenceStatus(str, Enum):
    NO_EVIDENCE = "NO_EVIDENCE"
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"
    INTERNAL_ONLY = "INTERNAL_ONLY"
    PAPER_PRELIMINARY = "PAPER_PRELIMINARY"
    FORWARD_SUPPORTED = "FORWARD_SUPPORTED"
    FALSIFIED = "FALSIFIED"


class HealthState(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


@dataclass
class Claim:
    """Afirmacao acompanhada da evidencia necessaria para interpreta-la."""

    claim_type: str
    subject: str
    statement: str
    stage: str = CandidateStage.GENERATED.value
    evidence_status: str = EvidenceStatus.NO_EVIDENCE.value
    data_state: str = DataState.UNAVAILABLE.value
    execution: str = ExecutionEvidence.NONE.value
    settled_sessions: int = 0
    benchmark: Optional[str] = None
    after_costs: bool = False
    multiple_testing_controlled: bool = False
    code_hash: str = ""
    data_hash: str = ""
    confidence: Optional[float] = None
    limitations: list[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ClaimDecision:
    allowed: bool
    normalized_type: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ClaimGuard:
    """Impede que uma palavra afirme mais do que a evidencia permite."""

    FORBIDDEN = {"CERTIFIED", "CERTIFICADO", "PROVEN", "COMPROVADO"}
    EDGE_TERMS = {"EDGE", "EDGE_SUPPORTED", "ALPHA", "VANTAGEM_COMPROVADA"}
    LEARNING_TERMS = {"LEARNED", "APRENDEU", "LEARNING_CONFIRMED"}
    REAL_TERMS = {"REAL", "REAL_EXECUTION", "LIVE_REAL"}
    VALIDATED_TERMS = {"VALIDATED", "VALIDADO"}

    def validate(self, claim: Claim) -> ClaimDecision:
        kind = str(claim.claim_type).strip().upper()
        reasons: list[str] = []

        if kind in self.FORBIDDEN:
            reasons.append("termo absoluto proibido; declare o teste especifico")

        if kind in self.EDGE_TERMS:
            if claim.execution not in {
                ExecutionEvidence.SIMULATED_CLOSE_FILL.value,
                ExecutionEvidence.LIVE_BROKER_FILL.value,
            }:
                reasons.append("edge exige evidencia forward, nao backtest/shadow")
            if int(claim.settled_sessions or 0) < int(config.promotion_min_forward_sessions):
                reasons.append(
                    f"edge exige >= {config.promotion_min_forward_sessions} sessoes finalizadas"
                )
            if claim.data_state != DataState.FINALIZED.value:
                reasons.append("edge exige dados finalizados")
            if not claim.benchmark:
                reasons.append("edge exige benchmark declarado")
            if not claim.after_costs:
                reasons.append("edge exige resultado liquido de custos")
            if not claim.multiple_testing_controlled:
                reasons.append("edge exige controle de selecao/testes multiplos")

        if kind in self.LEARNING_TERMS:
            if not bool(claim.metadata.get("parameters_updated")):
                reasons.append("registro/feedback nao e aprendizado sem atualizar politica")
            if not bool(claim.metadata.get("oos_improved")):
                reasons.append("aprendizado exige melhoria OOS predefinida")
            if not claim.code_hash or not claim.data_hash:
                reasons.append("aprendizado exige versoes de codigo e dados")

        if kind in self.REAL_TERMS:
            if claim.execution != ExecutionEvidence.LIVE_BROKER_FILL.value:
                reasons.append("real exige fill confirmado por broker real")
            if not claim.metadata.get("broker_fill_id"):
                reasons.append("real exige identificador do fill do broker")

        if kind in self.VALIDATED_TERMS:
            if not claim.code_hash or not claim.data_hash:
                reasons.append("aprovacao interna exige hash de codigo e dados")
            if claim.stage not in {
                CandidateStage.INTERNAL_TESTS_PASSED.value,
                CandidateStage.SHADOW.value,
                CandidateStage.PAPER.value,
                CandidateStage.LIVE_CAPITAL.value,
            }:
                reasons.append("candidato nao passou pelos testes internos")

        return ClaimDecision(not reasons, kind, reasons)

    def require(self, claim: Claim) -> None:
        decision = self.validate(claim)
        if not decision.allowed:
            raise ValueError("afirmacao bloqueada: " + "; ".join(decision.reasons))


claim_guard = ClaimGuard()


def _sha256_files(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    found = False
    for path in sorted(paths, key=lambda p: p.as_posix()):
        if not path.is_file():
            continue
        found = True
        digest.update(path.name.encode("utf-8", errors="ignore"))
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<unreadable>")
    return digest.hexdigest() if found else "unavailable"


def current_data_fingerprint() -> str:
    """Hash da proveniencia do universo ATIVO, nao de ativos aposentados."""
    root = Path(config.data_lake_dir) / "provenance"
    active = set(config.data_assets_stocks + config.data_assets_crypto
                 + list(getattr(config, "data_assets_reference", [])))
    by_asset: Dict[str, Dict[str, Any]] = {}
    for path in root.glob("*.json"):
        payload = _read_json(path, {})
        if str(payload.get("asset")) in active:
            by_asset[str(payload.get("asset"))] = payload
    records = []
    for asset in sorted(active):
        payload = by_asset.get(asset, {})
        # retrieved_at e outros metadados operacionais nao mudam a amostra.
        # A identidade cientifica usa somente conteudo/recorte/provedor. Ativo
        # sem manifest tambem entra, para mudanca de universo invalidar cache.
        records.append({
            "asset": asset,
            **{key: payload.get(key) for key in (
                "timeframe", "source", "status",
                "first_observation", "final_observation",
            )},
            "final_data_hash": payload.get("final_data_hash", payload.get("data_hash")),
        })
    if not records:
        return "unavailable"
    canonical = json.dumps(sorted(records, key=lambda row: (str(row.get("asset")),
                                                            str(row.get("timeframe")))),
                           sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_data_lineage() -> str:
    """Identidade estável do universo/provedor, mesmo quando chega nova sessão."""
    root = Path(config.data_lake_dir) / "provenance"
    active = sorted(set(config.data_assets_stocks + config.data_assets_crypto
                        + list(getattr(config, "data_assets_reference", []))))
    manifests: Dict[str, Dict[str, Any]] = {}
    for path in root.glob("*.json"):
        payload = _read_json(path, {})
        manifests[str(payload.get("asset", ""))] = payload
    rows = []
    for asset in active:
        payload = manifests.get(asset, {})
        rows.append({"asset": asset, "timeframe": payload.get("timeframe", "1d"),
                     "source": payload.get("source", "unavailable"),
                     "first_observation": payload.get("first_observation")})
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def current_code_fingerprint() -> str:
    root = Path(config.root_dir)
    paths = [
        root / "backtest" / "engine.py",
        root / "backtest" / "cpu_cluster.py",
        root / "validation" / "validator.py",
        root / "evolution" / "strategy_dna.py",
        root / "evolution" / "genetic.py",
        root / "execution" / "signal_executor.py",
        root / "core" / "config.py",
        root / "data" / "event_contracts.py",
        root / "data" / "event_lake.py",
        root / "data" / "event_plane.py",
        root / "data" / "event_silver.py",
        root / "data" / "providers" / "databento_provider.py",
    ]
    return _sha256_files(paths)


def current_scientific_epoch() -> str:
    return (f"scientific-v3:data={current_data_fingerprint()}:"
            f"code={current_code_fingerprint()}")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def _count_jsonl(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return 0


def _settled_csv(path: Path, calendar: str = "equity") -> Dict[str, Any]:
    """Resumo sem chamar de evidencia real.

    calendar="equity" descarta fins de semana/feriados; calendar="crypto"
    aceita todo dia UTC — cripto nao tem pregao e cada dia e sessao real.
    """
    try:
        import pandas as pd

        frame = pd.read_csv(path)
        if "date" not in frame.columns:
            return {"sessions": 0, "available": False}
        if frame.empty:
            # Ledger existe (cabecalho) mas ainda sem sessao liquidada.
            return {"sessions": 0, "available": True}
        dates = pd.to_datetime(frame["date"], errors="coerce")
        if calendar == "crypto":
            valid = dates.notna()
        else:
            from core.market_clock import is_trading_day
            valid = dates.notna() & dates.dt.date.map(is_trading_day)
        frame = frame.loc[valid].copy()
        if frame.empty:
            return {"sessions": 0, "available": False}
        result: Dict[str, Any] = {
            "sessions": int(frame["date"].nunique()),
            "available": True,
            "first_session": str(frame["date"].iloc[0])[:10],
            "last_session": str(frame["date"].iloc[-1])[:10],
        }
        if "nav" in frame:
            nav = frame["nav"].astype(float)
            result["nav"] = float(nav.iloc[-1])
            result["return"] = float(nav.iloc[-1] / nav.iloc[0] - 1.0) if nav.iloc[0] else None
        return result
    except Exception as exc:
        return {"sessions": 0, "available": False, "error": type(exc).__name__}


def _provenance_summary() -> Dict[str, Any]:
    root = Path(config.data_lake_dir) / "provenance"
    counts = {state.value: 0 for state in DataState}
    active = list(dict.fromkeys(
        list(config.data_assets_stocks) + list(config.data_assets_crypto)
        + list(getattr(config, "data_assets_reference", []))
    ))
    manifests: Dict[str, Dict[str, Any]] = {}
    ignored_retired = 0
    for path in root.glob("*.json"):
        payload = _read_json(path, {})
        asset = str(payload.get("asset", ""))
        if asset not in active:
            ignored_retired += 1
            continue
        manifests[asset] = payload
    sources: Dict[str, int] = {}
    issues: list[Dict[str, Any]] = []
    for asset in active:
        payload = manifests.get(asset, {})
        status = str(payload.get("status", "")).lower()
        source = str(payload.get("source", "unavailable"))
        sources[source] = sources.get(source, 0) + 1
        if status == "approved":
            counts[DataState.FINALIZED.value] += 1
        elif status == "partial":
            counts[DataState.PARTIAL.value] += 1
        elif status == "quarantined":
            counts[DataState.QUARANTINED.value] += 1
        else:
            counts[DataState.UNAVAILABLE.value] += 1
        if status != "approved":
            issues.append({"asset": asset, "status": status or "missing",
                           "reason": payload.get("reason", "missing_manifest")})
    total = len(active)
    if total == 0:
        acquisition_state = DataState.UNAVAILABLE.value
    elif counts[DataState.QUARANTINED.value] > 0:
        acquisition_state = DataState.QUARANTINED.value
    elif counts[DataState.PARTIAL.value] > 0:
        acquisition_state = DataState.PARTIAL.value
    else:
        acquisition_state = DataState.FINALIZED.value
    # Uma barra atual parcial e esperada durante o pregao. O storage a exclui
    # antes de features/pesquisa; logo aquisicao pode ser PARTIAL enquanto a
    # visao consumivel e FINALIZED. Misturar os dois gerava falso alarme diario.
    safe_partial = all(
        item.get("status") == "partial" and item.get("reason") == "current_bar_excluded"
        for item in issues
    ) if issues else True
    if acquisition_state in {DataState.FINALIZED.value, DataState.PARTIAL.value} and safe_partial:
        state = DataState.FINALIZED.value
    else:
        state = acquisition_state
    return {"state": state, "research_view_state": state,
            "acquisition_state": acquisition_state, "total": total, "counts": counts,
            "sources": sources, "issues": issues,
            "ignored_retired_manifests": ignored_retired}


def _event_plane_fact() -> Dict[str, Any]:
    """Estado factual da camada de eventos, sem ativa-la nem consultar rede."""
    try:
        from data.event_plane import EventPlane
        return EventPlane().status()
    except Exception as exc:
        return {
            "format": "qhos-event-plane-v1",
            "enabled": False,
            "status": "UNAVAILABLE",
            "error": type(exc).__name__,
            "research_isolation": True,
        }


def _regime_fact() -> Dict[str, Any]:
    lake = Path(config.data_lake_dir)
    skill = _read_json(lake / "regime_skill.json", {})
    latest: Dict[str, Any] = {}
    try:
        import pandas as pd

        files = sorted((lake / "regimes").glob("regime_*.parquet"))
        if files:
            frame = pd.read_parquet(files[-1])
            if not frame.empty:
                latest = frame.iloc[-1].to_dict()
    except Exception:
        latest = {}
    raw_source = str(latest.get("source", "")).lower()
    confidence = latest.get("confidence")
    try:
        confidence = float(confidence)
        if not math.isfinite(confidence):
            confidence = None
    except (TypeError, ValueError):
        confidence = None
    if raw_source == "transformer":
        source = InferenceSource.MODEL.value
    elif "baseline" in raw_source:
        source = InferenceSource.BASELINE.value
    elif raw_source or confidence == 0.25:
        source = InferenceSource.FALLBACK.value
    else:
        source = InferenceSource.UNAVAILABLE.value
    gain = skill.get("gain")
    usable = source == InferenceSource.MODEL.value and isinstance(gain, (int, float)) and gain > 0.01
    return {
        "label": latest.get("regime"),
        "source": source,
        "raw_source": raw_source or None,
        "confidence": confidence,
        "model_skill_gain": gain,
        "model_usable": bool(usable),
    }


# A reconciliação roda a cada 5 s no metrics loop e o CURRENT_STATE é reescrito
# a cada 30 s. Um snapshot com mais de 5 min não é carteira saudável: é prova de
# que o ``os.replace`` do snapshot está falhando (WinError 5 já observado).
_RECONCILIATION_MAX_AGE_S = 300.0


def _reconciliation_fact() -> Dict[str, Any]:
    """Veredito de reconciliação com a IDADE como parte do veredito.

    Ler apenas ``status`` fazia o estado autoritativo reportar OK enquanto o log
    registrava BLOCKED: se a gravação do snapshot falha, o disco congela no
    último veredito bom e o consumidor não tem como perceber.
    """
    path = Path(config.data_lake_dir) / "governance" / "reconciliation.json"
    snapshot = _read_json(path, {})
    if not snapshot:
        return {"state": "UNKNOWN_NO_SNAPSHOT", "status": "UNKNOWN",
                "age_seconds": None, "issues": []}
    status = str(snapshot.get("status", "UNKNOWN"))
    issues = list(snapshot.get("issues", []) or [])
    try:
        age: Optional[float] = max(0.0, time.time() - float(snapshot.get("ts", 0.0)))
    except (TypeError, ValueError):
        age = None
    # ``snapshot_persisted`` só existe se um escritor futuro o gravar; a idade é
    # o sinal que sobrevive à própria falha de disco.
    persisted = bool(snapshot.get("snapshot_persisted", True))
    if age is None:
        state = "UNKNOWN_NO_TIMESTAMP"
    elif age > _RECONCILIATION_MAX_AGE_S:
        state = f"STALE_{int(age // 60)}MIN_LAST_{status}"
    elif not persisted:
        state = f"{status}_SNAPSHOT_NOT_PERSISTED"
    else:
        state = status
    return {"state": state, "status": status, "age_seconds": age,
            "issues": issues, "codes": [str(i.get("code")) for i in issues
                                        if isinstance(i, dict)]}


def _generation_channels_fact() -> Dict[str, Any]:
    """Saúde dos canais de geração de hipóteses.

    Um relatório de pesquisa que diz ``zero_internal_candidates_passed`` sem
    declarar que um gerador está inoperante está omitindo parte do diagnóstico.
    """
    snapshot = _read_json(Path(config.data_lake_dir) / "llm_agents_state.json", {})
    agents = snapshot.get("agents", {}) if isinstance(snapshot, dict) else {}
    blockers: list[str] = []
    for name, health in sorted(agents.items()):
        if not isinstance(health, dict):
            continue
        if not health.get("enabled", False):
            reason = str(health.get("disabled_reason") or "unknown")
            blockers.append(f"llm_{name}_agent_disabled:{reason}")
        elif int(health.get("consecutive_failures", 0) or 0) > 3:
            blockers.append(
                f"llm_{name}_agent_failing:{health.get('last_error_class') or 'unknown'}")
    return {"agents": agents, "blockers": blockers,
            "observed": bool(agents),
            "genetic_engine": "ACTIVE"}


def _promotion_summary() -> Dict[str, Any]:
    path = Path(config.data_lake_dir) / "governance" / "strategy_promotions.jsonl"
    latest: Dict[str, Dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            latest[str(row.get("strategy_id"))] = row
    except (OSError, json.JSONDecodeError):
        pass
    counts = {stage.value: 0 for stage in CandidateStage}
    stage_map = {
        "RESEARCH": CandidateStage.INTERNAL_TESTS_PASSED.value,
        "SHADOW": CandidateStage.SHADOW.value,
        "PAPER": CandidateStage.PAPER.value,
        "REJECTED": CandidateStage.FALSIFIED.value,
    }
    for row in latest.values():
        stage = stage_map.get(str(row.get("stage", "RESEARCH")).upper(), CandidateStage.GENERATED.value)
        counts[stage] = counts.get(stage, 0) + 1
    return {"counts": counts, "latest": latest}


def build_current_state(runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Constroi snapshot factual. Nao abre DuckDB e nao faz chamadas de rede."""
    runtime = dict(runtime or {})
    lake = Path(config.data_lake_dir)
    heartbeat_path = lake / "server_heartbeat.txt"
    now = time.time()
    heartbeat_age = None
    try:
        heartbeat_age = max(0.0, now - float(heartbeat_path.read_text().strip()))
    except (OSError, ValueError):
        try:
            heartbeat_age = max(0.0, now - heartbeat_path.stat().st_mtime)
        except OSError:
            pass
    process_online = heartbeat_age is not None and heartbeat_age <= 180.0
    disk_code_hash = current_code_fingerprint()
    runtime_code_hash = str(runtime.get("code_hash", "") or "")
    loaded_code_state = (
        "MATCH" if runtime_code_hash and runtime_code_hash == disk_code_hash
        else "MISMATCH" if runtime_code_hash
        else "UNKNOWN"
    )

    data = _provenance_summary()
    regime = _regime_fact()
    evolution = _read_json(lake / "evolution_state.json", {})
    cache = _read_json(lake / "validation_cache.json", {})
    scientific_epoch = current_scientific_epoch()
    evolution_current = evolution.get("data_epoch") == scientific_epoch
    cache_current = cache.get("data_epoch") == scientific_epoch
    if not evolution_current:
        evolution = {}
    if not cache_current:
        cache = {}
    cache_results = cache.get("results", {}) if isinstance(cache, dict) else {}
    passed = sum(1 for value in cache_results.values() if isinstance(value, dict) and value.get("passed"))
    rejected = sum(1 for value in cache_results.values() if isinstance(value, dict) and not value.get("passed"))
    promotions = _promotion_summary()
    try:
        from governance.experiment_registry import experiments
        unique_hypotheses = experiments.count_unique_hypotheses(current_data_lineage())
    except Exception:
        unique_hypotheses = 0
    reconciliation = _reconciliation_fact()
    broker = _read_json(lake / "paper_broker_state.json", {})
    execution_mode = str(runtime.get("execution_mode", "SIMULATED_CLOSE_FILL"))
    live_capital = execution_mode == ExecutionEvidence.LIVE_BROKER_FILL.value

    tracks = {
        "paper_portfolio": _settled_csv(lake / "track_record" / "nav.csv"),
        "trend_candidate": _settled_csv(lake / "track_record" / "trend_nav.csv"),
        "symb1_candidate": _settled_csv(lake / "track_record" / "symb1_nav.csv"),
        # SHADOW (nao paper): observa funding liquidado, sem fill simulado.
        # Relogio proprio de dia UTC — HIPOTESE_CRYPTO_spec_falsificavel.md §E.
        "crypto_carry_shadow": _settled_csv(
            lake / "track_record" / "crypto_carry_nav.csv", calendar="crypto"),
    }
    # O rotulo de evidencia e PAPER_*; sessoes SHADOW nao entram no calculo
    # (chamar shadow de paper violaria o vocabulario do contrato).
    max_sessions = max((int(payload.get("sessions", 0))
                        for name, payload in tracks.items()
                        if not name.endswith("_shadow")), default=0)
    evidence_status = (
        EvidenceStatus.PAPER_PRELIMINARY.value
        if max_sessions > 0
        else EvidenceStatus.NO_EVIDENCE.value
    )
    if 0 < max_sessions < config.promotion_min_forward_sessions:
        evidence_status = EvidenceStatus.INSUFFICIENT_SAMPLE.value

    generation = _generation_channels_fact()
    research_blockers: list[str] = []
    if not regime.get("model_usable"):
        research_blockers.append("regime_model_without_oos_skill")
    if data["state"] in {DataState.QUARANTINED.value, DataState.UNAVAILABLE.value}:
        research_blockers.append("data_integrity_not_final")
    if passed == 0:
        research_blockers.append("zero_internal_candidates_passed")
    research_blockers.extend(generation["blockers"])

    execution_blockers: list[str] = []
    reconciliation_state = str(reconciliation.get("state", "UNKNOWN"))
    if reconciliation_state != "OK":
        # O motivo entra no blocker: "reconciliation_not_ok" sozinho não permitia
        # distinguir divergência de posição de snapshot que parou de ser gravado.
        detail = ",".join(reconciliation.get("codes") or []) or reconciliation_state
        execution_blockers.append(f"reconciliation_not_ok:{detail}")
    paper_count = promotions["counts"].get(CandidateStage.PAPER.value, 0)
    if paper_count == 0:
        execution_blockers.append("zero_candidates_authorized_for_paper")

    health = {
        "process": HealthState.OK.value if process_online else HealthState.OFFLINE.value,
        "data_integrity": (
            HealthState.OK.value
            if data["state"] == DataState.FINALIZED.value
            else HealthState.DEGRADED.value
            if data["state"] == DataState.PARTIAL.value
            else HealthState.BLOCKED.value
        ),
        "research_integrity": (
            HealthState.BLOCKED.value
            if data["state"] != DataState.FINALIZED.value
            else HealthState.DEGRADED.value if research_blockers
            else HealthState.OK.value
        ),
        "execution_readiness": HealthState.BLOCKED.value if execution_blockers else HealthState.OK.value,
    }

    state = {
        "schema": "qhos_current_state_v1",
        "authoritative": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "identity": {
            "brand": config.system_name,
            "technical_name": "local_quant_research_runtime",
            "version": config.system_version,
        },
        "process": {
            "state": "PROCESS_ONLINE" if process_online else "PROCESS_OFFLINE",
            "heartbeat_age_seconds": heartbeat_age,
            "loaded_code_state": loaded_code_state,
            "operational_means": "bootstrap_completed_and_process_responsive",
        },
        "health": health,
        "data": {**data, "fingerprint": current_data_fingerprint(),
                 "event_plane": _event_plane_fact()},
        "inference": {"market_state": regime},
        "research": {
            "activity": "CANDIDATE_SEARCH",
            "generation": int(evolution.get("generation", 0) or 0),
            "population": len(evolution.get("population", []) or []),
            "internally_passed": passed,
            "internally_rejected": rejected,
            "judgments_cached": len(cache_results),
            "unique_hypotheses_tested": unique_hypotheses,
            "validation_cache_current": cache_current,
            "evolution_checkpoint_current": evolution_current,
            "archive_label": "candidate_archive",
            "generation_channels": generation["agents"],
            "research_blockers": research_blockers,
        },
        "execution": {
            "mode": execution_mode,
            "live_capital": live_capital,
            "paper_positions": len(broker.get("positions", {}) or {}),
            "reconciliation": reconciliation_state,
            "reconciliation_detail": {
                "snapshot_status": reconciliation.get("status"),
                "snapshot_age_seconds": reconciliation.get("age_seconds"),
                "issues": reconciliation.get("issues"),
            },
            "paper_authorized_candidates": paper_count,
            "execution_blockers": execution_blockers,
        },
        "evidence": {
            "status": evidence_status,
            "minimum_forward_sessions": int(config.promotion_min_forward_sessions),
            "tracks": tracks,
            "claims_supported": [],
        },
        "versions": {
            "code_hash": disk_code_hash,
            "runtime_code_hash": runtime_code_hash or None,
            "data_hash": current_data_fingerprint(),
            "data_lineage": current_data_lineage(),
            "scientific_epoch": scientific_epoch,
        },
        "language_contract": {
            "edge": "reserved_until_forward_after_costs_benchmark_and_selection_control",
            "learning": "reserved_until_policy_update_and_predefined_oos_improvement",
            "real": "reserved_for_confirmed_live_broker_fills",
            "validated": "use_internal_tests_passed_with_code_and_data_hash",
            "operational": "process_state_only",
        },
        "runtime": runtime,
    }
    return state


CURRENT_STATE_PATH = Path(config.data_lake_dir) / "governance" / "CURRENT_STATE.json"


def write_current_state(runtime: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Publica a fonte de verdade com replace atomico."""
    state = build_current_state(runtime)
    CURRENT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(CURRENT_STATE_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, CURRENT_STATE_PATH)
    return state


def load_current_state(max_age_seconds: float = 180.0) -> Dict[str, Any]:
    state = _read_json(CURRENT_STATE_PATH, {})
    if not state:
        return build_current_state()
    try:
        age = time.time() - CURRENT_STATE_PATH.stat().st_mtime
        state["snapshot_age_seconds"] = max(0.0, age)
        state["snapshot_stale"] = age > max_age_seconds
    except OSError:
        state["snapshot_stale"] = True
    return state


__all__ = [
    "CandidateStage",
    "Claim",
    "ClaimDecision",
    "ClaimGuard",
    "DataState",
    "EvidenceStatus",
    "ExecutionEvidence",
    "HealthState",
    "InferenceSource",
    "build_current_state",
    "claim_guard",
    "current_code_fingerprint",
    "current_data_fingerprint",
    "current_data_lineage",
    "current_scientific_epoch",
    "load_current_state",
    "write_current_state",
]
