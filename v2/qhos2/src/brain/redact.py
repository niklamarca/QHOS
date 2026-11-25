"""Deterministic redaction BEFORE any CognitiveProvider sees context.

Fail closed. Secrets never enter model context, memory, audit, or Laços.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Tuple


CLASS_SAFE = "SAFE"
CLASS_RESTRICTED = "RESTRICTED"
CLASS_DENIED = "DENIED"

_SECRET_RES = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(postgres|mysql|mongodb|redis|amqp)://\S+"),
    re.compile(r"(?i)(aws_secret_access_key|private_key)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password|passwd|authorization)\s*[:=]\s*\S+"),
)

_DENIED_PATHS = (
    ".env", "secrets", "credentials", "id_rsa", ".pem", ".key",
    "governance/control.py", "data_lake/",
)
_RESTRICTED_PATHS = ("execution/", "risk/", "governance/")


def sanitize_error(exc: BaseException | str) -> str:
    text = str(exc)[:240]
    for pat in _SECRET_RES:
        text = pat.sub("[REDACTED]", text)
    return text


def trace_tail(exc: BaseException, limit: int = 4) -> str:
    """Last frames as ``file.py:line func``; no locals, arguments or exception text."""
    import traceback

    frames = traceback.extract_tb(exc.__traceback__)[-max(1, int(limit)):]
    return " <- ".join(
        f"{str(frame.filename).replace(chr(92), '/').rsplit('/', 1)[-1]}:{frame.lineno} {frame.name}"
        for frame in reversed(frames)
    )


def classify_text(text: str) -> str:
    raw = text or ""
    low = raw.lower()
    if any(p.search(raw) for p in _SECRET_RES):
        return CLASS_DENIED
    if any(tok in low for tok in _DENIED_PATHS):
        if any(tok in low for tok in (".env", "secret", "credential", "private key", "api_key")):
            return CLASS_DENIED
    if any(tok in low for tok in _RESTRICTED_PATHS):
        return CLASS_RESTRICTED
    if len(raw) > 8000:
        return CLASS_RESTRICTED
    return CLASS_SAFE


def classify_path_for_provider(path: str) -> str:
    low = str(path or "").replace("\\", "/").lower()
    if any(tok in low for tok in _DENIED_PATHS):
        return CLASS_DENIED
    if any(low.startswith(tok) or f"/{tok}" in f"/{low}" for tok in _RESTRICTED_PATHS):
        return CLASS_RESTRICTED
    return CLASS_SAFE


def redact_text(text: str) -> str:
    out = text or ""
    for pat in _SECRET_RES:
        out = pat.sub("[REDACTED]", out)
    return out[:4000]


def redact_mapping(row: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in row.items():
        lk = str(key).lower()
        if lk in {"api_key", "secret", "token", "password", "authorization", "private_key"}:
            out[key] = "[REDACTED]"
            continue
        if isinstance(value, str):
            out[key] = redact_text(value)
        elif isinstance(value, Mapping):
            out[key] = redact_mapping(value)
        elif isinstance(value, list):
            out[key] = [redact_mapping(x) if isinstance(x, Mapping) else redact_text(str(x)) if isinstance(x, str) else x for x in value[:20]]
        else:
            out[key] = value
    return out


def prepare_provider_context(raw: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Sanitize then classify. Denied material is dropped, not sent."""
    notes: List[str] = []
    blob = redact_mapping(raw)
    denied = classify_text(str(blob)) == CLASS_DENIED
    if denied:
        notes.append("denied_secret_material")
        blob = {
            "observations": [],
            "memory_candidates": [],
            "files": [],
            "graph": [],
            "redacted": True,
            "reason": "denied_secret_material",
            "instructions": blob.get("instructions") or "",
            "ground_truth_present": False,
        }
        return blob, notes
    files = []
    for item in blob.get("files") or []:
        if not isinstance(item, Mapping):
            continue
        klass = classify_path_for_provider(str(item.get("path") or ""))
        if klass == CLASS_DENIED:
            notes.append(f"denied_file:{item.get('path')}")
            continue
        if klass == CLASS_RESTRICTED:
            notes.append(f"restricted_file:{item.get('path')}")
            files.append({**dict(item), "excerpt": "[RESTRICTED_SNIPPET]", "class": CLASS_RESTRICTED})
            continue
        files.append({**dict(item), "class": CLASS_SAFE})
    blob["files"] = files
    blob["data_policy"] = {"schema": "qhos-brain-data-policy-v1", "default": "fail_closed"}
    return blob, notes
