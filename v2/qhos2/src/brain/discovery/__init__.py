"""Live cognitive shadow intelligence. Read-only. Never grants write authority."""
from __future__ import annotations

from .engine import DiscoveryEngine
from .policy import STATIC_STRATEGY_POLICY, recommend_strategy

__all__ = ["DiscoveryEngine", "STATIC_STRATEGY_POLICY", "recommend_strategy"]
