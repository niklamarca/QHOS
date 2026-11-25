"""Optional provider construction without importing the managed server."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional, Tuple


def anthropic_client(
    api_key: str, *, timeout_s: Optional[float] = None, max_retries: Optional[int] = None,
) -> Tuple[Optional[object], Optional[str]]:
    try:
        import anthropic
    except ImportError:
        return None, "anthropic_not_installed"
    kwargs: Dict[str, Any] = {"api_key": api_key}
    if timeout_s is not None:
        kwargs["timeout"] = float(timeout_s)
    if max_retries is not None:
        kwargs["max_retries"] = int(max_retries)
    try:
        return anthropic.Anthropic(**kwargs), None
    except Exception as exc:
        return None, f"client_init_failed:{type(exc).__name__}"


def close_client(client: Any) -> None:
    closer = getattr(client, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def is_timeout_error(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def loads_json_lenient(raw: str) -> Optional[Dict[str, Any]]:
    """Accept a complete object with prose/fences; never repair truncated JSON."""
    text = str(raw or "").strip()
    start = text.find("{")
    if start < 0:
        return None
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
