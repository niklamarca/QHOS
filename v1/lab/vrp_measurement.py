#!/usr/bin/env python3
"""
VRP — Bateria de MEDICAO (antes de qualquer estrategia)
=======================================================
1) Block-bootstrap clusterizado vs iid  -> a cauda HONESTA de sobrevivencia
2) Stress de borrow                      -> o premio sobrevive a custo de short?
3) Split por regime (+ versao GATED)     -> so calmaria? gate melhora a sobrevivencia?
Le so parquets locais. Short VIXY = vender vol.
"""
import numpy as np, pandas as pd, glob

def load(sym):
    df=pd.read_parquet(glob.glob(f"data_lake/raw/{sym}_1d.parquet")[0])
    df.index=pd.to_datetime(df.index); return df["close"].astype(float)

vixy=load("VIXY"); spy=load("SPY")
long=vixy.pct_change(); short=(-long)[long.abs()<=0.60].dropna()   # vender vol, sem artefato split
# regime: vol realizada 20d do SPY (anualizada)
rv=spy.pct_change().rolling(20).std()*np.sqrt(252)
df=pd.DataFrame({"short":short,"rv":rv}).dropna()
thr=df["rv"].quantile(0.70)
calm=df[df["rv"]<=thr]["short"]; stress=df[df["rv"]>thr]["short"]

CAP0,TARGET,FLOOR,MAXD,N,B=10000.,11500.,9000.,150,40000,10
rng=np.random.default_rng(31)

def sim(paths, L):   # paths: (N, MAXD) matriz de retornos ja montada
    eq=np.full(len(paths),CAP0); res=np.zeros(len(paths),bool); out=np.zeros(len(paths),np.int8)
    for j in range(paths.shape[1]):
        r=paths[:,j]; act=~res
        eq=np.where(act, eq*(1+L*r-0.0001*L), eq)
        p=act&(eq>=TARGET); l=act&(eq<=FLOOR); out[p]=1;res[p]=True;out[l]=2;res[l]=True
    out[~res]=3; return out

def iid_paths(R):   return R[rng.integers(0,len(R),(N,MAXD))]
def block_paths(R): # amostra blocos consecutivos de B dias (preserva cluster de crash)
    nb=int(np.ceil(MAXD/B)); starts=rng.integers(0,len(R)-B,(N,nb))
    cols=[R[starts[:,k][:,None]+np.arange(B)] for k in range(nb)]
    return np.concatenate(cols,axis=1)[:,:MAXD]

def stats(R):
    return (R.mean()*252*100, R.std()*np.sqrt(252)*100, (R.mean()/R.std()*np.sqrt(252)) if R.std()>0 else 0,
            R.min()*100, R[R<=np.quantile(R,0.05)].mean()*100)

print("="*82)
print("  VRP — BATERIA DE MEDICAO (short VIXY, 2021-2026)")
print("="*82)
R=short.values
a,v,s,wd,cv=stats(R)
print(f"  base: ret {a:+.0f}%/ano | vol {v:.0f}% | Sharpe {s:+.2f} | pior dia {wd:+.0f}% | CVaR5 {cv:+.0f}%")

print("\n  [1] IID vs BLOCK-BOOTSTRAP (cauda clusterizada) — liquidacao sobe = cauda real pior")
print(f"      {'personalidade':18} {'PASSA iid':>10} {'LIQ iid':>8} | {'PASSA blk':>10} {'LIQ blk':>8}")
for nome,L in [("Conservadora L=1",1),("Equilibrada L=3",3),("Agressiva L=8",8)]:
    oi=sim(iid_paths(R),L); ob=sim(block_paths(R),L)
    print(f"      {nome:18} {np.mean(oi==1):9.1%} {np.mean(oi==2):7.1%} | {np.mean(ob==1):9.1%} {np.mean(ob==2):7.1%}")

print("\n  [2] STRESS DE BORROW (short paga aluguel) — quanto o custo come o premio")
print(f"      {'borrow/ano':12} {'ret liq/ano':>12} {'Sharpe':>8} {'PASSA(blk,L=3)':>16} {'LIQ':>7}")
for bor in [0.0,0.05,0.15,0.30]:
    Rn=R-bor/252
    an=Rn.mean()*252*100; sh=Rn.mean()/Rn.std()*np.sqrt(252) if Rn.std()>0 else 0
    ob=sim(block_paths(Rn),3.0)
    print(f"      {bor*100:9.0f}%   {an:+11.0f}% {sh:+7.2f} {np.mean(ob==1):15.1%} {np.mean(ob==2):6.1%}")

print("\n  [3] SPLIT POR REGIME (vol realizada SPY) — o premio so existe na calmaria?")
for nome,seg in [("CALMARIA (70% dos dias)",calm),("STRESS (30% piores)",stress)]:
    a,v,s,wd,cv=stats(seg.values)
    print(f"      {nome:26} ret {a:+.0f}%/ano  Sharpe {s:+.2f}  pior dia {wd:+.0f}%  CVaR5 {cv:+.0f}%  (n={len(seg)})")

print("\n  [3b] VERSAO GATED: vender vol SO na calmaria (fica de fora no stress)")
Rg=calm.values
a,v,s,wd,cv=stats(Rg)
print(f"      gated: ret {a:+.0f}%/ano | Sharpe {s:+.2f} | pior dia {wd:+.0f}% | CVaR5 {cv:+.0f}%")
print(f"      {'personalidade':18} {'PASSA blk':>10} {'LIQ blk':>8} {'timeout':>8}")
for nome,L in [("Conservadora L=1",1),("Equilibrada L=3",3),("Agressiva L=8",8)]:
    ob=sim(block_paths(Rg),L)
    print(f"      {nome:18} {np.mean(ob==1):9.1%} {np.mean(ob==2):7.1%} {np.mean(ob==3):7.1%}")
print("="*82)
