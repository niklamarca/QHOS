# Hipótese SYMB-1 — Spec Falsificável (pré-registrada antes do forward)

*Primeira estrutura descoberta pelo Motor de Regressão Simbólica a sobreviver a todos os tribunais estatísticos. Graduada ao forward em 2026-08-11. Parâmetros CONGELADOS — qualquer mudança = novo teste, relógio zera.*

## 1. A regra (exata, congelada)

- Universo: SPY, QQQ, IWM, GLD, TLT.
- Por ativo: `posição = −sign( (r − σ63) × ((σ5 − r) + (σ5 − σ21)) )` onde r = retorno diário, σw = desvio-padrão rolante de w dias dos retornos. **Defasado 1 dia.**
- Portfólio: média das 5 pernas − custo 2 bps × turnover. Sem alavancagem, sem vol-target (a regra certificada não tinha).
- Interpretação: reversão diária condicionada ao regime de vol (compra quedas / vende altas, modulada por vol curta vs média). Comprado ~79% do tempo no regime recente.

## 2. Por que ela está aqui (o histórico completo dos tribunais)

| Tribunal | Resultado |
|---|---|
| Triagem + permutação (screen 400) | Sharpe 1,06 (custo honesto), piso do teste |
| Escalação 20.000 permutações | **venceu 20.000/20.000** → p = 5,0×10⁻⁵ < limiar-família 8,3×10⁻⁵ (Bonferroni, 300 testadas ×2) |
| Estabilidade (metades) | +1,06 / +1,07 |
| Stress de custo 3x (6 bps) | +0,55 (pesquisa) / +0,30 (holdout) |
| Por ativo | positivo nos 5 (0,27 a 0,82) |
| Por período (2004-22) | +1,12 / +0,96 / +1,09 — **sem decaimento** (contra a previsão do operador, concedida) |
| Breakeven de custo | 10,3 bps por unidade de turnover (margem 8 bps) |
| Jackknife | 0,94–1,11 sem qualquer ativo |
| **Holdout (1.093 dias nunca vistos, 2022-04→2026-08)** | **Sharpe +0,76, +45,4%** — passou o critério pré-registrado (≥0,42 e >0) |

## 2b. SEGUNDO HOLDOUT — 2002-07 a 2004-11 (adicionado 2026-08-12)

Descoberto que o histórico estendido continha uma janela **anterior** à pesquisa (que começa em 2004-11 por causa do GLD) e portanto **nunca vista pela busca**: 518 dias cobrindo o fim do bear pós-bolha e a recuperação de 2003. Critério pré-registrado antes de rodar: `Sharpe ≥ 0,42 e retorno > 0` (mesmo padrão de degradação 0,4×).

| | Sharpe | retorno | MDD |
|---|---|---|---|
| **SYMB-1** | **+1,88** | **+56,7%** | −11,4% |
| SPY buy&hold | +1,09 | +37,1% | −14,2% |

Por ano: 2003 — SPY +28,2% / SYMB-1 **+42,1%**; 2004 — SPY +8,1% / SYMB-1 +7,4%.

**PASSOU** — e desta vez **superando o SPY** em Sharpe, retorno e drawdown, num regime de mercado radicalmente diferente do da pesquisa. Somado ao primeiro holdout (2022-2026, Sharpe +0,76) e às 20.000 permutações vencidas, o SYMB-1 acumula: 2 holdouts independentes + permutação + autópsia por ativo/período/custo + jackknife.

**Ressalvas que continuam de pé:** universo do teste com 4 ativos (GLD ainda não existia); 518 dias é amostra modesta; a família Bonferroni segue local (300 estruturas desta máquina, não as ~12.000 da sessão); e — o essencial — **nada disso é forward.** No único holdout recente ele perdeu do SPY ajustado a risco (0,76 vs 0,88) e mostrou corr +0,70. Os portões da §4 não mudam.

## 2c. CORREÇÃO DE MÉTODO — o nulo estava errado (2026-08-12, tarde)

**Erro de quem escreveu esta spec, admitido:** as 20.000 permutações da §2 embaralhavam os retornos. Isso é um nulo de **drift zero**. Ações têm drift positivo. Uma estratégia comprada 76% do tempo bate um nulo sem drift *por construção* — não por conter informação. O `p = 5×10⁻⁵` respondia "isto é distinguível do acaso?" quando a pergunta que importa é **"isto é distinguível de simplesmente segurar a cesta?"**.

Descoberto ao auditar a geração 3 do motor simbólico, onde quatro "finalistas" distintos deram Sharpe idêntico (+0,89): todos eram `posição = +1 sempre` — o benchmark fantasiado de descoberta. O motor não tinha filtro de degenerescência. Agora tem.

### O que sobrevive ao nulo honesto

| Nulo | Resultado |
|---|---|
| Retornos embaralhados (drift zero) | venceu 20.000/20.000 — **teste fácil demais, descartado como evidência principal** |
| **Moeda enviesada** (mesmo viés comprado 76%, mesma taxa de troca 29,5%) | **0/2.000 a bateram · z = +4,34** — passou |
| **Segurar a cesta** (buy & hold 5 ativos, Sharpe +0,884) | **NÃO passou** |

Contra "segurar a cesta": retorno ativo +1,36%/ano com **t = +0,55**; com vol igualada **t = +0,84**; bootstrap em blocos (21d, 4.000 reamostras) da diferença de Sharpe **+0,172, IC95% [−0,286; +0,601], P(diferença ≤ 0) = 0,236**.

**Piso de seleção:** testadas 400 estruturas nesta máquina, o melhor *por puro acaso* exibiria vantagem esperada de ~**0,83** de Sharpe (√(2·ln 400) desvios × erro-padrão do Sharpe em 4.371 dias). A margem observada do SYMB-1 sobre o benchmark é **0,17** — **um quinto do piso de ruído da própria busca**.

### Leitura honesta

Sobrevive: há **informação de timing** no sinal (bate decisivamente a moeda de mesmo viés e mesma rotatividade, z = +4,34) e há **redução real de drawdown** (−22,6% vs −31,5%). Ambos são fatos.

Não sobrevive: a alegação de que **bate comprar e segurar**. Não está estabelecida — e o tamanho da vantagem está abaixo do que a própria seleção produziria por acaso.

**Os portões da §4 NÃO mudam** — foram pré-registrados e não se movem depois de ver o resultado. O que muda é o *peso da evidência prévia*: o SYMB-1 entra no forward como candidato **muito menos credenciado** do que a §2 fazia parecer. O forward continua sendo o juiz; ele apenas ficou mais importante, não menos.

---

## 3. A fraqueza conhecida (registrada ANTES do forward — a régua que decide)

No holdout, revelou identidade **long-equity com timing** (79% comprado, corr +0,70 com SPY) e **perdeu do SPY ajustado a risco** (0,76 vs 0,88); combo 50/50 ≈ SPY sozinho. Em 2022 (bear) foi levemente negativo — **não é defensivo**. Estatística certificou a anomalia de timing; **valor de portfólio segue não-provado.** É exatamente o que o forward vai julgar.

Ressalvas adicionais: família Bonferroni é local (300 estruturas desta máquina; a seleção global da sessão é ~12.000+); anomalia da classe "reversão diária de índice", documentada e sabidamente sensível a custo.

## 4. Portões de morte (pré-registrados, decididos agora)

Após **≥ 6 meses** de forward contínuo, SYMB-1 **morre** se:
1. **não** bater o SPY ajustado a risco no período **E** **não** melhorar o Sharpe do combo 50/50 vs SPY sozinho (falhar nos dois = sem valor);
2. MDD próprio > 25%;
3. Sharpe forward < 0 sustentado;
4. correlação com SPY > 0,85 sustentada (virou o índice com custo a mais).

**Proibido** ajustar σ-janelas, universo, custo ou sinal durante o forward.

## 5. Implementação

Braço paper independente (`strategies/symb1_reversal.py`): ledger próprio (`symb1_nav.csv`), estado persistido, passo diário defasado, fora do GA/HRP. Quatro relógios na mesa: **GA · TREND · SPY · SYMB-1** — mesmo período, mesma régua, o forward decide.

---

## 6. TESTE DE SENSIBILIDADE A FONTE — o confound de dado, medido (2026-08-19)

*Motivado por uma decisão de compra: contratar dado point-in-time do Databento (adjustment factors, US$ 225/mês). Antes de pagar, mediu-se se o problema existe.*

**A suspeita, declarada antes do teste:** o SYMB-1 é reversão em retorno diário. Erro de fator de ajuste produz exatamente isso — um dia anômalo seguido de "reversão". A classe de defeito da fonte **coincide** com a classe de sinal da hipótese. A fonte atual (yfinance) ajusta retroativamente e não tem vintage. Enquanto a coincidência não fosse medida, não se podia descartar que parte do resultado fosse artefato de ajuste.

Ferramenta: `lab/symb1_source_sensitivity.py` (offline, não escreve no data_lake). Regra congelada, janela 2004-11-18 a 2026-08-19, 5.407 observações.

### 6.1 A estrutura depende da camada de ajuste?

| Tratamento | Sharpe | t | Retorno total | MDD |
|---|---|---|---|---|
| Disco (ajustado, em uso pelo braço) | **+0,977** | +4,53 | +973,3% | −22,3% |
| Yahoo ajustado, refetch de hoje | +0,978 | +4,53 | +975,3% | −22,3% |
| **Yahoo NÃO ajustado (preço bruto)** | **+0,878** | +4,07 | +733,2% | −22,2% |

**Delta do ajuste: −0,099 de Sharpe.** A estrutura sobrevive **sem nenhum ajuste**. O ajuste contribui ~0,10, não fabrica o sinal.

**Deriva retroativa da mesma fonte: +0,001.** Entre a ingestão e o refetch de hoje, o Yahoo não reajustou nada de material nestes 5 ETFs. O medo de "série que muda debaixo do backtest" não se materializou neste universo.

### 6.2 O lucro se concentra em dia de ex-dividendo?

Ex-date é exatamente onde erro de ajuste nasce. Contaminando o dia do ex-date **e o seguinte** (onde a "reversão" cairia):

- dias próximos de ex-date: 867 de 5.407 (**16,0%**);
- fatia do P&L total nesses dias: **17,5%**.

Proporcional. Removendo **todos** os dias de ex-date, o Sharpe vai de +0,977 para **+0,963**. O sinal não vive do ex-date.

### 6.3 Quanto erro de dado a estrutura tolera?

Injeção de erro de ajuste trimestral, sinal aleatório, 200 réplicas por magnitude:

| Erro por ex-date | Sharpe médio | p5 | p95 | Delta |
|---|---|---|---|---|
| 0,10% | +0,982 | +0,960 | +1,006 | +0,005 |
| 0,25% | +0,984 | +0,945 | +1,022 | +0,007 |
| 0,50% | +0,997 | +0,941 | +1,049 | +0,021 |
| 1,00% | +1,031 | +0,960 | +1,120 | +0,054 |

Erro de dado **infla** o Sharpe — coerente com a direção da suspeita (ruído fabrica reversão que a regra colhe). Mas a magnitude é uma ordem de grandeza pequena demais: mesmo 1% por ex-date move +0,054, contra a margem de **+0,17** sobre buy&hold. Dado impecável, se algo, reduziria ligeiramente o Sharpe medido.

### 6.4 Conclusão — e o que ela NÃO diz

**Diz:** a camada de ajuste da fonte não é o confound. A hipótese "o SYMB-1 é artefato de dado sujo" está **descartada** para este universo e esta janela. Consequência de negócio: contratar adjustment factors do Databento **não tem trabalho a fazer nesta hipótese**, e o argumento de "fundação de medição" que justificaria a compra cai por terra — medido de graça.

**Não diz** que o SYMB-1 tem edge. A margem sobre buy&hold segue **+0,17 contra piso de seleção +0,83** (§2c), e o holdout recente segue perdendo do SPY ajustado a risco. Este teste removeu **uma** explicação alternativa; não promoveu a hipótese. **Os portões da §4 não mudam.**

**Limites deste teste:** universo de 5 ETFs megalíquidos com dividendo trimestral bem documentado — o resultado não se estende a small caps, ADRs, ativos com split frequente ou empresas com evento societário complexo. Não testa survivorship (universo fixo por construção) nem mudança de composição. E mede *sensibilidade*, não *acurácia*: não prova que o yfinance está certo, prova que estar errado na magnitude plausível não moveria a conclusão.
