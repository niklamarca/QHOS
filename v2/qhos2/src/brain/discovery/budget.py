"""Cognitive cost governor, backpressure, discovery breaker. Trader stays up."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Set

from brain.models import utc_ts


@dataclass
class CognitiveGovernor:
    max_llm_per_hour: int = 4
    max_tools_per_investigation: int = 6
    max_simultaneous: int = 2
    max_xhigh: int = 1
    cooldown_s: float = 300.0
    llm_calls: int = 0
    window_start: float = 0.0
    active: int = 0
    xhigh: int = 0
    cool: Dict[str, float] = field(default_factory=dict)
    deferred: int = 0
    open: bool = False

    def reset_window(self, now: float) -> None:
        if not self.window_start or now - self.window_start >= 3600:
            self.window_start = now
            self.llm_calls = 0

    def allow_llm(self, fingerprint: str, now: float | None = None) -> bool:
        now = now or utc_ts()
        self.reset_window(now)
        if self.open:
            return False
        if self.llm_calls >= self.max_llm_per_hour:
            return False
        last = self.cool.get(fingerprint)
        if last is not None and now - last < self.cooldown_s:
            return False
        return True

    def record_llm(self, fingerprint: str, now: float | None = None) -> None:
        now = now or utc_ts()
        self.llm_calls += 1
        self.cool[fingerprint] = now

    def admit_investigation(self, *, effort: str = "LOW") -> bool:
        if self.open:
            return False
        if self.active >= self.max_simultaneous:
            self.deferred += 1
            return False
        if effort in {"XHIGH", "MAX"} and self.xhigh >= self.max_xhigh:
            self.deferred += 1
            return False
        self.active += 1
        if effort in {"XHIGH", "MAX"}:
            self.xhigh += 1
        return True

    def release(self, *, effort: str = "LOW") -> None:
        self.active = max(0, self.active - 1)
        if effort in {"XHIGH", "MAX"}:
            self.xhigh = max(0, self.xhigh - 1)

    def trip(self) -> None:
        self.open = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_calls_hour": self.llm_calls,
            "active_investigations": self.active,
            "deferred": self.deferred,
            "breaker_open": self.open,
            "fallback": "deterministic_only" if self.open else None,
        }
