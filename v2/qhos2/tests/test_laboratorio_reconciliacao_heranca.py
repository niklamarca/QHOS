import pytest
from qhos2.laboratorio.reconciliacao import monetary_move,align_observations

T=1_790_000_000_000_000_000
def point(delay=0,environment='Paper'):
    return dict(received_ns=T-delay,event_time_ns=T-delay,valid=True,environment=environment)

def test_es_and_gold_use_distinct_money_per_tick():
    es=monetary_move(.25,1,dict(tick_size=.25,point_value=50,currency='USD'))
    gc=monetary_move(.1,1,dict(tick_size=.1,point_value=100,currency='USD'))
    assert es['amount']==12.5 and gc['amount']==10

def test_unknown_reference_cannot_become_money():
    with pytest.raises(ValueError):monetary_move(1,1,dict(tick_size=.25,point_value=None,currency='USD'))

def test_join_never_uses_future_receipt():
    out=align_observations({'ES':[point()],'GC':[point(-1)]},T)
    assert not out['eligible'] and 'GC' not in out['selected']

def test_join_does_not_fill_stale_asset_with_another_price():
    out=align_observations({'ES':[point()],'GC':[point(6_000_000_000)]},T)
    assert not out['eligible'] and 'GC' not in out['selected']

def test_paper_and_test_never_merged():
    out=align_observations({'ES':[point()],'GC':[point(environment='Test')]},T)
    assert not out['eligible'] and 'ENVIRONMENTS_UNKNOWN_OR_MIXED' in out['reasons']

def test_same_environment_but_different_time_is_not_a_joint_observation():
    out=align_observations({'ES':[point()],'GC':[point(1_000_000_000)]},T)
    assert not out['eligible'] and 'PROVIDER_TIME_SKEW_EXCEEDED' in out['reasons']

def test_causal_aligned_observations_remain_separate():
    out=align_observations({'ES':[point()],'GC':[point(100_000_000)]},T)
    assert out['eligible'] and set(out['selected'])=={'ES','GC'}
    assert out['predictive_validity'] is False
