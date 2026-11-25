"""ResearchNeed foundation. No crawler. No silent external search."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict


TRIGGERS = (
    "unknown_dependency_behavior",
    "new_provider_version",
    "unknown_error_signature",
    "documentation_uncertainty",
)


@dataclass
class ResearchNeed:
    trigger: str
    question: str
    component: str
    internal_detail_allowed: bool = False
    web_search_allowed: bool = False
    status: str = "RESEARCH_NEEDED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "crawler": False,
            "sends_internal_details": False,
        }


def propose_research(blob: str, component: str = "unknown") -> ResearchNeed | None:
    text = str(blob or "").lower()
    if "unknown error" in text or "unfamiliar signature" in text:
        return ResearchNeed("unknown_error_signature", "identify error family locally first", component)
    if "unknown dependency" in text:
        return ResearchNeed("unknown_dependency_behavior", "inspect lockfile locally", component)
    return None
