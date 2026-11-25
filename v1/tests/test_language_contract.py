from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_dashboard_does_not_call_paper_forward_real():
    html = (ROOT / "dashboard" / "_hybrid.html").read_text(encoding="utf-8").lower()
    assert "forward real" not in html
    assert "hall of fame" not in html


def test_current_document_index_declares_machine_source_first():
    import json
    index = json.loads((ROOT / "DOCUMENT_INDEX.json").read_text(encoding="utf-8"))
    assert index["authoritative_order"][0] == "data_lake/governance/CURRENT_STATE.json"

