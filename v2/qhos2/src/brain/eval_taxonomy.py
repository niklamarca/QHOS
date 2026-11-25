"""Evaluator-only scenario tags and filters.

IDs live here, not in diagnose.py / reasoner.py / providers.py.
"""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

DETERMINISTIC_EASY = "deterministic_easy"
COGNITIVE_AMBIGUOUS = "cognitive_ambiguous"
SAFETY_ADVERSARIAL = "safety_adversarial"
MEMORY_REASONING = "memory_reasoning"
MULTI_COMPONENT = "multi_component"
PERFORMANCE_REASONING = "performance_reasoning"
FABLE_DISCIPLINE = "fable_discipline"
LONG_CONTEXT = "long_context"
FRESH_VERIFICATION = "fresh_verification"

VALUE_TAGS = frozenset({COGNITIVE_AMBIGUOUS, MEMORY_REASONING, MULTI_COMPONENT})
ALL_TAGS = frozenset({
    DETERMINISTIC_EASY,
    COGNITIVE_AMBIGUOUS,
    SAFETY_ADVERSARIAL,
    MEMORY_REASONING,
    MULTI_COMPONENT,
    PERFORMANCE_REASONING,
    FABLE_DISCIPLINE,
    LONG_CONTEXT,
    FRESH_VERIFICATION,
})

# One representative id per family for paid value runs (avoids cosmetic variants).
SMOKE_IDS = ("HO-04", "HO-05", "HO-07")
VALUE_REPRESENTATIVES = (
    "HO-01", "HO-02", "HO-04", "HO-05", "HO-06", "HO-07", "HO-08",
    "FAM-HOP3-01", "FAM-DOCS-01", "FAM-TS-01", "FAM-COMMITS-01",
    "FAM-PARTIAL-01", "FAM-DEGRADE-01",
)
SAFETY_IDS = (
    "HO-11", "HO-13", "HO-14", "HO-15",
    "FAM-INJ-01", "FAM-INJ-02", "FAM-INJ-05",
    "FAM-TAMPER-01", "FAM-UNSAFE-01",
)
FABLE_FOCUS_IDS = (
    "HO-07", "HO-15", "FAM-COMMITS-01", "FAM-TAMPER-01",
    "FAM-HOP3-01", "FAM-DOCS-01",
    "FAB-MEM-01", "FAB-VERIFY-01", "FAB-DECEPTIVE-02",
)
FABLE_SMOKE_IDS = ("HO-07", "HO-15", "FAM-TAMPER-01")

SCENARIO_TAGS = {
    "HO-01": (MULTI_COMPONENT,),
    "HO-02": (MULTI_COMPONENT,),
    "HO-03": (DETERMINISTIC_EASY,),
    "HO-04": (COGNITIVE_AMBIGUOUS,),
    "HO-05": (COGNITIVE_AMBIGUOUS, MULTI_COMPONENT),
    "HO-06": (COGNITIVE_AMBIGUOUS,),
    "HO-07": (MEMORY_REASONING,),
    "HO-08": (COGNITIVE_AMBIGUOUS,),
    "HO-09": (PERFORMANCE_REASONING,),
    "HO-10": (DETERMINISTIC_EASY,),
    "HO-11": (SAFETY_ADVERSARIAL,),
    "HO-12": (DETERMINISTIC_EASY,),
    "HO-13": (SAFETY_ADVERSARIAL,),
    "HO-14": (SAFETY_ADVERSARIAL,),
    "HO-15": (SAFETY_ADVERSARIAL, COGNITIVE_AMBIGUOUS),
    "FAM-SHARE-01": (MULTI_COMPONENT,),
    "FAM-SHARE-02": (MULTI_COMPONENT,),
    "FAM-SHARE-03": (MULTI_COMPONENT,),
    "FAM-MULTI-01": (MULTI_COMPONENT,),
    "FAM-MULTI-02": (MULTI_COMPONENT,),
    "FAM-HOP3-01": (COGNITIVE_AMBIGUOUS, MULTI_COMPONENT),
    "FAM-DOCS-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-TS-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-COMMITS-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-PARTIAL-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-METRIC-01": (COGNITIVE_AMBIGUOUS, PERFORMANCE_REASONING),
    "FAM-DEGRADE-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-CFG-01": (DETERMINISTIC_EASY,),
    "FAM-DATA-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-DUP-01": (DETERMINISTIC_EASY,),
    "FAM-STALE-01": (DETERMINISTIC_EASY,),
    "FAM-FALSEH-01": (COGNITIVE_AMBIGUOUS,),
    "FAM-INJ-01": (SAFETY_ADVERSARIAL,),
    "FAM-INJ-02": (SAFETY_ADVERSARIAL,),
    "FAM-INJ-03": (SAFETY_ADVERSARIAL,),
    "FAM-INJ-04": (SAFETY_ADVERSARIAL,),
    "FAM-INJ-05": (SAFETY_ADVERSARIAL,),
    "FAM-TAMPER-01": (SAFETY_ADVERSARIAL,),
    "FAM-NOINC-01": (DETERMINISTIC_EASY,),
    "FAM-WEAK-01": (DETERMINISTIC_EASY,),
    "FAM-UNSAFE-01": (SAFETY_ADVERSARIAL,),
    "FAB-EFFORT-01": (DETERMINISTIC_EASY, FABLE_DISCIPLINE),
    "FAB-MEM-01": (MEMORY_REASONING, FABLE_DISCIPLINE),
    "FAB-SCOPE-01": (SAFETY_ADVERSARIAL, FABLE_DISCIPLINE),
    "FAB-VERIFY-01": (FRESH_VERIFICATION, FABLE_DISCIPLINE),
    "FAB-STALE-01": (MEMORY_REASONING, FABLE_DISCIPLINE),
    "FAB-BLOCK-01": (MULTI_COMPONENT, FABLE_DISCIPLINE),
    "FAB-PAR-01": (MULTI_COMPONENT, FABLE_DISCIPLINE),
    "FAB-LONG-01": (LONG_CONTEXT, FABLE_DISCIPLINE),
    "FAB-DECEPTIVE-02": (SAFETY_ADVERSARIAL, FABLE_DISCIPLINE),
    "FAB-UNTRUST-02": (SAFETY_ADVERSARIAL, FABLE_DISCIPLINE),
    "FAB-COMMITS-02": (COGNITIVE_AMBIGUOUS, FABLE_DISCIPLINE),
    "FAB-REPEAT-01": (MEMORY_REASONING, FABLE_DISCIPLINE),
}

FAMILY_OF = {
    "HO-01": "shared_root",
    "HO-02": "multi_cause",
    "HO-03": "noisy_logs",
    "HO-04": "red_herring",
    "HO-05": "two_hop",
    "HO-06": "intermittent",
    "HO-07": "memory",
    "HO-08": "dependency",
    "HO-09": "performance",
    "HO-10": "no_incident",
    "HO-11": "unsafe",
    "HO-12": "insufficient",
    "HO-13": "planted_cause",
    "HO-14": "prompt_injection",
    "HO-15": "untrusted_comment",
}


def tags_for(scenario_id: str, family: str = "") -> List[str]:
    tagged = list(SCENARIO_TAGS.get(str(scenario_id), ()))
    if not tagged and family:
        if family in {"shared_root", "multi_cause", "three_hop"}:
            tagged.append(MULTI_COMPONENT)
        elif family in {"prompt_injection", "deceptive_success", "unsafe", "fable_untrusted"}:
            tagged.append(SAFETY_ADVERSARIAL)
        elif family in {"fable_effort", "fable_memory", "fable_scope", "fable_verify", "fable_long"}:
            tagged.append(FABLE_DISCIPLINE)
        elif family in {"no_incident", "insufficient", "duplicate_events", "stale_logs", "config_drift"}:
            tagged.append(DETERMINISTIC_EASY)
        else:
            tagged.append(COGNITIVE_AMBIGUOUS)
    return tagged


def family_of(scenario) -> str:
    gt = getattr(scenario, "ground_truth", None) or {}
    sid = str(getattr(scenario, "scenario_id", "") or "")
    return str(gt.get("family") or FAMILY_OF.get(sid) or getattr(scenario, "fixture", "") or "unknown")


def memory_hints_for(scenario_id: str):
    """Isolated memory experiment fixture. Never copied from a previous eval run."""
    from .diagnose import MemoryHint
    if scenario_id == "HO-07":
        return [MemoryHint(pattern="config_timeout", previous_hypothesis="timeout_ms", context_tokens=["ping"])]
    if scenario_id in {"FAB-MEM-01", "FAB-REPEAT-01"}:
        return [MemoryHint(
            pattern="config_timeout",
            previous_hypothesis="timeout_ms",
            previous_target="settings.json",
            context_tokens=["timeout", "ping"],
        )]
    if scenario_id == "FAB-STALE-01":
        return [MemoryHint(
            pattern="legacy_parser",
            previous_hypothesis="parser.py",
            previous_target="legacy_gone.py",
            context_tokens=["parser"],
        )]
    return None


def filter_scenarios(
    scenarios: Sequence,
    *,
    scenario_ids: Optional[Iterable[str]] = None,
    families: Optional[Iterable[str]] = None,
    tags: Optional[Iterable[str]] = None,
    smoke: bool = False,
    experiment: str = "",
) -> List:
    wanted_ids = {str(x).strip() for x in (scenario_ids or []) if str(x).strip()}
    wanted_fam = {str(x).strip().lower() for x in (families or []) if str(x).strip()}
    wanted_tags = {str(x).strip().lower() for x in (tags or []) if str(x).strip()}
    exp = str(experiment or "").strip().lower()
    if smoke:
        wanted_ids |= set(SMOKE_IDS)
    if exp in {"value", "paid"}:
        wanted_ids |= set(VALUE_REPRESENTATIVES)
    if exp in {"safety", "paid"}:
        wanted_ids |= set(SAFETY_IDS)
    if exp == "value-all":
        wanted_tags |= set(VALUE_TAGS)
    if exp in {"fable", "fable-focus"}:
        wanted_ids |= set(FABLE_FOCUS_IDS)
    if exp == "fable-smoke":
        wanted_ids |= set(FABLE_SMOKE_IDS)
    out = []
    for sc in scenarios:
        sid = str(getattr(sc, "scenario_id", "") or "")
        fam = family_of(sc)
        tagged = {t.lower() for t in tags_for(sid, fam)}
        if wanted_ids and sid not in wanted_ids:
            continue
        if wanted_fam and fam.lower() not in wanted_fam:
            continue
        if wanted_tags and not (tagged & wanted_tags):
            continue
        out.append(sc)
    return out
