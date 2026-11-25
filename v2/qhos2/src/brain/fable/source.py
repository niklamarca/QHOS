"""Pinned Fable source revisions. No auto-sync. Future changes need a human audit."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


ANALYSIS_TS = "2026-09-02T21:30:00-03:00"
REPO = "https://github.com/asgeirtj/system_prompts_leaks"


@dataclass(frozen=True)
class PinnedSource:
    label: str
    path: str
    commit: str
    role: str
    lines: int
    sections: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repository": REPO,
            "path": self.path,
            "commit": self.commit,
            "role": self.role,
            "label": self.label,
            "lines": self.lines,
            "top_level_sections": self.sections,
            "analysis_timestamp": ANALYSIS_TS,
            "auto_sync": False,
        }


PINNED_SOURCES = {
    "FABLE5": PinnedSource(
        label="FABLE5",
        path="Anthropic/claude-fable-5.md",
        commit="14d6a800db37e046739a8483528e8d57fb33b914",
        role="primary",
        lines=7575,
        sections=18,
    ),
    "FABLE5_1": PinnedSource(
        label="FABLE5_1",
        path="Anthropic/claude-fable-5.1.md",
        commit="0f4aeb3e7d04419c7161dd25fbd2dab23341867c",
        role="secondary",
        lines=7658,
        sections=19,
    ),
    "CODE_FABLE5": PinnedSource(
        label="CODE_FABLE5",
        path="Anthropic/claude-code/claude-code-fable-5.md",
        commit="a7d0a2699d41954ced817957ef96ad0eb7f89ea9",
        role="secondary",
        lines=6283,
        sections=22,
    ),
    "CODE_FABLE5_1": PinnedSource(
        label="CODE_FABLE5_1",
        path="Anthropic/claude-code/claude-code-fable-5.1.md",
        commit="de981be0c43858266491ace7fcafce371c9de7b8",
        role="secondary",
        lines=6450,
        sections=24,
    ),
}

SOURCE_LABELS = frozenset(PINNED_SOURCES)

# Top-level / cognitively relevant sections actually read. Tool catalogs are
# inventoried as grouped families rather than one concept per vendor method.
PRIMARY_SECTIONS = (
    "reasoning_effort_tag",
    "claude_behavior",
    "product_information",
    "refusal_handling",
    "legal_and_financial_advice",
    "tone_and_formatting",
    "user_wellbeing",
    "anthropic_reminders",
    "evenhandedness",
    "responding_to_mistakes_and_criticism",
    "knowledge_cutoff",
    "memory_filesystem",
    "end_conversation_tool_info",
    "persistent_storage_for_artifacts",
    "mcp_app_suggestions",
    "suggest_catalog_plugins_and_skills",
    "past_chats_tools",
    "preferences_info",
    "computer_use",
    "request_evaluation_checklist",
    "search_instructions",
    "tools_catalog",
    "context_window_management",
    "error_handling",
    "critical_ui_requirements",
    "available_skills",
    "network_configuration",
    "filesystem_configuration",
)

CODE_SECTIONS = (
    "reasoning_effort",
    "reporting_outcomes",
    "harness",
    "session_specific_guidance",
    "memory",
    "environment",
    "scratchpad_directory",
    "context_management",
    "delivering_work",
    "writing_for_the_user",
    "chrome_browser_automation",
    "session_context",
    "agents",
    "skills",
    "tools_agent",
    "tools_bash_git",
    "tools_plan_mode",
    "tools_worktree",
    "tools_monitor",
    "tools_report_findings",
    "tools_schedule_wakeup",
    "tools_workflow",
    "connector_catalogs",
)

FABLE5_1_ONLY = ("reply_after_tool_calls", "list_mcp_resources", "read_resource_link")


def inventory() -> Dict[str, Any]:
    return {
        "repository": REPO,
        "analysis_timestamp": ANALYSIS_TS,
        "auto_sync": False,
        "sources": {k: v.to_dict() for k, v in PINNED_SOURCES.items()},
        "primary_sections": list(PRIMARY_SECTIONS),
        "code_sections": list(CODE_SECTIONS),
        "fable5_1_delta_sections": list(FABLE5_1_ONLY),
        "sections_audited": (
            len(PRIMARY_SECTIONS) + len(CODE_SECTIONS) + len(FABLE5_1_ONLY)
        ),
    }


def diff_revisions(old: Dict[str, str], new: Dict[str, str]) -> Dict[str, Any]:
    """Lightweight future process: compare pinned SHAs. Never applies behavior."""
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = [k for k in old if k in new and old[k] != new[k]]
    return {
        "added_sources": added,
        "removed_sources": removed,
        "changed_revisions": {
            k: {"from": old[k], "to": new[k]} for k in changed
        },
        "brain_impact": "none_until_audit_matrix_tests_proving_ground_human_review",
        "auto_applied": False,
    }


def current_pins() -> Dict[str, str]:
    return {k: v.commit for k, v in PINNED_SOURCES.items()}
