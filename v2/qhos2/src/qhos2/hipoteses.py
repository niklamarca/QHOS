"""Deterministic reference hypotheses over causal, complete 30-second bars.

Ported from the preserved qhos-v2-quant models.py, reviewed with its tests.
This module has no dependency on the legacy services or their decoder.
No model return authorizes trading or establishes profitability. VWAP is a
benchmark; the other outputs are mathematical hypotheses awaiting validation.
"""
from __future__ import annotations

import math
from decimal import Decimal, localcontext
from statistics import fmean, pstdev


MODEL_SPECS = (
    ('vwap', 'VWAP exato · janela de 20 barras', 20, 'Σ(pv_ticks × tick_size) / Σ(volume)',
     {'window': 20, 'role': 'benchmark', 'price_basis': 'exact observed trade ticks'}),
    ('mean_reversion', 'Reversão à média', 21, 'z = (C[t] − média(C[t−20:t])) / desvio_pop(C[t−20:t])',
     {'lookback': 20, 'z_entry': 2.0, 'role': 'hypothesis'}),
    ('breakout', 'Rompimento', 21, 'C[t] > max(H[t−20:t]) ou C[t] < min(L[t−20:t])',
     {'lookback': 20, 'role': 'hypothesis'}),
    ('trend_following', 'Seguidor de tendência', 41,
     'SMA20[t−1] > SMA40[t−1] e C[t] > SMA40[t−1]; inverso para baixa',
     {'fast': 20, 'slow': 40, 'role': 'hypothesis'}),
)


def _number(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def _eligible_suffix(bars):
    """Keep the latest contiguous complete window; never sort causal input."""
    if not isinstance(bars, (list, tuple)):
        return [], 'INVALID_BAR_SEQUENCE'
    complete = []
    previous_time = None
    open_bar_seen = False
    for bar in bars:
        if not isinstance(bar, dict) or not _number(bar.get('time')) or bar['time'] < 0:
            return [], 'INVALID_BAR_TIMESTAMP'
        at = bar['time']
        if abs(at / 30 - round(at / 30)) > 1e-8:
            return [], 'BAR_NOT_ALIGNED_TO_30_SECONDS'
        if previous_time is not None and at <= previous_time:
            return [], 'BARS_NOT_IN_STRICT_TIME_ORDER'
        if open_bar_seen:
            return [], 'CLOSED_BAR_AFTER_OPEN_BAR'
        if bar.get('closed') is not True:
            if bar.get('closed') is not False:
                return [], 'UNKNOWN_BAR_COMPLETION'
            open_bar_seen = True
            previous_time = at
            continue
        if previous_time is not None and at != previous_time + 30:
            complete = []
        previous_time = at
        if bar.get('complete') is not True:
            complete = []
            continue
        if any(not _number(bar.get(key)) or bar[key] <= 0 for key in ('open', 'high', 'low', 'close')):
            return [], 'INVALID_OHLC'
        if bar['high'] < max(bar['open'], bar['close'], bar['low']) or bar['low'] > min(bar['open'], bar['close']):
            return [], 'INCONSISTENT_OHLC'
        if type(bar.get('volume')) is not int or bar['volume'] < 0:
            return [], 'INVALID_BAR_VOLUME'
        complete.append(bar)
    return complete, None


def _evaluate_models(bars, quality=None):
    valid, invalid = _eligible_suffix(bars)
    quality = quality or {}
    if not isinstance(quality, dict):
        quality = {'blocked': True, 'reasons': ['INVALID_QUALITY_GATE']}
    blocked = quality.get('blocked') is True
    reasons = quality.get('reasons') if isinstance(quality.get('reasons'), list) else []
    results = []
    for model_id, name, required, formula, parameters in MODEL_SPECS:
        result = dict(id=model_id, name=name, state='waiting', reason='WARMUP_INCOMPLETE',
                      formula=formula, parameters=dict(parameters), evidence={}, signal=None,
                      required_bars=required, available_bars=len(valid),
                      trade_eligible=False, profitability_verified=False,
                      status_label='Hipótese matemática; rentabilidade não validada')
        if invalid or blocked:
            result.update(state='blocked', reason=invalid or 'QUALITY_GATE_BLOCKED',
                          evidence={'quality_reasons': list(reasons)})
        elif len(valid) >= required:
            window = valid[-required:]
            latest = window[-1]
            prior = window[:-1]
            bar_end = latest['time'] + 30
            available_ns = latest.get('closed_at_received_ns')
            if type(available_ns) is not int or available_ns < int(bar_end * 1e9):
                available_ns = None
            result.update(state='ready', reason='CAUSAL_REFERENCE_CALCULATED',
                          evidence=dict(bar_end_time=bar_end,
                                        decision_time=available_ns / 1e9 if available_ns is not None else None,
                                        decision_available_ns=available_ns,
                                        availability='OBSERVED' if available_ns is not None else 'NOT_AVAILABLE',
                                        close_trigger_event_id=latest.get('close_trigger_event_id'),
                                        decision_event_id=latest.get('event_id'),
                                        first_bar_time=window[0]['time'], last_bar_time=latest['time'],
                                        closed_complete_bars=required, latest_close=latest['close']))
            direction, value = 'neutral', 0.0
            if model_id == 'vwap':
                if any(type(bar.get('pv_ticks')) is not int or bar['pv_ticks'] < 0
                       or not _number(bar.get('tick_size')) or bar['tick_size'] <= 0 for bar in window):
                    result.update(state='blocked', reason='EXACT_TRADE_PRICE_VOLUME_UNAVAILABLE')
                    results.append(result)
                    continue
                if len({str(bar['tick_size']) for bar in window}) != 1:
                    result.update(state='blocked', reason='INCONSISTENT_TICK_SIZE')
                    results.append(result)
                    continue
                total_volume = sum(bar['volume'] for bar in window)
                if total_volume == 0:
                    result.update(state='blocked', reason='ZERO_OBSERVED_VOLUME')
                    results.append(result)
                    continue
                with localcontext() as context:
                    context.prec = 50
                    price_volume = sum(Decimal(bar['pv_ticks']) * Decimal(str(bar['tick_size'])) for bar in window)
                    exact_value = price_volume / Decimal(total_volume)
                value = float(exact_value)
                direction = 'benchmark'
                result['evidence'].update(vwap=value, volume=total_volume,
                                          vwap_decimal=str(exact_value), price_volume_decimal=str(price_volume),
                                          price_basis='Exact trade price × size aggregated in integer ticks',
                                          gap_to_close=latest['close'] - value)
            elif model_id == 'mean_reversion':
                mean, deviation = fmean(bar['close'] for bar in prior), pstdev(bar['close'] for bar in prior)
                result['evidence'].update(previous_mean=mean, previous_std=deviation,
                                          threshold_window_end=prior[-1]['time'])
                if deviation == 0:
                    result.update(state='blocked', reason='ZERO_VARIANCE_REFERENCE_WINDOW')
                    results.append(result)
                    continue
                value = (latest['close'] - mean) / deviation
                direction = 'sell' if value > 2 else 'buy' if value < -2 else 'neutral'
            elif model_id == 'breakout':
                high, low = max(bar['high'] for bar in prior), min(bar['low'] for bar in prior)
                direction = 'buy' if latest['close'] > high else 'sell' if latest['close'] < low else 'neutral'
                value = latest['close'] - (high if direction == 'buy' else low if direction == 'sell' else latest['close'])
                result['evidence'].update(previous_high=high, previous_low=low,
                                          threshold_window_end=prior[-1]['time'])
            else:
                fast, slow = fmean(bar['close'] for bar in prior[-20:]), fmean(bar['close'] for bar in prior[-40:])
                direction = 'buy' if fast > slow and latest['close'] > slow else 'sell' if fast < slow and latest['close'] < slow else 'neutral'
                value = fast - slow
                result['evidence'].update(previous_sma20=fast, previous_sma40=slow,
                                          threshold_window_end=prior[-1]['time'])
            result['signal'] = dict(direction=direction, value=value, trade_eligible=False,
                                    role=parameters['role'])
        results.append(result)
    return results


def evaluate_models(bars, quality=None):
    """Formula availability is not statistical qualification or a trading permit."""
    try:
        results = _evaluate_models(bars, quality)
        def finite_tree(value):
            if isinstance(value, float):
                return math.isfinite(value)
            if isinstance(value, dict):
                return all(finite_tree(v) for v in value.values())
            if isinstance(value, (list, tuple)):
                return all(finite_tree(v) for v in value)
            return True
        if not finite_tree(results):
            raise ArithmeticError("nonfinite_calculation")
        return results
    except (OverflowError, ArithmeticError):
        return [dict(id=mid, name=name, state="blocked", reason="NUMERICAL_RANGE_EXCEEDED",
                     formula=formula, parameters=dict(parameters), evidence={}, signal=None,
                     required_bars=required, available_bars=0, trade_eligible=False,
                     profitability_verified=False, status_label="Hipotese matematica; rentabilidade nao validada")
                for mid, name, required, formula, parameters in MODEL_SPECS]



def barras_da_captura(pasta, *, max_barras=100_000):
    """30s bars from qualified observed trades, using receipt time explicitly.

    First and final buckets are partial. Empty intervals are never invented.
    Each closed bucket becomes available only when the next observed frame
    arrives. No snapshot trade is counted as a new trade. The complete capture
    must pass integrity/book checks before any bars are returned.
    """
    import itertools
    from qhos2 import eventos as ev
    from qhos2.livro import Livro
    from qhos2.pesquisa import _identificar_capturas, identidade_codigo, PesquisaError
    if type(max_barras) is not int or not 1 <= max_barras <= 1_000_000:
        raise ValueError("INVALID_BAR_LIMIT")
    before = _identificar_capturas([pasta])
    code = identidade_codigo()
    book, result, current, previous = Livro(), [], None, None
    first_bucket, last_bucket, incomplete_bucket = None, None, False
    for index, group in itertools.groupby(ev.eventos_da_captura(pasta), key=lambda e: e.indice):
        events = list(group)
        received = events[-1].recebido_ns
        if previous is not None and received < previous:
            raise ValueError("NON_MONOTONE_RECEIPT_TIME")
        previous = received
        bucket = received // 30_000_000_000 * 30
        if first_bucket is None:
            first_bucket = bucket
        if bucket != last_bucket:
            incomplete_bucket = False
            last_bucket = bucket
        if current is not None and bucket > current["time"]:
            current.update(closed=True, closed_at_received_ns=received,
                           close_trigger_event_id=f"frame-{index}")
            result.append(current)
            current = None
            if len(result) > max_barras:
                raise ValueError("BAR_LIMIT_EXCEEDED")
        book.aplicar_quadro(events)
        if not book.qualificado:
            incomplete_bucket = True
            if current is not None:
                current["complete"] = False
            continue
        for event in events:
            if event.tipo != ev.NEGOCIO or event.retrato:
                continue
            if type(event.preco) is not int or type(event.quantidade) is not int or event.preco <= 0 or event.quantidade <= 0:
                raise ValueError("INVALID_OBSERVED_TRADE")
            price = event.preco * .25
            if not math.isfinite(price):
                raise ValueError("INVALID_OBSERVED_TRADE")
            if current is None:
                current = dict(time=bucket, open=price, high=price, low=price, close=price,
                               volume=0, pv_ticks=0, tick_size=.25, closed=False,
                               complete=bucket != first_bucket and not incomplete_bucket, event_id=f"frame-{index}:{event.posicao}")
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
            current["volume"] += event.quantidade
            current["pv_ticks"] += event.preco * event.quantidade
            current["event_id"] = f"frame-{index}:{event.posicao}"
    if current is not None:
        current["complete"] = False
        result.append(current)
    if len(result) > max_barras:
        raise ValueError("BAR_LIMIT_EXCEEDED")
    if before != _identificar_capturas([pasta]) or code != identidade_codigo():
        raise PesquisaError("entrada_mudou_durante_as_barras")
    return {"esquema": "qhos2.barras_pesquisa.v1", "capturas": before, "codigo": code, "bars": result,
            "time_basis": "local_receipt", "utc_qualified": False, "bar_seconds": 30,
            "execution_enabled": False, "profitability_verified": False}


def avaliar_captura(pasta):
    """Reference hypotheses on verified V2 capture; no old decoder/service."""
    dataset = barras_da_captura(pasta)
    return {**dataset, "models": evaluate_models(dataset["bars"])}

def main(argv=None):
    """Evaluate explicit local bars; never fetch data or execute orders."""
    import argparse
    import json
    from pathlib import Path
    ap = argparse.ArgumentParser(prog="qhos2.hipoteses")
    ap.add_argument("barras", type=Path, help="JSON com barras completas de 30 segundos e proveniencia")
    ap.add_argument("--captura", action="store_true", help="Ler uma captura V2 qualificada, em vez de barras JSON")
    args = ap.parse_args(argv)
    try:
        if args.captura:
            payload = avaliar_captura(args.barras)
            print(json.dumps(payload, allow_nan=False, sort_keys=True, indent=2))
            return 2 if any(r["state"] == "blocked" for r in payload["models"]) else 0
        if args.barras.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("INPUT_TOO_LARGE")
        rows = json.loads(args.barras.read_text(encoding="utf-8"))
        result = evaluate_models(rows)
        print(json.dumps(result, allow_nan=False, sort_keys=True, indent=2))
        return 2 if any(r["state"] == "blocked" for r in result) else 0
    except (OSError, ValueError, TypeError, OverflowError):
        print("hipoteses recusadas: entrada invalida")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
