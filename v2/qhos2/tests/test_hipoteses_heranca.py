import copy

from qhos2.hipoteses import evaluate_models


def bars(count=41):
    return [dict(time=i * 30, open=100 + i, high=101 + i, low=99 + i,
                 close=100 + i, volume=10, closed=True, complete=True, event_id=f'e{i}',
                 tick_size=.25, pv_ticks=(100 + i) * 40, closed_at_received_ns=(i * 30 + 30) * 10**9 + 100_000_000,
                 close_trigger_event_id=f'next-{i}')
            for i in range(count)]


def indexed(value):
    return {row['id']: row for row in evaluate_models(value)}


def test_warmup_counts_include_decision_bar_and_do_not_approve_trading():
    models = indexed(bars(20))
    assert models['vwap']['state'] == 'ready'
    assert models['mean_reversion']['state'] == 'waiting'
    assert models['mean_reversion']['required_bars'] == 21
    assert models['trend_following']['required_bars'] == 41
    assert all(row['trade_eligible'] is False and row['profitability_verified'] is False for row in models.values())


def test_breakout_threshold_excludes_current_bar():
    value = bars(21)
    value[-1].update(close=500, high=550, low=120, open=120)
    model = indexed(value)['breakout']
    assert model['evidence']['previous_high'] == 120
    assert model['evidence']['threshold_window_end'] == 570
    assert model['signal']['direction'] == 'buy'
    assert model['evidence']['bar_end_time'] == 630
    assert model['evidence']['decision_time'] == 630.1
    assert model['evidence']['decision_available_ns'] == 630_100_000_000


def test_mean_reversion_reference_excludes_extreme_current_close():
    value = bars(21)
    value[-1].update(close=500, high=500)
    model = indexed(value)['mean_reversion']
    assert model['evidence']['previous_mean'] == 109.5
    assert model['signal']['direction'] == 'sell'


def test_trend_uses_previous_fast_and_slow_windows():
    model = indexed(bars())['trend_following']
    assert model['evidence']['previous_sma20'] == 129.5
    assert model['evidence']['previous_sma40'] == 119.5
    assert model['signal']['direction'] == 'buy'


def test_open_bar_cannot_change_previous_closed_signal():
    value = bars()
    expected = evaluate_models(value)
    value.append(dict(time=41 * 30, open=1, high=10000, low=1, close=10000,
                      volume=100000, closed=False, complete=False))
    assert evaluate_models(value) == expected


def test_gap_and_incomplete_bar_reset_warmup_without_bridging():
    value = bars(50)
    value[35]['complete'] = False
    assert all(row['available_bars'] == 14 for row in evaluate_models(value))
    value = bars(50)
    del value[35]
    assert all(row['available_bars'] == 14 for row in evaluate_models(value))


def test_receipt_order_is_not_repaired_by_sorting_bars():
    value = bars()
    value[1], value[2] = value[2], value[1]
    assert all(row['state'] == 'blocked' for row in evaluate_models(value))


def test_quality_blocks_even_with_sufficient_bars():
    result = evaluate_models(bars(), {'blocked': True, 'reasons': ['DISCONNECTED']})
    assert all(row['state'] == 'blocked' and row['signal'] is None for row in result)


def test_zero_variance_and_zero_volume_are_not_fabricated_signals():
    value = bars()
    for row in value:
        row.update(open=100, high=100, low=100, close=100, volume=0, pv_ticks=0)
    models = indexed(value)
    assert models['mean_reversion']['reason'] == 'ZERO_VARIANCE_REFERENCE_WINDOW'
    assert models['vwap']['reason'] == 'ZERO_OBSERVED_VOLUME'


def test_vwap_is_volume_weighted_benchmark_and_never_execution_strategy():
    value = bars(20)
    value[-1]['volume'] = 100
    value[-1]['pv_ticks'] = 119 * 400
    model = indexed(value)['vwap']
    expected = sum(row['close'] * row['volume'] for row in value) / sum(row['volume'] for row in value)
    assert model['signal']['value'] == expected
    assert model['signal']['role'] == 'benchmark'
    assert model['signal']['direction'] == 'benchmark'


def test_vwap_uses_actual_price_volume_and_blocks_missing_exact_fields():
    value = bars(20)
    # Same OHLC, but actual trade-weighted price differs from typical-price proxy.
    value[-1]['pv_ticks'] = 118 * 40
    model = indexed(value)['vwap']
    assert model['signal']['value'] == 109.45
    del value[-1]['pv_ticks']
    assert indexed(value)['vwap']['reason'] == 'EXACT_TRADE_PRICE_VOLUME_UNAVAILABLE'


def test_no_mutation_and_invalid_ohlc_rejected():
    value = bars()
    before = copy.deepcopy(value)
    evaluate_models(value)
    assert value == before
    value[-1]['low'] = 1000
    assert all(row['reason'] == 'INCONSISTENT_OHLC' for row in evaluate_models(value))


def test_missing_close_observation_time_is_not_replaced_by_bar_end():
    value = bars()
    del value[-1]['closed_at_received_ns']
    for model in evaluate_models(value):
        assert model['evidence']['decision_time'] is None
        assert model['evidence']['decision_available_ns'] is None
        assert model['evidence']['availability'] == 'NOT_AVAILABLE'


def test_nonfinite_calculated_result_is_blocked_not_ready():
    value = bars(20)
    for row in value:
        row["pv_ticks"] = 10**1000
    assert all(row["state"] == "blocked" for row in evaluate_models(value))
    for row in bars(20):
        row["volume"] = 10**1000
    invalid = bars(20)
    invalid[0]["close"] = float("nan")
    assert all(row["state"] == "blocked" for row in evaluate_models(invalid))


def test_v2_capture_feeds_reference_hypotheses_without_legacy_decoder(tmp_path):
    from datetime import datetime, timezone
    from qhos2 import sintetico as si
    from qhos2.gravador import Gravador
    from qhos2.hipoteses import avaliar_captura
    t = int(datetime(2026, 11, 10, 15, tzinfo=timezone.utc).timestamp()) * 10**9
    g = Gravador(tmp_path / "capturas", identidade={"system_name": "QHOS2 Sintetico",
                                                  "requested_symbol": "ESZ6", "exchange": "CME"})
    for i, frame in enumerate([si.quadro_retrato("C", 25., [(4, 1, "c")]),
                               si.quadro_retrato("V", 25.25, [(1, 1, "v")]), si.quadro_retrato_fim()]):
        g(frame, 116, t + i)
    # Snapshot LastTrade must not double-count a pre-subscription trade.
    g(si.quadro_negocio(25., 999, "C", "c", "a", t, retrato=True), 150, t+3)
    for i in range(44):
        at = t + i * 30_000_000_000 + 1_000_000_000
        g(si.quadro_negocio(25. + i * .25, 2, "C", "p", "a", at), 150, at)
    g.fechar({"estado": "FECHADA", "retrato_completo": True, "erro": None,
              "referencia": {"min_qprice_change": .25}}, fechamento_normal=True)
    out = avaliar_captura(g.pasta)
    assert len(out["bars"]) == 44
    assert out["bars"][0]["complete"] is False
    assert out["bars"][-1]["closed"] is False
    assert out["bars"][1]["volume"] == 2
    assert out["bars"][1]["pv_ticks"] == 202
    assert out["bars"][1]["closed_at_received_ns"] == t + 61_000_000_000
    assert all(row["state"] == "ready" for row in out["models"])
    assert not out["execution_enabled"] and not out["utc_qualified"]
    assert out["time_basis"] == "local_receipt"
