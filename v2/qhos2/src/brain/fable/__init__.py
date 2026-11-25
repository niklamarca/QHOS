"""Fable-derived cognitive discipline for the Trader Brain.

Fable is a SOURCE OF OPERATING PRINCIPLES. It is not the Brain.
Never paste leaked prompts into runtime. Never auto-sync upstream.
Never make this the default runtime strategy.
"""
from __future__ import annotations

from .concepts import CONCEPTS, classification_counts, completeness_report
from .effort import CognitiveEffortPolicy, EffortLevel, select_effort
from .result import FableReasoningResult
from .strategy import FableDerivedStrategy
from .source import PINNED_SOURCES, SOURCE_LABELS

__all__ = [
    "CONCEPTS",
    "CognitiveEffortPolicy",
    "EffortLevel",
    "FableDerivedStrategy",
    "FableReasoningResult",
    "PINNED_SOURCES",
    "SOURCE_LABELS",
    "classification_counts",
    "completeness_report",
    "select_effort",
]
