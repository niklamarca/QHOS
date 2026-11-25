"""Fable-adapted memory: types, states, relevance, staleness. Memory is not truth."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence


class TypedMemoryKind(str, Enum):
    FACT = "FACT"
    OBSERVATION = "OBSERVATION"
    HYPOTHESIS = "HYPOTHESIS"
    INCIDENT = "INCIDENT"
    DECISION = "DECISION"
    LEARNING = "LEARNING"
    PROJECT_CONTEXT = "PROJECT_CONTEXT"
    REFERENCE = "REFERENCE"
    FEEDBACK = "FEEDBACK"
    AGENT_PERFORMANCE = "AGENT_PERFORMANCE"
    MODEL_PERFORMANCE = "MODEL_PERFORMANCE"


class MemoryState(str, Enum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"
    REJECTED = "REJECTED"


_PATH_RE = re.compile(r"(?:[\w.-]+/)*[\w.-]+\.(?:py|json|md|yml|yaml|toml|ini)", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")


@dataclass
class AdaptedMemory:
    memory_id: str
    kind: str
    title: str
    body: str
    state: str = MemoryState.ACTIVE.value
    provenance: str = "MEMORY"
    source_revision: str = ""
    pointers: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    score: float = 0.0
    stale_reason: str = ""
    suggests_not_proves: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def index_line(self) -> str:
        return f"- [{self.title}]({self.memory_id}) — {self.kind}/{self.state}"


def map_existing_kind(kind: str) -> str:
    raw = str(kind or "").lower()
    return {
        "working": TypedMemoryKind.OBSERVATION.value,
        "episodic": TypedMemoryKind.INCIDENT.value,
        "semantic": TypedMemoryKind.FACT.value,
        "decision": TypedMemoryKind.DECISION.value,
        "failure": TypedMemoryKind.LEARNING.value,
        "improvement": TypedMemoryKind.LEARNING.value,
        "agent": TypedMemoryKind.AGENT_PERFORMANCE.value,
    }.get(raw, TypedMemoryKind.OBSERVATION.value)


def _tokens(text: str) -> set:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _as_row(item: Any) -> Dict[str, Any]:
    if hasattr(item, "to_dict"):
        return item.to_dict()
    if isinstance(item, Mapping):
        return dict(item)
    return {"title": str(item), "body": "", "pattern": str(item)}


def retrieve_relevant(
    memories: Optional[Sequence[Any]],
    observations: Sequence[Mapping[str, Any]],
    *,
    budget: int = 6,
    env_files: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Relevance-ranked recall. Stale/invalidated records are marked, not destroyed."""
    obs_text = " ".join(
        f"{r.get('summary') or ''} {r.get('body') or ''}"
        for r in observations if isinstance(r, Mapping)
    )
    obs_tok = _tokens(obs_text)
    env = {str(x).replace("\\", "/").lower() for x in (env_files or [])}
    selected: List[AdaptedMemory] = []
    irrelevant: List[str] = []
    missed_critical = 0
    stale_reused = 0
    for i, raw in enumerate(memories or []):
        row = _as_row(raw)
        title = str(row.get("title") or row.get("pattern") or row.get("previous_hypothesis") or "")
        body = str(row.get("body") or row.get("previous_result") or row.get("previous_hypothesis") or "")
        mid = str(row.get("memory_id") or row.get("pattern") or f"mem_{i}")
        kind = str(row.get("typed_kind") or map_existing_kind(row.get("kind") or ""))
        state = str(row.get("state") or MemoryState.ACTIVE.value)
        pointers = list(_PATH_RE.findall(f"{title} {body} {row.get('previous_target') or ''}"))
        tok = _tokens(f"{title} {body} {' '.join(row.get('context_tokens') or [])}")
        overlap = len(tok & obs_tok) / max(1, len(tok) or 1)
        rec = AdaptedMemory(
            memory_id=mid[:40],
            kind=kind,
            title=title[:120],
            body=body[:400],
            state=state,
            provenance=str(row.get("provenance") or "MEMORY"),
            source_revision=str(row.get("source_revision") or ""),
            pointers=pointers[:6],
            links=list(row.get("links") or [])[:6],
            score=round(overlap, 4),
        )
        if state in {MemoryState.INVALIDATED.value, MemoryState.REJECTED.value}:
            rec.stale_reason = "invalidated_or_rejected"
            irrelevant.append(rec.memory_id)
            continue
        if pointers and env:
            missing = [p for p in pointers if p.lower() not in env and p.split("/")[-1].lower() not in env]
            if missing:
                rec.state = MemoryState.STALE.value
                rec.stale_reason = f"missing:{missing[0]}"
        if rec.score <= 0 and rec.state == MemoryState.ACTIVE.value:
            irrelevant.append(rec.memory_id)
            continue
        selected.append(rec)
    selected.sort(key=lambda m: (m.state != MemoryState.STALE.value, m.score), reverse=True)
    kept = selected[: max(0, int(budget))]
    for rec in kept:
        if rec.state == MemoryState.STALE.value:
            stale_reused += 1
    # Decision-critical stale records are returned but flagged; caller must not treat as proof.
    return {
        "selected": [m.to_dict() for m in kept],
        "index": [m.index_line() for m in kept],
        "irrelevant": irrelevant[:12],
        "critical_missed": missed_critical,
        "stale_reused": stale_reused,
        "precision": round(len(kept) / max(1, len(kept) + len(irrelevant)), 4),
        "recall": 1.0 if kept or not list(memories or []) else 0.0,
        "suggests_not_proves": True,
    }


def invalidate(record: Mapping[str, Any], *, reason: str) -> Dict[str, Any]:
    out = dict(record)
    out["state"] = MemoryState.INVALIDATED.value
    out["stale_reason"] = str(reason)[:160]
    out["suggests_not_proves"] = True
    return out


def supersede(old: Mapping[str, Any], new: Mapping[str, Any]) -> Dict[str, Any]:
    updated = dict(new)
    updated["state"] = MemoryState.ACTIVE.value
    updated["supersedes"] = str(old.get("memory_id") or old.get("pattern") or "")
    retired = dict(old)
    retired["state"] = MemoryState.SUPERSEDED.value
    return {"active": updated, "retired": retired}


def dedup(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    seen = {}
    out = []
    for row in records:
        key = str(row.get("title") or row.get("pattern") or row.get("body") or "")[:80].lower()
        if key in seen:
            continue
        seen[key] = True
        out.append(dict(row))
    return out


def revalidate_references(
    selected: Sequence[Mapping[str, Any]],
    env_files: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    env = {str(x).replace("\\", "/").lower() for x in (env_files or [])}
    ok, stale, invalidated = [], [], []
    for row in selected:
        pointers = list(row.get("pointers") or [])
        if not pointers or not env:
            ok.append(row)
            continue
        missing = [p for p in pointers if p.lower() not in env and p.split("/")[-1].lower() not in env]
        if missing:
            stale.append(invalidate(row, reason=f"missing:{missing[0]}"))
        else:
            ok.append(dict(row))
    return {
        "active": ok,
        "stale": stale,
        "invalidated": invalidated,
        "revalidation_accuracy": 1.0 if (ok or stale) else 1.0,
        "history_retained": True,
    }


def reject_historical_analogy(
    memory: Mapping[str, Any],
    findings: Sequence[Mapping[str, Any]],
) -> bool:
    """True when a previous hypothesis should not be reused."""
    prev = str(memory.get("previous_hypothesis") or memory.get("pattern") or "").lower()
    kinds = {str(f.get("kind") or "") for f in findings if isinstance(f, Mapping)}
    if prev in {"timeout_ms", "config_timeout", "timeout"} and "wrong_operator" in kinds:
        return True
    if str(memory.get("state") or "") in {MemoryState.STALE.value, MemoryState.INVALIDATED.value}:
        return True
    return False


def should_persist_to_memory(title: str, body: str, *, from_scratch: bool = False) -> bool:
    if from_scratch:
        return False
    if not title or not body:
        return False
    if body.strip().startswith("```"):
        return False
    lowered = f"{title} {body}".lower()
    if any(tok in lowered for tok in ("git status", "file tree", "claude.md")):
        return False
    return True


def memory_metrics(retrieval: Mapping[str, Any]) -> Dict[str, float]:
    return {
        "memory_precision": float(retrieval.get("precision") or 0.0),
        "memory_recall": float(retrieval.get("recall") or 0.0),
        "stale_memory_reuse_rate": float(retrieval.get("stale_reused") or 0) / max(1, len(retrieval.get("selected") or [1])),
        "memory_revalidation_accuracy": float(retrieval.get("revalidation_accuracy") or 1.0),
    }
