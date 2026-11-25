import math
import pytest
from qhos2.laboratorio import monetary_move, align_observations, run_simulation


def test_numeric_overflow_is_rejected_not_profit():
    for bad in (math.nan, math.inf, -math.inf, 10**1000):
        with pytest.raises(ValueError):
            monetary_move(bad, 1, {"point_value": 50, "tick_size": .25, "currency": "USD"})
        with pytest.raises(ValueError):
            run_simulation([], {"fee_per_contract": bad})
    with pytest.raises(ValueError):
        monetary_move(1e308, 10000, {"point_value": 1e308, "tick_size": .25, "currency": "USD"})
    with pytest.raises(ValueError):
        monetary_move(1, 1, None)


def test_unknown_or_malformed_environment_does_not_qualify_join():
    for env in ("unknown", "", [], {}):
        result = align_observations({"ES": [{"received_ns": 100, "event_time_ns": 100,
                                             "valid": True, "environment": env}]}, 100)
        assert result["eligible"] is False
        assert result["can_execute"] is False
    with pytest.raises(ValueError):
        align_observations({"ES": [None]}, 100)


def test_limits_and_labels_are_explicit():
    with pytest.raises(ValueError, match="LIMIT"):
        run_simulation([{}] * 100001)
    result = run_simulation([])
    assert result["mode"] == "LOCAL_SIMULATION" and not result["can_execute"]
    amount = monetary_move(.25, 1, {"point_value": 50, "tick_size": .25, "currency": "USD"})
    assert amount["amount"] == 12.5 and amount["hypothetical"] and not amount["can_execute"]
