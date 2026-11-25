"""Deterministic root-cause fingerprinting. No LLM, no network, no randomness.

The soak exposed one missing dependency (``ccxt``) surfacing as one error line per
crypto symbol. Those lines share a root cause and must collapse into a single
``PotentialIncident`` carrying many ``IncidentOccurrence`` rows.

Two rules keep this safe:

* collapse only on discriminators we actually extracted (exception type,
  dependency, error code);
* when nothing concrete is extracted, fall back to a structure-preserving message
  skeleton so unrelated messages never share a root.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ROOT_FINGERPRINT_VERSION = "rootfp-v1"

# Root tiers, ordered from most to least specific.
ROOT_TIERS = ("DEPENDENCY", "ERROR_CODE", "EXCEPTION", "STATE", "MESSAGE")

_EXCEPTION_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9_]{2,60}(?:Error|Exception|Warning|Timeout|Failure|Fault))\b"
)
_DEPENDENCY_RES = (
    re.compile(r"no module named ['\"]?([A-Za-z0-9_][A-Za-z0-9_.\-]{0,60})", re.I),
    re.compile(r"cannot import name ['\"]?([A-Za-z0-9_][A-Za-z0-9_.\-]{0,60})", re.I),
    re.compile(r"modulenotfounderror[:\s]+([A-Za-z0-9_][A-Za-z0-9_.\-]{0,60})", re.I),
    re.compile(r"importerror[:\s]+([A-Za-z0-9_][A-Za-z0-9_.\-]{0,60})", re.I),
)
_ERROR_CODE_RE = re.compile(
    r"\b(?:err(?:no)?|error_code|errcode|code|status_code|http)[=:\s]+([A-Z0-9][A-Z0-9_\-]{1,15})\b",
    re.I,
)
_LOG_PIPE_RE = re.compile(
    r"^\s*(?P<ts>\S+)\s*\|\s*(?P<level>[A-Z]+)\s*\|\s*(?P<logger>[\w.\-]{1,60})\s*\|\s*(?P<body>.*)$",
    re.S,
)
_STACK_FRAME_RE = re.compile(r'File "([^"]+)", line \d+, in (\S+)')

# High-cardinality tokens that must never enter a root key.
_SCRUBBERS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:[+-]\d{2}:?\d{2}|Z)?"), "<ts>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<date>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<time>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b(?:pid|ppid|tid|thread|thread_id|worker)[=:\s#]+\d+", re.I), "<procid>"),
    (re.compile(r"\b(?:request_id|req_id|correlation_id|corr_id|trace_id|order_id|client_id)[=:\s]+\S+", re.I), "<reqid>"),
    (re.compile(r"\b[A-Z]{2,10}[/\-][A-Z]{2,10}(?::[A-Z0-9]{1,8})?\b"), "<symbol>"),
    (re.compile(r"\b[A-Z]{1,4}[FGHJKMNQUVXZ]\d{1,2}\b"), "<symbol>"),
    (re.compile(r"\b(?:price|qty|quantity|size|notional|pnl|nav)[=:\s]+-?\d+(?:\.\d+)?", re.I), "<num>"),
    (re.compile(r"\b0x[0-9a-f]{4,}\b", re.I), "<hex>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?"), "<num>"),
    (re.compile(r"\s+"), " "),
)

_SYMBOL_RES = (
    re.compile(r"\b([A-Z]{2,10}[/\-][A-Z]{2,10}(?::[A-Z0-9]{1,8})?)\b"),
    re.compile(r"\b([A-Z]{1,4}[FGHJKMNQUVXZ]\d{1,2})\b"),
)

# Component names carry instance suffixes we do not want in the root key.
_COMPONENT_SPLIT_RE = re.compile(r"[^a-z0-9]+")
_GENERIC_COMPONENTS = frozenset({"logs", "log", "unknown", "runtime", "stream", ""})


def _scrub(text: str) -> str:
    out = str(text or "")
    for pattern, repl in _SCRUBBERS:
        out = pattern.sub(repl, out)
    return out.strip()


def normalize_message(text: str) -> str:
    """Structure-preserving skeleton with high-cardinality tokens removed."""
    parsed = _LOG_PIPE_RE.match(str(text or ""))
    body = parsed.group("body") if parsed else str(text or "")
    return _scrub(body).lower()[:200]


def component_family(component: str, *, logger: str = "") -> str:
    """Collapse instance-specific component names to a stable family."""
    raw = str(component or "").strip().lower()
    if raw in _GENERIC_COMPONENTS and logger:
        raw = str(logger).strip().lower()
    tokens = [t for t in _COMPONENT_SPLIT_RE.split(raw) if t and not t.isdigit()]
    # Drop trailing instance markers such as ``worker_3`` or ``shard0``.
    while tokens and re.fullmatch(r"(?:worker|shard|node|replica|instance|slot)\d*", tokens[-1]):
        tokens.pop()
    return "_".join(tokens)[:60] or "unknown"


def component_key(name: str) -> str:
    """Separator-insensitive identity for one component.

    ``component_family`` keeps word boundaries, which is right for a root key but
    wrong for asking "do these two names refer to the same component?".
    A registry entry says ``risk_engine`` while a log line says ``RiskEngine``;
    their families are ``risk_engine`` and ``riskengine`` and never compare equal.
    Cross-source matching must use this canonical form instead.
    """
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())[:60]


_CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+")

# A shorter key matches too much: ``test`` would hit ``latest``.
MIN_COMPONENT_KEY_LEN = 4
# Words joined per candidate key. Real component names are 1-3 words.
MAX_MENTION_RUN = 4
# Bound on work per line so a pathological log line cannot dominate a cycle.
MAX_MENTION_WORDS = 80


def component_mentions(text: str) -> frozenset:
    """Component keys a free-text line plausibly names.

    Tokens are split on separators and camelCase boundaries, then re-joined so
    ``DataIngestion`` yields ``dataingestion``, ``data`` and ``ingestion``.
    Deliberately not a raw substring test: that would make ``test`` match
    ``latest``.
    """
    words: List[str] = []
    for token in re.split(r"[^A-Za-z0-9]+", str(text or "")):
        if not token:
            continue
        words.extend(p.lower() for p in _CAMEL_SPLIT_RE.findall(token) or [token.lower()])
        if len(words) >= MAX_MENTION_WORDS:
            break
    out = set()
    for start in range(len(words)):
        for end in range(start + 1, min(start + MAX_MENTION_RUN, len(words)) + 1):
            key = "".join(words[start:end])
            if len(key) >= MIN_COMPONENT_KEY_LEN:
                out.add(key[:60])
    return frozenset(out)


def mentions_component(text: str, name: str) -> bool:
    """Whether free text names a component, tolerating separator style.

    ``risk_engine`` matches ``RiskEngine``, ``risk-engine`` and ``risk engine``.
    """
    key = component_key(name)
    if len(key) < MIN_COMPONENT_KEY_LEN:
        return False
    return key in component_mentions(text)


def extract_exception(text: str) -> str:
    match = _EXCEPTION_RE.search(str(text or ""))
    if match:
        return match.group(1)
    # G1.1 proposal hook: dotted lowercase names such as ``socket.gaierror``,
    # which the capitalised pattern above cannot see. Inactive by default.
    from .policy_g11 import extract_dotted_exception

    return extract_dotted_exception(text)


def extract_dependency(text: str) -> str:
    for pattern in _DEPENDENCY_RES:
        match = pattern.search(str(text or ""))
        if not match:
            continue
        name = match.group(1).strip("'\"` ,.;:()")
        if name and not name.lower().startswith("no module"):
            return name.lower()[:60]
    return ""


def extract_error_code(text: str) -> str:
    match = _ERROR_CODE_RE.search(str(text or ""))
    if not match:
        return ""
    code = match.group(1).upper()
    return "" if code in {"OK", "NONE"} else code[:16]


def extract_instance(text: str) -> str:
    """Instance label (symbol/instrument) an occurrence belongs to."""
    for pattern in _SYMBOL_RES:
        match = pattern.search(str(text or ""))
        if match:
            return match.group(1)[:24]
    return ""


def stack_signature(text: str) -> str:
    frames = _STACK_FRAME_RE.findall(str(text or ""))
    if not frames:
        return ""
    parts = [f"{path.replace(chr(92), '/').rsplit('/', 1)[-1]}:{func}" for path, func in frames[-3:]]
    return "|".join(parts)[:120]


def callsite_family(text: str) -> str:
    parsed = _LOG_PIPE_RE.match(str(text or ""))
    if parsed:
        return component_family(parsed.group("logger"))
    stack = stack_signature(text)
    return component_family(stack.split(":")[0]) if stack else ""


@dataclass
class RootSignature:
    root_id: str
    tier: str
    kind: str = ""
    exception_type: str = ""
    dependency: str = ""
    error_code: str = ""
    component_family: str = ""
    callsite_family: str = ""
    stack_signature: str = ""
    normalized_message: str = ""
    state: str = ""
    instance_hint: str = ""
    concrete: bool = False
    basis: List[str] = field(default_factory=list)
    version: str = ROOT_FINGERPRINT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _hash(parts: Any) -> str:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def root_signature(
    *,
    kind: str,
    component: str,
    summary: str,
    state: str = "",
) -> RootSignature:
    """Deterministic root signature. Same inputs always give the same root_id."""
    text = str(summary or "")
    logger = callsite_family(text)
    fam = component_family(component, logger=logger)
    exc = extract_exception(text)
    dep = extract_dependency(text)
    code = extract_error_code(text)
    stack = stack_signature(text)
    skeleton = normalize_message(text)
    instance = extract_instance(text)
    kind_l = str(kind or "").lower()

    if dep:
        # A missing dependency is one root cause regardless of caller or symbol.
        tier = "DEPENDENCY"
        key = ("DEPENDENCY", exc or "ImportError", dep)
        basis = ["dependency", "exception_type"] if exc else ["dependency"]
        concrete = True
    elif exc and code:
        tier = "ERROR_CODE"
        key = ("ERROR_CODE", fam, exc, code)
        basis = ["component_family", "exception_type", "error_code"]
        concrete = True
    elif exc:
        # Same component + same exception + same skeleton. Skeleton retains
        # structure so two unrelated failures do not merge.
        tier = "EXCEPTION"
        key = ("EXCEPTION", fam, exc, stack or skeleton)
        basis = ["component_family", "exception_type", "stack_signature" if stack else "normalized_message"]
        concrete = True
    elif kind_l in {"health", "state", "heartbeat"} or state:
        tier = "STATE"
        key = ("STATE", fam, (state or skeleton or "unknown").lower())
        basis = ["component_family", "state"]
        concrete = False
    else:
        tier = "MESSAGE"
        key = ("MESSAGE", kind_l, fam, skeleton)
        basis = ["kind", "component_family", "normalized_message"]
        concrete = False

    return RootSignature(
        root_id=_hash((ROOT_FINGERPRINT_VERSION,) + key),
        tier=tier,
        kind=kind_l,
        exception_type=exc,
        dependency=dep,
        error_code=code,
        component_family=fam,
        callsite_family=logger,
        stack_signature=stack,
        normalized_message=skeleton,
        state=str(state or "")[:40],
        instance_hint=instance,
        concrete=concrete,
        basis=basis,
    )


def root_title(sig: RootSignature) -> str:
    """Human-facing root label. Deterministic, no model text."""
    if sig.dependency:
        return f"missing dependency '{sig.dependency}' ({sig.exception_type or 'ImportError'})"
    if sig.exception_type and sig.error_code:
        return f"{sig.exception_type} code={sig.error_code} in {sig.component_family}"
    if sig.exception_type:
        return f"{sig.exception_type} in {sig.component_family}"
    if sig.tier == "STATE":
        return f"{sig.component_family} state={sig.state or 'unknown'}"
    return f"{sig.component_family}: {sig.normalized_message[:80]}"


@dataclass
class RootCluster:
    """Occurrences grouped under one root cause."""

    root_id: str
    signature: RootSignature
    occurrences: List[Dict[str, Any]] = field(default_factory=list)
    instances: List[str] = field(default_factory=list)
    components: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    max_severity: str = "low"

    @property
    def occurrence_count(self) -> int:
        return len(self.occurrences)

    @property
    def affected_instances(self) -> int:
        return len(self.instances) or 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_id": self.root_id,
            "root_title": root_title(self.signature),
            "signature": self.signature.to_dict(),
            "occurrence_count": self.occurrence_count,
            "affected_instances": self.affected_instances,
            "instances": list(self.instances)[:24],
            "affected_components": list(self.components)[:16],
            "sources": list(self.sources)[:8],
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "max_severity": self.max_severity,
            "occurrences": list(self.occurrences)[:40],
        }


_SEV_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def severity_max(a: str, b: str) -> str:
    return a if _SEV_ORDER.get(str(a), 0) >= _SEV_ORDER.get(str(b), 0) else b


def group_by_root(rows: Any, *, state_of: Optional[Any] = None) -> Dict[str, RootCluster]:
    """Group observation-like rows into root clusters. Order-independent keys."""
    clusters: Dict[str, RootCluster] = {}
    for row in rows or []:
        get = row.get if hasattr(row, "get") else (lambda k, d=None: getattr(row, k, d))
        kind = str(get("kind", "") or "")
        component = str(get("component", "") or "")
        summary = str(get("payload_summary", None) or get("summary", "") or "")
        state = ""
        if state_of is not None:
            state = str(state_of(row) or "")
        sig = root_signature(kind=kind, component=component, summary=summary, state=state)
        cluster = clusters.get(sig.root_id)
        ts = float(get("timestamp", 0.0) or 0.0)
        severity = str(get("severity_hint", None) or get("severity", "low") or "low")
        if cluster is None:
            cluster = RootCluster(
                root_id=sig.root_id,
                signature=sig,
                first_seen=ts,
                last_seen=ts,
                max_severity=severity,
            )
            clusters[sig.root_id] = cluster
        occurrence = {
            "occurrence_id": _hash((sig.root_id, get("id", "") or "", ts, sig.instance_hint)),
            "root_id": sig.root_id,
            "observation_id": str(get("id", "") or ""),
            "component": component,
            "instance": sig.instance_hint,
            "source": str(get("source", "") or ""),
            "timestamp": ts,
            "severity": severity,
            "summary": summary[:200],
        }
        cluster.occurrences.append(occurrence)
        if sig.instance_hint and sig.instance_hint not in cluster.instances:
            cluster.instances.append(sig.instance_hint)
        if component and component not in cluster.components:
            cluster.components.append(component)
        src = occurrence["source"]
        if src and src not in cluster.sources:
            cluster.sources.append(src)
        cluster.first_seen = min(cluster.first_seen or ts, ts) if ts else cluster.first_seen
        cluster.last_seen = max(cluster.last_seen, ts)
        cluster.max_severity = severity_max(cluster.max_severity, severity)
    return clusters
