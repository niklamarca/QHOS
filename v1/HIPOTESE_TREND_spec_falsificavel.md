# Hipótese TREND — Spec Falsificável (pré-registrada ANTES do forward)

*Segunda hipótese com mecanismo nomeado. Escrita antes de o forward começar, para os critérios de morte não serem ajustados depois de ver o resultado.*

---

## 1. O mecanismo (por que existiria)

**Time-series momentum (TSMOM):** tendências persistem porque a informação se difunde devagar e o comportamento amplifica — sub-reação inicial (ancoragem, disposição a realizar ganhos cedo), depois herding; e fluxos institucionais (rebalanceamento, risk-parity, CTA) que se estendem no tempo. Documentado academicamente (Moskowitz-Ooi-Pedersen 2012) em décadas e dezenas de mercados.

**A alegação central NÃO é retorno absoluto.** A permutação (p=0,35 em 21 anos) mostrou que o alpha absoluto desta forma simples não se distingue do acaso. A alegação que sobreviveu ao teste justo é **estrutural de portfólio**: correlação ~0 com equity e **crisis alpha** (ganha quando o equity quebra devagar — 2008: +8,5% vs SPY −36,8%), porque num bear prolongado o trend vira short/defensivo. O produto é o **combo**: em 20 anos, 50/50 SPY+TREND deu Sharpe 0,83 vs 0,65 do SPY, com metade do drawdown (−27% vs −55%).

## 2. A regra (exata, sem graus de liberdade escondidos)

- Universo: SPY, TLT, GLD, IWM, QQQ (cross-asset, ETFs líquidos).
- Sinal por ativo: média dos sinais `sign(retorno de 63d)`, `sign(126d)`, `sign(252d)` — os lookbacks canônicos 3/6/12m, **sem tunar**.
- Posição: sinal × inverse-vol (0.01/σ63), cap ±0,5 por ativo. **Defasado 1 dia** (decisão de hoje usa dados até ontem).
- Portfólio: média das pernas − custo 2 bps × turnover.
- Vol-target 15% a.a. (escala pela vol realizada 63d do próprio portfólio, defasada, cap 3x).
- Long-short permitido (o sinal vira short em downtrend — é daí que vem o crisis alpha).


## 2c. AUDITORIA CONTRA O NULO CERTO (2026-08-12, tarde)

Mesma correção aplicada ao SYMB-1 (§2c daquela spec): o nulo deixa de ser "acaso" e passa a ser **o benchmark**. Janela 2006-02 → 2026-08, 5.150 dias.

| | Sharpe | ret/ano | MDD |
|---|---|---|---|
| SPY | +0,647 | 11,20% | −55,2% |
| TREND | +0,527 | 6,30% | −19,1% |
| **combo 50/50** | **+0,836** | 9,48% | **−27,2%** |

correlação TREND × SPY: **−0,010**

### O que NÃO passou

Melhora de Sharpe do combo sobre o SPY: **+0,189**, bootstrap em blocos (21d, 4.000 reamostras) **IC95% [−0,052; +0,409], P(≤0) = 0,062**. Não passa em 5%.

Removendo 2008-09: margem cai para **+0,081**, **P(≤0) = 0,276**. A vantagem de *retorno* depende materialmente da crise grande. Registrado.

### O que PASSOU

Nos **10 piores meses** do SPY, o TREND foi positivo em **9** (binomial contra moeda justa: **p = 0,011**), distribuídos por **cinco crises distintas** — 2008, 2009, 2018, 2020, 2022. Não é um evento de sorte replicado.

Drawdown: TREND −19,1% e combo −27,2% contra SPY −55,2%. Redução pela metade ao custo de 1,7pp/ano.

### Leitura

O TREND **falha numa alegação que nunca fez** (bater o índice) e **passa na que fez** (diversificar e proteger em crise). É o inverso do SYMB-1.

**Assimetria a favor do TREND:** é hipótese de **mecanismo** — janelas 3/6/12m fixas, sem busca — logo não carrega penalidade de seleção. O SYMB-1 emergiu de varredura sobre 400 estruturas e carrega piso de ruído de 0,83 de Sharpe.

**Ressalva contra o próprio resultado:** a redução de drawdown do combo não é descoberta, é aritmética de diversificação (correlação ~0 entre dois ativos de drift positivo produz isso mecanicamente). Isso é uma virtude, não um defeito — mecanismo é justamente o que se procurava —, mas não deve ser contado como evidência independente.

**Os portões da §4 não mudam.**

---


## 2d. TESTE FORA-DA-AMOSTRA — janela dot-com (2026-08-12, noite)

Descoberto que XLE/XLF/XLK existem desde 1998 e QQQ desde 1999 — dados que a janela de teste (2006-02 →) **nunca tocou**. Critério pré-registrado antes de rodar: (1) TREND positivo em ≥7 dos 10 piores meses do SPY; (2) correlação < 0,30; (3) MDD menor que o do SPY. Regra idêntica à congelada, **zero parâmetro ajustado**. Universo forçado a só ações/setores (TLT e GLD não existiam) — teste **mais duro**, pois remove a diversificação cross-asset.

Janela 2000-06-08 → 2006-02-21, 1.433 dias:

| | Sharpe | ret/ano | MDD |
|---|---|---|---|
| SPY | +0,046 | −0,86% | −47,3% |
| **TREND** | **+0,453** | +5,93% | **−21,6%** |
| combo 50/50 | +0,380 | +3,42% | −18,6% |

correlação TREND × SPY: **−0,279**

**TREND positivo em 10/10 dos piores meses do SPY** (p binomial = 0,001). Somando às duas janelas: **19 de 20**, sete crises distintas (2000-02, 2001, 2002, 2008, 2018, 2020, 2022), p combinado ≈ **2×10⁻⁵**. Os três critérios pré-registrados **PASSARAM**.

### O contexto que impede a comemoração

Isto **não é uma descoberta** — é uma replicação de literatura publicada e amplamente conhecida:

- Moskowitz, Ooi & Pedersen (2012), *Time Series Momentum*: momentum de série temporal em 58 instrumentos líquidos, retorno anormal com pouca exposição a fatores padrão, **melhor desempenho em mercados extremos**.
- Hurst, Ooi & Pedersen (2017), *A Century of Evidence on Trend-Following Investing*: a mesma estratégia construída até **1880**, lucrativa por 110 anos em **67 mercados**.

Ou seja: 110 anos × 67 mercados contra os meus 26 anos × 5 tickers. **O teste desta spec é uma nota de rodapé confirmando o trabalho deles**, não evidência nova.

**Consequências que precisam ser ditas:**

1. **Confiabilidade sobe muito.** Um resultado com um século de evidência independente é qualitativamente diferente de um Sharpe encontrado numa varredura.
2. **Edge informacional é zero.** Todo mundo sabe disso; fundos de managed futures vendem exatamente isto há décadas. Não há vantagem sobre outros participantes — há acesso a um prêmio de risco conhecido.
3. **Regime pós-crise é adverso.** A própria literatura registra que, em períodos prolongados após crises financeiras, o retorno médio do trend-following cai para **menos da metade** do obtido fora deles. Estamos nesse regime desde 2009.

**Enquadramento correto:** o TREND é **seguro barato**, não alfa. O valor dele na carteira é redução de drawdown (−21,6% vs −47,3% aqui; −19,1% vs −55,2% na outra janela), não retorno excedente — e isso é consistente com a §2c, onde a margem de retorno do combo não foi significativa (P=0,062).

**Os portões da §4 não mudam.** O forward continua com 2 dias.

---

## 3. Evidência inicial (medida em 2006–2026, 20 anos)

| | ret/ano | Sharpe | MDD | corr c/ SPY |
|---|---|---|---|---|
| TREND | +7,0% | 0,53 | −19% | **−0,01** |
| SPY | +12,5% | 0,65 | −55% | — |
| **50/50** | +9,7% | **0,83** | **−27%** | — |

Crisis alpha: 2008 +8,5% (SPY −37%); 2022 −8% (SPY −18%); custo do whipsaw: 2015 −9,5%.

**Caveats pré-registrados:** n≈3 crises independentes na amostra (o caso repousa muito em 2008); alpha absoluto NÃO provado (permutação p=0,35); custo modelado simples (2 bps); desconto de seleção se aplica (terceira hipótese testada — DSR cobra); momentum tem crowding crescente (CTAs) que pode degradar o prêmio.

## 4. Ciclo de vida

- **Vivo enquanto:** correlação com SPY permanecer baixa e o comportamento em stress for defensivo.
- **Invalidação ESTRUTURAL (mecanismo morto):** correlação forward com SPY persistentemente > 0,5 (virou beta disfarçado); ou whipsaw estrutural — mercados sem tendência por anos com o custo comendo tudo (regime de reversão permanente); ou crowding visível (drawdowns sincronizados de CTA sem crise no equity).
- **Invalidação EMPÍRICA (limiares de morte, decididos AGORA):** após ≥ 6 meses de forward contínuo:
  1. correlação com SPY > 0,3 sustentada → **morre** (não diversifica);
  2. MDD próprio > 20% → **morre** (pior que o histórico, algo errado);
  3. num drawdown de equity ≥ 10% no período, trend perder JUNTO na mesma magnitude → **morre** (sem crisis alpha, sem razão de existir);
  4. combo simulado 50/50 não melhorar o Sharpe do SPY sozinho no forward → **morre** (falhou no único teste que importa).
- **Proibido:** mexer em lookbacks/parâmetros durante o forward. Qualquer mudança = novo teste, zera o relógio.

## 5. Implementação no sistema (paper, ao lado — não dentro)

Módulo **independente** (`strategies/trend_following.py`): ledger próprio (`trend_nav.csv`), estado persistido, mark-to-market diário. **Não entra** no GA, no HRP nem no broker do sistema — comparação limpa de três braços: sistema GA vs TREND vs SPY, todos no mesmo relógio forward. O monitor/dashboard leem o ledger.

O veredito, como sempre, não é deste documento: é do forward + DSR daqui a meses. Este documento só garante que não vamos mover a régua.
