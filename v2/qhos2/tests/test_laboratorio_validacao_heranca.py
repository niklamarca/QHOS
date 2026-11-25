import copy

import qhos2.laboratorio.validacao as research
from qhos2.hipoteses import evaluate_models


N = 1_800_000_000_000_000_000


def events():
    def row(kind, ms, **fields):
        return dict(kind=kind, event_id=f'{kind}-{ms}', session_id='a', received_ns=N + ms * 10**6,
                    event_time_ns=N + ms * 10**6, **fields)
    result = [row('reference', 0, tick_size=.25, point_value=50)]
    for ms in (1, 1002, 2002, 3002):
        result.append(row('bbo', ms, bid=100, ask=100.25, bid_size=10 + ms // 1000, ask_size=10 + ms // 1000,
                          bid_received_ns=N + ms * 10**6, ask_received_ns=N + ms * 10**6,
                          bid_event_time_ns=N + ms * 10**6, ask_event_time_ns=N + ms * 10**6))
        result.append(row('trade', ms + 1, price=100.25, size=100, side='buy'))
    return result


def bars():
    return [dict(time=index * 30, open=100 + index, high=101 + index, low=99 + index, close=100 + index,
                 volume=10, pv_ticks=(100 + index) * 40, tick_size=.25, closed=True, complete=True,
                 closed_at_received_ns=(index * 30 + 30) * 10**9 + 100000000, event_id=f'b{index}')
            for index in range(41)]


def test_stresses_are_hypothetical_and_never_qualify_profitability():
    result = research.evaluate_research(events(), bars(), evaluate_models(bars()))
    assert result['status'] == 'INSUFFICIENT_EVIDENCE'
    assert result['engineering_status'] == 'NO_COUNTEREXAMPLE_IN_TESTED_CHECKS'
    assert result['profitability_verified'] is False and result['can_execute'] is False
    assert {row['id'] for row in result['scenarios']} == {
        'baseline', 'adverse_costs', 'latency_1000ms', 'liquidity_half', 'quotes_removed'}
    assert all(row['hypothetical'] is True for row in result['scenarios'])
    assert all(row['perturbed'] is True for row in result['scenarios'] if row['id'] != 'baseline')
    assert next(row for row in result['scenarios'] if row['id'] == 'quotes_removed')['summary']['filled_qty'] == 0
    assert next(row for row in result['checks'] if row['id'] == 'prefix-causality')['status'] == 'PASS'


def test_model_threshold_attack_keeps_prior_window_and_detects_corrupted_evidence():
    source = bars()
    models = evaluate_models(source)
    result = research.evaluate_research(events(), source, models)
    thresholds = [row for row in result['checks'] if row['id'].startswith('threshold-')]
    assert len(thresholds) == 3
    assert all(row['status'] == 'PASS' and row['evidence']['perturbed'] is True for row in thresholds)
    next(row for row in models if row['id'] == 'breakout')['evidence']['previous_high'] += 100
    result = research.evaluate_research(events(), source, models)
    assert result['engineering_status'] == 'FAIL'
    assert result['status'] == 'INSUFFICIENT_EVIDENCE'


def test_future_bar_close_knowledge_cannot_be_labeled_causal():
    source = bars()
    models = evaluate_models(source)
    models[0]['evidence']['decision_available_ns'] -= 1_000_000_000
    result = research.evaluate_research(events(), source, models)
    assert next(row for row in result['checks'] if row['id'] == 'model-time-vwap')['status'] == 'FAIL'


def test_no_models_ready_stays_untested_and_requires_external_validation():
    result = research.evaluate_research(events(), [], evaluate_models([]))
    assert next(row for row in result['checks'] if row['id'] == 'model-warmup')['status'] == 'NOT_TESTED'
    requirements = {row['id']: row for row in result['requirements']}
    assert requirements['holdout']['status'] == 'UNKNOWN'
    assert requirements['calibrated_costs']['status'] == 'UNKNOWN'
    assert requirements['independent_sessions']['observed_sessions'] == 1


def test_truncated_fills_never_prove_full_prefix(monkeypatch):
    real = research.run_simulation
    def truncated(values, config=None):
        result = real(values, config)
        result['truncation']['fills'] = True
        result['summary']['fills_total'] = 201
        return result
    monkeypatch.setattr(research, 'run_simulation', truncated)
    result = research.evaluate_research(events(), [], [])
    assert next(row for row in result['checks'] if row['id'] == 'prefix-causality')['status'] == 'NOT_TESTED'
    assert next(row for row in result['checks'] if row['id'] == 'latency-causality')['status'] == 'NOT_TESTED'


def test_source_inputs_are_not_mutated_and_no_random_prices_added():
    source_events, source_bars = events(), bars()
    before_events, before_bars = copy.deepcopy(source_events), copy.deepcopy(source_bars)
    result = research.evaluate_research(source_events, source_bars, evaluate_models(source_bars))
    assert source_events == before_events and source_bars == before_bars
    assert result['coverage']['observed_incremental_volume'] == 400
    assert result['coverage']['trade_events'] == 4
    assert result['coverage']['session_count'] == result['coverage']['day_count'] == 1


def test_equal_receipt_group_is_not_split_for_prefix_comparison():
    source = events()
    # Distinct identities at equal receive time remain ordered; the boundary moves.
    source[4]['received_ns'] = source[3]['received_ns']
    source[4]['event_time_ns'] = source[3]['event_time_ns']
    result = research.evaluate_research(source, [], [])
    check = next(row for row in result['checks'] if row['id'] == 'prefix-causality')
    assert check['status'] == 'PASS'
    assert check['evidence']['prefix_events'] == 5


def test_malformed_row_cannot_be_silently_qualified_as_clean_input():
    result = research.evaluate_research(events() + [None], [], [])
    assert result['engineering_status'] == 'FAIL'
    assert next(row for row in result['checks'] if row['id'] == 'input-shape')['status'] == 'FAIL'


def test_zero_fills_cannot_be_a_passing_execution_prefix_check():
    source=[row for row in events() if row['kind']!='bbo']
    result=research.evaluate_research(source,[],[])
    assert next(row for row in result['checks'] if row['id']=='prefix-causality')['status']=='NOT_TESTED'
