"""Persistent Brain memory. JSONL under QHOS2_DATA_ROOT/brain/, separate from source code.

Not a vector database. Not DuckDB. Append-only files with an in-memory index.
Raw LLM dumps are rejected; store a short title + compressed body only.
"""
from __future__ import annotations

import json
import os
import threading
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .models import (
    AuditEvent,
    MemoryKind,
    MemoryRecord,
    new_id,
    utc_ts,
)

MAX_BODY = 4000
MAX_TITLE = 240


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".brain-", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str, allow_nan=False))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if os.name != "nt":
            parent = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        tmp.unlink(missing_ok=True)


class JsonlStore:
    def __init__(self, path: Path, *, read_only: bool = False) -> None:
        self.path = path
        self.read_only = read_only
        self._lock = threading.Lock()
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: Mapping[str, Any]) -> None:
        if self.read_only:
            raise PermissionError("brain_store_read_only")
        line = json.dumps(record, ensure_ascii=False, default=str, allow_nan=False) + "\n"
        with self._lock:
            with self.path.open("a+b") as fh:
                size = fh.tell()
                if size:
                    fh.seek(-1, os.SEEK_END)
                    if fh.read(1) != b"\n":
                        fh.write(b"\n")
                fh.write(line.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())

    def tail(self, n: int = 50) -> List[Dict[str, Any]]:
        try:
            with self.path.open("rb") as fh:
                # UI tail, not a full audit export: bounded memory even after years.
                fh.seek(0, os.SEEK_END)
                start = max(0, fh.tell() - 2 * 1024 * 1024)
                fh.seek(start)
                if start:
                    fh.readline()  # Drop a possibly partial first row.
                lines = fh.read(2 * 1024 * 1024).decode("utf-8", errors="replace").splitlines()[-max(1, min(n, 5000)):]
        except OSError:
            return []
        out: List[Dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
        return out


class KnowledgeGraph:
    """Adjacency list. Swap later for a graph DB without changing callers."""

    def __init__(self) -> None:
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.relations: List[Dict[str, str]] = []
        self._rel_keys: set[str] = set()

    def upsert_entity(self, entity_id: str, kind: str, **attrs: Any) -> None:
        row = dict(self.entities.get(entity_id) or {})
        row.update(attrs)
        row["id"] = entity_id
        row["kind"] = kind
        if "last_seen" not in row and "last_seen" not in attrs:
            from .models import utc_ts
            row["last_seen"] = utc_ts()
        self.entities[entity_id] = row

    def prune_ephemeral(
        self,
        *,
        kinds: tuple[str, ...] = ("discovery", "observation"),
        max_keep: int = 48,
        ttl_s: float = 3600.0,
        now: float | None = None,
        protect_kinds: tuple[str, ...] = ("incident", "goal", "reviewed"),
    ) -> Dict[str, int]:
        """Drop stale ephemeral nodes. Never delete audit-critical incidents/goals."""
        from .models import utc_ts
        now = float(now or utc_ts())
        ephemeral = [
            (eid, row) for eid, row in self.entities.items()
            if str(row.get("kind") or "") in kinds
            and str(row.get("kind") or "") not in protect_kinds
            and not row.get("reviewed")
        ]
        keep: set[str] = set()
        fresh = sorted(
            ephemeral,
            key=lambda item: float(item[1].get("last_seen") or 0.0),
            reverse=True,
        )
        for eid, row in fresh:
            age = now - float(row.get("last_seen") or 0.0)
            if age <= ttl_s and len(keep) < max_keep:
                keep.add(eid)
        dropped = 0
        for eid, _row in ephemeral:
            if eid in keep:
                continue
            self.entities.pop(eid, None)
            dropped += 1
        if dropped:
            self.relations = [
                r for r in self.relations
                if r.get("source") in self.entities and r.get("target") in self.entities
            ]
            self._rel_keys = {f"{r['source']}|{r['verb']}|{r['target']}" for r in self.relations}
        return {"dropped": dropped, "kept_ephemeral": len(keep), "entities": len(self.entities)}

    def relate(self, source: str, verb: str, target: str) -> bool:
        key = f"{source}|{verb}|{target}"
        if key in self._rel_keys:
            return False
        self._rel_keys.add(key)
        self.relations.append({"source": source, "verb": verb, "target": target})
        return True

    def neighbors(self, entity_id: str) -> List[Dict[str, str]]:
        return [r for r in self.relations if r["source"] == entity_id or r["target"] == entity_id]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": list(self.entities.values()),
            "relations": list(self.relations),
            "counts": {
                "entities": len(self.entities),
                "relations": len(self.relations),
            },
        }

    def load(self, payload: Mapping[str, Any]) -> None:
        self.entities.clear()
        self.relations.clear()
        self._rel_keys.clear()
        for ent in payload.get("entities") or []:
            if isinstance(ent, dict) and ent.get("id"):
                self.entities[str(ent["id"])] = dict(ent)
        for rel in payload.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            src, verb, tgt = str(rel.get("source") or ""), str(rel.get("verb") or ""), str(rel.get("target") or "")
            if src and verb and tgt:
                self.relate(src, verb, tgt)


class BrainStore:
    def __init__(self, root: Path, *, read_only: bool = False) -> None:
        self.root = Path(root)
        self.read_only = bool(read_only)
        if not self.read_only:
            self.root.mkdir(parents=True, exist_ok=True)
        self.events = JsonlStore(self.root / "events.jsonl", read_only=self.read_only)
        self.memory = JsonlStore(self.root / "memory.jsonl", read_only=self.read_only)
        self.audit = JsonlStore(self.root / "audit.jsonl", read_only=self.read_only)
        self.state_path = self.root / "state.json"
        self.graph = KnowledgeGraph()
        # Insertion-ordered so trimming really drops the oldest ids (QHOS-AUD-316).
        self.processed_ids: Dict[str, None] = {}
        self._lock = threading.Lock()

    def remember(
        self,
        kind: MemoryKind,
        title: str,
        body: str,
        *,
        tags: Optional[Iterable[str]] = None,
        correlation_id: str = "",
    ) -> Optional[MemoryRecord]:
        title = str(title or "").strip()[:MAX_TITLE]
        body = str(body or "").strip()
        if not title or not body:
            return None
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY] + "…"
        if kind == MemoryKind.WORKING and body.startswith("```"):
            return None
        rec = MemoryRecord(
            memory_id=new_id("mem"),
            kind=kind,
            title=title,
            body=body,
            tags=[str(t) for t in (tags or []) if t][:12],
            correlation_id=correlation_id,
            ts=utc_ts(),
        )
        if not self.read_only:
            self.memory.append(rec.to_dict())
        return rec

    def record_audit(self, event: AuditEvent) -> None:
        if self.read_only:
            return
        self.audit.append(event.to_dict())

    def mark_processed(self, event_id: str) -> bool:
        """Return True if this id is new (first time). Idempotent ingest.

        Bounded memory: past 20 000 ids the oldest 10 000 are forgotten, so an id
        older than that is accepted again as new (contract gap recorded in
        QHOS-AUD-316; the previous set-based trim forgot arbitrary ids instead).
        """
        if not event_id:
            return True
        with self._lock:
            if event_id in self.processed_ids:
                return False
            self.processed_ids[event_id] = None
            if len(self.processed_ids) > 20_000:
                for oldest in list(self.processed_ids)[:-10_000]:
                    del self.processed_ids[oldest]
            return True

    def save_state(self, payload: Mapping[str, Any]) -> None:
        if self.read_only:
            return
        blob = dict(payload)
        blob["graph"] = self.graph.to_dict()
        blob["processed_n"] = len(self.processed_ids)
        _atomic_write(self.state_path, blob)

    def load_state(self) -> Dict[str, Any]:
        try:
            blob = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(blob, dict):
            return {}
        graph = blob.get("graph")
        if isinstance(graph, dict):
            self.graph.load(graph)
        return blob
