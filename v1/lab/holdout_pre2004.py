#!/usr/bin/env python3
"""
SEGUNDO HOLDOUT — 2000-2004 (virgem: a busca NUNCA viu este periodo).
Contem o estouro da bolha .com e o bear de 2002. Testa as duas hipoteses vivas
(SYMB-1 e TREND) num regime de mercado radicalmente diferente do da pesquisa.
Criterio PRE-REGISTRADO (mesmo padrao 0.4x da degradacao):
  SYMB-1 passa se Sharpe >= 0.42 e retorno > 0
  TREND  passa se corr(SPY) < 0.3 E for defensivo no bear (>= SPY no periodo)
"""
import glob, math
import numpy as np, pandas as pd
U=["SPY","QQQ","IWM","TLT"]; COST=0.0002
def L(s):
    d=pd.read_parquet(glob.glob(f"data_lake/raw/{s}_1d.parquet")[0])["close"].astype(float)
    d.index=pd.to_datetime(d.index); return d
px=pd.DataFrame({s:L(s) for s in U}).sort_index().ffill().dropna()
print("universo comum:",px.index.min().date(),"->",px.index.max().date(),f"({len(px)} barras)")
hold=px[px.index<"2004-11-18"]           # antes do inicio da janela de pesquisa
print("HOLDOUT VIRGEM:",hold.index.min().date(),"->",hold.index.max().date(),f"({len(hold)} barras)\n")
def sh(x):
    x=np.asarray(x,float); x=x[~np.isnan(x)]
    return float(x.mean()/x.std()*math.sqrt(252)) if len(x)>50 and x.std()>0 else 0.0
def mdd(x):
    eq=np.cumprod(1+np.nan_to_num(x)); return float((eq/np.maximum.accumulate(eq)-1).min()*100)

# ---------- SYMB-1 ----------
ret=px.pct_change(); s5,s21,s63=[ret.rolling(w).std() for w in (5,21,63)]
f=(ret-s63)*((s5-ret)+(s5-s21))
pos=(-np.sign(f)).shift(1)
turn=pos.diff().abs().sum(axis=1)
sym=((pos*ret).mean(axis=1)-COST*turn/len(U)).dropna()
sym_h=sym[sym.index<"2004-11-18"]
# ---------- TREND ----------
def sig(p): return (np.sign(p.pct_change(63))+np.sign(p.pct_change(126))+np.sign(p.pct_change(252)))/3
S=px.apply(sig).shift(1); vol=ret.rolling(63).std().shift(1)
tpos=(S*(0.01/vol)).clip(-0.5,0.5)
tturn=tpos.diff().abs().sum(axis=1)
pre=(tpos*ret).mean(axis=1)-COST*tturn/len(U)
pv=pre.rolling(63).std().shift(1)*np.sqrt(252)
tr=(pre*(0.15/pv).clip(0,3)).dropna()
tr_h=tr[tr.index<"2004-11-18"]
spy_h=ret["SPY"].reindex(sym_h.index)

print("="*78); print("  RESULTADOS NO HOLDOUT VIRGEM (2000-2004)"); print("="*78)
for nm,x in [("SYMB-1",sym_h),("TREND",tr_h),("SPY buy&hold",spy_h)]:
    x=x.dropna(); print(f"  {nm:14} Sharpe {sh(x):+.2f}  ret {(np.prod(1+x)-1)*100:+7.1f}%  MDD {mdd(x):+6.1f}%  ({len(x)}d)")
c=np.corrcoef(tr_h.reindex(spy_h.index).fillna(0),spy_h.fillna(0))[0,1]
print(f"\n  correlacao TREND vs SPY: {c:+.2f}")
print("\n  por ano:")
Y=pd.DataFrame({"symb":sym_h,"trend":tr_h,"spy":spy_h})
for y in sorted(set(Y.index.year)):
    sub=Y[Y.index.year==y]
    if len(sub)>60:
        print(f"    {y}: SPY {((1+sub['spy'].fillna(0)).prod()-1)*100:+6.1f}%   SYMB-1 {((1+sub['symb'].fillna(0)).prod()-1)*100:+6.1f}%   TREND {((1+sub['trend'].fillna(0)).prod()-1)*100:+6.1f}%")
print("\n"+"-"*78)
s1,r1=sh(sym_h),float(np.prod(1+sym_h.dropna())-1)
ok1 = s1>=0.42 and r1>0
print(f"  SYMB-1: {'PASSOU' if ok1 else 'REPROVOU'} (criterio: Sharpe>=0.42 e ret>0)")
spy_tot=float(np.prod(1+spy_h.dropna())-1); tr_tot=float(np.prod(1+tr_h.dropna())-1)
ok2 = abs(c)<0.3 and tr_tot>=spy_tot
print(f"  TREND : {'PASSOU' if ok2 else 'REPROVOU'} (criterio: |corr|<0.3 e bater SPY no bear)")
print("="*78)
