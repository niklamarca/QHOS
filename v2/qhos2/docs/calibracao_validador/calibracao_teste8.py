"""Calibracao do TESTE 8 do validador do V1 (margem sobre buy&hold), com a funcao REAL de bootstrap.

Uso (na raiz do repositorio, com numpy instalado):
    python qhos2/docs/calibracao_validador/calibracao_teste8.py [raiz_do_repositorio]
So le validation/block_bootstrap.py do V1; nao altera nada. Resultado e analise em CALIBRACAO.md.
"""
import importlib.util, math, sys
from pathlib import Path
import numpy as np
W=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location("bb",str(W/"validation"/"block_bootstrap.py")); bb=importlib.util.module_from_spec(spec); spec.loader.exec_module(bb)
draws=bb.paired_sharpe_draws
def sh(x):
    sd=x.std(); return float(x.mean()/sd*math.sqrt(252)) if sd>1e-12 else 0.0

def regra_validador(n_test, ds):
    """Replica exatamente a decisao do validador: alpha, B e p-valor empirico."""
    alpha=0.05/max(n_test,2)
    B=max(4000,min(100000,int(math.ceil(5.0/alpha))))
    return alpha,B

def serie(T,sigma_b,mu_b,te,alfa_ann,rng):
    b=rng.normal(mu_b/252,sigma_b/math.sqrt(252),T)
    eps=rng.normal(0,te/math.sqrt(252),T)
    s=b+alfa_ann/252+eps
    return s,b

rng=np.random.default_rng(1)
print("== A) LIMITE ESTRUTURAL: o menor p-valor alcancavel e 1/(B+1)")
for n in [1,10,100,1000,4000,4836,5000,5001,6000,10000]:
    alpha,B=regra_validador(n,None); pmin=1/(B+1)
    print(f"n_test={n:6d}  alpha={alpha:.3e}  B={B:6d}  p_min={pmin:.3e}  {'POSSIVEL' if pmin<alpha else 'IMPOSSIVEL'}")

print("\n== B) CONTROLE NEGATIVO (sem vantagem): taxa de falso positivo com alpha=5% (n=1), B=4000")
T=1500; fp=0; N=300
for i in range(N):
    s,b=serie(T,0.16,0.07,0.06,0.0,rng)
    ds=draws(s,b,draws=4000,rng=np.random.default_rng(i),block=21)
    p=(np.count_nonzero(ds<=0)+1)/(4001)
    if (sh(s)-sh(b))>0 and p<0.05: fp+=1
print(f"falsos positivos: {fp}/{N} = {fp/N:.1%} (esperado ~2.5% a 5%)")

print("\n== C) MENOR VANTAGEM DETECTAVEL (diferenca de Sharpe contra o buy&hold) por n_test")
from math import erf
def z_de_p(p):  # inversa aproximada da normal (bissecao)
    lo,hi=0.0,12.0
    for _ in range(80):
        mid=(lo+hi)/2
        tail=0.5*(1-erf(mid/math.sqrt(2)))
        if tail>p: lo=mid
        else: hi=mid
    return (lo+hi)/2
for anos in [4,6,10]:
    T=int(anos*252)
    for te in [0.03,0.06,0.10]:
        ses=[]
        for k in range(6):
            s,b=serie(T,0.16,0.07,te,0.0,rng)
            ds=draws(s,b,draws=3000,rng=np.random.default_rng(100+k),block=21); ses.append(ds.std())
        se=float(np.mean(ses))
        linha=f"{anos:2d} anos, tracking error {te:.0%}: SE(dif. Sharpe)={se:.2f} | "
        for n in [1,100,1000,4836]:
            alpha=0.05/n; z=z_de_p(alpha); linha+=f"n={n}: precisa d>={z*se:.2f}  "
        print(linha)
