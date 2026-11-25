"""LIVE_SHADOW_SOAK. Default off. Bounded, sanitized, outside the data lake."""
from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from brain.eval_paths import EVAL_DIRNAME, atomic_write_json, state_root
from brain.models import new_id, utc_ts
from brain.redact import classify_text, redact_mapping, redact_text, CLASS_DENIED
from brain.local_config import BRAIN_ROOT_DIR, config

from .inventory import inventory


SHADOW_SUBDIR = "shadow"
SOAK_STATUSES = ("CREATED", "RUNNING", "COMPLETED", "INTERRUPTED", "FAILED")
MAX_SESSIONS_DEFAULT = 8
MAX_EVENTS_DEFAULT = 4000
MAX_AGE_DAYS_DEFAULT = 7
MAX_BYTES_DEFAULT = 8_000_000
# Artifact families under retention. Analysis outputs age with their session.
ARTIFACT_PREFIXES = (
    "soak_", "reviews_", "replay_", "long_soak_", "hybrid_replay_", "calibration_",
)


def shadow_dir() -> Path:
    state = state_root()
    dest = state / SHADOW_SUBDIR
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def soak_enabled() -> bool:
    return bool(getattr(config, "brain_live_shadow_soak_enabled", False))


def repo_revision() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(getattr(config, "brain_root_dir", None) or BRAIN_ROOT_DIR),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return (proc.stdout or "").strip()[:16] or "unknown"
    except Exception:
        return "unknown"


def inventory_revision() -> str:
    names = ",".join(s.get("name") or "" for s in inventory().get("sources") or [])
    return hashlib.sha256(names.encode("utf-8")).hexdigest()[:16]


def sanitize_observation_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Redact and classify BEFORE persistence. Denied rows are dropped."""
    cleaned = redact_mapping(row if isinstance(row, dict) else {})
    blob = " ".join(str(cleaned.get(k) or "") for k in ("payload_summary", "summary", "kind"))
    if classify_text(blob) == CLASS_DENIED:
        return None
    out = {
        "id": str(cleaned.get("id") or "")[:40],
        "timestamp": cleaned.get("timestamp"),
        "source": str(cleaned.get("source") or "")[:40],
        "component": str(cleaned.get("component") or "")[:80],
        "kind": str(cleaned.get("kind") or "")[:40],
        "severity_hint": str(cleaned.get("severity_hint") or cleaned.get("severity") or "")[:16],
        "payload_summary": redact_text(str(cleaned.get("payload_summary") or cleaned.get("summary") or ""))[:240],
        "fingerprint": str(cleaned.get("fingerprint") or "")[:24],
        # Pending classification depends on these two. Dropping them made every
        # replay of a recorded session take a different branch from the run it
        # was replaying, so the artifact could not be used to audit the run.
        # Neither field can carry a secret: one is a status word, one a boolean.
        "state": str(cleaned.get("state") or "")[:32],
        "lifecycle_known": bool(cleaned.get("lifecycle_known", True)),
    }
    if classify_text(out["payload_summary"]) == CLASS_DENIED:
        return None
    return out


def prune_shadow(
    dest: Optional[Path] = None,
    *,
    max_sessions: Optional[int] = None,
    max_age_days: Optional[int] = None,
    max_bytes: int = MAX_BYTES_DEFAULT,
) -> Dict[str, int]:
    folder = dest or shadow_dir()
    max_sessions = int(max_sessions or getattr(config, "brain_soak_max_sessions", MAX_SESSIONS_DEFAULT) or MAX_SESSIONS_DEFAULT)
    max_age_days = int(max_age_days or getattr(config, "brain_soak_max_age_days", MAX_AGE_DAYS_DEFAULT) or MAX_AGE_DAYS_DEFAULT)
    now = utc_ts()
    files = sorted(
        [p for p in folder.glob("*.json") if p.is_file() and p.name.startswith(ARTIFACT_PREFIXES)],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Retention applies to soak session artifacts; derived analysis artifacts
    # (reviews, replays, calibration) age with them.
    soak_files = [p for p in files if p.name.startswith("soak_")]
    review_files = [p for p in files if not p.name.startswith("soak_")]
    dropped = 0
    kept = 0
    for i, path in enumerate(soak_files):
        age_days = (now - path.stat().st_mtime) / 86400.0
        too_old = age_days > max_age_days
        too_many = i >= max_sessions
        too_big = path.stat().st_size > max_bytes
        if too_old or too_many or too_big:
            try:
                path.unlink()
                dropped += 1
            except OSError:
                pass
        else:
            kept += 1
    for path in review_files:
        age_days = (now - path.stat().st_mtime) / 86400.0
        if age_days > max_age_days:
            try:
                path.unlink()
                dropped += 1
            except OSError:
                pass
    return {"dropped": dropped, "kept": kept}


class SoakSession:
    def __init__(
        self,
        *,
        revision: str = "",
        duration_s: float = 0.0,
        max_events: Optional[int] = None,
        brain_revision: str = "qhos-brain-v1",
    ) -> None:
        self.session_id = new_id("soak")
        self.revision = revision or repo_revision()
        self.brain_revision = brain_revision
        self.inventory_revision = inventory_revision()
        self.started = utc_ts()
        self.ended: Optional[float] = None
        self.duration_s = float(duration_s or getattr(config, "brain_soak_max_duration_s", 60) or 60)
        self.max_events = int(max_events or getattr(config, "brain_soak_max_events", MAX_EVENTS_DEFAULT) or MAX_EVENTS_DEFAULT)
        self.status = "CREATED"
        self.signals = 0
        self.candidates = 0
        self.investigations = 0
        self.improvements = 0
        self.strategy_usage: Dict[str, int] = {}
        self.provider_usage: Dict[str, int] = {}
        self.tokens = 0
        self.human_feedback = 0
        self.safety_events = 0
        self.writes_attempted = 0
        self.llm_calls = 0
        self.tool_calls = 0
        self.observations: List[Dict[str, Any]] = []
        self.candidates_rows: List[Dict[str, Any]] = []
        self.improvements_rows: List[Dict[str, Any]] = []
        self.seen_ids: Set[str] = set()
        self.seen_improvement_keys: Set[str] = set()
        self.metrics: Dict[str, Any] = {}
        self.llm_enabled = False
        self.level3_enabled = False
        self.sandbox_enabled = False

    def start(self) -> None:
        self.status = "RUNNING"
        self.started = utc_ts()

    def expired(self, now: Optional[float] = None) -> bool:
        now = float(now if now is not None else utc_ts())
        return (now - self.started) >= self.duration_s

    def record_cycle(self, intel: Dict[str, Any], observations: List[Dict[str, Any]]) -> None:
        if self.status == "CREATED":
            self.status = "RUNNING"
        if len(self.observations) >= self.max_events:
            return
        self.signals += len(observations)
        self.candidates += len(intel.get("incidents") or [])
        self.investigations += len(intel.get("investigations") or [])
        self.improvements += len(intel.get("improvements") or [])
        if intel.get("metrics"):
            self.metrics = dict(intel.get("metrics") or {})
        for cand in list(intel.get("incidents") or []):
            if isinstance(cand, dict):
                fp = str(cand.get("fingerprint") or cand.get("candidate_id") or "")
                if fp and fp in {str(x.get("fingerprint") or "") for x in self.candidates_rows}:
                    continue
                row = redact_mapping(cand)
                row["disposition"] = "UNREVIEWED"
                row["not_training_truth"] = True
                self.candidates_rows.append(row)
        for imp in list(intel.get("improvements") or []):
            if isinstance(imp, dict):
                key = f"{imp.get('component')}|{imp.get('observed_problem')}"
                if key in self.seen_improvement_keys:
                    continue
                self.seen_improvement_keys.add(key)
                self.improvements_rows.append(redact_mapping(imp))
        for row in observations:
            if not isinstance(row, dict):
                continue
            oid = str(row.get("id") or "")
            if oid and oid in self.seen_ids:
                continue
            cleaned = sanitize_observation_row(row)
            if cleaned is None:
                continue
            if cleaned.get("id"):
                self.seen_ids.add(str(cleaned["id"]))
            self.observations.append(cleaned)
            if len(self.observations) >= self.max_events:
                break

    def checkpoint(self) -> Dict[str, Any]:
        payload = self.to_dict()
        payload["status"] = "RUNNING" if self.status == "RUNNING" else self.status
        atomic_write_json(shadow_dir() / f"{self.session_id}.json", payload)
        return payload

    def interrupt(self) -> Dict[str, Any]:
        self.status = "INTERRUPTED"
        self.ended = utc_ts()
        return self._persist()

    def fail(self, reason: str = "") -> Dict[str, Any]:
        self.status = "FAILED"
        self.ended = utc_ts()
        self.safety_events += 1
        payload = self.to_dict()
        payload["fail_reason"] = str(reason)[:160]
        atomic_write_json(shadow_dir() / f"{self.session_id}.json", payload)
        prune_shadow()
        return payload

    def close(self) -> Dict[str, Any]:
        self.status = "COMPLETED"
        self.ended = utc_ts()
        return self._persist()

    def _persist(self) -> Dict[str, Any]:
        payload = self.to_dict()
        atomic_write_json(shadow_dir() / f"{self.session_id}.json", payload)
        prune_shadow()
        return payload

    def safety_assertions(self) -> Dict[str, Any]:
        return {
            "change_requests": 0,
            "sandbox_runs": 0,
            "production_writes": 0,
            "trading_actions": 0,
            "autonomy_changes": 0,
            "remote_llm_calls": int(self.llm_calls),
            "ok": self.llm_calls == 0 and self.writes_attempted == 0,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": "qhos-brain-soak-v1",
            "session_id": self.session_id,
            "status": self.status,
            "repository_revision": self.revision,
            "brain_revision": self.brain_revision,
            "inventory_revision": self.inventory_revision,
            "discovery_config": {
                "llm_enabled": False,
                "level3_enabled": False,
                "sandbox_enabled": False,
                "threshold": 0.42,
                "duration_s": self.duration_s,
                "max_events": self.max_events,
            },
            "start": self.started,
            "end": self.ended,
            "start_time": self.started,
            "end_time": self.ended,
            "duration_s": None if self.ended is None else round(self.ended - self.started, 3),
            "planned_duration_s": self.duration_s,
            "signal_counts": self.signals,
            "candidate_counts": self.candidates,
            "investigations": self.investigations,
            "improvements": self.improvements,
            "strategy_usage": dict(self.strategy_usage),
            "provider_usage": dict(self.provider_usage),
            "tokens": self.tokens,
            "human_feedback": self.human_feedback,
            "safety_events": self.safety_events,
            "observations": list(self.observations),
            "candidates": list(self.candidates_rows),
            "improvement_candidates": list(self.improvements_rows),
            "metrics": dict(self.metrics),
            "safety": self.safety_assertions(),
            "llm_enabled": False,
            "level3_enabled": False,
            "sandbox_enabled": False,
            "read_only": True,
            "path_isolated": True,
            "storage": str(EVAL_DIRNAME + "/" + SHADOW_SUBDIR),
        }

    @classmethod
    def load(cls, path: Path) -> "SoakSession":
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
        session = cls(revision=str(blob.get("repository_revision") or ""))
        session.session_id = str(blob.get("session_id") or session.session_id)
        session.status = str(blob.get("status") or "INTERRUPTED")
        session.started = float(blob.get("start") or blob.get("start_time") or utc_ts())
        session.observations = list(blob.get("observations") or [])
        session.seen_ids = {str(o.get("id")) for o in session.observations if o.get("id")}
        session.candidates_rows = list(blob.get("candidates") or [])
        session.improvements_rows = list(blob.get("improvement_candidates") or [])
        session.signals = int(blob.get("signal_counts") or 0)
        session.candidates = int(blob.get("candidate_counts") or 0)
        return session
