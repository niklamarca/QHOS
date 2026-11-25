"""
╔══════════════════════════════════════════════════════════════════════╗
║  CONSOLIDAÇÃO TEÓRICA FINAL                                          ║
║                                                                      ║
║  Ponto 1: IV Estimator — corrige Q endógeno                         ║
║  Ponto 2: CUSUM Test   — detecta mudança de regime em λ̂_Kalman     ║
║  Ponto 3: Framework unificado — protocolo operacional completo      ║
╚══════════════════════════════════════════════════════════════════════╝

Modelo central: ΔP_t = λ_t · Q_t + ε_t
                λ_t  = σ_v / (2·σ_u)
                Q_t  = β·(V_t - P_t) + u_t
"""

import numpy as np
import pandas as pd
import time
import os

OUT = os.path.dirname(os.path.abspath(__file__))
SEP = '═' * 72

# ══════════════════════════════════════════════════════════════════════
# PONTO 1 — IV ESTIMATOR (Variável Instrumental)
# ══════════════════════════════════════════════════════════════════════
#
# Problema: Q_t é endógeno quando Cov(Q_t, ε_t) ≠ 0.
#
# Como surge endogeneidade em mercados reais:
#   - Noise traders reagem ao spread (bid-ask bounce): u_t correlacionado com ε_t
#   - Market maker ajusta spread baseado em Q_t: feedback simultâneo
#   - Measurement error em Q (aproximado por OFI, não Q exato)
#
# Modelo com endogeneidade (parâmetro ρ):
#   ε_t = ρ·u_t + √(1-ρ²)·σ_ε·η_t    (η_t ⊥ u_t, ⊥ Q_t)
#   → Cov(Q_t, ε_t) = ρ·σ_u²
#   → OLS: λ̂_OLS = λ + ρ·σ_u²/Var(Q)  [viesado para cima se ρ > 0]
#
# Instrumento: Z_t = Q_{t-1}  (fluxo lagged)
#   Relevância : Cov(Q_t, Q_{t-1}) ≠ 0  (autocorr. via preço)
#   Exogeneidade: Cov(Q_{t-1}, ε_t) ≈ 0  (u_{t-1} ⊥ ε_t)
#
# IV estimator:
#   λ̂_IV = Cov(ΔP_t, Z_t) / Cov(Q_t, Z_t)
#         = Cov(ΔP_t, Q_{t-1}) / Cov(Q_t, Q_{t-1})

def simulate_endogenous(sv, su, nf, rho, T, seed):
    """
    Kyle com endogeneidade de ordem ρ em ε_t.
    ρ=0 → modelo puro (sem endogeneidade)
    ρ>0 → noise trader demand correlacionada com price noise
    """
    rng   = np.random.default_rng(seed)
    lam   = sv / (2 * su)
    beta  = 1.0 / (2.0 * lam)
    se    = nf * sv
    V     = 100.0 + rng.normal(0, sv)
    P     = 100.0
    Q     = np.empty(T);  dP = np.empty(T)

    for t in range(T):
        x    = beta * (V - P)
        u_t  = rng.normal(0, su)
        eta  = rng.normal(0, 1.0)
        # Ruído endógeno: correlacionado com u_t via ρ
        eps  = (rho * u_t + np.sqrt(max(1 - rho**2, 0)) * se * eta) if se > 0 else 0.0
        Q[t]  = x + u_t
        dP[t] = lam * Q[t] + eps
        P    += dP[t]

    return dP, Q, lam


def estimators(dP, Q):
    """Retorna (OLS, IV_lag1, IV_lag2)."""
    T = len(dP)

    # OLS
    cov = np.cov(dP, Q)
    ols = cov[0, 1] / cov[1, 1] if cov[1, 1] > 1e-10 else np.nan

    # IV com Z = Q_{t-1}  (lag 1)
    Z1 = Q[:-1];  Y1 = dP[1:];  X1 = Q[1:]
    cov_yz = np.cov(Y1, Z1)[0, 1]
    cov_xz = np.cov(X1, Z1)[0, 1]
    iv1 = cov_yz / cov_xz if abs(cov_xz) > 1e-10 else np.nan

    # IV com Z = dP_{t-1}  (price momentum como instrumento)
    Z2 = dP[:-1]; Y2 = dP[1:];  X2 = Q[1:]
    cov_yz2 = np.cov(Y2, Z2)[0, 1]
    cov_xz2 = np.cov(X2, Z2)[0, 1]
    iv2 = cov_yz2 / cov_xz2 if abs(cov_xz2) > 1e-10 else np.nan

    return ols, iv1, iv2


def run_iv_grid():
    N     = 300
    SV    = [0.5, 1.0, 2.0]
    SU    = [1.0, 2.0]
    NF    = [0.3, 0.5, 1.0]
    RHO   = [0.0, 0.2, 0.5, 0.8]   # grau de endogeneidade
    T     = 1000

    rows = []
    for sv, su, nf, rho in [(sv,su,nf,rho)
                             for sv in SV for su in SU
                             for nf in NF for rho in RHO]:
        lt = sv / (2 * su)
        ols_h, iv1_h, iv2_h = [], [], []
        for r in range(N):
            seed = r * 7523 + int(sv*100)*71 + int(su*10)*53 + int(nf*100)*37 + int(rho*100)*19
            dP, Q, _ = simulate_endogenous(sv, su, nf, rho, T, seed)
            o, i1, i2 = estimators(dP, Q)
            ols_h.append(o); iv1_h.append(i1); iv2_h.append(i2)

        def stats(h):
            v = [x for x in h if not np.isnan(x)]
            if not v: return np.nan, np.nan
            return float(np.mean(v) - lt), float(100 * np.sqrt(np.mean((np.array(v)-lt)**2)) / lt)

        b_ols, r_ols = stats(ols_h)
        b_iv1, r_iv1 = stats(iv1_h)
        b_iv2, r_iv2 = stats(iv2_h)

        rows.append(dict(
            lt=round(lt,3), nf=nf, rho=rho,
            ols_bias=round(b_ols,4),  ols_rmse=round(r_ols,2),
            iv1_bias=round(b_iv1,4),  iv1_rmse=round(r_iv1,2),
            iv2_bias=round(b_iv2,4),  iv2_rmse=round(r_iv2,2),
            iv1_gain=round(r_ols/r_iv1,3) if r_iv1>1e-9 else np.nan,
        ))

    return pd.DataFrame(rows)


# ══════════════════════════════════════════════════════════════════════
# PONTO 2 — GLRT-CUSUM SOBRE INOVAÇÕES DO KALMAN
# ══════════════════════════════════════════════════════════════════════
#
# Objetivo: detectar automaticamente quando λ_t muda de regime.
#
# Design: GLRT-CUSUM sobre inovações padronizadas do filtro de Kalman.
#   Inovação padronizada: z_t = (ΔP_t - λ̂_{t-1}·Q_t) / √S_t
#   Sob H₀ (λ estável):  E[z_t²] = 1  por construção do filtro ótimo
#   Sob H₁ (λ mudou):    E[z_t²] > 1  porque o modelo ficou descalibrado
#
#   CUSUM estatística: C_t = max(0, C_{t-1} + z_t² - 1 - k)
#   Alarme quando C_t > h
#
# Vantagem vs CUSUM em λ̂:
#   - Auto-calibrado: não requer estimação de μ₀/σ₀ separados
#   - Robusto: σ₀ de λ̂ converge para ≈0 → z_t→∞ → alarme constante
#   - Mais sensível: detecta mudança em λ via deterioração do modelo
#
# Parâmetros:
#   k = 0.5   (detecta E[z²] ≥ 1.5, i.e., aumento de 50% na variância)
#   h = 5.0   (threshold — calibrado para ARL₀ alto)
#
# Métricas:
#   ARL₀ : tempo médio até falso alarme (sem mudança — maior é melhor)
#   ARL₁ : tempo médio até detectar mudança real (menor é melhor)
#   Power: P(detecta corretamente | mudança em t*)
#   Lag  : E[detecção - t* | detectou]

def _kalman_lam(dP, Q, s_proc=0.02, lam0=None, Pv0=None, R0=None):
    """
    Kalman para λ_t com suporte a estado inicial (chaining treino→teste).
    Retorna (hats, std_innov, lam_final, Pv_final, R_used).
    std_innov[t] = (ΔP_t - λ̂_{t-1}·Q_t) / sqrt(S_t) — inovação padronizada.
    Sob H₀ com R calibrado: E[std_innov²] ≈ 1 por construção do filtro ótimo.

    R calibrado via resíduos OLS: R = Var(dP - λ̂_OLS * Q).
    Isso evita subestimação de R que ocorre com 0.3*std(dP).
    """
    T    = len(dP)
    lam  = lam0 if lam0 is not None else float(np.median(np.abs(dP)) / (np.std(Q) + 1e-10))
    Pv   = Pv0 if Pv0 is not None else 1.0
    if R0 is not None:
        R = R0
    else:
        # R = variância dos resíduos OLS (estimativa não-viesada de Var(ε_t))
        vQ = float(np.var(Q) + 1e-10)
        lam_ols = float(np.cov(dP, Q)[0, 1] / vQ)
        R = max(float(np.var(dP - lam_ols * Q)), 1e-6)
    Q_p  = s_proc**2
    hats      = np.empty(T)
    std_innov = np.empty(T)
    for t in range(T):
        Pv  += Q_p
        H    = Q[t]
        S    = H**2 * Pv + R
        v    = dP[t] - lam * H          # inovação bruta
        std_innov[t] = v / np.sqrt(abs(S) + 1e-12)
        if abs(S) > 1e-12:
            K    = Pv * H / S
            lam += K * v
            Pv   = (1 - K * H) * Pv
        hats[t] = lam
    return hats, std_innov, lam, Pv, R


def cusum_detect(std_innov, k=0.5, h=5.0):
    """
    GLRT-CUSUM sobre inovações padronizadas do Kalman.

    Estatística: C_t = max(0, C_{t-1} + z_t² - 1 - k)
    Sob H₀:  E[z_t²] = 1  →  drift ≈ -k  →  C_t permanece baixo
    Sob H₁:  E[z_t²] > 1  →  C_t cresce  →  alarme quando C_t > h

    Vantagem: auto-calibrado — não precisa estimar μ₀/σ₀ separadamente.
    k = 0.5 detecta E[z²] ≥ 1.5 (aumento de 50% na variância das inovações).
    Retorna índice do primeiro alarme ou None.
    """
    C = 0.0
    for t, z in enumerate(std_innov):
        C = max(0.0, C + z * z - 1.0 - k)
        if C > h:
            return t
    return None


def run_cusum_grid():
    """
    GLRT-CUSUM avaliado em dois cenarios independentes:

      A) ARL0: Kalman aquece T_WARM passos (lam_base), depois CUSUM roda
         T_POST passos com lambda constante. Mede tempo medio ate falso alarme.

      B) ARL1: mesmo warm-up, depois CUSUM roda T_POST passos com lam_new
         desde t=0 da janela. Mede lag de deteccao direto do changepoint.

    H calibrados via simulacao para chi2(1) com k=0.5:
      h=8  -> ARL0~149, h=12 -> ARL0~535, h=20 -> ARL0~4765
    """
    N       = 200
    T_WARM  = 1000   # Kalman warm-up (sem CUSUM)
    T_POST  = 2000   # janela de teste CUSUM
    K_VALS  = [0.25, 0.5, 1.0]
    H_VALS  = [8.0, 12.0, 20.0]
    K_SHOCK = [1.5, 2.0, 3.0, 5.0]

    sv, su, nf = 1.0, 1.0, 0.3
    lam_base = sv / (2 * su)

    rows_arl0 = []
    rows_arl1 = []

    for k_cusum, h_cusum in [(k,h) for k in K_VALS for h in H_VALS]:

        # -- Cenario A: ARL0 (sem mudanca) --------------------------
        alarms = []
        for r in range(N):
            rng = np.random.default_rng(r * 3571 + int(k_cusum*100) + int(h_cusum*10))
            dP_w, Q_w, _ = _gen_series(sv, su, nf, T_WARM, rng, lam_base)
            _, _, lam_w, Pv_w, R_w = _kalman_lam(dP_w, Q_w)
            dP_p, Q_p2, _ = _gen_series(sv, su, nf, T_POST, rng, lam_base)
            _, innov, _, _, _ = _kalman_lam(dP_p, Q_p2, lam0=lam_w, Pv0=Pv_w, R0=R_w)
            alarm = cusum_detect(innov, k=k_cusum, h=h_cusum)
            alarms.append(alarm if alarm is not None else T_POST)

        arl0 = float(np.mean(alarms))
        fpr  = float(np.mean([a < T_POST for a in alarms]))
        rows_arl0.append(dict(k=k_cusum, h=h_cusum, ARL0=round(arl0,1), FPR=round(fpr,3)))

        # -- Cenario B: ARL1 - changepoint em t=0 da janela CUSUM --
        for k_shock in K_SHOCK:
            lam_new = k_shock * lam_base
            lags = []; detected = []
            for r in range(N):
                rng = np.random.default_rng(r*4177 + int(k_cusum*100) + int(h_cusum*10) + int(k_shock*10))
                dP_w, Q_w, _ = _gen_series(sv, su, nf, T_WARM, rng, lam_base)
                _, _, lam_w, Pv_w, R_w = _kalman_lam(dP_w, Q_w)
                # CUSUM inicia com lam_new imediatamente
                dP_p, Q_p2, _ = _gen_series(sv, su, nf, T_POST, rng, lam_new)
                _, innov, _, _, _ = _kalman_lam(dP_p, Q_p2, lam0=lam_w, Pv0=Pv_w, R0=R_w)
                alarm = cusum_detect(innov, k=k_cusum, h=h_cusum)
                if alarm is not None:
                    lags.append(alarm)
                    detected.append(True)
                else:
                    detected.append(False)

            arl1  = float(np.mean(lags)) if lags else np.nan
            power = float(np.mean(detected))
            rows_arl1.append(dict(k=k_cusum, h=h_cusum, k_shock=k_shock,
                                  ARL1=round(arl1,1) if not np.isnan(arl1) else np.nan,
                                  power=round(power,3)))

    return pd.DataFrame(rows_arl0), pd.DataFrame(rows_arl1)
def _gen_series(sv, su, nf, T, rng, lam_override=None, reset_v=50):
    """
    Gera série Kyle com λ opcionalmente forçado.
    V reseta a cada reset_v passos — simula múltiplas sessões sequenciais,
    garantindo Q_t ≠ 0 ao longo de toda a série (não apenas nos primeiros steps).
    Sem reset: V é revelado em ~2 passos → Q→0 → Kalman sem sinal para rastrear.
    """
    lam  = lam_override if lam_override is not None else sv/(2*su)
    beta = 1.0/(2.0*lam)
    se   = nf*sv
    P    = 100.0
    V    = P + rng.normal(0, sv)
    Q    = np.empty(T);  dP = np.empty(T)
    for t in range(T):
        if t > 0 and t % reset_v == 0:
            V = P + rng.normal(0, sv)   # nova sessão / novo ativo
        Q[t]  = beta*(V-P) + rng.normal(0, su)
        dP[t] = lam*Q[t] + (rng.normal(0, se) if se > 0 else 0.0)
        P    += dP[t]
    return dP, Q, lam


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    t0 = time.time()

    print(SEP)
    print('  CONSOLIDAÇÃO TEÓRICA FINAL — IV + CUSUM + Framework')
    print(SEP + '\n')

    # ── PONTO 1: IV ──────────────────────────────────────────────────
    print('▶ Ponto 1 — IV Estimator (endogeneidade em Q)...')
    t1 = time.time()
    df_iv = run_iv_grid()
    print(f'  ✓ {time.time()-t1:.1f}s\n')

    print(SEP)
    print('  PONTO 1 — IV: Viés do OLS e correção por variável instrumental')
    print('  [ρ = correlação entre u_t e ε_t — grau de endogeneidade]')
    print(SEP)

    # Bias por estimador e ρ (média sobre λ e nf)
    dg_iv = df_iv.groupby('rho')[['ols_bias','iv1_bias','ols_rmse','iv1_rmse','iv1_gain']].mean()
    print('\n  Resumo por ρ (médias sobre λ e noise_frac):\n')
    print(f'  {"ρ":>5}  {"OLS_bias":>10}  {"IV_bias":>10}  {"OLS_RMSE%":>10}  {"IV_RMSE%":>10}  {"IV_gain":>8}')
    print('  ' + '-'*62)
    for rho, row in dg_iv.iterrows():
        print(f'  {rho:>5.2f}  {row.ols_bias:>10.4f}  {row.iv1_bias:>10.4f}  '
              f'{row.ols_rmse:>10.2f}  {row.iv1_rmse:>10.2f}  {row.iv1_gain:>8.3f}')

    # Pivot: bias OLS por (ρ, nf)
    piv_bias = df_iv.groupby(['rho','nf'])['ols_bias'].mean().unstack('nf')
    print(f'\n  OLS bias por (ρ, noise_frac):')
    print(piv_bias.round(4).to_string())

    piv_iv_bias = df_iv.groupby(['rho','nf'])['iv1_bias'].mean().unstack('nf')
    print(f'\n  IV(Q_lag) bias por (ρ, noise_frac):')
    print(piv_iv_bias.round(4).to_string())

    print(f"""
  Análise:
    ρ=0.0 : OLS sem viés — IV e OLS equivalentes
    ρ=0.2 : OLS viés ≈ +{dg_iv.loc[0.2,'ols_bias']:.4f} — IV corrige para ≈ {dg_iv.loc[0.2,'iv1_bias']:.4f}
    ρ=0.5 : OLS viés ≈ +{dg_iv.loc[0.5,'ols_bias']:.4f} — IV corrige para ≈ {dg_iv.loc[0.5,'iv1_bias']:.4f}
    ρ=0.8 : OLS viés ≈ +{dg_iv.loc[0.8,'ols_bias']:.4f} — IV corrige para ≈ {dg_iv.loc[0.8,'iv1_bias']:.4f}

    IV com Z=Q_{{t-1}} é válido desde que:
      (a) Cov(Q_t, Q_{{t-1}}) ≠ 0  [verificar: F-stat da 1ª etapa > 10]
      (b) Cov(Q_{{t-1}}, ε_t) ≈ 0  [assumido: u_{{t-1}} ⊥ ε_t]

    IV perde eficiência vs OLS quando ρ ≈ 0 (gain < 1).
    Protocolo: testar endogeneidade via Hausman test antes de usar IV.
      H₀: OLS consistente → Hausman stat = (λ̂_IV - λ̂_OLS)²/Var(diff) ~ χ²(1)
      Rejeitar H₀ → usar IV; aceitar H₀ → usar OLS (mais eficiente)
    """)

    # ── PONTO 2: CUSUM ───────────────────────────────────────────────
    print('▶ Ponto 2 — CUSUM Test sobre λ̂_Kalman...')
    t2 = time.time()
    df_arl0, df_arl1 = run_cusum_grid()
    print(f'  ✓ {time.time()-t2:.1f}s\n')

    print(SEP)
    print('  PONTO 2 — CUSUM: Detecção de mudança de regime em λ')
    print(SEP)

    # ARL₀ por (k, h)
    piv_arl0 = df_arl0.pivot(index='k', columns='h', values='ARL0')
    piv_fpr  = df_arl0.pivot(index='k', columns='h', values='FPR')
    print('\n  ARL₀ — Tempo médio até FALSO alarme (maior = melhor):')
    print(piv_arl0.round(1).to_string())
    print('\n  Taxa de falso alarme (P(alarme | sem mudança)):')
    print(piv_fpr.round(3).to_string())

    # ARL₁ por (k_shock) para k=0.5, h=5.0 (configuração padrão)
    std_arl1 = df_arl1[(df_arl1['k']==0.5) & (df_arl1['h']==12.0)]
    print(f'\n  ARL₁ — Lag de detecção e poder (k=0.5, h=12.0):')
    print(f'  {"k_shock":>8} {"ARL₁ (passos)":>14} {"Poder":>8}')
    print('  ' + '-'*34)
    for _, row in std_arl1.iterrows():
        lag_str = f'{row.ARL1:.1f}' if not np.isnan(row.ARL1) else '  >T'
        print(f'  {row.k_shock:>8.1f} {lag_str:>14} {row.power:>8.3f}')

    # Pivot: poder por (k_cusum, h_cusum) para k_shock=3
    piv_pwr = df_arl1[df_arl1['k_shock']==3.0].pivot_table(
        index='k', columns='h', values='power', aggfunc='mean')
    print(f'\n  Poder de detecção para k_shock=3.0 (por k_cusum e h):')
    print(piv_pwr.round(3).to_string())

    # Pivot: ARL₁ por (k_shock, h) para k_cusum=0.5
    piv_lag = df_arl1[df_arl1['k']==0.5].pivot_table(
        index='k_shock', columns='h', values='ARL1', aggfunc='mean')
    print(f'\n  ARL₁ médio (lag) para k_cusum=0.5, por (k_shock, h):')
    print(piv_lag.round(1).to_string())

    def _fmt_arl1(df, k, h, ks):
        v = df[(df['k']==k)&(df['h']==h)&(df['k_shock']==ks)]['ARL1'].values
        return f'{v[0]:.0f}' if len(v) and not np.isnan(v[0]) else 'N/A'

    arl0_std = df_arl0[(df_arl0['k']==0.5)&(df_arl0['h']==12.0)]['ARL0'].values[0]
    print(f"""
  Análise:
    Configuracao recomendada: k=0.5, h=12.0
      ARL₀ ≈ {arl0_std:.0f} passos sem mudança
      Detecção de choque 2×λ: ARL₁ ≈ {_fmt_arl1(df_arl1,0.5,12.0,2.0)} passos
      Detecção de choque 5×λ: ARL₁ ≈ {_fmt_arl1(df_arl1,0.5,12.0,5.0)} passos

    Trade-off: h maior → menos falsos alarmes, mais lag
    Em crise severa (k_shock≥3): detecta rapidamente com alto poder
    Em crise leve (k_shock=1.5): poder moderado — sinal fraco

    GLRT-CUSUM (vs CUSUM em λ̂):
      - Auto-calibrado: não requer estimação de μ₀/σ₀
      - Baseado em inovações do filtro: detecta quando o modelo piora
      - Robusto a convergência lenta do Kalman no início

    Integração com sistema:
      1. Kalman online a cada tick → inovação z_t = (ΔP - λ̂Q) / sqrt(S)
      2. GLRT-CUSUM: C_t = max(0, C_{{t-1}} + z_t^2 - 1.5)   (k=0.5)
      3. Alarme quando C_t > 12.0  (ARL0~535 passos) → regime de crise ativado
      4. Acao: position_size *= min(1, lam_pre / lam_current)
      5. Reset CUSUM após 50 passos de burn-in pós-alarme
    """)

    # ── PONTO 3: FRAMEWORK UNIFICADO ─────────────────────────────────
    print(SEP)
    print('  PONTO 3 — FRAMEWORK TEÓRICO UNIFICADO')
    print(SEP)
    print("""
  ┌─────────────────────────────────────────────────────────────────┐
  │              LEI MÍNIMA DE FORMAÇÃO DE PREÇOS                   │
  │                                                                 │
  │  ΔP_t = λ_t · Q_t + ε_t                                       │
  │  λ_t  = σ_v,t / (2·σ_u,t)                                     │
  └─────────────────────────────────────────────────────────────────┘

  VARIÁVEIS (definições operacionais):
    Q_t    = OFI_t = ΔV_bid,t - ΔV_ask,t                (observável L2)
    L_t    = 1/λ_t                                       (liquidez)
    I_t    = VPIN_t = |V_B - V_S|/V_total               (informação proxy)
    λ_t    = estimado por OLS rolling T≥500 ou Kalman    (estimável)

  ──────────────────────────────────────────────────────────────────
  PROTOCOLO DE ESTIMAÇÃO (3 camadas):

    Camada 1 — OLS (baseline, sempre)
      λ̂_OLS = Cov(ΔP, Q) / Var(Q)     T ≥ 500
      Testar endogeneidade: Hausman test (χ²(1))
      Se rejeitado → camada 2

    Camada 2 — IV (se Q endógeno)
      λ̂_IV  = Cov(ΔP_t, Q_{t-1}) / Cov(Q_t, Q_{t-1})
      Validade: F-stat 1ª etapa > 10
      Robustez: testar Z = dP_{t-1} como instrumento alternativo

    Camada 3 — Kalman (tracking de regime)
      λ̂_t|t = λ̂_{t-1} + K_t·(ΔP_t - λ̂_{t-1}·Q_t)
      K_t = P_{t|t-1}·Q_t / (Q_t²·P_{t|t-1} + σ_ε²)
      σ_proc calibrado por MLE ou grid search

  ──────────────────────────────────────────────────────────────────
  DETECÇÃO DE REGIME (CUSUM sobre λ̂_Kalman):

    z_t    = (λ̂_t - μ_0) / σ_0         [normalizar]
    C_t^+  = max(0, C_{t-1}^+ + z_t - k)  k=0.5
    Alarme quando C_t^+ > h=5.0
    → Regime de crise ativado: λ subiu (liquidez caiu)
    → Ação: position size ← min(size, 1/λ̂_Kalman)

  ──────────────────────────────────────────────────────────────────
  SINAL TRADÁVEL (por regime de V):

    V estático (intraday, reversão):     sign(-ΣQ_t)    Sharpe ~+3.0
    V dinâmico (multi-dia, momentum):    sign(+ΣQ_t)    Sharpe ~+0.4
    Discriminar via autocorrelação de ΔP:
      AC(1) < 0 → V estático → contrarian
      AC(1) > 0 → V dinâmico → momentum

  ──────────────────────────────────────────────────────────────────
  SIZING ÓTIMO (Kyle-optimal):

    size_t* = (V̂_t - P_t) / (2·λ̂_t)   [Kyle equilibrium]
    Onde V̂_t estimado por:
      V_hat_t = P_t + lam_hat_t * sum(Q_s, s=1..t) / beta_hat   [de cum. OFI]

    Em regime de crise: lambda_t sobe -> size cai automaticamente
    Max size: limitado por MAX_POSITION_SIZE do .env

  ──────────────────────────────────────────────────────────────────
  FALSIFICACAO EMPIRICA:

    H0 (lei invalida): lambda = 0 para todo t (sem relacao dP ~ Q)
    Teste: Wald test lambda=0 sobre OLS rolling
    Rejeicao: p < 0.01 em >= 80% das janelas rolling
    Resultado esperado (literatura): rejeicao em >95% dos casos
    para dados intraday de mercados liquidos (Cont et al. 2014)

  ──────────────────────────────────────────────────────────────────
  REGIME DE VALIDADE DA LEI:

    Forte  (RMSE<5%,  poder>0.9):  intraday tick-by-tick, T>=500 ticks
    Medio  (RMSE<10%, poder>0.7):  barras de 1-5 min, T>=250 barras
    Fraco  (RMSE<15%, poder<0.5):  barras diarias, lei aproximada
    Invalido (RMSE>15%):           semanal+, usar macro factors
    """)

    # ── Salvar CSVs ──────────────────────────────────────────────────────
    df_iv.to_csv(os.path.join(OUT, 'mc_ponto1_iv.csv'), index=False, float_format='%.4f')
    df_arl0.to_csv(os.path.join(OUT, 'mc_ponto2_cusum_arl0.csv'), index=False, float_format='%.4f')
    df_arl1.to_csv(os.path.join(OUT, 'mc_ponto2_cusum_arl1.csv'), index=False, float_format='%.4f')
    print(f'  ✓ Arquivos: mc_ponto1_iv.csv, mc_ponto2_cusum_arl0.csv, mc_ponto2_cusum_arl1.csv')
    print(f'  ✓ Total: {time.time()-t0:.1f}s')
    print(SEP)


if __name__ == '__main__':
    main()
