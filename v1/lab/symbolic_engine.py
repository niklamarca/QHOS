#!/usr/bin/env python3
"""MOTOR SIMBOLICO v4 — fila PERSISTENTE (sem re-litigar geracoes passadas).

Mudanca v3->v4: a fila de estruturas irresolvidas ("piso de resolucao" da triagem)
agora vive no estado e e escalada a 20k na geracao SEGUINTE. Nao existe mais fila
hardcoded: cada pulso julga material NOVO + o que a geracao anterior deixou pendente.
"""
import json, os, glob, math
import numpy as np, pandas as pd

RNG=np.random.default_rng()
HERE=os.path.dirname(os.path.abspath(__file__)); STATE_F=os.path.join(HERE,"symbolic_state.json")
ASSETS=["SPY","QQQ","IWM","GLD","TLT"]
EMBARGO,COST,GEN_SIZE=0.20,0.0002,100
N_SCREEN,N_FINAL,ALPHA_FAM,MAX_DEPTH=400,20000,0.05,3

def rmean(x,w):
    cs=np.cumsum(x,axis=0); o=np.full_like(x,np.nan); o[w:]=(cs[w:]-cs[:-w])/w; return o
def rstd(x,w):
    cs=np.cumsum(x,axis=0); c2=np.cumsum(x*x,axis=0); o=np.full_like(x,np.nan)
    m=(cs[w:]-cs[:-w])/w; m2=(c2[w:]-c2[:-w])/w; o[w:]=np.sqrt(np.clip(m2-m*m,0,None)); return o
def rsum(x,w):
    cs=np.cumsum(x,axis=0); o=np.full_like(x,np.nan); o[w:]=cs[w:]-cs[:-w]; return o
LEAF=["r","mu5","mu21","mu63","sig5","sig21","sig63","dlv","vz21","rlag1","absr","sgn21"]
def leaves(R,LV):
    L={"r":R,"mu5":rmean(R,5),"mu21":rmean(R,21),"mu63":rmean(R,63),
       "sig5":rstd(R,5),"sig21":rstd(R,21),"sig63":rstd(R,63)}
    d=np.full_like(LV,np.nan); d[1:]=LV[1:]-LV[:-1]; L["dlv"]=d
    mv,sv=rmean(LV,21),rstd(LV,21); L["vz21"]=(LV-mv)/(sv+1e-9)
    rl=np.full_like(R,np.nan); rl[1:]=R[:-1]; L["rlag1"]=rl
    L["absr"]=np.abs(R); L["sgn21"]=np.sign(rsum(R,21)); return L
OPS=["+","-","*","/"]
def rnd(d=0):
    if d>=MAX_DEPTH or (d>0 and RNG.random()<0.35): return LEAF[RNG.integers(len(LEAF))]
    return [OPS[RNG.integers(4)],rnd(d+1),rnd(d+1)]
def ev(t,L):
    if isinstance(t,str): return L[t]
    o,a,b=t; x,y=ev(a,L),ev(b,L)
    if o=="+": z=x+y
    elif o=="-": z=x-y
    elif o=="*": z=x*y
    else: z=x/np.where(np.abs(y)<1e-9,np.nan,y)
    return np.clip(z,-1e6,1e6)
def ts(t): return t if isinstance(t,str) else f"({ts(t[1])} {t[0]} {ts(t[2])})"
def nn(t): return 1 if isinstance(t,str) else 1+nn(t[1])+nn(t[2])
def mut(t):
    t=json.loads(json.dumps(t))
    def w(n,d=0):
        if RNG.random()<0.25: return rnd(d)
        if isinstance(n,str): return n
        return [n[0] if RNG.random()>0.2 else OPS[RNG.integers(4)],w(n[1],d+1),w(n[2],d+1)]
    return w(t)
def cx(a,b):
    a=json.loads(json.dumps(a)); sub=b if isinstance(b,str) else (b[1] if RNG.random()<0.5 else b[2])
    if isinstance(a,str): return [OPS[RNG.integers(4)],a,sub]
    if RNG.random()<0.5: a[1]=sub
    else: a[2]=sub
    return a
def strat(t,L,R,sgn,cost=COST):
    """v3+: sinal aplicado na POSICAO, custo cobrado DEPOIS — como a realidade."""
    pos=sgn*np.sign(np.nan_to_num(ev(t,L)))
    pos=np.vstack([np.zeros((1,R.shape[1])),pos[:-1]])
    turn=np.abs(np.diff(pos,axis=0,prepend=np.zeros((1,R.shape[1]))))
    return np.nan_to_num(np.nanmean(pos*R,axis=1)-cost*turn.mean(axis=1))
def sh(x):
    s=np.std(x); return float(np.mean(x)/s*math.sqrt(252)) if s>1e-12 else 0.0

def posseries(t,L,sgn):
    pos=sgn*np.sign(np.nan_to_num(ev(t,L)))
    return np.vstack([np.zeros((1,pos.shape[1])),pos[:-1]])

def piso_selecao(n_testadas,T):
    """Vantagem de Sharpe que o MELHOR de n estruturas exibiria por puro acaso.

    Bailey & Lopez de Prado: E[max de n normais] ~ sqrt(2 ln n) desvios; o erro
    padrao do Sharpe anualizado em T dias e ~sqrt(252/T). Qualquer margem abaixo
    disto e indistinguivel do proprio ato de ter procurado.
    """
    return math.sqrt(2*math.log(max(n_testadas,2)))*math.sqrt(252.0/T)

def margem_benchmark(t,L,R,sgn,n_testadas,B=1500,bl=21,seed=3):
    """A estrutura acrescenta algo a SIMPLESMENTE SEGURAR a cesta?

    O nulo 'retornos embaralhados' tem drift ZERO — qualquer coisa majoritariamente
    comprada o bate por construcao. Foi o erro que inflou o SYMB-1 (p=5e-5 contra
    o nulo errado; margem real sobre buy&hold t=+0.55). Aqui o nulo e o benchmark.
    """
    rng=np.random.default_rng(seed)
    rs=strat(t,L,R,sgn); rb=np.nanmean(R,axis=1); T=len(rs)
    d=sh(rs)-sh(rb)
    nb=T//bl; ds=np.empty(B)
    for i in range(B):
        idx=np.concatenate([np.arange(x,x+bl) for x in rng.integers(0,T-bl,nb)])
        ds[i]=sh(rs[idx])-sh(rb[idx])
    p=float(np.mean(ds<=0)); piso=piso_selecao(n_testadas,T)
    return d,p,piso

def degenerado(t,L,R,sgn):
    """A estrutura e uma DESCOBERTA ou o benchmark fantasiado?

    Uma expressao cujo sinal quase nunca muda nao esta decidindo nada: ela e
    buy-and-hold (ou short-and-hold) com uma formula por cima. Na ger 3 quatro
    finalistas distintos deram Sharpe +0.89 identico — todos eram pos=+1 sempre.
    """
    pos=posseries(t,L,sgn)
    frac_long=float(np.mean(pos>0)); frac_flat=float(np.mean(pos==0))
    turn=float(np.mean(np.abs(np.diff(pos,axis=0))>0))
    if frac_long>0.95: return f"constante comprada ({frac_long:.0%} do tempo)"
    if frac_long<0.05 and frac_flat<0.05: return f"constante vendida ({1-frac_long:.0%})"
    if turn<0.005: return f"quase sem decisao (troca em {turn:.2%} dos dias)"
    # correlacao com o nulo 'sempre comprado' na MESMA cesta
    bh=np.nanmean(R,axis=1)
    r=strat(t,L,R,sgn)
    c=float(np.corrcoef(r,bh)[0,1]) if np.std(r)>1e-12 else 1.0
    if c>0.95: return f"indistinguivel de comprar e segurar (corr {c:+.2f})"
    return ""

LEGACY_SEEDS=[["*",["-","r","sig63"],["+",["-","sig5","r"],["-","sig5","sig21"]]],
              ["-","r",["+","sig21","mu63"]],
              ["/","r",["+",["-","sig21","rlag1"],["*","sig21","r"]]],
              ["-","r",["+","mu63","mu63"]]]

def main():
    st=json.load(open(STATE_F))
    st.setdefault("queue",[])
    px,vo={},{}
    for s_ in ASSETS:
        df=pd.read_parquet(glob.glob(f"data_lake/raw/{s_}_1d.parquet")[0]); df.index=pd.to_datetime(df.index)
        px[s_]=df["close"].astype(float); vo[s_]=df["volume"].astype(float)
    P=pd.DataFrame(px).sort_index().ffill().dropna(); V=pd.DataFrame(vo).reindex(P.index).ffill().fillna(0.0)
    cut=int(len(P)*(1-EMBARGO)); P,V=P.iloc[:cut],V.iloc[:cut]
    R=P.pct_change().values[1:]; LV=np.log(V.values+1.0)[1:]; T=R.shape[0]; half=T//2
    L=leaves(R,LV); gen=st["gen"]
    print("="*88)
    print(f"  MOTOR v4 — GERACAO {gen} | fila persistente | acumulado pre-geracao: {st['tested']}")
    print(f"  pesquisa: {T} dias ({P.index[0].date()} -> {P.index[-1].date()}) | embargo {int(EMBARGO*100)}%")
    print("="*88)

    st["tested"]+=GEN_SIZE
    thr=ALPHA_FAM/(2*st["tested"])

    # ---- FASE A: escalacao 20k da fila HERDADA (irresolvidas da geracao anterior) ----
    surv=[]
    esc=[]
    for q in st["queue"]:
        t=json.loads(q["tree"]); sg=int(q["sgn"]); r=strat(t,L,R,sg); esc.append((t,sg,r,sh(r)))
    if esc:
        allowed=max(int(thr*(N_FINAL+1))-1,0)
        print(f"\n  [ESCALACAO 20k] herdadas da ger {gen-1}: {len(esc)} | limiar familia {thr:.2e} | derrotas permitidas: {allowed}")
        reals=[float(np.sum(r)) for _,_,r,_ in esc]
        beats=[0]*len(esc); alive=[True]*len(esc); done=0
        for k in range(N_FINAL):
            if not any(alive): break
            idx=RNG.permutation(T); Lp=leaves(R[idx],LV[idx]); done=k+1
            for j,(t,sg,_,_) in enumerate(esc):
                if not alive[j]: continue
                if float(np.sum(strat(t,Lp,R[idx],sg)))>=reals[j]:
                    beats[j]+=1
                    if beats[j]>allowed: alive[j]=False
        for j,(t,sg,r,s) in enumerate(esc):
            p=(beats[j]+1)/(done+1); name=("-" if sg<0 else "+")+ts(t)
            if not alive[j]:
                print(f"    {name}\n      MORTE: {beats[j]} derrota(s) em {done} -> p>={p:.2e} >= {thr:.2e}")
                continue
            d=degenerado(t,L,R,sg)
            if d:
                print(f"    {name}\n      MORTE: {d} — nao e descoberta")
                continue
            s1,s2=sh(r[:half]),sh(r[half:]); s3=sh(strat(t,L,R,sg,cost=COST*3))
            print(f"    {name}\n      venceu {done-beats[j]}/{done} -> p={p:.2e} < {thr:.2e} [PASSOU familia]")
            print(f"      metades {s1:+.2f}/{s2:+.2f} | custo3x {s3:+.2f}")
            if s1>0 and s2>0 and s3>=0.35:
                print(f"      SOBREVIVEU TUDO -> CANDIDATO (pendente holdout unico + forward)")
                surv.append({"tree":json.dumps(t),"sgn":sg,"sharpe":s,"p":p,"gen":gen,"expr":ts(t),
                             "rets":[round(float(x),8) for x in r]})
            else:
                print(f"      MORTE pos-familia: {'instavel' if (s1<=0 or s2<=0) else 'custo 3x'}")
    else:
        print(f"\n  [ESCALACAO 20k] fila herdada vazia — nada a re-julgar (correto: nao se re-litiga o passado)")

    # ---- FASE B: geracao nova (semeada por sobreviventes + fila + legado) ----
    seeds=[json.loads(s_["tree"]) for s_ in st["survivors"] if "tree" in s_]
    seeds+=[json.loads(q["tree"]) for q in st["queue"]]
    if not seeds: seeds=list(LEGACY_SEEDS)
    pop=[]
    while len(pop)<GEN_SIZE:
        r_=RNG.random()
        if r_<0.45: pop.append(mut(seeds[RNG.integers(len(seeds))]))
        elif r_<0.60 and len(seeds)>1: pop.append(cx(seeds[RNG.integers(len(seeds))],seeds[RNG.integers(len(seeds))]))
        else: pop.append(rnd())
    scored=[]
    for t in pop:
        s_pos=sh(strat(t,L,R,+1)); s_neg=sh(strat(t,L,R,-1))
        sg=+1 if s_pos>=s_neg else -1; s=max(s_pos,s_neg)
        scored.append((t,sg,s))
    vivos=[(t,sg,s) for t,sg,s in scored if s>=0.35]
    n_sharpe=len(pop)-len(vivos)
    cands=[]; n_deg=0
    for t,sg,s in vivos:
        d=degenerado(t,L,R,sg)
        if d: n_deg+=1
        else: cands.append((t,sg,s))
    cands.sort(key=lambda x:-x[2]); cands=cands[:8]
    print(f"\n  [GERACAO {gen}] nasceram {len(pop)} | Sharpe<0.35 matou {n_sharpe} | "
          f"DEGENERADAS (benchmark fantasiado) matou {n_deg} | finalistas reais: {len(cands)}")
    queue_next=[]
    prev=[np.array(s_["rets"]) for s_ in st["survivors"]+surv if "rets" in s_]
    for t,sg,s in cands:
        r=strat(t,L,R,sg); real=float(np.sum(r)); beats=0
        for k in range(N_SCREEN):
            idx=RNG.permutation(T)
            if float(np.sum(strat(t,leaves(R[idx],LV[idx]),R[idx],sg)))>=real: beats+=1
        p=(beats+1)/(N_SCREEN+1); floor=p<=3/(N_SCREEN+1)
        s1,s2=sh(r[:half]),sh(r[half:]); s3=sh(strat(t,L,R,sg,cost=COST*3))
        ortho=all(abs(np.corrcoef(r,pr)[0,1])<0.5 for pr in prev) if prev else True
        name=("-" if sg<0 else "+")+ts(t)
        print(f"\n  NASCEU: {name}  [{nn(t)} nos]")
        print(f"    Sharpe(v4) {s:+.2f} | metades {s1:+.2f}/{s2:+.2f} | p {p:.4f}{' (PISO)' if floor else ''} | custo3x {s3:+.2f}")
        why=[]
        if not floor and p>=thr: why.append(f"p >= {thr:.1e}")
        if s1<=0 or s2<=0: why.append("instavel")
        if s3<0.35: why.append("morre a custo 3x")
        if not ortho: why.append("redundante (corr>0.5 com candidato vivo)")
        if not why:
            dmb,pmb,piso=margem_benchmark(t,L,R,sg,st["tested"])
            print(f"    vs SEGURAR A CESTA: margem {dmb:+.3f} | P(margem<=0) {pmb:.3f} | piso de selecao {piso:.3f}")
            if pmb>=0.05: why.append(f"margem sobre buy&hold indistinguivel de zero (P={pmb:.2f})")
            if dmb<piso: why.append(f"margem {dmb:+.3f} abaixo do piso de selecao {piso:.3f}")
        if why:
            print("    MORTE: "+"; ".join(why))
        elif floor:
            print(f"    IRRESOLVIDA no piso ({N_SCREEN} perms) -> FILA da ger {gen+1} para escalacao 20k")
            queue_next.append({"tree":json.dumps(t),"sgn":sg,"sharpe":s,"expr":name,"born_gen":gen})
        else:
            print("    (sem lamina — aguarda escalacao)")
            queue_next.append({"tree":json.dumps(t),"sgn":sg,"sharpe":s,"expr":name,"born_gen":gen})

    st["survivors"]=(st["survivors"]+surv)[:10]; st["queue"]=queue_next; st["gen"]=gen+1
    json.dump(st,open(STATE_F,"w"))
    print("\n"+"="*88)
    print(f"  RELATORIO GER {gen}: candidatos vivos: {len(st['survivors'])} | fila p/ ger {gen+1}: {len(queue_next)} | acumulado: {st['tested']}")
    for s_ in st["survivors"]:
        print(f"    CANDIDATO: {'-' if s_['sgn']<0 else '+'}{s_['expr']} (Sharpe {s_['sharpe']:+.2f}, p {s_['p']:.2e})")
    for q in queue_next:
        print(f"    FILA: {q['expr']} (Sharpe {q['sharpe']:+.2f})")
    print("="*88)

if __name__=="__main__":
    main()
