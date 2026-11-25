# Compêndio Sênior — Fórmulas do Mercado Financeiro + Modelos de Execução (e como se comparam ao nosso bot)

*"Todas as fórmulas que existem" é infinito. Este é o conjunto que de fato se usa, organizado por família. No fim, os algoritmos de execução (VWAP, TWAP, POV, Implementation Shortfall, Almgren-Chriss) detalhados e comparados com o `execution_engine.py` do projeto.*

**Aviso de analista, antes de tudo:** as fórmulas abaixo são *ferramentas de medição e de custo*, não fontes de edge. Saber todas não gera alpha. Os algoritmos de execução em particular **minimizam o custo de um trade já decidido** — eles não decidem *o quê* negociar. Para um player pequeno em nomes líquidos, o impacto de mercado é quase nulo, então execução sofisticada rende quase nada (importa em tamanho institucional). Guarde isso ao ler a Parte 8.

---

## 1. Retorno e performance

- **Retorno simples:** `r = (P_t − P_{t−1}) / P_{t−1}`
- **Retorno log:** `r = ln(P_t / P_{t−1})` (aditivo no tempo)
- **Retorno acumulado:** `∏(1 + r_i) − 1`
- **CAGR:** `(P_fim / P_ini)^(1/anos) − 1`
- **Anualização de retorno:** `(1 + r_período)^(períodos_por_ano) − 1`
- **Anualização de vol:** `σ_ano = σ_período × √(períodos_por_ano)` (√252 para diário)

## 2. Risco

- **Volatilidade:** `σ = √(Σ(r_i − r̄)² / (n−1))`
- **Downside deviation:** só desvios abaixo de um alvo (usada no Sortino)
- **VaR (paramétrico):** `VaR_α = μ − z_α·σ`
- **VaR (histórico):** percentil α da distribuição empírica de retornos
- **CVaR / Expected Shortfall:** `E[perda | perda > VaR]` — média da cauda (o que o VaR ignora)
- **Max Drawdown:** `min_t (P_t / max_{s≤t} P_s − 1)`
- **Beta:** `β = Cov(r_ativo, r_mercado) / Var(r_mercado)`
- **Tracking error:** `σ(r_portfólio − r_benchmark)`

## 3. Razões ajustadas ao risco

- **Sharpe:** `(r̄ − r_f) / σ` × √(períodos) → anualizado
- **Sortino:** `(r̄ − r_f) / downside_dev`
- **Calmar:** `CAGR / |MaxDD|`
- **Information Ratio:** `(r̄_p − r̄_bench) / tracking_error`
- **Probabilistic Sharpe Ratio (PSR):** probabilidade de o Sharpe verdadeiro > referência, corrigindo por assimetria e curtose
- **Deflated Sharpe Ratio (DSR):** PSR descontando o **número de tentativas** — o Sharpe esperado do melhor de N estratégias aleatórias. *É o que usamos no `consolidation_scorecard.py`.*
- **t-stat do Sharpe:** `SR × √n` (aprox.) — significância estatística

## 4. Teoria de portfólio

- **Retorno do portfólio:** `Σ w_i · r_i`
- **Variância do portfólio:** `wᵀ Σ w` (Σ = matriz de covariância)
- **Fronteira eficiente (Markowitz):** minimizar `wᵀΣw` sujeito a retorno-alvo e `Σw = 1`
- **Portfólio de variância mínima:** `w = Σ⁻¹𝟙 / (𝟙ᵀΣ⁻¹𝟙)`
- **Máximo Sharpe (tangência):** `w ∝ Σ⁻¹(μ − r_f)`
- **CAPM:** `E[r_i] = r_f + β_i(E[r_m] − r_f)`
- **Ledoit-Wolf shrinkage:** encolhe Σ amostral para um alvo estruturado (reduz ruído) — *usado no nosso HRP*
- **HRP (Hierarchical Risk Parity):** clusteriza por correlação, quasi-diagonaliza, aloca por bisecção recursiva inversa à variância — *é o que o `portfolio/hrp.py` faz*

## 5. Dimensionamento de posição

- **Kelly:** `f* = (p·b − q) / b` (p=prob ganho, q=1−p, b=odds) → fração ótima de capital
- **Kelly contínuo:** `f* = μ / σ²` (excesso de retorno / variância)
- **Fractional Kelly:** `½·f*` ou menos — na prática, porque Kelly cheio tem drawdowns brutais e μ/σ são estimados com erro
- **Vol targeting:** `w = σ_alvo / σ_realizada` — escala exposição para vol constante
- **Risk parity:** cada ativo contribui igualmente para o risco total

## 6. Opções e volatilidade (resumo — detalhe no módulo de greeks)

- **Black-Scholes (call):** `C = S·N(d₁) − K·e^{−rT}·N(d₂)`, com `d₁ = [ln(S/K) + (r + σ²/2)T] / (σ√T)`, `d₂ = d₁ − σ√T`
- **Greeks:** `Δ = ∂V/∂S`, `Γ = ∂²V/∂S²`, `ν = ∂V/∂σ`, `Θ = ∂V/∂t`, `ρ = ∂V/∂r`
- **Put-call parity:** `C − P = S − K·e^{−rT}`
- **Vol implícita:** σ que iguala o preço BS ao preço de mercado (resolvido numericamente)
- **VRP (prêmio de risco de vol):** `IV − RV` (implícita − realizada), na média positivo
- **Realized vol:** `√(Σ r_i² × períodos_por_ano)`

## 7. Microestrutura (a ponte com order flow)

- **Spread bid-ask:** `ask − bid`; **spread relativo:** `(ask − bid) / mid`
- **Mid price:** `(bid + ask)/2`; **micro-price:** ponderado pela profundidade
- **Kyle's λ:** `ΔP = λ · Q` — impacto de preço por unidade de fluxo assinado (mede iliquidez). *Temos proxy nas features.*
- **Amihud illiquidity:** `|r| / volume$` — impacto médio por dólar negociado
- **Order Flow Imbalance (OFI):** compra agressiva − venda agressiva
- **Roll spread estimator:** `2√(−Cov(Δp_t, Δp_{t−1}))`

## 8. MODELOS DE EXECUÇÃO (o pedido central)

Todos existem para responder uma pergunta: *dado que já decidi comprar/vender Q, como faço isso com o menor custo?* O custo tem duas forças em tensão — **impacto de mercado** (negociar rápido move o preço contra você) vs. **risco de timing** (negociar devagar te expõe à volatilidade). Todo algoritmo é um ponto nessa reta.

### 8.1 Impacto de mercado (a física por trás)

- **Impacto temporário** (some após o trade) vs. **permanente** (fica no preço).
- **Lei da raiz quadrada:** `Impacto ≈ Y · σ · √(Q / V)` — custo cresce com a raiz do tamanho relativo ao volume diário V.
- **Impacto linear (Kyle):** `ΔP = λ · Q`.

### 8.2 TWAP — Time-Weighted Average Price

- **Benchmark:** preço médio no tempo, `TWAP = (1/N)·Σ P_i`.
- **Como funciona:** fatia Q em N pedaços iguais, um a cada intervalo fixo. Ignora volume.
- **Prós/contras:** simples e robusto; mas **previsível** (gameável) e ineficiente quando o volume é irregular.
- **No nosso bot:** ✅ **existe** — `paper_broker.submit_twap(order, duration_seconds, n_slices)`, acionado no `execution_engine` quando `quantity > 1000`. É a única lógica de fatiamento real que temos.

### 8.3 VWAP — Volume-Weighted Average Price

- **Benchmark:** `VWAP = Σ(P_i · V_i) / Σ V_i`.
- **Como funciona:** fatia Q **proporcional à curva esperada de volume** (tipicamente em U ao longo do dia). Objetivo: bater o VWAP.
- **Precisa de:** perfil histórico de volume intraday.
- **No nosso bot:** ❌ **não temos** — exige volume intraday, que não coletamos (barra diária).

### 8.4 POV — Percentage of Volume (Participation)

- **Como funciona:** negocia uma fração fixa do volume **em tempo real**: `tamanho_filho = taxa_participação × volume_observado`. Adaptativo à liquidez.
- **Precisa de:** feed de volume em tempo real.
- **No nosso bot:** ❌ **não temos**.

### 8.5 Implementation Shortfall (IS) — "arrival price" (Perold)

*(Provavelmente o "downfall" que você quis dizer — o termo técnico é **shortfall**.)*

- **Definição:** `IS = (retorno do portfólio de papel) − (retorno do portfólio real)`. Ou seja, o **custo total de não ter executado instantaneamente** ao preço da decisão.
- **Decomposição:** `IS = Σ(P_exec − P_decisão)·shares + custo de oportunidade das ações não executadas + comissões`.
- **Como funciona o algo:** minimiza IS balanceando impacto (rápido) vs. risco de preço (devagar), geralmente começando mais agressivo perto do preço de chegada.
- **No nosso bot:** ⚠️ **parcial/implícito** — não otimizamos IS, mas o `paper_broker` mede `P_exec` vs. preço de referência via slippage, então temos os ingredientes para *medir* IS, não para *minimizá-lo*.

### 8.6 Almgren-Chriss — execução ótima

- **Objetivo:** minimizar `E[custo] + λ · Var[custo]`, onde λ = aversão a risco.
- **Trade-off:** impacto temporário (custo de velocidade) vs. variância de preço (custo de lentidão).
- **Resultado:** para impacto linear, a **trajetória ótima é um decaimento exponencial** do inventário; λ alto → executa rápido; λ baixo → espalha no tempo.
- **No nosso bot:** ❌ **não temos** — é o "estado da arte" institucional; overkill para o nosso tamanho.

### 8.7 Outros

- **Iceberg / hidden:** mostra pouco, esconde o tamanho.
- **MOC / arrival / close:** benchmark = preço de fechamento ou de chegada.
- **Liquidity-seeking / sniper:** caça liquidez oculta oportunisticamente (jogo de HFT).

---

## 9. Como o nosso bot se compara — leitura honesta

O que o `execution_engine.py` + `paper_broker.py` de fato fazem:

1. **Risk gate obrigatório** antes de qualquer ordem (peso resultante vs. limite de concentração).
2. **Fatiamento TWAP** para ordens grandes (`quantity > 1000`) — nossa única lógica de execução "algorítmica".
3. **Rebalance para pesos-alvo** com **ordenação sell-first** (libera caixa antes de comprar) e **banda de não-negociação** (só negocia se o peso mudou mais que a banda — anti-churn).
4. **Simulação de custo** no paper broker: `slippage` (bps) + `comissão` (bps), **fixos** — não dependem do tamanho.

**As lacunas (e por que a maioria não importa agora):**

- Não temos VWAP, POV, IS-otimizado nem Almgren-Chriss. Todos exigem **volume/tempo intraday** que não coletamos.
- Nosso "impacto" é **cego à escala** (slippage fixo em bps). Irrealista para ordens grandes — mas **realista o bastante para tamanho pequeno**, onde o impacto real é ~desprezível.
- A **banda anti-churn** é o nosso único mecanismo genuíno de *redução de custo de execução*, e é o que mais rende no nosso caso (o over-trading foi a maior fonte de perda histórica).

**Conclusão de analista:** para um player pequeno em nomes líquidos, **execução sofisticada é dinheiro parado.** VWAP/POV/Almgren-Chriss resolvem o problema de mover milhões sem derrubar o preço — não é o nosso problema. O que a nossa execução precisa é ser **correta** (registrar trade e custo, não vazar caixa, não churnar) — o que já corrigimos — não **ótima**. Investir em algoritmos de execução agora é otimizar o 3º dígito de um número cujo 1º dígito (o edge) ainda é zero.

Onde execução *passaria* a importar de verdade: se um dia o edge escolhido for **cripto perp de curto prazo** (order flow), aí sim latência e execução viram parte do edge — e o jogo muda para o território de velocidade, onde o pequeno é fraco. É mais uma razão pela qual o VRP (lento, insensível a execução) é o caminho mais coerente para o nosso tamanho.
