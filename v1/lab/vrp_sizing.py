#!/usr/bin/env python3
"""VRP gated + SIZING pra sobrevivencia (vol-target / Kelly fracionario) + benchmark SPY."""
import numpy as np, pandas as pd, glob
def L(s):
    df=pd.read_parquet(glob.glob(f"data_lake/raw/{s}_1d.parquet")[0]); df.index=pd.to_datetime(df.index); return df["close"].astype(float)
vix,vix3m,vixy,spy=L("VIX"),L("VIX3M"),L("VIXY"),L("SPY")
d=pd.DataFrame({"vix":vix,"vix3m":vix3m,"vixy":vixy,"spy":spy}).dropna()
ratio=d["vix"]/d["vix3m"]
short=(-d["vixy"].pct_change()).where(lambda x:x.abs()<=0.60)
gate=(ratio.shift(1)<1.0)                          # contango ontem -> vende hoje (sem look-ahead)
g=short.where(gate,0.0).dropna()                  # gated (fora = caixa)
spyr=d["spy"].pct_change().reindex(g.index)
kel=g.mean()/g.var() if g.var()>0 else 0

def size_voltarget(r,target):
    rv=(r.rolling(20).std()*np.sqrt(252)).shift(1)  # vol estimada ate ontem
    lev=(target/rv).clip(0,3.0).fillna(0.0)         # cap 3x
    return (lev*r).dropna(), lev
def stat(r,name):
    r=r.dropna(); ann=r.mean()*252*100; sh=r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
    eq=(1+r).cumprod(); mdd=(eq/eq.cummax()-1).min()*100; cv=r[r<=r.quantile(0.05)].mean()*100
    print(f"  {name:22} ret {ann:+6.1f}%/y  Sharpe {sh:+.2f}  vol {r.std()*np.sqrt(252)*100:4.0f}%  pior_dia {r.min()*100:+6.1f}%  CVaR5 {cv:+5.1f}%  MDD {mdd:+6.1f}%")
    return r

print("Kelly f* do gated: %.2fx\n"%kel)
print("=== SIZING (o que decide operabilidade) ===")
full=stat(g,"GATED full (~1x)")
vt15,_=size_voltarget(g,0.15); vt15=stat(vt15,"GATED vol-target 15%")
vt10,_=size_voltarget(g,0.10); vt10=stat(vt10,"GATED vol-target 10%")
qk=stat((0.25*kel*g).dropna(),"GATED 1/4-Kelly")
spY=stat(spyr,"SPY buy&hold (bench)")

# correlacao e combo com SPY (o VRP vale como DIVERSIFICADOR?)
cor=vt15.reindex(spyr.index).corr(spyr)
combo=(0.5*vt15.reindex(spyr.index).fillna(0)+0.5*spyr.fillna(0))
print("\n=== VRP vale vs/ com SPY? ===")
print(f"  correlacao (VRP vt15 vs SPY): {cor:+.2f}")
stat(spyr,"  SPY sozinho")
stat(vt15.reindex(spyr.index),"  VRP vt15 sozinho")
stat(combo,"  50/50 SPY+VRP")

# sobrevivencia na barreira
rng=np.random.default_rng(7);CAP,T,F,MAXD,N,B=10000.,11500.,9000.,150,40000,10
def paths(R):
    R=R.values;nb=int(np.ceil(MAXD/B));st=rng.integers(0,len(R)-B,(N,nb))
    return np.concatenate([R[st[:,k][:,None]+np.arange(B)] for k in range(nb)],axis=1)[:,:MAXD]
def sim(R):
    P=paths(R);eq=np.full(N,CAP);res=np.zeros(N,bool);o=np.zeros(N,np.int8)
    for j in range(P.shape[1]):
        r=P[:,j];a=~res;eq=np.where(a,eq*(1+r-0.00003),eq)
        p=a&(eq>=T);l=a&(eq<=F);o[p]=1;res[p]=True;o[l]=2;res[l]=True
    o[~res]=3;return o
print("\n=== SOBREVIVENCIA na barreira $11.5k/$9k (sizing embutido, L=1) ===")
for nm,R in [("GATED full",full),("vol-target 15%",vt15),("vol-target 10%",vt10),("1/4-Kelly",qk)]:
    o=sim(R.dropna());print(f"  {nm:18} PASSA {np.mean(o==1):5.1%}  LIQUIDA {np.mean(o==2):5.1%}  timeout {np.mean(o==3):5.1%}")
