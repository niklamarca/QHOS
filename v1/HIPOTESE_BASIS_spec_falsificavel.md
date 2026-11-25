# Hipótese BASIS — Spec Falsificável (pré-registrada em 2026-08-19)

*Escrita ANTES de qualquer medição. Números de morte fixados agora.*

## 0. Declaração de dependência (honestidade de seleção)

Esta spec **não é independente**: foi escolhida PORQUE o CRYPTO-CARRY (funding)
sobreviveu horas antes. O mecanismo é o mesmo pagador em outro instrumento —
é a segunda tentativa na família "prêmio de alavancagem". Consequências
declaradas: (a) a correlação do P&L desta estratégia com o funding carry será
medida e reportada; se ≥ 0,8, isto NÃO é uma segunda fonte de retorno, é o
mesmo prêmio em outro invólucro — vale como confirmação do mecanismo, não como
diversificação; (b) a família de specs derivadas do sucesso do funding começa
aqui com n=1 e será contada nas próximas.

## 1. O mecanismo (quem paga, e por que é obrigado)

O futuro trimestral com entrega não tem funding: o custo da alavancagem está
embutido no **prêmio sobre o spot** (contango). Quem quer alavancagem comprada
por prazo fixo paga esse prêmio na entrada; na entrega, futuro converge ao
spot por contrato. O carry (long spot + short trimestral, mesmo notional)
captura a basis com risco direcional neutralizado. Pagador: o alavancado
comprado — o mesmo do funding, escolhendo pagar à vista em vez de a cada 8h.
Se os dois preços da mesma alavancagem divergirem, arbitragem os aproxima;
por isso espera-se basis ≈ funding esperado acumulado, menos fricções.

## 2. Expressão para o player pequeno

Long spot + short trimestral corrente na Binance; na semana da entrega, rola
para o próximo trimestre. 4 rolagens/ano. Sem borrow.

## 3. Protocolo de medição (parâmetros CONGELADOS agora)

- Ativos: **BTC e ETH** (únicos com trimestral USDT líquido em todo o
  histórico). Janela mínima: ≥ 3 anos.
- Série: `continuousKlines` diário do contrato CURRENT_QUARTER (fapi) + spot
  diário. Basis anualizada `b_t = (F/S − 1) × 365/dte`, excluindo `dte < 7`
  (explosão numérica perto da entrega).
- P&L diário delta-neutro: `ret_spot − ret_fut`, com **ret_fut zerado no dia
  da entrega e no seguinte** (última sexta de mar/jun/set/dez, 08:00 UTC —
  a série contínua salta de contrato no roll; o salto não é P&L de posição.
  Zerar 2 dias/trimestre é conservador: joga fora carry, nunca adiciona).
- Gate estrutural (mesmo do funding, §A.3 da spec CRYPTO): média da basis
  anualizada dos 90 dias ESTRITAMENTE anteriores ≤ 0 → desligado. Sem
  lookahead.
- Custo pré-registrado: **1,5%/ano** all-in (idêntico ao da spec irmã, para
  comparabilidade; conservador — trimestral tem menos pernas que perp).

## 4. Critérios de morte EMPÍRICOS

- **B-1**: carry líquido de 1,5%/ano com t ≤ 2 (bloco-bootstrap) no agregado
  equal-weight BTC+ETH → **morre**.
- **B-2**: carry líquido anualizado ≤ **4%/ano** (proxy de caixa; abaixo disso
  não paga o risco de exchange) → **morre**.
- **B-3**: correlação do P&L diário com BTC ou SPY ≥ **0,3** → **morre**
  (não é o produto delta-neutro prometido).
- **Concentração** (não mata, qualifica): correlação com o P&L do funding
  carry ≥ 0,8 → declarar "mesmo prêmio, outro invólucro"; os dois nunca somam
  como fontes independentes num portfólio.
- Estrutural: basis 90d ≤ 0 sustentada = prêmio de alavancagem ausente →
  desliga, mesmo lucrando.

## 5. Disciplina

1. Nenhum parâmetro muda após a primeira execução. Mudou = novo teste.
2. Sobreviver aqui = candidata a SHADOW (relógio próprio, ≥ 60 sessões,
   humano) — nunca autorização de capital.
3. Medição no `lab/`, cache fora do `data_lake`.
4. Veredito — inclusive morte — registrado neste arquivo.

## 6. BATERIA EXECUTADA — o veredito (2026-08-19, mesmo dia)

Executada por `lab/basis_carry_measurement.py`. `continuousKlines` CURRENT_QUARTER
+ spot diário, 2021-02 → 2026-08 (~2.023 dias). Nenhum parâmetro alterado.

| ativo | dias | basis média (dte≥7) | % contango | carry bruto (gate on) |
|---|---|---|---|---|
| BTCUSDT | 2.023 | +8,22% | 96,7% | +6,91%/ano |
| ETHUSDT | 2.022 | +7,52% | 91,9% | +6,61%/ano |

- **B-1**: agregado líquido **+4,72%/ano, t +4,80** → passaria.
- **B-2**: +4,72% > 4% → passaria.
- **B-3**: corr P&L × BTC **−0,342**, × SPY −0,105. O critério escrito é
  "≥ 0,3" e o propósito (copiado da spec irmã A-3) é recusar produto que
  **não** é delta-neutro. |−0,342| ≥ 0,3 → **MORRE**. (A leitura só do sinal
  positivo passaria; aplicamos o valor absoluto porque o prometido era
  neutralidade, não "não correlacionar positivamente".)
- Concentração com funding carry: corr diária **+0,013**, mensal **−0,038**.
  Não é o mesmo P&L — mas também não é o hedge que a spec exigiu.
- MDD líquido −2,90%; gate desligado 3,9% do tempo.

**FALSIFIED por B-3.** O nível de contango existe e o carry bruto é real; o
P&L construído como `ret_spot − ret_fut` (com roll zerado) **carrega beta
negativo de BTC**. Economicamente coerente — a demanda por alavancagem sobe
com o preço — e suficiente para matar a expressão "delta-neutra" do player
pequeno. Não vai a shadow. Não se reparametriza o hedge depois de ver o
número. Encerrada.
