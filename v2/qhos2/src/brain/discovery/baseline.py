"""Known global failures captured at the start of this phase. PRE_EXISTING ≠ NEW."""
from __future__ import annotations

from typing import FrozenSet, Iterable


KNOWN_FAILED = frozenset({
    "tests/test_crypto_refresh.py::test_refresh_crypto_skips_stock_batch",
    "tests/test_crypto_refresh.py::test_ingestion_has_no_synthetic_fallback",
    "tests/test_crypto_refresh.py::test_ingestion_does_not_claim_alpaca_feeds_parquet",
    "tests/test_crypto_refresh.py::test_main_bootstrap_has_crypto_utc_branch",
    "tests/test_edge_birth_panel.py::test_dashboard_has_method_view_and_does_not_claim_strategy",
    "tests/test_edge_gate.py::test_bootstrap_and_status",
    "tests/test_edge_gate.py::test_index_lists_closed_hypotheses",
    "tests/test_search_persistence.py::test_epoch_change_seeds_stripped_historical_structures",
    "tests/test_search_persistence.py::test_same_epoch_checkpoint_does_not_reharvest",
    "tests/test_search_persistence.py::test_epoch_changed_checkpoint_merges_harvest",
    "tests/test_search_persistence.py::test_frozen_registry_stores_structure_not_judgment",
    "tests/test_search_persistence.py::test_frozen_failure_drops_previous_survivor",
    "tests/test_search_persistence.py::test_d_core_reanchors_frozen_not_in_elite",
    "tests/test_search_persistence.py::test_catalog_structures_ignore_epoch_but_capital_path_does_not",
})

KNOWN_COLLECTION_ERRORS = frozenset({
    "tests/test_crypto_schedule.py",
    "tests/test_desktop_spawn.py",
    "tests/test_gpu_stats_no_console.py",
    "tests/test_integrity_regressions.py",
    "tests/test_market_clock.py",
    "tests/test_orchestra.py",
})

KNOWN_FILES = frozenset(
    {nid.split("::", 1)[0] for nid in KNOWN_FAILED} | set(KNOWN_COLLECTION_ERRORS)
)


def is_preexisting(text: str) -> bool:
    blob = str(text or "")
    if blob in KNOWN_FAILED or blob in KNOWN_COLLECTION_ERRORS:
        return True
    for nid in KNOWN_FAILED:
        if nid in blob:
            return True
    for path in KNOWN_FILES:
        name = path.rsplit("/", 1)[-1]
        if name and name in blob and "NEW" not in blob.upper():
            return True
    return False


def baseline_record() -> dict:
    return {
        "brain_passed": 210,
        "global_passed": 322,
        "global_failed": 14,
        "collection_errors": 6,
        "known_failed": sorted(KNOWN_FAILED),
        "known_collection_errors": sorted(KNOWN_COLLECTION_ERRORS),
        "captured": "2026-09-02-live-intelligence",
    }
