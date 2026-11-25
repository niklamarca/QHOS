import json

from core.config import config
from evolution.genetic import GeneticEngine
from evolution.strategy_dna import StrategyFactory
from governance.experiment_registry import ExperimentRegistry


def test_checkpoint_keeps_structure_but_invalidates_old_judgment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    strategy = StrategyFactory.random(["SPY"])
    strategy.status = "validated"
    strategy.fitness = 0.9
    strategy.oos_sharpe = 2.0
    strategy.n_trades = 100
    payload = {
        "schema": 2,
        "data_epoch": "old-scientific-epoch",
        "generation": 12,
        "population": [strategy.to_dict()],
        "hall_of_fame": [strategy.to_dict()],
    }
    (tmp_path / "evolution_state.json").write_text(json.dumps(payload), encoding="utf-8")
    engine = GeneticEngine(storage=None)
    assert engine.restore_checkpoint()
    assert len(engine.population) == 1
    assert engine.population[0].status == "pending"
    assert engine.population[0].fitness == 0.0
    assert engine.population[0].n_trades == 0
    assert engine.hall_of_fame == []


def test_hypothesis_registry_counts_unique_fingerprints(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "data_lake_dir", tmp_path)
    registry = ExperimentRegistry()
    registry.record_hypothesis_test("abc", "lineage")
    registry.record_hypothesis_test("abc", "lineage")
    registry.record_hypothesis_test("def", "lineage")
    assert registry.count_unique_hypotheses("lineage") == 2
