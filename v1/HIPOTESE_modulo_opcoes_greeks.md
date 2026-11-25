# Módulo de Opções — Delta Hedging, Gamma, Vega, Theta como a mecânica *mensurável* da transferência de risco

*Extensão do framework "Dinâmica Estrutural de Capital, Risco e Decisão". Aqui o nó abstrato `Risk Transfer` ganha unidades observáveis, e a pergunta "quem paga quem, e por quê" ganha uma resposta concreta e falsificável.*

---

## 0. Por que os greeks completam o framework

No framework original, `Participant_A → Risk → Participant_B` era verdadeiro mas **não-mensurável**. Uma opção resolve isso: ela é um contrato que **decompõe e transfere riscos específicos**, e os greeks são as **unidades** dessa transferência. Vender uma call não é "estar bearish" (`ObservedAction ≠ Intent`) — é **estocar risco de gamma/vega curto por um aluguel (theta)** e depois neutralizar o delta. Os greeks são a linguagem intermediária que o ponto 8 pedia: `Conceito humano → estrutura matemática`.

E, crucialmente, eles nomeiam o **mecanismo econômico** que o framework não tinha: o **prêmio de risco de volatilidade (VRP)** — a demanda persistente e inelástica por seguro (gestores comprando puts de índice) contra uma oferta limitada de capacidade de absorver risco. É um dos poucos "porquês" reais e documentados acessíveis a um player pequeno.

---

## 1. Delta (Δ) — exposição direcional e o laço de feedback observável

**Definição:** `Δ = ∂V/∂S` — sensibilidade do valor da opção ao preço do ativo. *Delta hedging* = neutralizar essa direção negociando o ativo subjacente.

**Ligação com o framework — este é o `Market State → New Risk → New Action` feito mecânica observável.** Dealers que vendem opções ficam com delta residual e o *hedgeiam* comprando/vendendo o subjacente. Esse fluxo de hedge **é** um dos "forces" do seu `S_{t+1} = F(...)`:

- **Dealer com gamma curto** → hedge *desestabilizador* (compra na alta, vende na baixa) → amplifica o movimento.
- **Dealer com gamma longo** → hedge *estabilizador* (vende na alta, compra na baixa) → amortece o movimento.

Isso é o "pressão / suporte / rompimento" do ponto 8 revelado como o que realmente é em muitos casos: **fluxo de delta-hedging de dealer**. É estrutura mensurável, não narrativa.

---

## 2. Gamma (Γ) — convexidade e por que o hedge nunca termina

**Definição:** `Γ = ∂Δ/∂S = ∂²V/∂S²` — a velocidade com que o delta muda. É a **não-linearidade** que gera o laço de feedback.

**Quem absorve o risco (o coração da transferência):** quem está **short gamma** recebe theta mas sofre perda **convexa** em movimentos grandes. Essa é a resposta literal ao "quem paga quem": o vendedor de opção é **pago (prêmio) para absorver risco de cauda/gamma**. Um player pequeno *pode* ocupar esse lado — é a vantagem estrutural de "ser o absorvedor natural de risco" — mas ao custo de uma cauda esquerda gorda.

**Dado que operacionaliza isso:** o gamma agregado dos dealers (**GEX**, inferido do open interest de opções) aproxima onde o mercado está estável vs. instável. É exatamente o "upgrade de posicionamento" — sai do plano filosófico e vira sinal medível (com ressalvas na §6).

---

## 3. Vega (ν) — exposição ao *preço do risco*, não ao preço

**Definição:** `ν = ∂V/∂σ` — sensibilidade à volatilidade implícita.

**Ligação mais profunda com o framework:** a volatilidade implícita **é uma variável de estado do mercado** (`R_t`) que codifica a demanda agregada por transferência de risco / seguro. Vega é exposição ao **preço da apólice**, não ao ativo. O **prêmio de risco de volatilidade** — o fato empírico de que, na média, `IV > RV` (implícita acima da realizada), sobretudo em puts de índice — é o prêmio estrutural mais documentado e acessível a tamanho pequeno. Ele existe por uma razão econômica persistente: **demanda inelástica por hedge de queda** vs. **oferta escassa de capital disposto a carregar esse risco**.

Isto é o edge de verdade que o framework buscava — e é *falsificável*: `VRP = IV − RV` é medível diariamente.

---

## 4. Theta (Θ) — o fluxo de caixa da transferência de risco

**Definição:** `Θ = ∂V/∂t` — decaimento temporal.

Para quem vende opção, theta é o **aluguel** que o hedger paga para transferir risco ao warehouse. É a manifestação de P&L de coletar o prêmio: posições short-gamma/short-vega **ganham theta** (são pagas para esperar) e **pagam em convexidade** quando o movimento vem. O jogo inteiro do VRP é: *renda de theta vs. perda de gamma/vega*. Positivo na média (o prêmio) — com uma cauda desagradável.

---

## 5. Mapa: cada grego → nó do framework original

| Grego | O que transfere | Nó do framework que torna mensurável |
|---|---|---|
| **Delta** | Direção | `Market State → New Risk → New Action` (fluxo de hedge do dealer) |
| **Gamma** | Convexidade / risco de movimento | `Risk Transfer` + `Survival` (quem absorve a cauda) |
| **Vega** | Preço do risco (IV) | `R_t` (regime) + o "porquê econômico" (VRP) |
| **Theta** | Tempo / carrego | `Cost` e o fluxo de caixa do prêmio |

E os nós `Cost` (C_risk, C_capital) deixam de ser abstratos: viram **margem, financiamento e o custo de convexidade do gamma**.

---

## 6. Freios honestos (a hipótese tem que sobreviver a isto)

1. **Infra que não temos.** Negociar greeks exige **dados de opções** (cadeias, superfície de IV) e **execução de opções** — hoje o sistema só tem OHLCV diário e broker de papel de ações. É um salto real: superfície de IV, modelagem de margem, risco de exercício/atribuição.
2. **VRP não é dinheiro grátis** — é *"catar moedas na frente do rolo compressor"*: Sharpe positivo quase sempre, cauda esquerda catastrófica (Volmageddon 2018, mar/2020). Vender vol é justamente onde o **backtest fica lindo até a cauda te liquidar**. Aqui o `Survival = f(Return, Drawdown, ...)` do seu ponto 5 deixa de ser teoria e vira questão de vida ou morte: sizing e gestão de cauda são *o* problema.
3. **GEX / gamma de dealer é aproximação de posicionamento**, com premissas fortes (sinal do dealer, atribuição de OI). Sinal útil, não verdade.
4. **Capacidade a favor.** Esses prêmios são *mais* acessíveis a tamanho pequeno (casa com a vantagem do player pequeno) — mas exigem hedge disciplinado e contínuo e margem, o que é pesado num PC só.

---

## 7. O primeiro entregável honesto (antes de vender uma única opção)

Não construir estratégia. **Medir o VRP e sua cauda.** Concretamente:

1. Ingerir dados de opções de índice (IV) e computar `IV − RV` ao longo do tempo.
2. Estimar a **distribuição** do prêmio: média, Sharpe, e — o que importa — a **cauda esquerda** (CVaR, pior mês, comportamento em choques de vol).
3. Só depois de entender a cauda, testar uma regra de venda de vol coberta/definida no *harness honesto* (embargo + Deflated Sharpe + custo), com sizing derivado da sobrevivência, não do retorno.

Se o VRP líquido de custo não sobreviver ao DSR e à cauda, a hipótese está errada — e é para isso que ela precisa continuar falsificável.

---

# Módulo de Fita — Order Flow e Footprint como a transferência de risco no *momento* em que ela acontece

*Se os greeks são a transferência de risco no nível da **decomposição** (opções), order flow e footprint são a mesma transferência no nível do **evento** (a fita). Aqui `Participant_A → Risk → Participant_B` vira literalmente visível: o **agressor** (quem cruza o spread) é quem paga para transferir risco AGORA; o **passivo** (limit order) é o warehouse pago pelo spread.*

## 8. Order Flow — o fluxo assinado pela agressão

**Definição:** a sequência de negócios executados, classificados por **iniciador**: compra agressiva (levanta o ask) vs. venda agressiva (bate no bid). Conceitos centrais:

- **Trade sign / classificação de agressor** (tick rule, Lee-Ready) — em cripto a corretora costuma **entregar o lado agressor de graça**, o que elimina o erro de estimativa (vantagem enorme, ver §11).
- **Order Flow Imbalance (OFI):** compra agressiva líquida − venda agressiva líquida. Preditor de curtíssimo prazo do preço via inventário (a literatura de microestrutura é sólida aqui).
- **Cumulative Volume Delta (CVD):** soma corrente do volume assinado. **Divergência** (preço sobe, CVD cai) = **absorção**: limit orders passivos engolindo a agressão. Isto é `ObservedAction ≠ Intent` tornado visível — vendedores agressivos sendo absorvidos por um comprador passivo grande.
- **Kyle's λ** (impacto de preço por unidade de fluxo) — **nós já temos um proxy disso nas features**. É o `C_liquidity` / `MC_risk = ∂C/∂Risk` do framework virando número: o **custo de mover risco**.

## 9. Footprint — order flow visualizado por nível de preço

**Definição:** uma vela que mostra o volume negociado em **cada preço** dentro da barra, separado por bid (venda agressora) × ask (compra agressora). Conceitos:

- **Delta por nível / delta da barra:** volume agressor líquido.
- **Point of Control (POC):** preço de volume máximo = onde mais risco trocou de mãos = ímã de "fair value".
- **Imbalances diagonais empilhados:** fluxo agressivo unilateral → suporte/resistência de onde houve absorção.
- **Absorção / exaustão nos extremos:** volume alto na mínima da barra sem continuação = vendedores absorvidos → sinal de reversão. É a assinatura observável de um player de grande capacidade (`K = f(Capital, RiskBudget, ...)`) fazendo transferência de risco.

## 10. Mapa: fita → nó do framework

| Conceito | O que revela | Nó do framework |
|---|---|---|
| **OFI / trade sign** | Quem inicia a transferência | `Participant_A → Risk → Participant_B` |
| **CVD / absorção** | Intenção real vs. ação aparente | `ObservedAction ≠ Intent` |
| **Kyle's λ / impacto** | Custo de mover risco | `C_liquidity`, `MC_risk` |
| **POC / footprint** | Onde o risco trocou de mãos | `Market State` |

## 11. Freios honestos (mais duros que os dos greeks)

1. **Exige dado tick com direção de agressor.** Nós temos **OHLCV diário — que tem ZERO informação de order flow.** Footprint é fundamentalmente intraday/tick. No stack atual, order flow/footprint é **não-implementável** sem um feed completamente diferente.
2. **É curto prazo e sensível a execução** — OFI prevê preço em segundos-minutos; capturar isso encosta no **edge de velocidade**, onde somos fracos (HFT/co-location ganham). Leitura de absorção em barras mais lentas é possível de forma discricionária, mas o edge estatístico limpo é jogo de quem é mais rápido.
3. **Absorção é subjetiva e fácil de ver no retrovisor** — o clássico "explica tudo depois". Só vale se virar regra precisa e passar no DSR; senão é a armadilha narrativa que já critiquei.
4. **A abertura real:** **cripto perpétuos.** Tick data + lado agressor de graça, 24/7, e um player pequeno consegue operar. É o nicho onde footprint é *de fato* acessível — e casa com a tese "o edge do pequeno está no nicho". O preço: horizonte curto e sensibilidade a execução.

---

# Nota final do analista — pare de acumular, comece a escolher

Você adicionou, em sequência: risco estrutural → greeks/VRP → order flow/footprint. **Cada um é bom. Juntos, viram o pecado original do framework: amplitude sem um mecanismo escolhido e testado.** Quanto mais linguagens de "transferência de risco" você empilha, mais elegante fica — e mais longe da falsificação, porque sempre haverá uma lente que "explica" o que aconteceu.

Os dois módulos puxam o projeto para **direções opostas**, e você vai ter que escolher UM:

- **VRP / greeks:** horizonte mais lento, prêmio estrutural documentado, **não precisa de velocidade** (a favor do pequeno), mas exige dado de opções + gestão de cauda implacável.
- **Order flow / footprint:** horizonte curto, sensível a execução, **encosta no edge de velocidade** (contra o pequeno) — mas unicamente acessível em **cripto perpétuos**.

Visão sênior: os dois são defensáveis, mas **num PC só, sem velocidade, o VRP é o mais coerente** — a menos que você vá especificamente para cripto perp, onde o footprint é a única versão que um pequeno consegue rodar. Escolher os dois é diluir os dois. A disciplina do framework não é adicionar a próxima lente — é **pegar uma, escrevê-la como regra precisa, e tentar matá-la no harness honesto (embargo + DSR + custo + cauda).** O que sobreviver, vale. O resto é filosofia bonita.
