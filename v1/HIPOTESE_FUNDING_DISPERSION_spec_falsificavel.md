# Hipótese FUNDING DISPERSION — Spec Falsificável (pré-registrada em 2026-08-19)

*Escrita ANTES de qualquer medição. Números de morte fixados agora. Terceira
tentativa na família "prêmio de alavancagem" (n=2 após BASIS). Escolhida porque
o CRYPTO-CARRY sobreviveu: se o mesmo funding tem preços diferentes em venues
diferentes, a diferença é ineficiência com o mesmo pagador.*

## 1. O mecanismo

O comprado alavancado paga funding na venue em que está. Se Binance, Bybit e
OKX cotam funding diferente no mesmo ativo no mesmo instante, um player pequeno
pode estar short na venue cara e long na barata (mesmo notional) e coletar o
spread de funding sem direção. Quem paga: o alavancado na venue cara. O risco
que resta é **duas exchanges ao mesmo tempo** (FTX ×2) e basis residual entre
os perps.

## 2. Expressão

Delta-neutro cross-venue: short perp na venue de funding mais alto, long perp
na de funding mais baixo, mesmo notional, rebalance a cada evento de 8h.
Universo: BTC e ETH. Venues: Binance (fapi), Bybit (v5), OKX (swap).

## 3. Protocolo (congelado)

- Janela ≥ 3 anos, eventos de 8h alinhados por timestamp (tolerância 1h).
- Spread coletado = `max(venues) − min(venues)` quando as três venues têm
  observação; se só duas, usa as duas.
- Custo pré-registrado: **3%/ano** all-in (duas pernas taker em duas venues,
  mais spread de perp-perp; deliberadamente o dobro do carry single-venue).
- P&L por evento = spread − `0,03/1095`. Sem gate de nível (o produto é o
  *spread*, não o nível).

## 4. Critérios de morte

- **D-1**: líquido de 3%/ano com t ≤ 2 (bloco-bootstrap) no agregado BTC+ETH
  equal-weight → **morre**.
- **D-2**: líquido anualizado ≤ **4%/ano** (caixa; agora o risco é *duas*
  exchanges) → **morre**.
- **D-3**: correlação do P&L com BTC ou SPY ≥ 0,3 → **morre**.
- Qualificação (não mata): correlação com o funding carry single-venue ≥ 0,8
  → mesmo prêmio, não diversifica.

## 5. Disciplina

Mesmas regras da família: parâmetros imutáveis após a primeira execução;
sobreviver = candidata a SHADOW, nunca capital; medição no `lab/`, cache fora
do lake; veredito registrado aqui.

## 6. BATERIA EXECUTADA — o veredito (2026-08-19, mesmo dia)

Executada por `lab/funding_dispersion_measurement.py`.

Cobertura: Binance ~7.269 eventos, Bybit ~6.400–7.000, **OKX apenas 281**
(~3 meses). Após alinhamento das três venues, n=281 — **abaixo da janela
mínima de 3 anos**. Não se reexecuta Binance×Bybit só, isso seria hipótese
nova (duas venues) depois de ver o resultado.

| ativo | n alinhado | spread bruto | líquido (−3%/ano) | t |
|---|---|---|---|---|
| BTC | 281 | +5,29%/ano | +2,29% | +11,80 |
| ETH | 281 | +5,11%/ano | +2,11% | +10,62 |

- **D-1**: agregado t **+13,61** → passaria (amostra curta infla t).
- **D-2**: líquido **+2,19%/ano < 4%** → **MORRE**. Com risco de *duas*
  exchanges, 2% não paga o caixa.
- **D-3**: corr BTC −0,010, SPY +0,074 → passaria.
- Concentração com funding carry: −0,244 (não é o mesmo P&L).

**FALSIFIED por D-2**, e ainda inconclusiva na janela de 3 anos por falta
de história OKX. Não vai a shadow. Encerrada.
