import copy

import pytest

from qhos2.laboratorio.simulador import run_simulation


NOW = 1_800_000_000_000_000_000


def event(kind, offset, **fields):
    return dict(kind=kind, event_id=f'{kind}-{offset}', session_id='a',
                received_ns=NOW + offset * 1_000_000, event_time_ns=NOW + offset * 1_000_000,
                **fields)


def reference(offset=0):
    return event('reference', offset, tick_size=.25, point_value=50)


def quote(offset=1, **fields):
    result = event('bbo', offset, bid=100., ask=100.25, bid_size=10, ask_size=10,
                   bid_received_ns=NOW + offset * 1_000_000, ask_received_ns=NOW + offset * 1_000_000,
                   bid_event_time_ns=NOW + offset * 1_000_000, ask_event_time_ns=NOW + offset * 1_000_000)
    result.update(fields)
    return result


def trade(offset=2, size=100, **fields):
    result = event('trade', offset, price=100.25, size=size, side='buy')
    result.update(fields)
    return result


def test_latency_no_fill_at_submission_or_before_arrival():
    events = [reference(), quote(), trade(), quote(50)]
    assert run_simulation(events)['summary']['filled_qty'] == 0
    result = run_simulation(events + [quote(102)])
    assert result['summary']['filled_qty'] == 10
    fill = result['fills'][0]
    assert fill['filled_ns'] >= fill['arrival_ns'] > fill['submitted_ns']


def test_participation_target_integer_quantities_and_partial_fill():
    events = [reference(), quote(1, ask_size=3), trade(size=55)]
    result = run_simulation(events, dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 3
    assert result['orders'][0]['quantity'] == 5
    assert result['orders'][0]['remaining'] == 2
    assert result['orders'][0]['status'] == 'partial'
    assert result['summary']['realized_participation'] <= .1
    assert all(type(row['quantity']) is int for row in result['fills'])


def test_quote_capacity_is_not_reused_when_identical_quotes_arrive():
    events = [reference(), quote(1, ask_size=3), trade(size=100), quote(3, ask_size=3), quote(4, ask_size=3)]
    result = run_simulation(events, dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 3
    result = run_simulation(events + [quote(5, ask_size=5)], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 5


def test_adverse_slippage_and_commission_for_both_sides():
    events = [reference(), quote(), trade()]
    buy = run_simulation(events, dict(latency_ms=0, target_qty=2))
    sell = run_simulation(events, dict(latency_ms=0, target_qty=2, side='sell'))
    assert buy['fills'][0]['price'] == 100.5
    assert sell['fills'][0]['price'] == 99.75
    assert buy['summary']['commissions'] == sell['summary']['commissions'] == 5
    assert buy['summary']['adverse_slippage_cost'] == sell['summary']['adverse_slippage_cost'] == 25
    assert buy['summary']['inventory'] == 2
    assert sell['summary']['inventory'] == -2
    assert buy['summary']['realized_profit'] is None


def test_session_boundary_stops_without_silent_inventory_carry():
    events = [reference(), quote(), trade()]
    boundary = trade(3)
    boundary['session_id'] = 'b'
    result = run_simulation(events + [boundary, quote(4)], dict(latency_ms=0))
    assert result['summary']['reason'] == 'SESSION_BOUNDARY_STOP'
    assert result['summary']['filled_qty'] == 10
    assert result['summary']['inventory'] == 10
    assert result['summary']['session_id'] == 'a'
    assert len(result['fills']) == 1


def test_out_of_order_events_fail_closed_without_sorting_future_quote():
    events = [reference(), quote(100), trade(2)]
    result = run_simulation(events, dict(latency_ms=0))
    assert result['summary']['reason'] == 'RECEIPT_ORDER_INVALID'
    assert result['summary']['filled_qty'] == 0


def test_future_provider_or_future_quote_side_is_unusable():
    bad = quote()
    bad['ask_event_time_ns'] += 1_000_000
    result = run_simulation([reference(), bad, trade()], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 0
    future_trade = trade()
    future_trade['event_time_ns'] += 1
    result = run_simulation([reference(), quote(), future_trade], dict(latency_ms=0))
    assert result['summary']['observed_incremental_volume'] == 0


def test_provider_freshness_and_receipt_freshness_checked_separately():
    old_source = quote(10000, ask_event_time_ns=NOW)
    result = run_simulation([reference(), old_source, trade(10001)], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 0


def test_old_trade_cannot_create_participation_against_current_quote():
    old_trade = trade(10001)
    old_trade['event_time_ns'] = NOW
    result = run_simulation([reference(), quote(10000), old_trade], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 0
    assert result['summary']['observed_incremental_volume'] == 0
    assert result['rejections'][-1]['reason'] == 'STALE_TRADE_NOT_ELIGIBLE_FOR_POV'
    result = run_simulation([reference(), quote(), trade(10001)], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 0


@pytest.mark.parametrize('value', [None, 0, -1, 1.5, True])
def test_unknown_or_invalid_side_capacity_cannot_fill(value):
    result = run_simulation([reference(), quote(ask_size=value), trade()], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 0


def test_no_reference_no_execution_and_snapshot_no_participation():
    result = run_simulation([quote(), trade()], dict(latency_ms=0))
    assert result['summary']['filled_qty'] == 0
    result = run_simulation([reference(), quote(), trade(is_snapshot=True)], dict(latency_ms=0))
    assert result['summary']['observed_incremental_volume'] == 0


def test_no_mutation_or_real_execution_authority():
    events = [reference(), quote(), trade()]
    before = copy.deepcopy(events)
    result = run_simulation(events, dict(latency_ms=0))
    assert events == before
    assert result['hypothetical'] is True and result['can_execute'] is False
    assert all(row['hypothetical'] is True for row in result['fills'])
    assert all('not realized profit' in row['label'] for row in result['equity'])


def test_inventory_mark_updates_on_trade_even_when_quote_is_stale():
    events = [reference(), quote(), trade(), trade(10000, size=1, price=101)]
    result = run_simulation(events, dict(latency_ms=0, target_qty=2))
    assert result['summary']['filled_qty'] == 2
    assert result['equity'][-1]['mark_price'] == 101
    assert result['summary']['inventory_mark'] == 45
    assert result['equity'][-1]['mark_event_time_ns'] == events[-1]['event_time_ns']


@pytest.mark.parametrize('config', [dict(target_qty=True), dict(target_qty=-1), dict(participation=2),
                                   dict(latency_ms=-1), dict(slippage_ticks=.5), dict(side='unknown'),
                                   dict(fee_per_contract=float('nan')), dict(order_type='market')])
def test_invalid_config_rejected(config):
    with pytest.raises(ValueError):
        run_simulation([], config)
