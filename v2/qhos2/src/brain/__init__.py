"""Trader Brain — observability, memory and guarded orchestration.

The Brain observes the QHOS runtime. It does not trade, submit orders,
change risk limits, or write production state. Shadow mode is the default.
A Brain crash must never take the quantitative server down.
"""
from __future__ import annotations

SCHEMA = "qhos-brain-v1"
LACOS_SCHEMA = "qhos-brain-lacos-v1"

__all__ = ["SCHEMA", "LACOS_SCHEMA"]
