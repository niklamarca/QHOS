"""Causal hypothetical POV execution using only observed L1 capacity.

This is a scenario simulator, not an order router or proof of obtainable fills.
No network, filesystem, credentials, provider SDK or real orders are involved.
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


DEFAULT_CONFIG = dict(target_qty=25, participation=0.1, latency_ms=100,
                      slippage_ticks=1, fee_per_contract=2.5, side='buy',
                      max_quote_age_ms=5000, max_trade_age_ms=5000, initial_delay_ms=0)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _stamp(value):
    return type(value) is int and value > 0


def _config(config):
    config = {} if config is None else config
    if not isinstance(config, dict) or set(config) - set(DEFAULT_CONFIG):
        raise ValueError('INVALID_SIMULATION_CONFIG')
    result = dict(DEFAULT_CONFIG, **config)
    if type(result['target_qty']) is not int or not 1 <= result['target_qty'] <= 100000:
        raise ValueError('INVALID_TARGET_QTY')
    if result['side'] not in ('buy', 'sell'):
        raise ValueError('INVALID_SIDE')
    if not _finite(result['participation']) or not 0 < result['participation'] <= 1:
        raise ValueError('INVALID_PARTICIPATION')
    if type(result['slippage_ticks']) is not int or not 0 <= result['slippage_ticks'] <= 100:
        raise ValueError('INVALID_SLIPPAGE')
    for key in ('latency_ms', 'fee_per_contract', 'max_quote_age_ms', 'max_trade_age_ms', 'initial_delay_ms'):
        if not _finite(result[key]) or result[key] < 0:
            raise ValueError('INVALID_' + key.upper())
    if (not 0 < result['max_quote_age_ms'] <= 60000 or not 0 < result['max_trade_age_ms'] <= 60000
            or result['latency_ms'] > 60000 or result['initial_delay_ms'] > 3600000):
        raise ValueError('INVALID_TIME_BOUND')
    return result


def _run_simulation(events, config=None):
    cfg = _config(config)
    if not isinstance(events, (list, tuple)):
        raise ValueError('INVALID_EVENT_SEQUENCE')
    if len(events) > 100_000:
        raise ValueError('EVENT_LIMIT_EXCEEDED')
    side, sign = cfg['side'], 1 if cfg['side'] == 'buy' else -1
    selected_side = 'ask' if side == 'buy' else 'bid'
    session = None
    previous_received = first_received = None
    quote = {}
    tick = point_value = None
    seen = set()
    orders, fills, rejections, equity = [], [], [], []
    pending = []
    observed_volume = filled = accepted_events = 0
    cash = fees = slippage_cost = Decimal(0)
    last_trade = last_trade_time = None
    state, reason = 'complete', 'REPLAY_COMPLETE'

    def reject(code, event_id, now, *, fatal=False):
        nonlocal state, reason
        rejections.append(dict(reason=code, event_id=event_id, received_ns=now))
        if fatal:
            state, reason = 'blocked', code

    def mark_inventory(now):
        if not filled or last_trade is None or point_value is None:
            return
        mark = float(cash + sign * filled * Decimal(str(last_trade)) * Decimal(str(point_value)))
        row = dict(time=now / 1e9, inventory=sign * filled, mark_price=last_trade,
                   mark_event_time_ns=last_trade_time, mark_age_ms=(now - last_trade_time) / 1e6,
                   value=mark if math.isfinite(mark) else None,
                   label='Hypothetical inventory mark after fees; not realized profit', hypothetical=True)
        if equity and equity[-1]['time'] == row['time']:
            equity[-1] = row
        else:
            equity.append(row)

    for event in events:
        if not isinstance(event, dict):
            reject('INVALID_EVENT', None, None, fatal=True); break
        event_id, now = event.get('event_id'), event.get('received_ns')
        kind, provider_ns = event.get('kind'), event.get('event_time_ns')
        if not isinstance(event_id, str) or not event_id or event_id in seen:
            reject('INVALID_OR_DUPLICATE_EVENT_ID', event_id, now, fatal=True); break
        if not _stamp(now) or (previous_received is not None and now < previous_received):
            reject('RECEIPT_ORDER_INVALID', event_id, now, fatal=True); break
        current_session = event.get('session_id')
        if not isinstance(current_session, str) or not current_session:
            reject('SESSION_UNKNOWN', event_id, now, fatal=True); break
        if session is None:
            session, first_received = current_session, now
        elif current_session != session:
            reject('SESSION_BOUNDARY_STOP', event_id, now, fatal=True); break
        seen.add(event_id)
        previous_received = now
        if kind == 'session':
            continue
        if not _stamp(provider_ns) or provider_ns > now:
            reject('PROVIDER_TIME_UNKNOWN_OR_FUTURE', event_id, now); continue
        if kind == 'reference':
            new_tick, new_point = event.get('tick_size'), event.get('point_value')
            if not _finite(new_tick) or new_tick <= 0 or not _finite(new_point) or new_point <= 0:
                reject('INSTRUMENT_REFERENCE_INCOMPLETE', event_id, now); continue
            if tick is not None and (tick != new_tick or point_value != new_point):
                reject('INSTRUMENT_REFERENCE_CHANGED', event_id, now, fatal=True); break
            tick, point_value = new_tick, new_point
        elif kind == 'bbo':
            updates = {}
            for quote_side in ('bid', 'ask'):
                price, size = event.get(quote_side), event.get(quote_side + '_size')
                received = event.get(quote_side + '_received_ns')
                market = event.get(quote_side + '_event_time_ns')
                if (not _finite(price) or price <= 0 or type(size) is not int or size < 0
                        or not _stamp(received) or received > now or not _stamp(market) or market > received):
                    # Unknown/cleared side is removed; never inherit liquidity across it.
                    updates[quote_side] = None
                    continue
                old = quote.get(quote_side)
                if old and (received < old['received_ns'] or market < old['event_time_ns']):
                    reject('OUT_OF_ORDER_QUOTE_SIDE', event_id, now)
                    updates[quote_side] = None
                    continue
                if old and price == old['price']:
                    capacity = min(size, old['capacity'] + max(0, size - old['displayed']))
                else:
                    capacity = size
                updates[quote_side] = dict(price=price, displayed=size, capacity=capacity,
                                           received_ns=received, event_time_ns=market, event_id=event_id)
            quote = updates
            if quote.get('bid') and quote.get('ask') and quote['bid']['price'] >= quote['ask']['price']:
                quote = {}
                reject('CROSSED_OR_LOCKED_QUOTE', event_id, now)
        elif kind == 'trade':
            price, size = event.get('price'), event.get('size')
            if (not _finite(price) or price <= 0 or type(size) is not int or size <= 0
                    or event.get('is_snapshot') is True):
                reject('NON_INCREMENTAL_OR_INVALID_TRADE', event_id, now); continue
            if now - provider_ns > int(cfg['max_trade_age_ms'] * 1e6):
                reject('STALE_TRADE_NOT_ELIGIBLE_FOR_POV', event_id, now); continue
            observed_volume += size
            if last_trade_time is None or provider_ns >= last_trade_time:
                last_trade, last_trade_time = price, provider_ns
            due = int(Decimal(observed_volume) * Decimal(str(cfg['participation'])))
            submitted = filled + sum(order['remaining'] for order in pending)
            quantity = min(cfg['target_qty'], due) - submitted
            if quantity > 0 and now >= first_received + int(cfg['initial_delay_ms'] * 1e6):
                order = dict(order_id=f'sim-order-{len(orders) + 1}', side=side, quantity=quantity,
                             remaining=quantity, submitted_ns=now,
                             arrival_ns=now + int(cfg['latency_ms'] * 1e6), status='pending',
                             trigger_event_id=event_id, hypothetical=True)
                orders.append(order)
                pending.append(order)
        else:
            reject('UNSUPPORTED_EVENT_KIND', event_id, now); continue
        accepted_events += 1
        # Mark open simulated inventory even if current quotes cannot support a fill.
        mark_inventory(now)
        liquidity = quote.get(selected_side)
        if tick is None or point_value is None or not liquidity:
            continue
        if any(not 0 <= now - liquidity[key] <= int(cfg['max_quote_age_ms'] * 1e6)
               for key in ('received_ns', 'event_time_ns')):
            continue
        for order in list(pending):
            if order['arrival_ns'] > now or liquidity['capacity'] <= 0:
                continue
            quantity = min(order['remaining'], liquidity['capacity'], cfg['target_qty'] - filled)
            if quantity <= 0:
                continue
            tick_decimal = Decimal(str(tick))
            adverse = Decimal(str(liquidity['price'])) + sign * cfg['slippage_ticks'] * tick_decimal
            price = float((adverse / tick_decimal).to_integral_value(rounding=ROUND_CEILING if side == 'buy' else ROUND_FLOOR) * tick_decimal)
            if price <= 0:
                reject('INVALID_SLIPPAGE_PRICE', event_id, now, fatal=True); break
            commission = Decimal(str(cfg['fee_per_contract'])) * quantity
            gross = Decimal(str(price)) * Decimal(str(point_value)) * quantity
            adverse_cost = sign * (Decimal(str(price)) - Decimal(str(liquidity['price']))) * Decimal(str(point_value)) * quantity
            if not all(math.isfinite(float(value)) for value in (gross, commission, adverse_cost, cash - sign * gross - commission)):
                reject('NUMERICAL_RANGE_EXCEEDED', event_id, now, fatal=True); break
            cash -= sign * gross + commission
            fees += commission
            slippage_cost += adverse_cost
            filled += quantity
            order['remaining'] -= quantity
            liquidity['capacity'] -= quantity
            order['status'] = 'filled' if order['remaining'] == 0 else 'partial'
            fills.append(dict(fill_id=f'sim-fill-{len(fills) + 1}', order_id=order['order_id'], side=side,
                              quantity=quantity, price=price, quoted_price=liquidity['price'],
                              submitted_ns=order['submitted_ns'], arrival_ns=order['arrival_ns'], filled_ns=now,
                              trigger_event_id=order['trigger_event_id'], execution_event_id=event_id,
                              quote_event_id=liquidity['event_id'], quote_received_ns=liquidity['received_ns'],
                              quote_event_time_ns=liquidity['event_time_ns'], commission=float(commission),
                              hypothetical=True, liquidity_model='L1 capacity upper bound; no fill probability'))
            if not order['remaining']:
                pending.remove(order)
        mark_inventory(now)
        if state == 'blocked':
            break
    if state != 'blocked' and filled < cfg['target_qty']:
        state, reason = 'partial', 'TARGET_NOT_FILLED_WITH_OBSERVED_L1'
    for order in pending:
        order['terminal_reason'] = reason
    return dict(
        schema='qhos.quant.execution-simulation.v1', mode='LOCAL_SIMULATION', hypothetical=True, can_execute=False,
        config=cfg,
        summary=dict(state=state, reason=reason, session_id=session, target_qty=cfg['target_qty'],
                     filled_qty=filled, unfilled_qty=cfg['target_qty'] - filled,
                     observed_incremental_volume=observed_volume,
                     realized_participation=filled / observed_volume if observed_volume else None,
                     average_fill_price=float(sum(Decimal(str(row['price'])) * row['quantity'] for row in fills) / filled) if filled else None,
                     commissions=float(fees), adverse_slippage_cost=float(slippage_cost),
                     inventory=sign * filled, cash_flow=float(cash), point_value=point_value, tick_size=tick,
                     inventory_mark=equity[-1]['value'] if equity else None, realized_profit=None,
                     events_seen=len(seen), events_accepted=accepted_events,
                     orders_total=len(orders), fills_total=len(fills), rejections_total=len(rejections)),
        fills=fills[-200:], orders=orders[-200:], rejections=rejections[-200:], equity=equity[-600:],
        truncation=dict(fills=len(fills) > 200, orders=len(orders) > 200,
                        rejections=len(rejections) > 200, equity=len(equity) > 600),
        assumptions=[
            'POV hipotético: quantidade acumulada limitada à participação × volume incremental observado.',
            'A ordem hipotética nasce após o negócio observado e aguarda a latência configurada.',
            'A quantidade no mesmo preço é consumida uma vez; apenas aumentos observados a repõem.',
            'Slippage é uma penalidade de preço. Comissões são parâmetros do cenário, não tarifas verificadas.',
            'Todo fill exige referência do instrumento com tick e valor por ponto.',
        ],
        limitations=[
            'L1 não comprova probabilidade de fill, prioridade na fila, liquidez escondida ou impacto próprio.',
            'A quantidade exibida é uma hipótese de capacidade; não há níveis de profundidade para percorrer.',
            'Não há envio de ordens ou aprovação de rentabilidade.',
            'Mudança de sessão interrompe o cenário e mantém a posição hipotética anterior explícita.',
            'Marcação usa o último negócio observado; não é lucro realizado. A razão nocional não é margem nem caixa da conta.',
            'O replay considera somente eventos recebidos; não reconstrói intervalos desconectados.',
        ])


def run_simulation(events, config=None):
    """Pure L1 scenario. Input is explicit typed events, never raw MBO frames."""
    try:
        result = _run_simulation(events, config)
        def finite_tree(value):
            if isinstance(value, float):
                return math.isfinite(value)
            if isinstance(value, dict):
                return all(finite_tree(v) for v in value.values())
            if isinstance(value, (list, tuple)):
                return all(finite_tree(v) for v in value)
            return True
        if not finite_tree(result):
            raise ValueError('NONFINITE_SIMULATION_RESULT')
        return result
    except ArithmeticError:
        raise ValueError('NUMERICAL_RANGE_EXCEEDED') from None
