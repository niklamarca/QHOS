"""Source-trust classes. MODEL is never authoritative by default."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class Provenance(str, Enum):
    OBSERVATION = "OBSERVATION"
    TEST = "TEST"
    METRIC = "METRIC"
    GIT = "GIT"
    TOOL = "TOOL"
    MODEL = "MODEL"
    MEMORY = "MEMORY"
    HUMAN_FEEDBACK = "HUMAN_FEEDBACK"
    EVALUATOR = "EVALUATOR"
    EXTERNAL_REFERENCE = "EXTERNAL_REFERENCE"


AUTHORITATIVE = frozenset({
    Provenance.OBSERVATION.value,
    Provenance.TEST.value,
    Provenance.TOOL.value,
    Provenance.GIT.value,
    Provenance.METRIC.value,
    Provenance.HUMAN_FEEDBACK.value,
})


def is_authoritative(source: str) -> bool:
    return str(source or "").upper() in AUTHORITATIVE


def stamp(item: Dict[str, Any], source: str) -> Dict[str, Any]:
    out = dict(item)
    out["provenance"] = str(source or Provenance.MODEL.value).upper()
    out["authoritative"] = is_authoritative(out["provenance"])
    if out["provenance"] == Provenance.MODEL.value:
        out["authoritative"] = False
        out["epistemic_status"] = out.get("epistemic_status") or "HYPOTHESIS"
    return out
