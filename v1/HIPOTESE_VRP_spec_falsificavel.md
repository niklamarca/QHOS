# Hipótese VRP — Spec Falsificável (pré-registrada)

*Primeira hipótese do sistema com MECANISMO nomeado e INVALIDAÇÃO ESTRUTURAL. Escrita ANTES de construir estratégia, para não ajustar a meta depois de ver o resultado. Medição precede sizing.*

---

## 1. O mecanismo (o "porquê" — quem paga, e por que é obrigado)

Investidores (fundos, gestores) têm demanda **persistente e inelástica** por seguro de queda/volatilidade (puts de índice, VIX). A capacidade de *carregar* esse risco é limitada. O desequilíbrio faz a vol implícita ser, na média, **maior que a realizada** (`IV > RV`), e a estrutura a termo do VIX ficar em **contango** (futuros acima do spot). Quem **vende vol** é pago por absorver o risco de crash — via roll-down (contango) e via `IV − RV`.

**Contraparte natural do pequeno:** vender vol é onde um player pequeno *pode* ser o absorvedor de risco. Expressável **sem cadeia de opções**, via short de ETFs de vol (VIXY/VXX) na infraestrutura de ações que já temos.

## 2. Evidência inicial medida (VIXY, 2021-10 a 2026-08, 1210 dias)

| Métrica | Valor | Leitura |
|---|---|---|
| VIXY buy&hold | **−95%** | o decaimento = o prêmio existe |
| Short VIXY, retorno anualizado | **+39,1%** | prêmio real, positivo |
| Short VIXY, Sharpe | **+0,56** | modesto, mas positivo e de mecanismo |
| Kelly f* diário | **0,79x** | pela 1ª vez > 0 ("opere pequeno") |
| **Pior dia** | **−43,1%** (05/08/2024) | a cauda mata aqui |
| CVaR 5% | −11,9% | |
| Max drawdown | **−79,1%** | |

Laboratório (barreiras $11.5k/$9k): mesmo com edge real, **~43–49% das contas se liquidam** — a cauda atravessa o piso. **Sobrevivência > expectativa.**

**Caveats honestos, pré-registrados:** (a) bootstrap iid **subestima** a cauda — crashes de vol são clusterizados (XIV→~0 em fev/2018); a cauda verdadeira é pior. (b) +39%/ano é **dependente de regime** (parecia +100%/ano antes de fev/2018). (c) short de VIXY tem **custo de borrow** e pode ficar *hard-to-borrow* justamente no spike — ainda não modelado. (d) medição usou VIXY como proxy; o `IV−RV` limpo exige `^VIX` (yfinance pega).

## 3. Ciclo de vida da hipótese (começo / meio / fim)

- **Começo (liga):** estrutura a termo do VIX em contango E `IV − RV > 0`. Condição mensurável.
- **Meio (colhendo):** enquanto contango segura, o roll-down paga; mede-se prêmio realizado vs. previsto.
- **Fim (desliga):** contango vira **backwardation** persistente (spike de stress), OU o prêmio (`IV−RV`) some. O fim é definido pelo mecanismo, não por stop chutado.

## 4. Invalidação ESTRUTURAL (o mecanismo morreu) — monitorável, separada do P&L

- Estrutura a termo do VIX em **backwardation sustentada** (roll yield ≤ 0 por janela X).
- `IV − RV` ≤ 0 persistente.
- Regulação/produto que remova a demanda inelástica por hedge (improvável, mas é o gatilho estrutural).

> Estrutural pode disparar **ganhando** (quadrante mortal: você segue vendendo vol num regime que virou, prestes a estourar). Por isso monitora-se o **mecanismo**, não o lucro.

## 5. Invalidação EMPÍRICA (limiares de morte, pré-registrados ANTES de operar)

A hipótese é **abandonada** se, no forward líquido de custo (incl. borrow):

1. expectativa de vender vol **não** for positiva com **t-stat > 2**; ou
2. **não** bater o SPY em base ajustada a risco (senão, só segure o SPY); ou
3. a sobrevivência falhar: com sizing tal que o **pior dia plausível (assumir ≥ −50%, pior que o −43% observado)** NÃO viole o limite duro de risco. Se viola, o sizing é grande demais — reduza ou mate.

**Kill switches:** Sharpe forward < 0 por N meses → parar; um único evento rompe o limite de cauda → parar; term structure vira estruturalmente → parar.

## 6. Sizing orientado à SOBREVIVÊNCIA (não à expectativa)

- **Kelly fracionário agressivamente baixo:** ≤ ¼ do 0,79 → ~**0,2x**. A tentação de alavancar os +39%/ano é exatamente o que liquidou o XIV.
- **Cap de posição + stop de cauda duro** para nenhum dia único exceder X% de perda.
- Assumir a cauda **pior** que a observada (iid subestima; use block-bootstrap clusterizado).

## 7. Medição ANTES de estratégia (a ordem inegociável)

1. Ingerir `^VIX` (yfinance) → computar `IV − RV` limpo e o **sinal de roll** (contango/backwardation) = o monitor de invalidação estrutural.
2. **Block-bootstrap** (clusterizado) da cauda de vender vol — a verdade da sobrevivência, não o iid otimista.
3. **Stress de custo/borrow** — o prêmio sobrevive a borrow realista?
4. **Split por regime** — o prêmio sobrevive em regimes estressados, ou só colhe calmaria e estoura?
5. Só então: regra de venda de vol definida, sizing por sobrevivência, no harness honesto (embargo + DSR + forward + as guilhotinas do `lab/`).

Se o VRP líquido não sobreviver a isto, a hipótese está errada — e ela **tem que continuar podendo morrer.** É a diferença entre isto e as regras técnicas: aqui existe um mecanismo que pode ser falsificado estruturalmente, não só um padrão que aparece e some.

---

## 8. BATERIA EXECUTADA — o veredito (2026-08-19)

*A §7 exigia medição completa ANTES de definir estratégia. A bateria foi executada na ordem prescrita (`lab/vrp_measurement.py`, `lab/vrp_gated.py`, `lab/vrp_sizing.py`), sobre VIXY/VIX/VIX3M já no data_lake. Janela real: 2011-01 a 2026-08 (~3.928 sessões), maior do que os 1.210 dias da §2.*

### 8.1 O gate por estrutura a termo funciona — e isso é um fato novo

| Série | Retorno/ano | Sharpe | Pior dia | CVaR5 | MDD | Em mercado |
|---|---|---|---|---|---|---|
| BASE (sempre short) | +43,8% | +0,63 | −43,1% | −12,0% | **−91,8%** | 99% |
| **GATED (só contango)** | +47,8% | **+0,81** | −33,6% | −10,1% | **−62,5%** | 92% |

**O gate evitou o crash de 05/08/2024.** A estrutura a termo virou para backwardation em 02/08 (VIX/VIX3M = 1,002) e o gate ficou de fora nos dias −24,0% e −43,1%. Isso é o mecanismo da §3 funcionando como previsto, medido, sem look-ahead (sinal defasado 1 dia). **Registre-se como acerto da spec.**

### 8.2 Mas os critérios de morte da §5 foram aplicados

**Critério 1 — expectativa positiva com t > 2: PASSA.** Sharpe +0,81 em ~15,6 anos → t ≈ +3,2.

**Critério 2 — bater o SPY em base ajustada a risco: FALHA, em todo sizing.**

| Sizing | Retorno/ano | Sharpe | MDD |
|---|---|---|---|
| GATED full (~1x) | +47,8% | +0,81 | −62,5% |
| GATED vol-target 15% | +11,3% | +0,59 | −25,5% |
| GATED ¼-Kelly (o sizing que a §6 manda usar) | +16,3% | +0,81 | −24,2% |
| **SPY buy & hold (benchmark)** | +14,8% | **+0,87** | −33,7% |
| 50/50 SPY+VRP | +13,1% | +0,82 | −22,1% |

Nenhum sizing bate o SPY ajustado a risco. E a defesa de diversificação também cai: correlação **+0,54** com o SPY, e o combo 50/50 dá **+0,82 contra +0,87 do SPY sozinho** — o combo não melhora, piora. A §5 exige falhar *ou* o benchmark *ou* a sobrevivência; aqui falha o benchmark nas duas formas.

**Critério 3 — sobrevivência: FALHA no sizing operável.** Barreiras $11,5k/$9k: liquidação de **45,4%** no full e **28,0%** no ¼-Kelly. A §6 pedia sizing tal que o pior dia plausível (≥ −50%) não violasse o limite duro — a probabilidade de liquidação de ~28% no sizing recomendado não satisfaz isso.

**Stress de borrow (a ressalva (c) da §2, agora quantificada):**

| Borrow/ano | Retorno líquido | Sharpe |
|---|---|---|
| 0% | +44% | +0,63 |
| 5% | +39% | +0,55 |
| 15% | +29% | +0,41 |
| 30% | +14% | +0,20 |

A 15% de borrow — plausível para VIXY — o Sharpe cai a +0,41, menos da metade do SPY. E permanece **não modelado** o risco de ficar *hard-to-borrow* exatamente no spike, que é quando a posição precisa ser mantida.

**Split por regime (a pergunta "só colhe calmaria?"): sim.** Calmaria (70% dos dias) Sharpe +0,94; stress (30% piores) Sharpe +0,22 com pior dia −43%. O prêmio é majoritariamente colheita de calmaria com a cauda concentrada no stress — exatamente o perfil que a §6 chamou de perigoso.

### 8.3 Veredito

**FALSIFIED** pelo critério 2 da §5, com o critério 3 também insatisfeito. O VRP **não graduou ao forward** — e, pela ordem inegociável da §7, nunca deveria ter graduado antes desta medição.

Registre-se que o resultado é obtido no enquadramento **mais favorável possível**: gate escolhido com conhecimento da amostra, borrow zero no número principal, sem modelar hard-to-borrow, e usando VIXY como proxy em vez de cadeia de opções. Mesmo lisonjeado, perde de comprar e segurar o índice.

**O que sobrevive como conhecimento:** (a) o prêmio existe e é grande (+44%/ano bruto); (b) o gate de estrutura a termo tem informação real e evitou o pior dia da amostra; (c) o que mata a hipótese não é a ausência do prêmio, é que **a cauda e o borrow o transferem inteiro para quem carrega o risco** — e um player pequeno não consegue carregá-lo sem probabilidade de liquidação de ~28%. Isso é uma resposta econômica, não estatística, e é mais informativa do que o número.

**Consequência operacional:** não construir braço de venda de vol. A §4 (invalidação estrutural) continua valendo como *monitor* — o painel de estrutura a termo do dashboard segue útil como leitura de regime, não como sinal de posição.
