#!/usr/bin/env python3
"""
VRP com GATE por ESTRUTURA A TERMO (^VIX / ^VIX3M) — o gate CORRETO.
Contango (VIX<VIX3M) => vender vol ON. Backwardation (VIX>=VIX3M) => de fora.
Sinal DEFASADO 1 dia (decisao de hoje usa a razao de ontem) = sem look-ahead.
"""
import numpy as np, pandas as pd, glob
def L(s):
    df=pd.read_parquet(glob.glob(f"data_lake/raw/{s}_1d.parquet")[0]); df.index=pd.to_datetime(df.index); return df["close"].astype(float)
vix,vix3m,vixy=L("VIX"),L("VIX3M"),L("VIXY")
print("sanidade VIX: min %.1f max %.1f | VIX3M: min %.1f max %.1f"%(vix.min(),vix.max(),vix3m.min(),vix3m.max()))
print("janelas: VIX %s..%s | VIX3M %s..%s | VIXY %s..%s"%(vix.index.min().date(),vix.index.max().date(),vix3m.index.min().date(),vix3m.index.max().date(),vixy.index.min().date(),vixy.index.max().date()))
d=pd.DataFrame({"vix":vix,"vix3m":vix3m,"vixy":vixy}).dropna()
ratio=(d["vix"]/d["vix3m"])                      # <1 = contango
short=-d["vixy"].pct_change()
short=short.where(short.abs()<=0.60)             # remove artefato de split
gate=(ratio.shift(1)<1.0)                         # DEFASADO: contango ontem -> vende hoje
df=pd.DataFrame({"short":short,"gate":gate}).dropna()
base=df["short"]; gated=df["short"].where(df["gate"],0.0)   # fora do gate = caixa (0)

def stat(r,name):
    r=r.dropna(); ann=r.mean()*252*100; vol=r.std()*np.sqrt(252)*100; sh=r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
    eq=(1+r).cumprod(); mdd=(eq/eq.cummax()-1).min()*100; cv=r[r<=r.quantile(0.05)].mean()*100
    print(f"  {name:16} ret {ann:+6.1f}%/y  Sharpe {sh:+.2f}  pior_dia {r.min()*100:+6.1f}%  CVaR5 {cv:+5.1f}%  MDD {mdd:+6.1f}%  em_mercado {(r!=0).mean()*100:4.0f}%")
    return r
print("\n=== BASE (sempre short) vs GATED (so contango) ===")
stat(base,"BASE"); g=stat(gated,"GATED term-struct")

# o gate evitou 05/08/2024?
print("\n=== estrutura a termo em torno do crash de vol (05/08/2024) ===")
win=ratio.loc["2024-07-31":"2024-08-08"]
for dt,v in win.items():
    mark="  <-- BACKWARDATION (fica de fora)" if v>=1.0 else ""
    print(f"  {dt.date()}  VIX/VIX3M={v:.3f}  short_ret={short.get(dt,np.nan)*100:+.1f}%{mark}")

# laboratorio block-bootstrap: sobrevivencia
rng=np.random.default_rng(7); CAP,T,F,MAXD,N,B=10000.,11500.,9000.,150,40000,10
def paths(R):
    R=R.values; nb=int(np.ceil(MAXD/B)); st=rng.integers(0,len(R)-B,(N,nb))
    return np.concatenate([R[st[:,k][:,None]+np.arange(B)] for k in range(nb)],axis=1)[:,:MAXD]
def sim(R,Lv):
    P=paths(R);eq=np.full(N,CAP);res=np.zeros(N,bool);o=np.zeros(N,np.int8)
    for j in range(P.shape[1]):
        r=P[:,j];a=~res;eq=np.where(a,eq*(1+Lv*r-0.0001*Lv),eq)
        p=a&(eq>=T);l=a&(eq<=F);o[p]=1;res[p]=True;o[l]=2;res[l]=True
    o[~res]=3;return o
print("\n=== SOBREVIVENCIA (block-bootstrap, barreiras $11.5k/$9k) ===")
print(f"  {'':16} {'L=1 PASS/LIQ':>16} {'L=3 PASS/LIQ':>16} {'L=8 PASS/LIQ':>16}")
for nm,R in [("BASE",base),("GATED",g)]:
    row=""
    for Lv in [1,3,8]:
        o=sim(R.dropna(),Lv); row+=f"   {np.mean(o==1):5.0%}/{np.mean(o==2):<5.0%}     "
    print(f"  {nm:16}{row}")
