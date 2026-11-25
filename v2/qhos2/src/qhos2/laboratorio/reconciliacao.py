"""Multi-instrument reconciliation gates, independent from any signal model.

Matching timestamps does not establish common economic meaning. Raw prices and
contract volumes are deliberately never added across instruments.
"""
from decimal import Decimal
import math

def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False



def monetary_move(price_change,quantity,reference):
    """Scenario amount in the instrument's currency; not account P&L."""
    if not isinstance(reference, dict):
        raise ValueError('REFERENCE_INCOMPLETE')
    if type(quantity) is not int or quantity==0:
        raise ValueError('INTEGER_NONZERO_QUANTITY_REQUIRED')
    if not _finite(price_change):
        raise ValueError('PRICE_CHANGE_INVALID')
    point=reference.get('point_value')
    tick=reference.get('tick_size')
    currency=reference.get('currency')
    if any(not _finite(v) or v<=0 for v in (point,tick)) or not isinstance(currency,str) or not currency:
        raise ValueError('REFERENCE_INCOMPLETE')
    try:
        amount = float(Decimal(str(price_change))*quantity*Decimal(str(point)))
        tick_value = float(Decimal(str(point))*Decimal(str(tick)))
    except ArithmeticError:
        raise ValueError('NUMERICAL_RANGE_EXCEEDED') from None
    if not _finite(amount) or not _finite(tick_value):
        raise ValueError('NUMERICAL_RANGE_EXCEEDED')
    return dict(amount=amount, currency=currency, tick_value=tick_value,
                hypothetical=True, mode='LOCAL_SIMULATION', can_execute=False)


def align_observations(observations,at_ns,*,max_age_ms=5000,max_skew_ms=250):
    """Causal as-of join with both provider and receipt freshness.

Uses only data available by at_ns; never forward-fills stale or missing assets.
The caller must supply every required instrument as a distinct dictionary key.
"""
    if type(at_ns) is not int or at_ns<=0 or not isinstance(observations,dict):
        raise ValueError('INVALID_ALIGNMENT_INPUT')
    for value in (max_age_ms,max_skew_ms):
        if not _finite(value) or value<0:
            raise ValueError('INVALID_ALIGNMENT_WINDOW')
    selected={}
    reasons=[]
    if len(observations) > 1000:
        raise ValueError('ALIGNMENT_LIMIT_EXCEEDED')
    for key,rows in observations.items():
        if not isinstance(key, str) or not key or not isinstance(rows, (list, tuple)) or len(rows) > 100000:
            raise ValueError('INVALID_ALIGNMENT_INPUT')
        candidates=[]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('INVALID_ALIGNMENT_ROW')
            receipt=row.get('received_ns')
            event=row.get('event_time_ns')
            if type(receipt) is not int or type(event) is not int or min(receipt,event)<=0:
                continue
            if receipt<=at_ns and event<=receipt and row.get('valid') is True:
                candidates.append(row)
        if not candidates:
            reasons.append(f'{key}:NO_CAUSALLY_AVAILABLE_OBSERVATION')
            continue
        last=max(candidates,key=lambda row:row['received_ns'])
        if any(at_ns-last[field]>max_age_ms*1e6 for field in ('received_ns','event_time_ns')):
            reasons.append(f'{key}:STALE')
            continue
        selected[key]=dict(last)
    environments={row.get('environment') if isinstance(row.get('environment'), str) else None for row in selected.values()}
    if any(e not in {'Paper', 'Test', 'Production', 'Synthetic', 'Historical'} for e in environments):
        reasons.append('ENVIRONMENT_UNRECOGNIZED')
    if None in environments or len(environments)>1:
        reasons.append('ENVIRONMENTS_UNKNOWN_OR_MIXED')
    if len(selected)>1:
        times=[row['event_time_ns'] for row in selected.values()]
        if max(times)-min(times)>max_skew_ms*1e6:
            reasons.append('PROVIDER_TIME_SKEW_EXCEEDED')
    if not observations:
        reasons.append('NO_REQUIRED_INSTRUMENTS')
    return dict(eligible=not reasons and len(selected)==len(observations),reasons=reasons,
                selected=selected,as_of_ns=str(at_ns),clock_calibration='unknown',
                predictive_validity=False,aggregation='per_instrument_only', mode='LOCAL_SIMULATION', can_execute=False)


RULES=[
    'Identidade: fornecedor + ambiente + bolsa + contrato/vencimento; ESU6 e ESZ6 são instrumentos distintos.',
    'Negócios, BBO, MBP e MBO possuem contratos próprios; CSV amostrado não substitui ticks ou livro completo.',
    'Preços em ticks e valores financeiros por referência do contrato; volumes brutos de ativos diferentes não são somados.',
    'Alinhamento causal em UTC pelos instantes do fornecedor e de recepção; ativos atrasados ficam ausentes.',
    'Test, Paper e produção não são misturados. Histórico externo mantém fornecedor, calendário e granularidade.',
    'Retornos, volatilidade e fatores entre ativos exigem janelas simultâneas comparáveis e amostras suficientes.',
    'Contratos contínuos exigem política explícita de rolagem e ajuste; não concatenar vencimentos silenciosamente.',
    'Cada modelo conserva linhagem da entrada, versão, parâmetros, custos e causas de invalidação.'
]
