import json

import pytest

from core.config import config
from governance.promotion import PAPER, SHADOW, PromotionGate


def test_shadow_cannot_be_declared_without_current_internal_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    gate = PromotionGate()
    with pytest.raises(ValueError, match="testes internos"):
        gate.promote("invented", SHADOW, {"internal_tests_passed": True}, "human")


def test_paper_cannot_use_fabricated_session_count(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    gate = PromotionGate()
    gate.path.write_text(json.dumps({
        "strategy_id": "candidate", "stage": SHADOW, "approved_by": "human"
    }) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sessões forward"):
        gate.promote("candidate", PAPER,
                     {"forward_sessions": 999, "ledger_reconciled": True}, "human")
