#!/usr/bin/env python3
"""AUTOPSIA DO CANDIDATO (sem holdout): por-ativo, por-periodo, turnover/breakeven, jackknife."""
import json, glob, math
import numpy as np, pandas as pd

ASSETS=["SPY","QQQ","IWM","GLD","TLT"]; EMBARGO,COST=0.20,0.0002
TREE=["*",["-","r","sig63"],["+",["-","sig5","r"],["-","sig5","sig21"]]]; SGN=-1

def rmean(x,w):
    cs=np.cumsum(x,axis=0); o=np.full_like(x,np.nan); o[w:]=(cs[w:]-cs[:-w])/w; return o
def rstd(x,w):
    cs=np.cumsum(x,axis=0); c2=np.cumsum(x*x,axis=0); o=np.full_like(x,np.nan)
    m=(cs[w:]-cs[:-w])/w; m2=(c2[w:]-c2[:-w])/w; o[w:]=np.sqrt(np.clip(m2-m*m,0,None)); return o
def ev(t,L):
    if isinstance(t,str): return L[t]
    o,a,b=t; x,y=ev(a,L),ev(b,L)
    return np.clip({"+":x+y,"-":x-y,"*":x*y,"/":x/np.where(np.abs(y)<1e-9,np.nan,y)}[o],-1e6,1e6)
def sh(x):
    x=x[~np.isnan(x)]; s=np.std(x); return float(np.mean(x)/s*math.sqrt(252)) if s>1e-12 and len(x)>50 else 0.0

px={s:pd.read_parquet(glob.glob(f"data_lake/raw/{s}_1d.parquet")[0])["close"].astype(float) for s in ASSETS}
P=pd.DataFrame(px); P.index=pd.to_datetime(P.index); P=P.sort_index().ffill().dropna()
cut=int(len(P)*(1-EMBARGO)); P=P.iloc[:cut]
dates=P.index[1:]; R=P.pct_change().values[1:]
L={"r":R,"sig5":rstd(R,5),"sig21":rstd(R,21),"sig63":rstd(R,63)}
pos=SGN*np.sign(np.nan_to_num(ev(TREE,L)))
pos=np.vstack([np.zeros((1,R.shape[1])),pos[:-1]])
turn=np.abs(np.diff(pos,axis=0,prepend=np.zeros((1,R.shape[1]))))
gross_i=pos*R; net_i=gross_i-COST*turn
port=np.nanmean(net_i,axis=1)
print("="*84)
print(f"  AUTOPSIA — candidato -(r-sig63)x((sig5-r)+(sig5-sig21)) | pesquisa {dates[0].date()}..{dates[-1].date()}")
print("="*84)

print("\n[LAMINA 1] POR ATIVO (Sharpe liquido, perna isolada):")
for j,s in enumerate(ASSETS):
    print(f"    {s:5} Sharpe {sh(net_i[:,j]):+.2f}   turnover/dia {turn[:,j].mean():.2f}")

print("\n[LAMINA 2] POR PERIODO (a literatura preve decaimento pos-2010):")
years=pd.Series(port,index=dates)
for a,b in [(2004,2010),(2011,2016),(2017,2022)]:
    seg=years[(years.index.year>=a)&(years.index.year<=b)]
    print(f"    {a}-{b}: Sharpe {sh(seg.values):+.2f}  ({len(seg)}d)")
print("    por ano:")
yr=years.groupby(years.index.year).apply(lambda x: sh(x.values))
print("    "+"  ".join(f"{y}:{v:+.1f}" for y,v in yr.items()))

print("\n[LAMINA 3] TURNOVER / BREAKEVEN:")
tavg=turn.mean()
gross=np.nanmean(pos*R,axis=1)
be=np.mean(gross)/max(tavg,1e-9)*1e4
print(f"    turnover medio {tavg:.2f}/ativo/dia | retorno bruto por unidade de turnover: {be:.1f} bps")
print(f"    custo assumido 2bps -> margem {be-2:.1f} bps | morre se custo real > {be:.1f} bps")

print("\n[LAMINA 4] JACKKNIFE (remove 1 ativo):")
for j,s in enumerate(ASSETS):
    keep=[k for k in range(5) if k!=j]
    print(f"    sem {s:5}: Sharpe {sh(np.nanmean(net_i[:,keep],axis=1)):+.2f}")
print(f"    completo   : Sharpe {sh(port):+.2f}")
print("="*84)
