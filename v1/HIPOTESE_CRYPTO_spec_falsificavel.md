# Hipótese CRYPTO — Spec Falsificável (pré-registrada em 2026-08-19)

*Escrita ANTES de qualquer medição, com números de morte fixados agora. Motivada pela conclusão do `HIPOTESE_modulo_opcoes_greeks.md` §12: cripto perpétuo é o único lugar onde um player pequeno tem microestrutura real de graça. Duas sub-hipóteses independentes, cada uma com seu mecanismo nomeado. Dado: Binance pública, custo zero. Nada aqui toca o sistema vivo.*

---

## A. FUNDING CARRY — o prêmio de alavancagem

### A.1 O mecanismo (quem paga, e por que é obrigado)

O contrato perpétuo não vence; o que o mantém colado ao índice é o **funding**: a cada 8h, o lado lotado paga o lado vazio. Em cripto, a demanda por alavancagem **comprada** excede estruturalmente a vendida (acesso de varejo, viés de alta), então o funding é positivo na maior parte do tempo — **o comprado alavancado paga, por construção do produto, para manter a posição**. Quem coleta é o carry delta-neutro: comprado em spot, vendido no perp, sem direção. É o análogo cripto do VRP: prêmio documentado, pagador nomeado, demanda inelástica na euforia.

Diferença crucial contra o VRP (que morreu na cauda): a perna vendida do carry é **hedgeada pelo spot**. A cauda de preço é neutralizada; o que sobra é basis transitório, custo de rebalance e **risco de exchange** (não mensurável em série de preço — FTX é o precedente; vira teto de alocação, nunca é "resolvido").

### A.2 Expressão para o player pequeno

Long spot + short perp, mesmo notional, Binance. Sem borrow (o spot é próprio). Colete funding; rebalanceie o hedge quando divergir.

### A.3 Critérios de morte EMPÍRICOS (fixados antes de olhar o dado)

Janela mínima: **≥ 3 anos** e **4 ativos** (BTC, ETH, SOL, BNB). Custo pré-registrado: **1,5%/ano** all-in (4 pernas taker de 0,04% + rebalance mensal + spread), deliberadamente conservador.

- **A-1**: funding anualizado **líquido de 1,5%/ano** ≤ 0, ou t-stat ≤ 2 (por bloco-bootstrap na média), no agregado equal-weight → **morre**.
- **A-2**: carry líquido anualizado ≤ **4%/ano** (proxy de caixa: se paga menos que T-bill, não paga o risco de exchange) → **morre**.
- **A-3**: correlação do P&L de carry com BTC ou SPY ≥ **0,3** (não é o produto delta-neutro prometido) → **morre**.
- Estrutural: funding médio trailing 90d ≤ 0 sustentado = o desequilíbrio de alavancagem sumiu → desliga, mesmo lucrando.

## B. IMBALANCE DE AGRESSOR — o fluxo informado de curto prazo

### B.1 O mecanismo

Taker paga o spread porque tem pressa; pressa agregada revela informação ou liquidação forçada. Se o volume taker comprador domina persistentemente, o preço tende a continuar no curto prazo (market makers reposicionam). Quem paga: o taker atrasado e o liquidado. Fonte: campo `taker_buy_volume` dos klines da Binance — **lado agressor em resolução horária, grátis, sem tick**.

### B.2 Protocolo (parâmetros CONGELADOS agora, sem ajuste posterior)

Sinal: z-score do `taker_buy_ratio − 0,5` sobre janela trailing de **720h (30d)**, defasado 1 barra. Família de testes: **2 ativos (BTC, ETH) × 3 horizontes (1h, 4h, 24h) = 6 testes**; α por teste = 0,05/6 ≈ **0,0083**. Regra operável: long-short por quintis do z (sem threshold livre). Custo por rebalanço: **8 bps**.

### B.3 Critérios de morte

- **B-1**: correlação preditiva (IC) não significativa a α = 0,0083 com bloco-bootstrap em TODOS os 6 testes → **morre**.
- **B-2**: mesmo com IC significativo, a regra de quintis **líquida de 8 bps** com t ≤ 2 → registra-se "informação existe mas não é operável ao nosso custo" e **não se opera**. Informação inoperável não é edge.
- **B-3**: a margem líquida deve exceder o **piso de seleção** √(2·ln 6) × EP do Sharpe da amostra; abaixo disso é o que a própria busca produziria por acaso → **morre**.
- Nulo adicional: moeda de mesmo viés e mesma rotatividade (o nulo que desmascarou o SYMB-1).

## C. Regras de disciplina comuns

1. Nenhum parâmetro (janela do z, quintis, custo, ativos, horizontes) muda depois de ver resultado. Mudou = novo teste, família cresce.
2. Aprovação nesta bateria **não autoriza operação**: o próximo estágio seria SHADOW com relógio próprio, mínimo de 60 sessões, e aprovação humana — hierarquia do `EPISTEMIC_CONTRACT`.
3. Medição no `lab/`, cache de dados **fora** do `data_lake` (sem tocar a linhagem científica).
4. Resultado — inclusive morte — é registrado neste arquivo, como feito no VRP.

## D. BATERIA EXECUTADA — o veredito (2026-08-19, mesmo dia, após congelar A e B)

Executada por `lab/crypto_edge_measurement.py`. Funding desde 2020-01 (~7.300
períodos de 8h por ativo, até 2026-08); klines 1h de perp BTC/ETH (~58.000 barras).
Nenhum parâmetro foi alterado após a primeira execução.

### D.1 Teste A — funding carry: **SOBREVIVEU aos critérios pré-registrados**

| ativo | desde | % períodos positivos | bruto/ano | líquido (−1,5%) | t (bloco) |
|---|---|---|---|---|---|
| BTCUSDT | 2020-01 | 85,8% | +11,81% | **+10,31%** | +8,90 |
| ETHUSDT | 2020-01 | 86,1% | +13,98% | **+12,48%** | +8,45 |
| SOLUSDT | 2020-09 | 71,3% | +0,14% | −1,36% | −0,31 |
| BNBUSDT | 2020-02 | 25,9% | −0,12% | −1,62% | −0,84 |

- **A-1** (agregado equal-weight): líquido **+5,55%/ano, t +3,16** → passa.
- **A-2** (> 4% caixa): +5,55% > 4% → passa.
- **A-3** (delta-neutralidade): corr com BTC **+0,023**, com SPY **−0,001** → passa.
  O produto é de fato ortogonal a tudo que o sistema já tem.
- Cauda medida: MDD do carry líquido **−13,6%**; pior janela de 90 dias
  **−47%/ano anualizada**. O prêmio some por trimestres inteiros (funding 90d ≤ 0
  em **24,6%** do tempo — o gatilho estrutural de desligamento não é decorativo).

**Ressalvas que a aprovação NÃO apaga** (limitações declaradas, não resolvidas):

1. **Base de capital.** Os números são sobre o *notional*. Margem da perna short
   consome capital: a ~50% de eficiência, o agregado cai para ~2,8% líquido —
   **abaixo do A-2**. Só BTC+ETH continuaria acima, mas escolher BTC+ETH *depois*
   de ver a tabela é seleção pós-hoc; um teste "carry só BTC+ETH" seria hipótese
   NOVA, com família declarada.
2. **Risco de exchange** (FTX): não aparece em nenhuma série de preço. É teto de
   alocação permanente, nunca "passa" em teste.
3. O prêmio está concentrado em BTC/ETH; SOL e BNB mostram que ele **não** é
   propriedade universal de perpétuos.

Consequência epistêmica: sobreviver a esta bateria = **candidata a estágio
seguinte** (shadow com relógio próprio, ≥ 60 sessões, aprovação humana), nunca
autorização de capital. Operar exigiria conta de futuros, ingestão de funding no
lake e suporte do paper engine a posição dual spot+perp — decisão do operador.

### D.2 Teste B — imbalance de agressor: **FALSIFICADO**

| ativo | h | IC | p (shift circular) | signif. a α=0,0083 | Sharpe líquido | t |
|---|---|---|---|---|---|---|
| BTC | 1h | +0,0008 | 0,863 | não | −11,41 | −29,4 |
| BTC | 4h | +0,0028 | 0,489 | não | −2,74 | −7,1 |
| BTC | 24h | +0,0116 | 0,016 | não (α corrigido) | −0,63 | −1,6 |
| ETH | 1h | +0,0043 | 0,298 | não | −9,08 | −23,4 |
| ETH | 4h | +0,0069 | 0,102 | não | −1,62 | −4,2 |
| ETH | 24h | +0,0043 | 0,391 | não | −1,17 | −3,0 |

Morte dupla: **B-1** falha (nenhum dos 6 testes significativo ao α corrigido; o
melhor, BTC 24h com p=0,016, não passa de 0,0083 — exatamente o caso que a
correção de família existe para matar) e **B-2** falha (a regra de quintis é
negativa líquida de custos em TODAS as células; no 1h os 8 bps por rebalanço
trituram qualquer sinal). Não há sequer "informação inoperável": em resolução
horária com dado de kline, **não há informação**. Se fluxo de agressor tem valor,
está em resolução de tick/livro — que exige colocation e volta ao jogo de
velocidade que já declaramos não ser o nosso. **Encerrado.**

## E. PROTOCOLO SHADOW DO CARRY (congelado em 2026-08-19, antes da primeira sessão)

O CRYPTO-CARRY graduou da medição histórica para **SHADOW**: observação por
sessão liquidada, sem ordem, sem fill, sem capital — hierarquia do
`EPISTEMIC_CONTRACT`. Implementação: `strategies/crypto_carry_shadow.py`.
Parâmetros abaixo são imutáveis; mudar qualquer um = novo teste, relógio zera.

1. **Universo e pesos**: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, peso fixo de 1/4
   cada (o agregado que passou na bateria — NÃO o subconjunto BTC+ETH, que seria
   seleção pós-hoc). Ativo desligado contribui 0 (caixa a 0%).
2. **Retorno por evento de funding** (8h, 00:00/08:00/16:00 UTC): posição short
   perp hedgeada recebe `rate`; líquido = `rate − 0,015/1095` por evento
   (o 1,5%/ano pré-registrado, amortizado).
3. **Gate estrutural por ativo** (pré-registrado em §A.3): média do funding dos
   270 eventos (90 dias) ESTRITAMENTE anteriores ≤ 0 → ativo desligado no
   evento. Sem lookahead: o gate do evento t usa só eventos < t.
4. **Relógio próprio**: sessão = dia UTC completo. O dia D só entra no ledger
   quando `utc_today > D` (os 3 eventos de D já pagos e imutáveis). O relógio
   de ações (`should_act`) NÃO governa este braço; cripto não tem pregão.
5. **Zero crédito retroativo**: na primeira execução, `last_date = utc_today`.
   A primeira sessão contada é o primeiro dia UTC completo APÓS o congelamento
   deste protocolo. Nada anterior entra no ledger forward.
6. **Dados**: funding ingerido da Binance fapi (pública) para
   `data_lake/raw/FUNDING_{SYM}_8h.parquet` com manifest de proveniência;
   bootstrap de 120 dias só para aquecer o gate (não vira sessão).
7. **Ledger**: `data_lake/track_record/crypto_carry_nav.csv`, append-only,
   NAV inicial 100.000. Passado nunca é reescrito.
8. **Graduação a PAPER**: ≥ 60 sessões liquidadas, ledger íntegro, e as três
   ressalvas de §D.1 reavaliadas com o forward em mãos — mais aprovação humana.
   Nada aqui autoriza capital.
