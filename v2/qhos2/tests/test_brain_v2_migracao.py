"""Independent runtime, memory, untrusted evidence and boundary regression tests."""
import asyncio
import importlib
import json
import math
import pkgutil
from pathlib import Path

import pytest


def test_all_core_modules_import_without_network_or_application_imports(monkeypatch):
    import socket
    import subprocess
    import brain
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network on import"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("process on import"))
    modules = [item.name for item in pkgutil.walk_packages(brain.__path__, "brain.")]
    # Cada arquivo do pacote e um modulo importavel: comparacao exata com o disco, sem numero magico (QHOS-AUD-311).
    root = Path(brain.__path__[0])
    expected = {"brain." + ".".join(p.relative_to(root).with_suffix("").parts) for p in root.rglob("*.py")
                if "__pycache__" not in p.parts and p.name != "__init__.py"}
    expected |= {"brain." + ".".join(p.parent.relative_to(root).parts) for p in root.rglob("__init__.py") if p.parent != root}
    assert set(modules) == expected
    for name in modules:
        importlib.import_module(name)


def test_config_uses_own_data_and_installed_code_ignores_legacy_marker(tmp_path):
    from brain.local_config import BrainConfig
    code = tmp_path / "installed" / "brain"
    code.mkdir(parents=True)
    (code / "QUANT_ROOT.txt").write_text("/legacy/ignored")
    data = tmp_path / "independent-data"
    cfg = BrainConfig(code, environ={"QHOS2_DATA_ROOT": str(data), "BRAIN_QUANT_ROOT": "/legacy/ignored"})
    assert cfg.root_dir == code.parent / "qhos2"
    assert cfg.quant_data_lake_dir == data
    assert cfg.brain_state_dir == data / "brain" / "state"
    assert not data.exists(), "loading config must not create runtime state"


def test_readonly_runtime_does_not_create_files(tmp_path):
    from brain.runtime import BrainRuntime
    from brain.memory import BrainStore
    root = tmp_path / "absent"
    store = BrainStore(root, read_only=True)
    assert store.load_state() == {}
    store.save_state({"x": 1})
    with pytest.raises(PermissionError):
        store.audit.append({"x": 1})
    runtime = BrainRuntime(root, read_only=True, observer=lambda: {})
    assert runtime.snapshot(force=True)
    assert not root.exists()


def test_runtime_cycle_persists_and_resumes_real_state(tmp_path, monkeypatch):
    import brain.runtime as rt
    # Fixtures replace outside observations, never the actual Brain computation.
    monkeypatch.setattr(rt, "soak_enabled", lambda: False)
    monkeypatch.setattr(rt.config, "brain_state_dir", tmp_path / "eval-state")
    observation = {"registry": {"qhos2": {"healthy": False, "status": "error", "last_error": "snapshot refused"}},
                   "application_ready": False, "error_tail": ["snapshot refused"],
                   "resources": {}, "git": {}, "hpc": {}, "committee": {}, "truth": {}}
    first = rt.BrainRuntime(tmp_path / "brain", observer=lambda: observation)
    snapshot = asyncio.run(first.cycle())
    # Observaveis publicos (snapshot e state.json), nao atributos privados (QHOS-AUD-286).
    visto = first.snapshot(force=True)
    assert visto["cycles"] == 1
    assert json.loads((tmp_path / "brain/state.json").read_text())["cycle_count"] == 1
    assert visto["live_intelligence"]["llm_called"] is False
    assert visto["live_intelligence"]["change_request"] is None
    restored = rt.BrainRuntime(tmp_path / "brain", observer=lambda: observation)
    assert restored.snapshot(force=True)["cycles"] == 1
    assert restored.store.graph.to_dict()["counts"]["entities"] > 0
    asyncio.run(restored.cycle())
    assert restored.snapshot(force=True)["cycles"] == 2
    assert json.loads((tmp_path / "brain/state.json").read_text())["cycle_count"] == 2
    assert snapshot and restored.snapshot(force=True)


def test_memory_recovers_torn_append_and_atomic_write_preserves_previous_state(tmp_path):
    from brain.memory import JsonlStore, _atomic_write
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b'{"torn":')
    store = JsonlStore(path)
    store.append({"ok": 1})
    assert store.tail() == [{"ok": 1}]
    state = tmp_path / "state.json"
    _atomic_write(state, {"ok": 1})
    with pytest.raises(ValueError):
        _atomic_write(state, {"bad": math.nan})
    assert json.loads(state.read_text()) == {"ok": 1}
    assert not list(tmp_path.glob(".brain-*.tmp"))


def test_hypothesis_score_cannot_use_nan_or_count_same_evidence_twice():
    from brain.hypothesis import HypothesisEngine
    engine = HypothesisEngine()
    for invalid in (math.nan, math.inf, -math.inf, True, -1, 10**1000):
        with pytest.raises(ValueError):
            engine.propose("x", prior=invalid)
    hyp = engine.propose("capture may be incomplete")
    _, changed = engine.add_evidence(hyp.hypothesis_id, "e1", polarity="for", text="snapshot failed", weight=.2)
    before = hyp.confidence
    assert changed
    _, changed = engine.add_evidence(hyp.hypothesis_id, "e1", polarity="against", text="same evidence repeated", weight=.2)
    assert not changed and hyp.confidence == before
    for invalid in (math.nan, math.inf, -math.inf, True, -1, 10**1000):
        with pytest.raises(ValueError):
            engine.add_evidence(hyp.hypothesis_id, "e2", polarity="for", text="untrusted", weight=invalid)
    assert hyp.confidence == before


def test_scratch_and_evaluations_stay_in_own_state(tmp_path, monkeypatch):
    from brain import eval_paths
    from brain.fable import scratch
    monkeypatch.setattr(eval_paths.config, "brain_state_dir", tmp_path / "state")
    out = scratch.write_scratch("investigation1", "notes", {"text": "untrusted evidence"})
    assert out.is_relative_to(tmp_path / "state")
    for value in ("../escape", "/outside", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            scratch.write_scratch(value, "notes", {})
        with pytest.raises(ValueError):
            scratch.write_scratch("safe", value, {})
    with pytest.raises(ValueError):
        eval_paths.resolve_artifact_path(str(tmp_path / "outside.json"))


@pytest.mark.parametrize("host", ["http://example.com", "http://127.0.0.1@evil.test", "http://127.0.0.1/x", "file:///tmp/x"])
def test_observer_blocks_nonlocal_and_ambiguous_urls(host):
    from brain.discovery.runner import _get
    with pytest.raises(ValueError):
        _get(host, "/api/status")


def test_unknown_module_never_becomes_healthy(monkeypatch):
    from brain.discovery import runner
    monkeypatch.setattr(runner, "_get", lambda *a: {"modules": {"missing": {}, "other": "not evidence"}})
    observed = runner.enrich_from_live_api({})
    assert observed["registry"]["missing"]["healthy"] is None
    assert observed["registry"]["other"]["healthy"] is None
    for bad in (math.nan, math.inf, -1):
        with pytest.raises(ValueError):
            runner.run_bounded_soak(duration_s=bad)


def test_calibration_rejects_nonfinite_even_on_empty_histogram():
    from qhos2.desequilibrio import Histograma, DesequilibrioError
    hist = Histograma()
    for bad in (math.nan, math.inf, -math.inf, True, 10**1000):
        with pytest.raises(DesequilibrioError):
            hist.adicionar(bad)
        with pytest.raises(DesequilibrioError):
            hist.percentil(bad)
    assert hist.n == 0
