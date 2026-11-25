#!/usr/bin/env python3
"""Robustez do trend: SEM cripto (janela longa 2021-26) + custo de turnover."""
import numpy as np, pandas as pd, glob
def L(s):
    df=pd.read_parquet(glob.glob(f"data_lake/raw/{s}_1d.parquet")[0]); df.index=pd.to_datetime(df.index); return df["close"].astype(float)
px=pd.DataFrame({s:L(s) for s in ["SPY","TLT","GLD","IWM","QQQ"]}).sort_index().ffill().dropna()
ret=px.pct_change()
def sig(p): return (np.sign(p.pct_change(63))+np.sign(p.pct_change(126))+np.sign(p.pct_change(252)))/3
S=px.apply(sig).shift(1); vol=ret.rolling(63).std().shift(1)
pos=(S*(0.01/vol)).clip(-0.5,0.5)
COST=0.0002   # 2 bps por unidade de turnover
turn=pos.diff().abs().sum(axis=1)
port=(pos*ret).mean(axis=1)-COST*turn/len(px.columns)
pv=port.rolling(63).std().shift(1)*np.sqrt(252)
trend=(port*(0.15/pv).clip(0,3)).dropna()
spy=ret["SPY"].reindex(trend.index)
def stat(r,n):
    r=r.dropna();a=r.mean()*252*100;sh=r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
    eq=(1+r).cumprod();mdd=(eq/eq.cummax()-1).min()*100
    print(f"  {n:20} ret {a:+6.1f}%/y Sharpe {sh:+.2f} vol {r.std()*np.sqrt(252)*100:3.0f}% MDD {mdd:+6.1f}%");return r
print("janela",trend.index.min().date(),"->",trend.index.max().date(),"| dias",len(trend))
T=stat(trend,"TREND s/cripto +custo"); Sp=stat(spy,"SPY")
print(f"  correlacao TREND vs SPY: {trend.corr(spy):+.2f}")
stat((0.5*spy.fillna(0)+0.5*trend.reindex(spy.index).fillna(0)),"50/50 SPY+TREND")
# 2022 (SPY bear) especifico
y=pd.DataFrame({"trend":trend,"spy":spy})
for yr in [2022,2023,2024,2025]:
    sub=y[y.index.year==yr]
    if len(sub): print(f"  {yr}: SPY {((1+sub['spy']).prod()-1)*100:+5.1f}%  TREND {((1+sub['trend']).prod()-1)*100:+5.1f}%")
