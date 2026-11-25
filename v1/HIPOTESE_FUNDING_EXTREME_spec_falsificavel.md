# Hipótese FUNDING EXTREME — Spec Falsificável (pré-registrada em 2026-08-19)

*Escrita ANTES de qualquer medição. Números de morte fixados agora. Quarta
tentativa na família (n=3). Mecanismo distinto do carry: aqui o sinal é
**direcional** — extremos de funding precedem liquidação de comprados.*

## 1. O mecanismo

Funding muito alto = crowding de alavancagem comprada. O crowding se resolve
por liquidação, e a liquidação empurra o spot para baixo. O pagador é o
alavancado atrasado (sai por stop/liquidation, não por escolha). Expressão:
short o ativo quando o funding z-score (janela 90d, 270 eventos) cruza um
limiar pré-fixado; cobre quando o z volta abaixo de zero. Sem alavancagem
adicional — notional 1×.

Isto NÃO é o carry. É um timing de cauda sobre o mesmo insumo. Se o carry
sobreviveu e este também, ainda podem ser o mesmo crowding visto de dois
ângulos — a correlação será reportada.

## 2. Protocolo (congelado)

- Ativos: BTC, ETH. Série: funding 8h da Binance (já em cache do lab).
- Sinal: z-score do funding sobre 270 eventos anteriores (sem lookahead).
- Entrada: z > **2,0** (short). Saída: z < **0**. Sem threshold livre além
  destes dois números, congelados agora.
- Horizonte implícito: enquanto o z permanecer ≥ 0 após entrada.
- Custo: **8 bps por round-trip** (entrada + saída taker).
- Família: 2 ativos; α = 0,05/2 = 0,025.
- Posição = 0 ou −1; P&L = posição_t-1 × retorno do perp no intervalo de 8h
  − 8 bps × |Δposição|.

## 3. Critérios de morte

- **E-1**: IC do z (defasado 1) contra o retorno seguinte de 8h não
  significativo a α = 0,025 em NENHUM dos 2 ativos (shift circular) →
  **morre**.
- **E-2**: regra operável líquida de 8 bps/giro com t ≤ 2 no agregado
  equal-weight → **morre** (informação inoperável não é edge).
- **E-3**: Sharpe líquido abaixo do piso de seleção √(2·ln 2) / √anos →
  **morre**.
- Qualificação: correlação do P&L com o carry delta-neutro ≥ 0,5 → o
  "timing" é só o carry disfarçado de direção.

## 4. Disciplina

Parâmetros imutáveis. Sobreviver = SHADOW, nunca capital. Lab, cache fora do
lake. Veredito neste arquivo.

## 5. BATERIA EXECUTADA — o veredito (2026-08-19, mesmo dia)

Executada por `lab/funding_extreme_measurement.py` sobre o funding 8h já em
cache (2020–2026) e klines 1h.

| ativo | IC (z → ret 8h) | p (shift) | signif. α=0,025 | Sharpe líquido | t |
|---|---|---|---|---|---|
| BTC | −0,0128 | 0,458 | não | −0,63 | −1,20 |
| ETH | +0,0052 | 0,758 | não | −1,42 | −2,72 |

O sinal do IC em BTC é o "certo" (funding alto → retorno seguinte negativo)
e mesmo assim é ruído. Agregado: **−13,4%/ano**, Sharpe −0,91, t −2,61.

- **E-1**: nenhum IC significativo → **MORRE**.
- **E-2**: regra operável negativa → **MORRE**.
- **E-3**: Sharpe abaixo do piso → **MORRE**.
- Qualificação: corr com carry −0,089 — nem sequer é o carry disfarçado;
  é só ruído caro.

**FALSIFIED.** Extremos de funding em resolução de 8h não antecipam o preço
de forma operável. Encerrada.
