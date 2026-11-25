"""Deterministic falsification of hypotheses on the supplied recorded sample.

Stress scenarios intentionally perturb captured inputs or assumptions. They are
not observed executions, independent validation, or evidence of financial alpha.
"""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone

from qhos2.hipoteses import evaluate_models
from .simulador import run_simulation


def _integer(value):
    return type(value) is int and value > 0


def _day(stamp):
    if not _integer(stamp):
        return None
    try:
        return datetime.fromtimestamp(stamp / 1e9, timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def evaluate_research(events, bars, models):
    """Run bounded, inspectable counterexamples without external data or I/O."""
    if not isinstance(events, (list, tuple)) or not isinstance(bars, (list, tuple)) or not isinstance(models, list):
        raise ValueError('INVALID_RESEARCH_INPUT')
    if len(events) > 100_000 or len(bars) > 100_000:
        raise ValueError("RESEARCH_INPUT_LIMIT_EXCEEDED")
    captured = [row for row in events if isinstance(row, dict)]
    trades = [row for row in captured if row.get('kind') == 'trade'
              and type(row.get('size')) is int and row['size'] > 0 and row.get('is_snapshot') is not True]
    sessions = sorted({row['session_id'] for row in captured
                       if isinstance(row.get('session_id'), str) and row['session_id']})
    days = sorted({day for row in trades if (day := _day(row.get('event_time_ns'))) is not None})
    complete_bars = [bar for bar in bars if isinstance(bar, dict)
                     and bar.get('closed') is True and bar.get('complete') is True]
    checks, scenarios = [], []

    def check(key, label, status, evidence):
        checks.append(dict(id=key, name=label, status=status, evidence=evidence))

    if len(captured) != len(events):
        check('input-shape', 'Eventos malformados no conjunto de entrada', 'FAIL',
              dict(invalid_event_count=len(events) - len(captured),
                   reason='Malformed rows are excluded from stress construction, not counted as valid evidence'))

    def scenario(key, name, values, config, perturbation):
        try:
            outcome = run_simulation(values, config)
        except (ValueError, TypeError, OverflowError, ArithmeticError) as exc:
            # No upstream exception text or input payload is exposed.
            item = dict(id=key, name=name, status='ERROR', error=type(exc).__name__,
                        hypothetical=True, perturbed=key != 'baseline', perturbation=perturbation)
            scenarios.append(item)
            check(key + '-execution', name, 'FAIL', {'reason': 'SCENARIO_COULD_NOT_BE_EVALUATED'})
            return None
        scenarios.append(dict(id=key, name=name, status=outcome['summary']['state'],
                              summary=copy.deepcopy(outcome['summary']), config=outcome['config'],
                              hypothetical=True, perturbed=key != 'baseline',
                              perturbation=perturbation, source='recorded input replay; hypothetical executions',
                              fill_details_truncated=outcome['truncation']['fills']))
        return outcome

    baseline = scenario('baseline', 'POV de referência', captured, None,
                        'None: captured event values; default hypothetical execution assumptions')
    adverse = scenario('adverse_costs', 'Custos e slippage adversos', captured,
                       dict(slippage_ticks=3, fee_per_contract=5),
                       'Assumptions changed to 3 adverse ticks and 5 monetary units per contract')
    delayed = scenario('latency_1000ms', 'Latência de 1 segundo', captured,
                       dict(latency_ms=1000), 'Assumed order arrival latency increased to 1000ms')
    haircut = copy.deepcopy(captured)
    for event in haircut:
        if event.get('kind') == 'bbo':
            for key in ('bid_size', 'ask_size'):
                if type(event.get(key)) is int and event[key] >= 0:
                    event[key] //= 2
    scenario('liquidity_half', 'Metade da liquidez L1 exibida', haircut, None,
             'Every observed BBO side quantity replaced by floor(quantity / 2); prices unchanged')
    no_quotes = scenario('quotes_removed', 'Ataque: remover todas as cotações',
                         [event for event in captured if event.get('kind') != 'bbo'], None,
                         'All captured BBO events removed; trades and references unchanged')
    if no_quotes is not None:
        check('no-unobserved-liquidity', 'Sem cotações não pode haver execução simulada',
              'PASS' if no_quotes['summary']['filled_qty'] == 0 else 'FAIL',
              dict(filled_qty=no_quotes['summary']['filled_qty'], perturbed=True))
    if baseline is not None and adverse is not None:
        a, b = baseline['summary'], adverse['summary']
        comparable = a['filled_qty'] > 0 and a['filled_qty'] == b['filled_qty']
        ok = b['commissions'] >= a['commissions'] and b['adverse_slippage_cost'] >= a['adverse_slippage_cost']
        check('cost-direction', 'Custos adversos não melhoram o custo com quantidade igual',
              ('PASS' if ok else 'FAIL') if comparable else 'NOT_TESTED',
              dict(baseline_quantity=a['filled_qty'], adverse_quantity=b['filled_qty'],
                   baseline_fees=a['commissions'], adverse_fees=b['commissions'],
                   reason=None if comparable else 'No equal nonzero executed quantity for comparison'))
    if delayed is not None:
        if delayed['truncation']['fills']:
            status, reason = 'NOT_TESTED', 'Full fill list unavailable; bounded return cannot validate every fill'
        elif not delayed['fills']:
            status, reason = 'NOT_TESTED', 'No fill under delayed-arrival scenario'
        else:
            status = 'PASS' if all(row['filled_ns'] >= row['submitted_ns'] + 1_000_000_000 for row in delayed['fills']) else 'FAIL'
            reason = 'All returned fills inspected; list not truncated'
        check('latency-causality', 'Fill após a latência adversa', status,
              dict(reason=reason, full_fill_count=delayed['summary']['fills_total']))

    # Do not cut inside equal-receipt events: a later event with the same clock
    # reading is still causally later in the arrival stream.
    cut = len(captured) // 2
    while 0 < cut < len(captured) and captured[cut].get('received_ns') == captured[cut - 1].get('received_ns'):
        cut += 1
    if baseline is not None and 0 < cut < len(captured) and _integer(captured[cut - 1].get('received_ns')):
        prefix = run_simulation(captured[:cut])
        cutoff = captured[cut - 1]['received_ns']
        if baseline['truncation']['fills'] or prefix['truncation']['fills']:
            check('prefix-causality', 'Prefixo não pode usar eventos futuros', 'NOT_TESTED',
                  dict(reason='A bounded fill list cannot prove full prefix equivalence',
                       full_fills=baseline['summary']['fills_total'], prefix_fills=prefix['summary']['fills_total']))
        else:
            from_full = [row for row in baseline['fills'] if row['filled_ns'] <= cutoff]
            check('prefix-causality', 'Prefixo não pode usar eventos futuros',
                  ('PASS' if from_full == prefix['fills'] else 'FAIL') if from_full or prefix['fills'] else 'NOT_TESTED',
                  dict(prefix_events=cut, cutoff_received_ns=cutoff, compared_fills=len(from_full),
                       full_fill_list_verified=True, method='Exact fill records before cutoff must equal prefix-only replay'))
    else:
        check('prefix-causality', 'Prefixo não pode usar eventos futuros', 'NOT_TESTED',
              dict(reason='No nontrivial ordered prefix boundary or no baseline result'))

    ready = [model for model in models if isinstance(model, dict) and model.get('state') == 'ready']
    for model in ready:
        evidence = model.get('evidence') if isinstance(model.get('evidence'), dict) else {}
        last_time = evidence.get('last_bar_time')
        matching = [(index, bar) for index, bar in enumerate(bars)
                    if isinstance(bar, dict) and bar.get('time') == last_time]
        available = evidence.get('decision_available_ns')
        known = matching[-1][1].get('closed_at_received_ns') if matching else None
        if not _integer(available) or not _integer(known):
            timing_status = 'UNKNOWN'
        else:
            decision = evidence.get('decision_time')
            timing_status = 'PASS' if available >= known and type(decision) in (int, float) and math.isclose(
                decision, available / 1e9, rel_tol=0, abs_tol=1e-6) else 'FAIL'
        check('model-time-' + str(model.get('id')), 'Disponibilidade causal: ' + str(model.get('name')),
              timing_status, dict(decision_available_ns=available, close_known_at_ns=known,
                                  bar_end_time=evidence.get('bar_end_time')))
        threshold_keys = {'mean_reversion': ('previous_mean', 'previous_std'),
                          'breakout': ('previous_high', 'previous_low'),
                          'trend_following': ('previous_sma20', 'previous_sma40')}.get(model.get('id'))
        if threshold_keys is None or not matching:
            continue
        index, selected = matching[-1]
        alternative = next((bar for bar in complete_bars if bar.get('time') != last_time
                            and bar.get('close') != selected.get('close')), None)
        if alternative is None:
            check('threshold-' + model['id'], 'Ataque ao limiar anterior: ' + model['name'], 'NOT_TESTED',
                  dict(reason='No distinct observed OHLC to substitute'))
            continue
        stressed = copy.deepcopy(list(bars))
        for key in ('open', 'high', 'low', 'close'):
            stressed[index][key] = alternative[key]
        recalculated = next(row for row in evaluate_models(stressed) if row['id'] == model['id'])
        observed = recalculated.get('evidence', {})
        ok = all(key in evidence and key in observed and evidence[key] == observed[key] for key in threshold_keys)
        check('threshold-' + model['id'], 'Ataque ao limiar anterior: ' + model['name'], 'PASS' if ok else 'FAIL',
              dict(perturbed=True, observed_scenario=False,
                   perturbation='Decision-bar OHLC replaced by another actual observed bar; reference window must remain unchanged',
                   replacement_bar_time=alternative['time'], checked_fields=list(threshold_keys)))
    if not ready:
        check('model-warmup', 'Modelos ainda sem amostra causal suficiente', 'NOT_TESTED',
              dict(reason='No mathematically ready model; no synthetic bars added',
                   models=[dict(id=row.get('id'), state=row.get('state'), required_bars=row.get('required_bars'),
                                available_bars=row.get('available_bars')) for row in models if isinstance(row, dict)]))

    requirements = [
        dict(id='independent_sessions', status='NOT_VERIFIED', observed_sessions=len(sessions),
             required_evidence='A preregistered sampling plan across independent sessions and market regimes; count alone cannot qualify a model.'),
        dict(id='multiple_days', status='NOT_VERIFIED', observed_utc_days=len(days),
             required_evidence='Multiple representative trading days, including adverse regimes and continuous-coverage evidence.'),
        dict(id='holdout', status='UNKNOWN',
             required_evidence='Immutable out-of-sample split fixed before parameter selection; no held-out designation is supplied here.'),
        dict(id='calibrated_costs', status='UNKNOWN',
             required_evidence='Instrument-specific fees and independently measured latency, slippage and impact; scenario defaults are assumptions.'),
        dict(id='fill_probability', status='UNKNOWN',
             required_evidence='Independent execution/queue evidence; L1 snapshots cannot establish obtainable fills.'),
        dict(id='statistical_plan', status='UNKNOWN',
             required_evidence='Preregistered objectives, effect size, uncertainty estimates, dependence-aware validation and multiple-testing controls.'),
    ]
    return dict(
        schema='qhos.quant.research-validation.v1', mode='LOCAL_SIMULATION', status='INSUFFICIENT_EVIDENCE',
        engineering_status='FAIL' if any(row['status'] == 'FAIL' for row in checks) else 'NO_COUNTEREXAMPLE_IN_TESTED_CHECKS',
        can_execute=False, profitability_verified=False, observed_executions=False,
        coverage=dict(input_events=len(events), valid_mapping_events=len(captured), trade_events=len(trades),
                      observed_incremental_volume=sum(row['size'] for row in trades),
                      sessions=sessions, session_count=len(sessions), utc_days=days, day_count=len(days),
                      closed_complete_bars=len(complete_bars), ready_mathematical_models=len(ready),
                      exchange_sequence_complete='UNKNOWN', full_trading_session_coverage='UNKNOWN'),
        scenarios=scenarios, checks=checks, requirements=requirements,
        statistical_limitations=[
            'Engineering checks and stress scenarios do not demonstrate alpha or approve a trading model.',
            'Costs, latency and liquidity haircuts are assumptions; perturbed scenarios are not observed data.',
            'Inventory marks are hypothetical and are not realized trading profit.',
            'This sample provides neither a verified holdout nor calibrated fill probability or statistical power.',
            'NOT_TESTED and UNKNOWN are not passing results; bounded fill lists never establish unchecked prefix behavior.',
        ])
