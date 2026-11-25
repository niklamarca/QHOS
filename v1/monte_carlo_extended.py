"""
╔══════════════════════════════════════════════════════════════════════╗
║  MONTE CARLO EXTENDED — 3 Módulos                                   ║
║                                                                      ║
║  Módulo 0: Kyle (1985) estático           — baseline                ║
║  Módulo 1: Back (1992) V dinâmico         — momentum real           ║
║  Módulo 2: Kalman λ variante              — tracking de liquidez    ║
║  Módulo 3: Regime-switching Markov        — crises de liquidez      ║
╚══════════════════════════════════════════════════════════════════════╝

Lei central: ΔP_t = λ_t · Q_t + ε_t   onde   λ = σ_v / (2σ_u)
"""

import numpy as np
import pandas as pd
from multiprocessing import Pool
from itertools import product
import time
import os

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ══════════════════════════════════════════════════════════════════════
# GRIDS
# ══════════════════════════════════════════════════════════════════════

SV   = [0.5, 1.0, 2.0, 5.0]     # volatilidade do valor fundamental
SU   = [1.0, 2.0]               # volatilidade uninformed
NF   = [0.0, 0.2, 0.5, 1.0]    # noise_frac = σ_ε / σ_v
TS   = [100, 500, 1000, 5000]   # passos por simulação
N    = 500                       # replicações Monte Carlo

# Regime-switching
K_RANGE  = [2.0, 3.0, 5.0]     # λ₂ = k · λ₁ em crise
P12_RANGE = [0.01, 0.05]       # prob de entrar em crise
P21_RANGE = [0.05, 0.20]       # prob de sair da crise


# ══════════════════════════════════════════════════════════════════════
# MÓDULO 0 — KYLE (1985) ESTÁTICO  [baseline]
# ══════════════════════════════════════════════════════════════════════

def _kyle_static(sv, su, nf, T, seed):
    """
    V fixo. ΔP_t = λ·Q_t + ε_t.
    Retorna (dP, Q) arrays.
    """
    rng  = np.random.default_rng(seed)
    lam  = sv / (2 * su)
    beta = 1.0 / (2.0 * lam)
    se   = nf * sv
    V    = 100.0 + rng.normal(0, sv)
    P    = 100.0
    Q    = np.empty(T);  dP = np.empty(T)
    for t in range(T):
        Q[t]  = beta * (V - P) + rng.normal(0, su)
        dP[t] = lam * Q[t] + (rng.normal(0, se) if se > 0 else 0.0)
        P    += dP[t]
    return dP, Q


# ══════════════════════════════════════════════════════════════════════
# MÓDULO 1 — BACK (1992): V DINÂMICO  [gera momentum real]
# ══════════════════════════════════════════════════════════════════════
#
# Diferença central vs Kyle estático:
#   V_{t+1} = V_t + σ_v · ε^V_t   (novo fluxo de informação a cada passo)
#
# Consequência: o trader informado SEMPRE tem alpha fresco para negociar.
# OFI cumulativo prediz direção futura → momentum é positivo.
#
# λ de equilíbrio por-período: σ_v / (2σ_u) — igual ao Kyle, mas agora
# σ_v representa a inovação de V_t, não o desvio total de V₀.

def _back_dynamic(sv, su, nf, T, seed):
    """
    Back (1992) discreto: V random walk.
    """
    rng  = np.random.default_rng(seed)
    lam  = sv / (2 * su)
    beta = 1.0 / (2.0 * lam)
    se   = nf * sv
    P    = 100.0
    V    = 100.0
    Q    = np.empty(T);  dP = np.empty(T)
    for t in range(T):
        V    += rng.normal(0, sv)              # V evolui (inovação fundamental)
        Q[t]  = beta * (V - P) + rng.normal(0, su)
        dP[t] = lam * Q[t] + (rng.normal(0, se) if se > 0 else 0.0)
        P    += dP[t]
    return dP, Q


def _sharpe(Q, dP, contrarian=False):
    """Sharpe da estratégia sign(±ΣQ_t) × ΔP_{t+1}."""
    cum = np.cumsum(Q[:-1])
    pos = np.sign(cum) * (-1 if contrarian else 1)
    ret = pos * dP[1:]
    sd  = np.std(ret)
    return float(np.mean(ret) / sd * 252**0.5) if sd > 1e-10 else np.nan


def _ols(dP, Q):
    cov = np.cov(dP, Q)
    return cov[0, 1] / cov[1, 1] if cov[1, 1] > 1e-10 else np.nan


def run_mod0(args):
    sv, su, nf, T, n = args
    lt = sv / (2 * su)
    ols_h = np.empty(n); sh_mom = np.empty(n); sh_ctr = np.empty(n)
    for r in range(n):
        seed = r * 9973 + int(sv*100)*97 + int(su*100)*13 + int(nf*100)*7 + T
        dP, Q = _kyle_static(sv, su, nf, T, seed)
        lh = _ols(dP, Q)
        ols_h[r]  = lh
        sh_mom[r] = _sharpe(Q, dP, contrarian=False)
        sh_ctr[r] = _sharpe(Q, dP, contrarian=True)
    vo = ols_h[~np.isnan(ols_h)]
    sm = sh_mom[~np.isnan(sh_mom)]; sc = sh_ctr[~np.isnan(sh_ctr)]
    if len(vo) == 0: return None
    rmse = np.sqrt(np.mean((vo - lt)**2))
    return dict(mod=0, sv=sv, su=su, nf=nf, T=T,
                lt=round(lt, 3), lam_hat=round(float(np.mean(vo)), 4),
                rmse_pct=round(float(100*rmse/lt), 2),
                atten=round(float(np.mean(vo)/lt), 3),
                sharpe_mom=round(float(np.mean(sm)), 2) if len(sm) else np.nan,
                sharpe_ctr=round(float(np.mean(sc)), 2) if len(sc) else np.nan)


def run_mod1(args):
    sv, su, nf, T, n = args
    lt = sv / (2 * su)
    ols_h = np.empty(n); sh_mom = np.empty(n); sh_ctr = np.empty(n)
    for r in range(n):
        seed = r * 7919 + int(sv*100)*83 + int(su*100)*11 + int(nf*100)*5 + T
        dP, Q = _back_dynamic(sv, su, nf, T, seed)
        lh = _ols(dP, Q)
        ols_h[r]  = lh
        sh_mom[r] = _sharpe(Q, dP, contrarian=False)
        sh_ctr[r] = _sharpe(Q, dP, contrarian=True)
    vo = ols_h[~np.isnan(ols_h)]
    sm = sh_mom[~np.isnan(sh_mom)]; sc = sh_ctr[~np.isnan(sh_ctr)]
    if len(vo) == 0: return None
    rmse = np.sqrt(np.mean((vo - lt)**2))
    return dict(mod=1, sv=sv, su=su, nf=nf, T=T,
                lt=round(lt, 3), lam_hat=round(float(np.mean(vo)), 4),
                rmse_pct=round(float(100*rmse/lt), 2),
                atten=round(float(np.mean(vo)/lt), 3),
                sharpe_mom=round(float(np.mean(sm)), 2) if len(sm) else np.nan,
                sharpe_ctr=round(float(np.mean(sc)), 2) if len(sc) else np.nan)


# ══════════════════════════════════════════════════════════════════════
# MÓDULO 2 — KALMAN FILTER: λ VARIANTE NO TEMPO
# ══════════════════════════════════════════════════════════════════════
#
# Modelo de estado:
#   Estado  : λ_t  (escalar)
#   Transição: λ_{t+1} = λ_t + ν_t,  ν_t ~ N(0, σ_λ²)
#   Observação: ΔP_t = λ_t · Q_t + ε_t,  ε_t ~ N(0, σ_ε²)
#
# Kalman recursivo:
#   Predict : λ̂_{t|t-1} = λ̂_{t-1},  P_{t|t-1} = P_{t-1} + σ_λ²
#   Update  : K_t = P_{t|t-1}·Q_t / (Q_t²·P_{t|t-1} + σ_ε²)
#             λ̂_t = λ̂_{t|t-1} + K_t·(ΔP_t - λ̂_{t|t-1}·Q_t)
#             P_t = (1 - K_t·Q_t)·P_{t|t-1}
#
# Comparação: em λ estático OLS ≈ Kalman (ambos consistentes).
# Kalman domina quando λ muda — rastreia as mudanças com lag menor.

def _kalman_lambda(dP, Q, sigma_proc=0.01, sigma_obs_frac=0.3):
    """
    Kalman filter para λ_t variante.
    sigma_proc : desvio padrão da inovação do processo (mobilidade de λ)
    sigma_obs_frac : σ_ε como fração de std(dP)
    """
    T     = len(dP)
    lam   = np.median(np.abs(dP)) / (np.std(Q) + 1e-10)  # prior
    P_var = 1.0
    Q_p   = sigma_proc**2
    R_obs = (sigma_obs_frac * np.std(dP))**2

    hats = np.empty(T)
    for t in range(T):
        # Predict
        P_var += Q_p
        # Update
        H  = Q[t]
        S  = H**2 * P_var + R_obs
        if abs(S) > 1e-12:
            K   = P_var * H / S
            lam += K * (dP[t] - lam * H)
            P_var = (1 - K * H) * P_var
        hats[t] = lam

    return hats   # λ̂_t para cada t


def run_mod2_static(args):
    """Kalman em Kyle estático: deve convergir para OLS."""
    sv, su, nf, T, n = args
    lt = sv / (2 * su)
    ols_h = np.empty(n); kal_h = np.empty(n)
    for r in range(n):
        seed = r * 6271 + int(sv*100)*71 + int(su*100)*17 + T
        dP, Q = _kyle_static(sv, su, nf, T, seed)
        ols_h[r] = _ols(dP, Q)
        hats = _kalman_lambda(dP, Q)
        kal_h[r] = float(np.mean(hats[T//4:]))   # descarta burn-in
    vo = ols_h[~np.isnan(ols_h)]; vk = kal_h[~np.isnan(kal_h)]
    if len(vo) == 0 or len(vk) == 0: return None
    rmse_ols = np.sqrt(np.mean((vo - lt)**2))
    rmse_kal = np.sqrt(np.mean((vk - lt)**2))
    return dict(sv=sv, su=su, nf=nf, T=T, lt=round(lt, 3),
                ols_rmse_pct=round(float(100*rmse_ols/lt), 2),
                kal_rmse_pct=round(float(100*rmse_kal/lt), 2),
                kalman_gain=round(float(rmse_ols/rmse_kal), 3) if rmse_kal > 1e-10 else np.nan)


# ══════════════════════════════════════════════════════════════════════
# MÓDULO 3 — REGIME-SWITCHING MARKOV
# ══════════════════════════════════════════════════════════════════════
#
# Dois estados latentes:
#   S=0 (normal) : λ₁ = σ_v / (2σ_u)
#   S=1 (crise)  : λ₂ = k · λ₁          (liquidez cai, impact sobe)
#
# Transição Markov:
#   P(S_{t+1}=1 | S_t=0) = p12   (prob de entrar em crise)
#   P(S_{t+1}=0 | S_t=1) = p21   (prob de sair de crise)
#
# Métricas:
#   - RMSE do OLS vs λ médio esperado: E[λ] = π₁λ₁ + π₂λ₂
#     (onde π₂ = p12/(p12+p21) é a distribuição estacionária de crise)
#   - RMSE do Kalman vs λ_t verdadeiro (rastreia o regime atual)
#   - Tempo médio para Kalman detectar mudança de regime (detection lag)
#   - Sharpe com Kalman adaptativo vs OLS fixo

def _simulate_regime(sv, su, nf, T, k, p12, p21, seed):
    """
    Kyle com regime-switching Markov em λ.
    """
    rng  = np.random.default_rng(seed)
    lam1 = sv / (2 * su)
    lam2 = k * lam1
    beta1 = 1.0 / (2.0 * lam1)
    beta2 = 1.0 / (2.0 * lam2)
    se    = nf * sv

    P       = 100.0
    V       = 100.0 + rng.normal(0, sv * 5)  # V mais afastado para ter sinal
    state   = 0   # começa em normal
    lam_true = np.empty(T)
    Q        = np.empty(T);  dP = np.empty(T)

    for t in range(T):
        # Transição de regime
        if state == 0:
            state = 1 if rng.random() < p12 else 0
        else:
            state = 0 if rng.random() < p21 else 1

        lam  = lam1 if state == 0 else lam2
        beta = beta1 if state == 0 else beta2
        lam_true[t] = lam

        Q[t]  = beta * (V - P) + rng.normal(0, su)
        dP[t] = lam * Q[t] + (rng.normal(0, se) if se > 0 else 0.0)
        P    += dP[t]

    return dP, Q, lam_true


def run_mod3(args):
    sv, su, nf, T, k, p12, p21, n = args
    lam1 = sv / (2 * su)
    lam2 = k * lam1
    pi2  = p12 / (p12 + p21)          # prob estacionária de crise
    lam_avg = (1 - pi2) * lam1 + pi2 * lam2  # E[λ]

    ols_h = np.empty(n); kal_h = np.empty(n)
    detection_lags = []

    for r in range(n):
        seed = r * 5003 + int(sv*100)*61 + int(k*10)*43 + T
        dP, Q, lam_true = _simulate_regime(sv, su, nf, T, k, p12, p21, seed)

        ols_h[r] = _ols(dP, Q)

        # Kalman tracking
        hats = _kalman_lambda(dP, Q, sigma_proc=lam1 * 0.1)
        kal_h[r] = float(np.mean(hats[T//4:]))

        # Detection lag: tempo até Kalman detectar transição normal→crise
        transitions = np.where(np.diff(lam_true > lam1 * 1.5))[0]
        lags = []
        for idx in transitions[:5]:   # primeiras 5 transições
            if idx + 50 < T:
                target = lam_true[idx+1]
                window = hats[idx:]
                detected = np.where(np.abs(window - target) / target < 0.3)[0]
                if len(detected) > 0:
                    lags.append(int(detected[0]))
        if lags:
            detection_lags.append(np.mean(lags))

    vo = ols_h[~np.isnan(ols_h)]; vk = kal_h[~np.isnan(kal_h)]
    if len(vo) == 0: return None

    rmse_ols_vs_avg = np.sqrt(np.mean((vo - lam_avg)**2))
    rmse_kal_vs_avg = np.sqrt(np.mean((vk - lam_avg)**2))

    return dict(sv=sv, su=su, nf=nf, T=T, k=k, p12=p12, p21=p21,
                lam1=round(lam1, 3), lam2=round(lam2, 3), lam_avg=round(lam_avg, 3),
                pi_crisis=round(pi2, 3),
                ols_rmse_pct=round(float(100*rmse_ols_vs_avg/lam_avg), 2),
                kal_rmse_pct=round(float(100*rmse_kal_vs_avg/lam_avg), 2),
                kalman_gain=round(float(rmse_ols_vs_avg/rmse_kal_vs_avg), 3) if rmse_kal_vs_avg > 1e-10 else np.nan,
                detect_lag_mean=round(float(np.mean(detection_lags)), 1) if detection_lags else np.nan)


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def print_sep(title='', width=74):
    print('═' * width)
    if title:
        print(f'  {title}')
        print('═' * width)


def main():
    from multiprocessing import cpu_count
    n_cores = cpu_count()
    t_total = time.time()

    print_sep('MONTE CARLO EXTENDED — 3 Módulos')
    print(f'  Cores: {n_cores}  |  Reps/célula: {N}')
    print()

    # ── Módulo 0: Kyle estático ───────────────────────────────────────
    cells0 = list(product(SV, SU, NF, TS, [N]))
    print(f'▶ Módulo 0 — Kyle estático ({len(cells0)} células × {N} = {len(cells0)*N:,} sims)...')
    t0 = time.time()
    with Pool(n_cores) as p: rows0 = [r for r in p.map(run_mod0, cells0) if r]
    print(f'  ✓ {time.time()-t0:.1f}s\n')
    df0 = pd.DataFrame(rows0)
    dg0 = df0.groupby(['lt','nf','T']).mean(numeric_only=True).reset_index()

    # ── Módulo 1: Back dinâmico ───────────────────────────────────────
    cells1 = list(product(SV, SU, NF, TS, [N]))
    print(f'▶ Módulo 1 — Back dinâmico ({len(cells1)} células × {N} = {len(cells1)*N:,} sims)...')
    t0 = time.time()
    with Pool(n_cores) as p: rows1 = [r for r in p.map(run_mod1, cells1) if r]
    print(f'  ✓ {time.time()-t0:.1f}s\n')
    df1 = pd.DataFrame(rows1)
    dg1 = df1.groupby(['lt','nf','T']).mean(numeric_only=True).reset_index()

    # ── Módulo 2: Kalman vs OLS (estático) ───────────────────────────
    cells2 = list(product(SV, SU, NF, TS, [N]))
    print(f'▶ Módulo 2 — Kalman λ variante ({len(cells2)} células × {N} = {len(cells2)*N:,} sims)...')
    t0 = time.time()
    with Pool(n_cores) as p: rows2 = [r for r in p.map(run_mod2_static, cells2) if r]
    print(f'  ✓ {time.time()-t0:.1f}s\n')
    df2 = pd.DataFrame(rows2)
    dg2 = df2.groupby(['lt','nf','T']).mean(numeric_only=True).reset_index()

    # ── Módulo 3: Regime-switching ────────────────────────────────────
    cells3 = [(sv, su, nf, T, k, p12, p21, N)
              for sv, su, nf, T, k, p12, p21
              in product([0.5,1.0,2.0], [1.0,2.0], [0.0,0.5], [500,1000],
                         K_RANGE, P12_RANGE, P21_RANGE)]
    print(f'▶ Módulo 3 — Regime-switching ({len(cells3)} células × {N} = {len(cells3)*N:,} sims)...')
    t0 = time.time()
    with Pool(n_cores) as p: rows3 = [r for r in p.map(run_mod3, cells3) if r]
    print(f'  ✓ {time.time()-t0:.1f}s\n')
    df3 = pd.DataFrame(rows3)
    dg3 = df3.groupby(['k','p12','p21','T']).mean(numeric_only=True).reset_index()

    # ══════════════════════════════════════════════════════════════════
    # OUTPUTS
    # ══════════════════════════════════════════════════════════════════

    lams = sorted(dg0['lt'].unique())
    nfs  = sorted(dg0['nf'].unique())

    # ── Comparação Módulo 0 vs 1: RMSE% ──────────────────────────────
    print_sep('COMPARAÇÃO 0 vs 1 — RMSE% (Kyle estático vs Back dinâmico)')
    print('  [Esperado: similar — λ_true idêntico, só V evolui diferente]\n')
    for lv in lams:
        s0 = dg0[dg0['lt']==lv]; s1 = dg1[dg1['lt']==lv]
        p0 = s0.pivot(index='nf',columns='T',values='rmse_pct')
        p1 = s1.pivot(index='nf',columns='T',values='rmse_pct')
        print(f'  λ={lv:.3f}   Kyle / Back')
        for nf_v in nfs:
            row0 = p0.loc[nf_v] if nf_v in p0.index else [np.nan]*len(TS)
            row1 = p1.loc[nf_v] if nf_v in p1.index else [np.nan]*len(TS)
            vals = '  '.join(f'{a:.2f}/{b:.2f}' for a,b in zip(row0,row1))
            print(f'    nf={nf_v}: {vals}')
        print()

    # ── Módulo 1: Sharpe momentum vs contrarian ───────────────────────
    print_sep('MÓDULO 1 (BACK DINÂMICO) — Sharpe: Momentum vs Contrarian')
    print('  [V evolui → OFI acumulado prediz direção → momentum > 0]\n')
    for lv in lams[:3]:
        s1 = dg1[dg1['lt']==lv]
        print(f'  λ={lv:.3f}')
        print('  T        nf=0.0          nf=0.2          nf=0.5          nf=1.0')
        print('           mom  /  ctr     mom  /  ctr     mom  /  ctr     mom  /  ctr')
        for T_v in TS:
            row = s1[s1['T']==T_v]
            vals = []
            for nf_v in nfs:
                cell = row[row['nf']==nf_v]
                if not cell.empty:
                    m = cell['sharpe_mom'].values[0]
                    c = cell['sharpe_ctr'].values[0]
                    vals.append(f'{m:+.2f} / {c:+.2f}')
                else:
                    vals.append('  --  /  --  ')
            print(f'  T={T_v:<5} ' + '   '.join(vals))
        print()

    # ── Módulo 2: Kalman gain ratio ───────────────────────────────────
    print_sep('MÓDULO 2 — Kalman vs OLS em λ estático  [gain = RMSE_OLS / RMSE_Kalman]')
    print('  [>1 = Kalman melhor | ≈1 = equivalentes | <1 = OLS melhor]\n')
    for lv in lams[:3]:
        s2 = dg2[dg2['lt']==lv]
        print(f'  λ={lv:.3f}')
        piv = s2.pivot(index='nf',columns='T',values='kalman_gain')
        print(piv.round(3).to_string())
        print()
    print('  [Em λ estático: Kalman≈OLS. Kalman domina nos módulos 3 e real.]\n')

    # ── Módulo 3: Regime-switching ────────────────────────────────────
    print_sep('MÓDULO 3 — Regime-Switching: Kalman gain e detection lag')
    print('  [k=λ₂/λ₁: magnitude do choque | p12: freq de crise]\n')

    print('  KALMAN GAIN vs OLS (RMSE_OLS / RMSE_Kalman):')
    piv3_gain = dg3.pivot_table(index=['k','p12'],columns='T',
                                values='kalman_gain',aggfunc='mean')
    print(piv3_gain.round(3).to_string())

    print('\n  DETECTION LAG (passos até Kalman detectar mudança de regime):')
    piv3_lag = dg3.pivot_table(index=['k','p12'],columns='T',
                               values='detect_lag_mean',aggfunc='mean')
    print(piv3_lag.round(1).to_string())

    print('\n  OLS RMSE% vs λ_médio esperado (quanto OLS erra ao ignorar regime):')
    piv3_ols = dg3.pivot_table(index=['k','p12'],columns='T',
                               values='ols_rmse_pct',aggfunc='mean')
    print(piv3_ols.round(2).to_string())

    # ── Comparativo final: sinal operacional ─────────────────────────
    print()
    print_sep('COMPARATIVO FINAL — Sinal operacional recomendado por regime')
    print("""
  ┌─────────────────┬──────────────────┬──────────────────┬──────────────────┐
  │ Regime          │ Modelo correto   │ Estimador λ      │ Sinal            │
  ├─────────────────┼──────────────────┼──────────────────┼──────────────────┤
  │ Kyle estático   │ V fixo           │ OLS (T≥500)      │ Contrarian OFI   │
  │                 │ λ constante      │ Kalman≈OLS       │ sign(-ΣQ_t)      │
  ├─────────────────┼──────────────────┼──────────────────┼──────────────────┤
  │ Back dinâmico   │ V random walk    │ OLS (T≥500)      │ Momentum OFI     │
  │                 │ λ constante      │ Kalman≈OLS       │ sign(+ΣQ_t)      │
  ├─────────────────┼──────────────────┼──────────────────┼──────────────────┤
  │ Regime-switch   │ λ_t ∈ {λ₁,λ₂}  │ Kalman domina    │ Kalman-adaptive  │
  │ (crise)         │ Markov latente   │ (gain>1.5×)      │ scale by 1/λ̂_t  │
  └─────────────────┴──────────────────┴──────────────────┴──────────────────┘

  Protocolo de produção:
    1. Estimar noise_frac empiricamente: Var(ΔP - λ̂·Q) / Var(ΔP)
    2. Se noise_frac < 0.3 → OLS com T≥500 suficiente
    3. Se noise_frac > 0.3 ou λ varia → Kalman com σ_proc calibrado
    4. Detectar regime via test estatístico em λ̂_t (Chow test, CUSUM)
    5. Sinal: sign(ΣQ) se Back, sign(-ΣQ) se Kyle, 1/λ̂_t se regime
    """)

    # ── Salvar ────────────────────────────────────────────────────────
    df0.to_csv(os.path.join(OUT_DIR, 'mc_mod0_kyle_static.csv'),    index=False, float_format='%.4f')
    df1.to_csv(os.path.join(OUT_DIR, 'mc_mod1_back_dynamic.csv'),   index=False, float_format='%.4f')
    df2.to_csv(os.path.join(OUT_DIR, 'mc_mod2_kalman.csv'),         index=False, float_format='%.4f')
    df3.to_csv(os.path.join(OUT_DIR, 'mc_mod3_regime_switch.csv'),  index=False, float_format='%.4f')
    print(f'  ✓ CSVs salvos em {OUT_DIR}')

    elapsed = time.time() - t_total
    total_sims = (len(cells0)+len(cells1)+len(cells2)+len(cells3)) * N
    print(f'  ✓ Total: {total_sims:,} simulações em {elapsed:.1f}s  '
          f'({total_sims/elapsed:,.0f} sims/seg)\n')


if __name__ == '__main__':
    main()
