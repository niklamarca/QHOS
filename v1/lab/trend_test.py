#!/usr/bin/env python3
"""Trend-following (TSMOM cross-asset) — o teste e a CORRELACAO com SPY e o crisis-alpha."""
import numpy as np, pandas as pd, glob, os
def L(s):
    hit=glob.glob(f"data_lake/raw/{s}_1d.parquet")
    if not hit: return None
    df=pd.read_parquet(hit[0]); df.index=pd.to_datetime(df.index); return df["close"].astype(float)
# universo cross-asset: equity, bonds, ouro, small-cap, tech, + cripto se existir
syms=["SPY","TLT","GLD","IWM","QQQ"]
for c in ["BTC_USDT","BTCUSDT","BTC-USD","ETH_USDT"]:
    if glob.glob(f"data_lake/raw/{c}_1d.parquet"): syms.append(c); break
data={s:L(s) for s in syms}; data={k:v for k,v in data.items() if v is not None}
px=pd.DataFrame(data).sort_index().ffill().dropna(how="all")
px=px.dropna()                                  # janela comum
ret=px.pct_change()
print("universo:",list(px.columns),"| janela",px.index.min().date(),"->",px.index.max().date(),"| dias",len(px))

# sinal TSMOM: media dos sinais de 63/126/252 dias (3/6/12m), DEFASADO 1 dia
def sig(p):
    s=np.sign(p.pct_change(63))+np.sign(p.pct_change(126))+np.sign(p.pct_change(252))
    return (s/3.0)
S=px.apply(sig).shift(1)
vol=ret.rolling(63).std().shift(1)              # vol p/ risk-scaling (sem look-ahead)
pos=(S*(0.01/vol)).clip(-0.5,0.5)               # inverse-vol, cap
stratA=(pos*ret)                                # retorno por ativo
port=stratA.mean(axis=1).dropna()               # combina (equal risk)
# escala pra ~15% vol (defasado)
pv=port.rolling(63).std().shift(1)*np.sqrt(252)
trend=(port*(0.15/pv).clip(0,3)).dropna()

spy=ret["SPY"].reindex(trend.index)
def stat(r,n):
    r=r.dropna();a=r.mean()*252*100;sh=r.mean()/r.std()*np.sqrt(252) if r.std()>0 else 0
    eq=(1+r).cumprod();mdd=(eq/eq.cummax()-1).min()*100
    print(f"  {n:20} ret {a:+6.1f}%/y  Sharpe {sh:+.2f}  vol {r.std()*np.sqrt(252)*100:4.0f}%  pior_dia {r.min()*100:+6.1f}%  MDD {mdd:+6.1f}%")
    return r
print("\n=== TREND vs SPY ===")
T=stat(trend,"TREND (cross-asset)"); Sp=stat(spy,"SPY buy&hold")
cor=trend.corr(spy)
print(f"\n  *** correlacao TREND vs SPY: {cor:+.2f}  (o que decide se e diversificador) ***")

# crisis-alpha: como o trend foi quando o SPY caiu?
print("\n=== CRISIS-ALPHA (retorno nos piores meses do SPY) ===")
m=pd.DataFrame({"trend":trend,"spy":spy}).resample("ME").apply(lambda x:(1+x).prod()-1)
worst=m.nsmallest(6,"spy")
for dt,row in worst.iterrows():
    tag="  <- trend GANHOU" if row["trend"]>0 else ""
    print(f"  {dt.date()}  SPY {row['spy']*100:+6.1f}%   TREND {row['trend']*100:+6.1f}%{tag}")

# combo: adicionar trend ao SPY MELHORA ou dilui?
print("\n=== SPY + TREND melhora o portfolio? ===")
stat(spy,"  SPY sozinho"); stat(trend,"  TREND sozinho")
stat((0.5*spy.fillna(0)+0.5*trend.reindex(spy.index).fillna(0)),"  50/50 SPY+TREND")

# sobrevivencia
rng=np.random.default_rng(7);CAP,TT,F,MAXD,N,B=10000.,11500.,9000.,150,40000,10
def sim(R):
    R=R.dropna().values;nb=int(np.ceil(MAXD/B));st=rng.integers(0,len(R)-B,(N,nb))
    P=np.concatenate([R[st[:,k][:,None]+np.arange(B)] for k in range(nb)],axis=1)[:,:MAXD]
    eq=np.full(N,CAP);res=np.zeros(N,bool);o=np.zeros(N,np.int8)
    for j in range(P.shape[1]):
        r=P[:,j];a=~res;eq=np.where(a,eq*(1+r),eq);p=a&(eq>=TT);l=a&(eq<=F);o[p]=1;res[p]=True;o[l]=2;res[l]=True
    o[~res]=3;return o
o=sim(trend);print(f"\n=== SOBREVIVENCIA barreira $11.5k/$9k (trend @15% vol) ===\n  PASSA {np.mean(o==1):.1%}  LIQUIDA {np.mean(o==2):.1%}  timeout {np.mean(o==3):.1%}")
