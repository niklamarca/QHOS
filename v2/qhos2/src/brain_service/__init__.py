"""Standalone Brain process — observes Quant, never shares its fate.

This package is the process boundary. The domain library stays in ``brain/``.
The Linux Level-2 profile is observation-only and cannot control the Trader.
Legacy guarded relight remains opt-in for the old Windows launcher only. The
service may not FORCE, deploy, trade, or bind a QuantHPCSystem owner.
"""
from __future__ import annotations

PROCESS_NAME = "qhos-brain-service"
DEFAULT_PORT = 8091
DEFAULT_QUANT_URL = "http://127.0.0.1:8766"

__all__ = ["PROCESS_NAME", "DEFAULT_PORT", "DEFAULT_QUANT_URL"]
