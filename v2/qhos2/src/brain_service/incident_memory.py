"""Bounded incident-result memory backed only by verifiable local evidence.

Memory is historical context, never proof of the Trader's current runtime
state.  Recording and reading do not grant authority to execute a repair.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from brain_service.knowledge import DEFAULT_SOURCE_DIRS, SourceBoundary
from brain_service.supervisor import atomic_json_write


SCHEMA = "brain-incident-memory-v1"
MAX_RECORDS = 128
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_RECORD_BYTES = 12 * 1024
MAX_EVIDENCE = 12
MAX_TEXT = 500
MAX_KEYS = 32
MAX_DEPTH = 4

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "access_token", "auth_token", "password",
    "secret", "credential", "credentials", "private_key",
})
_STATUSES = frozenset({
    "observed",
    "investigating",
    "verified",
    "monitoring",
    "resolved",
    "unresolved",
    "failed",
    "closed",
})
_OUTCOMES = frozenset({
    "fixed",
    "not_fixed",
    "partial",
    "no_change",
    "recovered",
    "failed",
    "verified",
    "unknown",
})


def _bounded_json(value: Any, *, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        raise ValueError("structure_too_deep")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            raise ValueError("text_too_large")
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non_finite_number")
        return value
    if isinstance(value, Mapping):
        if len(value) > MAX_KEYS:
            raise ValueError("too_many_keys")
        result = {}
        for key, item in value.items():
            key_text = str(key)
            if not key_text or len(key_text) > 80:
                raise ValueError("invalid_key")
            if key_text.casefold().replace("-", "_") in _SENSITIVE_KEYS:
                raise ValueError("sensitive_key_denied")
            result[key_text] = _bounded_json(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_KEYS:
            raise ValueError("list_too_large")
        return [_bounded_json(item, depth=depth + 1) for item in value]
    raise ValueError("non_json_value")


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class IncidentMemory:
    """Deduplicated, atomic memory of verified incident outcomes."""

    def __init__(
        self,
        target_root: Path | str,
        state_dir: Path | str,
        *,
        source_dirs: Optional[Iterable[Path | str]] = None,
        max_records: int = MAX_RECORDS,
        clock=time.time,
    ) -> None:
        self.boundary = SourceBoundary(
            target_root,
            DEFAULT_SOURCE_DIRS if source_dirs is None else source_dirs,
        )
        self.target_root = self.boundary.target_root
        self.state_dir = Path(state_dir).absolute()
        self.path = self.state_dir / "incident_memory.json"
        self.max_records = max(1, min(int(max_records), MAX_RECORDS))
        self._clock = clock
        self._lock = threading.RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._last_validation_at = 0.0
        self._load()

    def _now(self) -> float:
        value = float(self._clock())
        return value if math.isfinite(value) and value > 0 else time.time()

    def _load(self) -> None:
        try:
            if self.path.is_symlink():
                return
            with self.path.open("rb") as handle:
                raw = handle.read(MAX_STATE_BYTES + 1)
            if len(raw) > MAX_STATE_BYTES:
                return
            payload = json.loads(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError, ValueError):
            return
        if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
            return
        rows = payload.get("records")
        if not isinstance(rows, list):
            return
        loaded: dict[str, dict[str, Any]] = {}
        for row in rows[-self.max_records:]:
            if not isinstance(row, Mapping):
                continue
            incident_id = row.get("incident_id")
            if not isinstance(incident_id, str) or not _ID_RE.fullmatch(incident_id):
                continue
            try:
                recorded_status = str(row.get("recorded_status") or row["status"])
                outcome = str(row["outcome"])
                timestamp = float(row["timestamp"])
                source_hash = str(row["source_hash"])
                if recorded_status not in _STATUSES or outcome not in _OUTCOMES:
                    continue
                if not math.isfinite(timestamp) or timestamp <= 0 or not _SHA256_RE.fullmatch(source_hash):
                    continue
                evidence = row.get("evidence")
                if not isinstance(evidence, list) or not evidence or len(evidence) > MAX_EVIDENCE:
                    continue
                clean_evidence = []
                for item in evidence:
                    if not isinstance(item, Mapping):
                        raise ValueError("invalid_reference")
                    clean_evidence.append({
                        "path": str(item["path"]),
                        "line_start": int(item["line_start"]),
                        "line_end": int(item["line_end"]),
                        "line_ref": str(item["line_ref"]),
                        "sha256": str(item["sha256"]),
                        "version": str(item["version"]),
                    })
                    if not _SHA256_RE.fullmatch(clean_evidence[-1]["sha256"]):
                        raise ValueError("invalid_sha256")
                content = {
                    "incident_id": incident_id,
                    "timestamp": timestamp,
                    "source_hash": source_hash,
                    "status": recorded_status,
                    "outcome": outcome,
                    "before": _bounded_json(row["before"]),
                    "after": _bounded_json(row["after"]),
                    "evidence": clean_evidence,
                }
                if row.get("record_hash") != _canonical_hash(content):
                    continue
                clean = {
                    "schema": SCHEMA,
                    **content,
                    "record_hash": str(row["record_hash"]),
                    "revision": max(1, int(row.get("revision") or 1)),
                    "first_recorded_at": float(row.get("first_recorded_at") or row["timestamp"]),
                    "recorded_at": float(row.get("recorded_at") or row["timestamp"]),
                    "source_current": row.get("source_current") is True,
                }
                if row.get("status") == "invalidated":
                    clean["recorded_status"] = content["status"]
                    clean["status"] = "invalidated"
                    clean["invalidated_at"] = float(row.get("invalidated_at") or clean["recorded_at"])
                    reasons = row.get("invalidation_reasons")
                    clean["invalidation_reasons"] = [str(reason)[:80] for reason in reasons[:8]] if isinstance(reasons, list) else []
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            clean["memory_is_current_proof"] = False
            clean["requires_current_observation"] = True
            loaded[incident_id] = clean
        self._records = loaded

    def _persist(self) -> None:
        rows = sorted(
            self._records.values(),
            key=lambda row: (float(row.get("recorded_at") or 0.0), row["incident_id"]),
        )[-self.max_records:]
        self._records = {row["incident_id"]: row for row in rows}
        payload = {
            "schema": SCHEMA,
            "updated_at": self._now(),
            "max_records": self.max_records,
            "records": rows,
            "memory_is_current_proof": False,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > MAX_STATE_BYTES:
            # The per-record bound normally makes this unreachable; reduce from
            # the oldest edge rather than ever writing an unbounded state file.
            while rows and len(encoded) > MAX_STATE_BYTES:
                rows.pop(0)
                payload["records"] = rows
                encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self._records = {row["incident_id"]: row for row in rows}
        atomic_json_write(self.path, payload)

    @staticmethod
    def _reply(*, accepted: bool, reason: str, incident_id: str = "", deduplicated: bool = False) -> dict[str, Any]:
        if accepted:
            action = "já constava" if deduplicated else "foi registrada"
            note = (
                f"Memória do incidente {incident_id} {action} com evidência verificada. "
                "Ela é contexto histórico e não comprova o estado atual do Trader."
            )
        else:
            note = f"Memória de incidente não registrada: {reason}."
        return {
            "accepted": accepted,
            "incident_id": incident_id or None,
            "deduplicated": deduplicated,
            "reason": reason,
            "note": note,
            "memory_is_current_proof": False,
        }

    def record(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Store one verified structured result; arbitrary prose is discarded."""
        if not isinstance(payload, Mapping):
            return self._reply(accepted=False, reason="invalid_payload")
        incident_id = str(payload.get("incident_id") or payload.get("id") or "").strip()
        if not _ID_RE.fullmatch(incident_id):
            return self._reply(accepted=False, reason="invalid_incident_id")
        if payload.get("verified") is not True:
            return self._reply(accepted=False, reason="verification_required", incident_id=incident_id)
        status = str(payload.get("status") or "").strip().casefold()
        if status not in _STATUSES:
            return self._reply(accepted=False, reason="invalid_status", incident_id=incident_id)
        outcome = str(payload.get("outcome") or "").strip().casefold()
        if outcome not in _OUTCOMES:
            return self._reply(accepted=False, reason="invalid_outcome", incident_id=incident_id)
        if not isinstance(payload.get("before"), Mapping) or not isinstance(payload.get("after"), Mapping):
            return self._reply(accepted=False, reason="structured_before_after_required", incident_id=incident_id)
        try:
            before = _bounded_json(payload["before"])
            after = _bounded_json(payload["after"])
        except ValueError as exc:
            return self._reply(accepted=False, reason=str(exc), incident_id=incident_id)

        raw_evidence = payload.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence or len(raw_evidence) > MAX_EVIDENCE:
            return self._reply(accepted=False, reason="bounded_evidence_required", incident_id=incident_id)
        evidence: list[dict[str, Any]] = []
        for reference in raw_evidence:
            verified = self.boundary.verify_reference(reference) if isinstance(reference, Mapping) else {
                "valid": False,
                "reason": "invalid_reference",
            }
            if not verified.get("valid"):
                return self._reply(
                    accepted=False,
                    reason=f"evidence_{verified.get('reason', 'invalid')}",
                    incident_id=incident_id,
                )
            evidence.append(verified["reference"])
        evidence.sort(key=lambda row: (row["path"], row["line_start"], row["line_end"]))
        source_hash = evidence[0]["sha256"] if len(evidence) == 1 else _canonical_hash({"evidence": evidence})

        timestamp = payload.get("timestamp", self._now())
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            return self._reply(accepted=False, reason="invalid_timestamp", incident_id=incident_id)
        timestamp = float(timestamp)
        now = self._now()
        if not math.isfinite(timestamp) or timestamp <= 0 or timestamp > now + 60:
            return self._reply(accepted=False, reason="invalid_timestamp", incident_id=incident_id)

        content = {
            "incident_id": incident_id,
            "timestamp": timestamp,
            "source_hash": source_hash,
            "status": status,
            "outcome": outcome,
            "before": before,
            "after": after,
            "evidence": evidence,
        }
        record_hash = _canonical_hash(content)
        if len(json.dumps(content, ensure_ascii=False).encode("utf-8")) > MAX_RECORD_BYTES:
            return self._reply(accepted=False, reason="record_too_large", incident_id=incident_id)

        with self._lock:
            previous = self._records.get(incident_id)
            if previous and previous.get("record_hash") == record_hash:
                return self._reply(
                    accepted=True,
                    reason="duplicate",
                    incident_id=incident_id,
                    deduplicated=True,
                )
            row = {
                "schema": SCHEMA,
                **content,
                "record_hash": record_hash,
                "revision": int(previous.get("revision") or 0) + 1 if previous else 1,
                "first_recorded_at": float(previous.get("first_recorded_at") or now) if previous else now,
                "recorded_at": now,
                "source_current": True,
                "memory_is_current_proof": False,
                "requires_current_observation": True,
            }
            self._records[incident_id] = row
            self._persist()
        return self._reply(accepted=True, reason="recorded", incident_id=incident_id)

    def validate_sources(self) -> dict[str, Any]:
        """Invalidate memories whose cited local source no longer matches."""
        with self._lock:
            invalidated = 0
            changed = False
            checked_at = self._now()
            for row in self._records.values():
                failures = []
                for evidence in row.get("evidence") or []:
                    verdict = self.boundary.verify_reference(evidence)
                    if not verdict.get("valid"):
                        failures.append(str(verdict.get("reason") or "invalid_reference"))
                if failures:
                    invalidated += 1
                    if row.get("status") != "invalidated":
                        row["recorded_status"] = row.get("status")
                        row["status"] = "invalidated"
                        row["invalidated_at"] = checked_at
                        changed = True
                    if row.get("source_current") is not False or row.get("invalidation_reasons") != sorted(set(failures)):
                        row["source_current"] = False
                        row["invalidation_reasons"] = sorted(set(failures))
                        changed = True
                elif row.get("status") != "invalidated" and row.get("source_current") is not True:
                    row["source_current"] = True
                    changed = True
                row["memory_is_current_proof"] = False
                row["requires_current_observation"] = True
            if changed:
                self._persist()
            self._last_validation_at = checked_at
            return {
                "checked": len(self._records),
                "invalidated": invalidated,
                "changed": changed,
                "checked_at": checked_at,
                "memory_is_current_proof": False,
            }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        self.validate_sources()
        try:
            bound = max(0, min(int(limit), self.max_records))
        except (TypeError, ValueError, OverflowError):
            bound = min(20, self.max_records)
        with self._lock:
            rows = sorted(
                self._records.values(),
                key=lambda row: (float(row.get("recorded_at") or 0.0), row["incident_id"]),
                reverse=True,
            )[:bound]
            return json.loads(json.dumps(rows))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            records = list(self._records.values())
            return {
                "schema": SCHEMA,
                "records": len(records),
                "invalidated": sum(1 for row in records if row.get("status") == "invalidated"),
                "source_current": sum(1 for row in records if row.get("source_current") is True),
                "max_records": self.max_records,
                "state_path": str(self.path),
                "source_validation_checked_at": self._last_validation_at or None,
                "memory_is_current_proof": False,
            }


__all__ = ["IncidentMemory", "SCHEMA"]
