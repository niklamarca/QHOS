# Estado atual da arquitetura

Fonte humana resumida. Para números e estados atuais, consulte
`data_lake/governance/CURRENT_STATE.json`.

O QHOS é um runtime local de pesquisa quantitativa com dados públicos,
backtests, busca evolutiva, validação interna, observação shadow, execução paper,
controle de risco e observabilidade. Não é um sistema institucional, não executa
capital real e não demonstrou edge.

Fluxo vigente:

`raw + proveniência → visão finalizada → features → candidatos monoativo → backtest → testes internos → SHADOW → PAPER`

Plano de eventos em construção, ainda isolado do fluxo vigente:

`DBN nativo imutável → Parquet particionado → catálogo COMMITTED → futura camada silver`

O adaptador histórico Databento, o gate de custo e o event lake já existem,
e aguardam a chave da conta. O primeiro boot autenticado executa apenas um
probe shadow limitado; ainda não há dado Databento nem consumidor automático.

Regras de integridade:

- barra parcial pode ser armazenada, mas não consumida por pesquisa/execução;
- identidade de dados vem do conteúdo dos manifests ativos;
- identidade do DNA inclui sinal, regras, ativo, regimes, saída e alocação;
- candidato rejeitado não reproduz e não entra no arquivo de aprovados;
- regime informa `MODEL`, `BASELINE` ou `FALLBACK`;
- PAPER exige ledger SHADOW reconciliado e aprovação humana;
- dashboard, Discord e LLM recebem a mesma fonte factual;
- localhost é obrigatório enquanto não houver autenticação.
- evento bronze não altera a linhagem da pesquisa diária; somente um dataset
  derivado e promovido poderá entrar no fluxo científico.

Gargalo atual (revisado em 2026-08-19, fim do dia): até hoje era **ausência de
hipótese com vantagem demonstrada**. O CRYPTO-CARRY (abaixo) é a primeira a
sobreviver a uma bateria pré-registrada e **entrou em SHADOW no mesmo dia**
(`strategies/crypto_carry_shadow.py`, protocolo congelado em §E da spec:
funding ingerido com proveniência, relógio próprio de dia UTC, zero crédito
retroativo). O gargalo agora é **tempo de relógio**: ≥ 60 sessões UTC de shadow
antes de qualquer decisão, mais as três ressalvas de §D.1. Mais CPU continua
aumentando tentativas, não conhecimento.

A metade "qualidade dos dados" desse gargalo foi **medida e não se sustentou**
para o universo em uso. Ver `HIPOTESE_SYMB1_spec_falsificavel.md` §6: a estrutura
do SYMB-1 sobrevive sem nenhum ajuste de preço (Sharpe +0,88 contra +0,98), o
Yahoo não reajustou nada de material entre a ingestão e o refetch (+0,001), o
P&L não se concentra em ex-date (17,5% do lucro em 16,0% dos dias) e erro
injetado de até 1% por ex-date move o Sharpe em +0,054 — contra margem de +0,17
sobre buy&hold. Para 5 ETFs megalíquidos, a fonte não é o fator limitante.

Consequência: **contratar dado pago não está justificado hoje.** O argumento mais
forte a favor era "fundação de medição point-in-time"; ele foi falsificado de
graça. Nenhuma hipótese pré-registrada exige intradiário. Reavaliar somente se
existir spec falsificável nova que barra diária não possa testar.

Estado das hipóteses pré-registradas:

- **VRP**: `FALSIFIED` em 2026-08-19 pelo critério 2 da própria spec (não bate o
  SPY ajustado a risco em nenhum sizing; combo 50/50 dá +0,82 contra +0,87 do
  SPY sozinho; liquidação de ~28% no sizing recomendado). Não graduou ao
  forward. O gate de estrutura a termo, porém, funcionou: evitou 05/08/2024.
- **SYMB-1** e **TREND**: no forward, parâmetros congelados, 7 sessões de um
  mínimo de 60. Nada a interpretar ainda.
- **CRYPTO-IMBALANCE** (fluxo de agressor em klines 1h): `FALSIFIED` em
  2026-08-19 — nenhum dos 6 testes da família significativo ao α corrigido e a
  regra operável é negativa líquida de custos em todas as células. Ver
  `HIPOTESE_CRYPTO_spec_falsificavel.md` §D.2. Fluxo em resolução de kline não
  carrega informação; em tick/livro seria jogo de velocidade, que não é o nosso.
- **CRYPTO-CARRY** (funding de perpétuos, delta-neutro): **sobreviveu** à bateria
  pré-registrada em 2026-08-19 (+5,55%/ano líquido no agregado, t +3,16, corr
  ~0 com BTC e SPY) — a primeira hipótese desde o início do projeto a sobreviver
  a uma spec falsificável completa. Ver §D.1: as ressalvas (base de capital ~50%
  de eficiência derruba o agregado abaixo do proxy de caixa; risco de exchange
  não mensurável; prêmio concentrado em BTC/ETH) fazem parte do veredito.
  Sobreviver à medição ≠ operar. **Em SHADOW desde 2026-08-19** (§E da spec):
  braço `crypto_carry_shadow` observa funding liquidado por dia UTC, sem ordem
  e sem fill, ledger `crypto_carry_nav.csv`, visível em `evidence.tracks` e no
  dashboard. Graduação a PAPER (≥ 60 sessões + ressalvas de §D.1 + humano) e
  qualquer operação real (conta de futuros, dual spot+perp) seguem sendo
  decisão do operador.
- **BASIS** (cash-and-carry de trimestrais BTC/ETH): `FALSIFIED` em 2026-08-19
  pelo B-3 — |corr| do P&L com BTC = 0,342 (o produto não é delta-neutro).
  B-1 e B-2 passariam (+4,72%/ano, t +4,80). Ver `HIPOTESE_BASIS_spec_falsificavel.md` §6.
  Não foi a shadow.
- **FUNDING-DISPERSION** (spread Binance/Bybit/OKX): `FALSIFIED` em 2026-08-19
  pelo D-2 (+2,19%/ano líquido < 4% caixa) e amostra OKX de ~3 meses, abaixo
  da janela de 3 anos. Ver `HIPOTESE_FUNDING_DISPERSION_spec_falsificavel.md` §6.
- **FUNDING-EXTREME** (short quando z de funding > 2): `FALSIFIED` em 2026-08-19
  — IC não significativo, regra −13%/ano líquida. Ver
  `HIPOTESE_FUNDING_EXTREME_spec_falsificavel.md` §5.

A família "prêmio de alavancagem" está esgotada no que o dado público testa
hoje: um sobrevivente (funding carry, em shadow) e três mortes no mesmo dia.
O próximo passo mensurável exigiria spec nova **fora** dessa família, ou o
relógio do CARRY/SYMB-1/TREND. Não se reabre morta nem se afrouxa critério.

O filtro anti-overfitting está confirmado funcionando: das 236 candidatas
reprovadas nas duas últimas épocas, **202 morreram por não bater comprar-e-segurar
do próprio ativo** e nenhuma por erro do teste. O zero de aprovadas é resposta
honesta da pesquisa, não defeito do pipeline — e por isso o motor **não** deve ser
endurecido nem afrouxado.
