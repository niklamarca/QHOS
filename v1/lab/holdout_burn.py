#!/usr/bin/env python3
"""QUEIMA DO HOLDOUT — uso unico e deliberado. Criterio PRE-REGISTRADO:
PASSA se Sharpe_holdout >= 0.42 (0.4 x 1.06 da pesquisa) E retorno total > 0."""
import glob, math
import numpy as np, pandas as pd
ASSETS=["SPY","QQQ","IWM","GLD","TLT"]; EMBARGO,COST=0.20,0.0002
TREE=["*",["-","r","sig63"],["+",["-","sig5","r"],["-","sig5","sig21"]]]; SGN=-1
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
cut=int(len(P)*(1-EMBARGO))
# computa TUDO com a serie completa (features precisam de warmup), mas AVALIA so no holdout
R=P.pct_change().values[1:]; dates=P.index[1:]
L={"r":R,"sig5":rstd(R,5),"sig21":rstd(R,21),"sig63":rstd(R,63)}
pos=SGN*np.sign(np.nan_to_num(ev(TREE,L)))
pos=np.vstack([np.zeros((1,R.shape[1])),pos[:-1]])
turn=np.abs(np.diff(pos,axis=0,prepend=np.zeros((1,R.shape[1]))))
def port(c): return np.nan_to_num(np.nanmean(pos*R,axis=1)-c*turn.mean(axis=1))
mask=np.arange(len(dates))>= (cut-1)
hold=port(COST)[mask]; hold3=port(COST*3)[mask]; hd=dates[mask]
s_h,s_h3=sh(hold),sh(hold3); tot=float(np.prod(1+hold)-1)
print("="*84)
print(f"  QUEIMA DO HOLDOUT — {hd[0].date()} .. {hd[-1].date()} ({len(hold)} dias NUNCA vistos pela busca)")
print("="*84)
print(f"  Sharpe liquido (2bps) : {s_h:+.2f}   [criterio pre-registrado: >= 0.42]")
print(f"  retorno total         : {tot*100:+.1f}%  [criterio: > 0]")
print(f"  Sharpe custo 3x (6bps): {s_h3:+.2f}")
yr=pd.Series(hold,index=hd); yy=yr.groupby(yr.index.year).apply(lambda x: sh(x.values))
print("  por ano: "+"  ".join(f"{y}:{v:+.1f}" for y,v in yy.items()))
spy=pd.Series(R[mask][:, ASSETS.index('SPY')],index=hd)
print(f"  correlacao com SPY no holdout: {np.corrcoef(hold,spy.values)[0,1]:+.2f}")
print("-"*84)
ok = s_h>=0.42 and tot>0
print(f"  VEREDITO: {'PASSOU NO HOLDOUT — candidato CERTIFICADO (pendente apenas forward)' if ok else 'REPROVADO — candidato MORTO. A busca havia se ajustado ao passado.'}")
print("="*84)
