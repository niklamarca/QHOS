"""O fallback deterministico do Fable fica visivel no intel (QHOS-AUD-291). Import simulado, nenhum provedor real."""
import sys
import types


def test_intel_flags_effort_policy_fallback(monkeypatch):
    from brain.discovery.engine import DiscoveryEngine
    monkeypatch.setitem(sys.modules, "brain.fable.effort", types.ModuleType("brain.fable.effort"))
    engine = DiscoveryEngine()
    intel = engine.ingest({"error_tail": ["worker crashed: ValueError"]})
    assert intel["effort_policy_source"] == "deterministic_fallback"
    assert engine.effort_policy.decide([]) == {"level": "LOW", "source": "deterministic_fallback"}


def test_intel_names_the_real_effort_policy_when_fable_imports():
    from brain.discovery.engine import DiscoveryEngine
    engine = DiscoveryEngine()
    intel = engine.ingest({})
    assert intel["effort_policy_source"] == "brain.fable.effort"
    assert type(engine.effort_policy).__name__ == "CognitiveEffortPolicy"
