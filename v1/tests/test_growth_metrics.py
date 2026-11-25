"""Painel de crescimento: relatório sem NAV do GA."""
import json

from core.growth_metrics import build_growth_report


def test_growth_report_has_arms_and_verdict():
    report = build_growth_report(force=True)
    assert report["schema"] == "qhos_growth_v1"
    assert report["ga_paper_nav_is_not_growth"] is True
    assert report["verdict"] in {"CRESCENDO", "LADO", "TRAVADO", "DESCOBERTA"}
    assert "symb1" in report["arms"]
    assert "trend" in report["arms"]
    assert "carry" in report["arms"]
    assert "research" in report
    assert "generation" in report["research"]


def test_growth_snapshot_persists_time_series(tmp_path, monkeypatch):
    """A série de aprendizado precisa acumular em disco — é ela que alimenta
    o veredito CRESCENDO/LADO no tempo. O loop 24/7 do dashboard cutuca este
    caminho mesmo sem navegador aberto."""
    from core import growth_metrics as gm

    monkeypatch.setattr(gm, "_lake", lambda: tmp_path)
    gm._cache = {"ts": 0.0, "payload": None}

    report = gm.build_growth_report(force=True)
    snap = tmp_path / "growth_snapshots.jsonl"
    assert snap.exists()
    lines = [ln for ln in snap.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 1
    row = json.loads(lines[-1])
    assert "ts" in row and "generation" in row
    assert report["snapshots"], "relatório deve reexpor a série recém-gravada"
