#!/usr/bin/env python3
"""
MOTOR EPISTEMOLOGICO — Teste de Permutacao (Axioma 3, H0: retorno = acaso)
==========================================================================
Destroi a ordem temporal dos retornos (permutacao sem reposicao), reconstroi
N universos sinteticos e re-executa a estrategia em cada um. O drift e a
distribuicao marginal sao PRESERVADOS; so a ESTRUTURA TEMPORAL morre.
Se a estrategia ganha igual nos universos sem estrutura -> o "edge" e ilusao.

  p-valor = (1/N) * sum I( R_sintetico_k >= R_real )

Testes:
 1) GATE VRP: gate (VIX/VIX3M) fixo no calendario, retornos VIXY permutados.
    H0: o timing do gate nao contem informacao -> p alto = gate sem valor.
 2) TREND: permutacao CONJUNTA dos dias (preserva correlacao cross-asset,
    destroi autocorrelacao/tendencias). Re-computa sinais TSMOM por universo.
    H0: nao ha estrutura temporal que o trend explore.
Vetorizado em numpy (matrizes N_perm x T). Sem look-ahead: sinais shift(1).
"""
import numpy as np, pandas as pd, glob

def L(s):
    df=pd.read_parquet(glob.glob(f"data_lake/raw/{s}_1d.parquet")[0]); df.index=pd.to_datetime(df.index); return df["close"].astype(float)

rng=np.random.default_rng(42)
ALPHA=0.05

# ================= 1) GATE VRP =================
vix,vix3m,vixy=L("VIX"),L("VIX3M"),L("VIXY")
d=pd.DataFrame({"vix":vix,"vix3m":vix3m,"vixy":vixy}).dropna()
short=(-d["vixy"].pct_change()).where(lambda x:x.abs()<=0.60)
gate=((d["vix"]/d["vix3m"]).shift(1)<1.0)
df=pd.DataFrame({"s":short,"g":gate}).dropna()
s=df["s"].values; g=df["g"].values.astype(float); T=len(s)
real_gated=float(np.sum(s*g))
base_total=float(np.sum(s))            # invariante sob permutacao (mesmo multiset)

N=20000
# permutacoes vetorizadas: argsort de ruido = permutacao uniforme por linha
P=np.argsort(rng.random((N,T)),axis=1)
synth=s[P]                              # N x T retornos permutados
synth_gated=synth@g                     # soma dos retornos nos dias em que o gate esta ON
p_vrp=float(np.mean(synth_gated>=real_gated))
print("="*78)
print("  MOTOR EPISTEMOLOGICO — teste de permutacao (H0: timing = acaso)")
print("="*78)
print(f"\n[1] GATE VRP (VIX/VIX3M) — {T} dias, N={N:,} universos")
print(f"    retorno somado REAL (gated): {real_gated*100:+.1f}%   [buy&hold fixo: {base_total*100:+.1f}%]")
print(f"    universos sinteticos (gated): media {synth_gated.mean()*100:+.1f}%  p5 {np.percentile(synth_gated,5)*100:+.1f}%  p95 {np.percentile(synth_gated,95)*100:+.1f}%")
print(f"    p-valor = {p_vrp:.4f}  -> {'REJEITA H0: o gate tem informacao de timing' if p_vrp<ALPHA else 'NAO rejeita H0: timing do gate indistinguivel do acaso'}")

# ================= 2) TREND =================
syms=["SPY","TLT","GLD","IWM","QQQ"]
px=pd.DataFrame({x:L(x) for x in syms}).sort_index().ffill().dropna()
ret=px.pct_change().dropna()
R=ret.values; Tt,A=R.shape
LOOK=[63,126,252]

def trend_ret_matrix(Rm):
    """retorno da estrategia TSMOM p/ CADA universo (vetorizado em N_perm).
    Rm: (N,T,A). Sinal = media dos sinais de momentum em LOOK, shift(1)."""
    N=Rm.shape[0]
    logc=np.cumsum(np.log1p(Rm),axis=1)               # log-precos
    sig=np.zeros_like(Rm)
    for Lk in LOOK:
        m=np.zeros_like(Rm)
        m[:,Lk:,:]=np.sign(logc[:,Lk:,:]-logc[:,:-Lk,:])
        sig+=m
    sig/=len(LOOK)
    pos=np.roll(sig,1,axis=1); pos[:,0,:]=0           # shift(1): sem look-ahead
    # vol-scaling simples por ativo (rolling 63d aproximado via std global do universo p/ velocidade)
    vol=np.std(Rm,axis=1,keepdims=True)+1e-9
    w=np.clip(0.01/vol,0,0.5)
    port=np.mean(pos*w*Rm,axis=2)                     # (N,T)
    return np.sum(port,axis=1)                         # retorno somado por universo

real=trend_ret_matrix(R[None,:,:])[0]
Np=300                                                 # re-computo de sinais e caro; 300 universos
perm=np.argsort(rng.random((Np,Tt)),axis=1)
Rp=R[perm,:]                                           # permutacao CONJUNTA dos dias (N,T,A)
synth_t=trend_ret_matrix(Rp)
p_tr=float(np.mean(synth_t>=real))
print(f"\n[2] TREND TSMOM cross-asset — {Tt} dias x {A} ativos, N={Np} universos (permutacao conjunta)")
print(f"    retorno somado REAL: {real*100:+.1f}%")
print(f"    universos sinteticos: media {synth_t.mean()*100:+.1f}%  p5 {np.percentile(synth_t,5)*100:+.1f}%  p95 {np.percentile(synth_t,95)*100:+.1f}%")
print(f"    p-valor = {p_tr:.4f}  -> {'REJEITA H0: ha estrutura temporal real' if p_tr<ALPHA else 'NAO rejeita H0: trend indistinguivel do acaso NESTA amostra'}")

print("\n  LEMBRETE: p-valor alto nao prova ausencia de edge; prova que ESTA amostra")
print("  nao o distingue do acaso. Amostra curta sem bear market = teste fraco.")
print("  E p<0.05 aqui NAO e certificado: e 1 teste entre os varios que ja rodamos")
print("  (selecao!). So o forward + DSR certificam.")
print("="*78)
