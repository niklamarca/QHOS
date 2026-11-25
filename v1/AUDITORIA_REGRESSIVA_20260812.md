# Auditoria Regressiva Completa — QHOS

*Varredura de 73 arquivos / 14.739 linhas de Python, 18 dias de log, 67 parquets, 1,2 GB de estado. Executada em 2026-08-12, noite, com o sistema no ar.*

---

## Veredito executivo

O sistema **está estável e não tem bug que trave**. Tudo compila, nada quebra, o forward grava corretamente. O problema não é fragilidade — é que **partes que parecem sofisticadas não produzem informação**, e a mais importante delas invalida silenciosamente toda a pesquisa do braço GA.

Três achados são críticos no sentido estrito: se não forem corrigidos, o que o sistema "aprende" continuará sendo ruído com aparência de conhecimento.

| Severidade | Quantidade | Natureza |
|---|---|---|
| 🔴 Crítico | 3 | Invalidam a ciência |
| 🟠 Grave | 4 | Risco operacional real |
| 🟡 Médio | 6 | Dívida técnica e incoerência |
| ✅ Saudável | 8 | Não mexer |

---

## 🔴 CRÍTICO 1 — O sistema descarta 94% dos dados que ingere

**Onde:** `data/ingestion.py:73` → `get_ohlcv(..., periods: int = 500)` e linha 89 `return df.tail(periods)`.

O motor de features chama `get_ohlcv(asset)` **sem passar `periods`**. Resultado: toda feature é calculada sobre as últimas 500 barras, e depois de descartar o aquecimento das janelas sobra **487 linhas**.

```
ativo    features    bruto   % usado
BAC           487    13476      3.6%
AAPL          487    11507      4.2%
SPY           487     8441      5.8%
TOTAL      28.540  153.208     18.6%   (por ativo: ~4%)
```

**Consequência medida:** a população evolutiva é de 500 indivíduos (`config.evolution_population_size`), rodando 40+ gerações. Isso são **milhares de estratégias candidatas testadas contra 487 observações**. A razão candidatos:observações passa de 8:1.

Isso não é otimização — é **memorização de ruído**. E explica, sozinho, o achado mais antigo do projeto: o catálogo com Sharpe 5,25 que se decompôs em expectativa negativa por trade. Não foi azar; foi consequência aritmética de procurar demais em dados de menos.

Também explica por que o SPY foi baixado até 1993 e a pesquisa nunca viu nada antes de ~2024.

**Correção:** `get_ohlcv` deve receber `periods=None` (= tudo) quando chamado pelo motor de features. É uma linha. O impacto é o maior de toda esta auditoria.

---

## 🔴 CRÍTICO 2 — O Regime Engine é teatro caro

**Onde:** `regime/gpu_model.py`.

Três defeitos independentes, cada um suficiente para anular o módulo:

**(a) Vazamento total de rótulo.** O rótulo de treino vem de `_heuristic_regime(row)`, que é uma função determinística de `vol_regime`, `hurst_approx`, `z_score_20`, `mom_21` — **features que estão na própria entrada do modelo**. O Transformer é treinado para reproduzir um if-else que já recebeu de bandeja. Daí a `acc final: 1.000` no log, que não é aprendizado: é cópia. **Valor informacional sobre a heurística de 6 linhas: exatamente zero.**

**(b) Saída praticamente constante.** Em todo o histórico de log:

```
8045  Regime: mean_reversion    (98,5%)
 119  Regime: trend             ( 1,5%)
   0  vol_expansion
   0  liq_stress
```

Um classificador de 4 classes que emite uma classe 98,5% das vezes é uma constante com margem de erro. E o `PortfolioEngine` condiciona a seleção de estratégias nele (`Portfolio: 12/100 selecionadas (regime=mean_reversion)`) — ou seja, o condicionamento por regime é um **no-op**.

**(c) Ramo inalcançável.** 

```python
if abs(vol_regime) > 1.8: return 2   # vol_expansion
if abs(vol_regime) > 2.5: return 3   # liq_stress   <-- MORTO
```

Qualquer valor acima de 2,5 já satisfez 1,8 e retornou antes. A classe `liq_stress` **nunca pode ser produzida**. Isso não é opinião, é ordem de avaliação.

**Correção:** duas opções honestas — (i) apagar o Transformer e assumir a heurística, economizando GPU e complexidade; ou (ii) torná-lo preditivo de verdade, rotulando com o **futuro** (ex.: vol realizada dos próximos 21 dias, discretizada) em vez do presente. Só (ii) adiciona inteligência. Manter como está é pagar por um espelho.

*Nota: o erro `softmax() received tuple` (100 ocorrências) é histórico — 16-17/06. Já não ocorre.*

---

## 🔴 CRÍTICO 3 — O fitness "live" não discrimina estratégias

**Onde:** `data_lake/live_performance.json`, alimentado por `execution/signal_executor.py:330`.

```
16 estratégias | 4 séries DISTINTAS de retorno
   7 estratégias compartilham exatamente [-0.0205533214]
   5 estratégias compartilham exatamente [+0.0009688, -0.0023453, +0.0014802]
```

Sete estratégias diferentes registram o **mesmo retorno com 10 casas decimais**. Isso não é coincidência: elas estão recebendo a marcação do ativo/portfólio, não o resultado da própria decisão. Como esse número entra em `compute_fitness`, o componente "live" do fitness **não separa boas de ruins** — ele adiciona uma constante por grupo.

O `learning_context.jsonl` tem **12 observações de 1 única estratégia**. Precisa de ~30 por estratégia para qualquer t-stat significar algo. O Learning Engine está ligado mas praticamente sem dados.

**Correção:** atribuição por estratégia — o retorno registrado tem de ser o P&L da posição que *aquela* estratégia pediu, não a marcação do símbolo.

---

## 🟠 GRAVE 4 — 39 ativos órfãos com features de até 58 dias atrás

67 parquets no disco, **30 no config**. Os 39 restantes não são mais atualizados, mas **têm features calculadas e presentes no pipeline**:

```
AMD, ARKK, DBC, DIA, EEM, EFA, IEF, IVV, IWF, LQD, MDY, MTUM, SCHD, SLV,
SMH, SOXX, SPLV, TQQQ, TSLA, USMV, USO, UVXY, VGT, VIG, VNQ, VOO, VTI,
VTV, VXX, VYM, XBI, XLB, XLC, XLG, XLI, XLP, XLU, XLV, XLY

AMD   feature mais recente 2026-07-08 (35d)
ARKK  feature mais recente 2026-06-16 (57d)
DIA   feature mais recente 2026-06-15 (58d)
```

Se qualquer consumidor iterar por arquivo em vez de por config, está lendo preço de dois meses atrás como se fosse hoje.

**Correção:** ou reincluir no config, ou mover para `data_lake/raw/_arquivado/`. Não deixar no meio do caminho.

---

## 🟠 GRAVE 5 — O projeto não está em Git

```
NAO E REPO GIT — zero historico, zero rollback
```

1,2 GB de trabalho, meses de evolução, **nenhum ponto de restauração**. Um script mal escrito (como o `Edit` que truncava `.py` no NTFS) pode destruir trabalho sem recuperação. Já houve edições em arquivos de 65 KB nesta sessão sem rede de segurança.

**Correção:** `git init` + `.gitignore` (excluindo `data_lake/`, `logs/`, `.env`) + commit inicial. Quinze minutos, e o risco de perda catastrófica vai a zero.

---

## 🟠 GRAVE 6 — `.env` sem `.gitignore`

O `.env` contém a URL do webhook do Discord. Não há `.gitignore` protegendo-o. Se o item 5 for feito sem cuidado, o segredo entra no primeiro commit. Ordem correta: `.gitignore` **antes** de `git init`.

---

## 🟠 GRAVE 7 — 1,2 GB sem política de retenção

```
680 MB  data_lake/quant_hpc.db          (cresce sem limite)
259 MB  logs/                            (18 arquivos)
207 MB  data_lake/quant_hpc.db.legacy_20260622_232818   <- morto há 2 meses
 12 MB  data_lake/quant_hpc.db.wal.legacy_...           <- morto há 2 meses
```

`backtest_results` e `strategy_catalog` acumulam indefinidamente — **nenhum `DELETE`, nenhum `VACUUM`, nenhuma retenção** em todo o código (só os logs têm, via loguru). O DB é 84× maior que os dados brutos que ele descreve.

**Correção imediata:** apagar os 219 MB de legado (parado desde 22/06). **Correção estrutural:** reter backtests só das últimas N gerações + estratégias do HOF.

---

## 🟡 MÉDIO 8 — Custos incoerentes entre backtest e forward

```
backtest : slippage 5bps + comissão 3bps = 8 bps
braços    : 2 bps
```

O backtest é **mais** conservador — direção certa, mas a incoerência significa que um Sharpe de backtest não é comparável a um Sharpe de forward. Deveria ser um único número, definido num lugar só.

## 🟡 MÉDIO 9 — 68 exceções engolidas em silêncio, 9 no caminho crítico

```
execution/execution_engine.py:124, 137, 207
execution/signal_executor.py:249, 341, 414
portfolio/constructor.py:154, 165
execution/paper_broker.py:242
```

`except Exception: pass` no caminho de execução significa que **uma ordem pode falhar sem deixar rastro**. As 22 do dashboard são aceitáveis (UI best-effort); estas 9 não são.

## 🟡 MÉDIO 10 — A saída do backtest é mais otimista que a entrada

`backtest/engine.py`: a entrada usa `signal_arr[i-1]` (defasada, correto), mas stop-loss/take-profit são **avaliados no fechamento da barra i e executados no fechamento da barra i**. Assimetria: a decisão de sair usa informação do mesmo instante em que executa. É aproximadamente realizável via ordem *market-on-close*, mas não é o mesmo padrão de rigor da entrada.

## 🟡 MÉDIO 11 — Alarme de concentração disparou 4.100 vezes sem ação

`Risk Breach: position_concentration` × 4.100, com **zero** rejeições de ordem correspondentes. Um alarme que dispara milhares de vezes sem consequência é ruído que treina o operador a ignorar alarmes. (Hoje as posições estão em 12,3% / 11,3% / 11,2% — o breach é histórico.)

## 🟡 MÉDIO 12 — O GA é chamado mais rápido do que consegue rodar

`run_generation já em execução — pulando chamada duplicada` × **833**. O orquestrador agenda evolução a cada 120 s; uma geração demora mais que isso. Não causa dano, mas polui o log e mascara falhas reais.

## 🟡 MÉDIO 13 — Arquivos mortos

`dashboard/_bloomberg.html` (24 KB, não servido), `dashboard/_hybrid.backup.html` (40 KB), comentários no `control_tower.py` ainda referenciando `_bloomberg.html`, 4 `.bak` de contaminação (podem ir depois de conferidos).

---

## ✅ O que está saudável — não mexer

1. **Todos os 73 arquivos compilam.** Zero erro de sintaxe.
2. **Zero look-ahead nas features.** Nenhum `rolling`/`ewm` sem defasagem correta.
3. **A entrada do backtest é corretamente defasada** (`signal_arr[i-1]`).
4. **Portão de mercado funcionando**, incluindo a recuperação de ingestão — validada em boot real hoje às 19:52 ET.
5. **Ledgers íntegros**: sem duplicatas, fechamento liquidado, aritmética consistente.
6. **Features limpas**: 95 colunas, 0 com >50% NaN, 0 constantes.
7. **Nenhum segredo hardcoded** no código.
8. **Timeouts de backtest irrelevantes**: 416 em ~695 mil (0,06%) — minha suspeita inicial estava errada.
9. **Governança gravando**: audit trail e journal com eventos reais e sequenciais.

---

## O que acrescentar à inteligência do sistema

Ordenado por **impacto ÷ esforço**. Os três primeiros valem mais que todo o resto somado.

### 1. Abrir a janela de dados (1 linha, impacto máximo)
`get_ohlcv(periods=None)` no motor de features. De 487 para 8.441 barras no SPY. Sozinho, muda a razão candidatos:observações de 8:1 para 0,5:1 — sai do território de memorização.

### 2. Validação cruzada purgada com embargo no GA
Hoje o GA usa split único. O padrão da área (López de Prado, *Advances in Financial ML*) é **Purged K-Fold com embargo**: as observações próximas à fronteira de teste são removidas para evitar vazamento por sobreposição de janelas. Com 8.441 barras isso passa a ser viável — com 487 não era.

### 3. Embutir o nulo "comprar e segurar" no fitness do GA
Já implementado no motor simbólico (v5, `margem_benchmark` + `piso_selecao`). O GA ainda pontua estratégias contra zero. Deveria pontuar contra **o benchmark**, com desconto pelo piso de seleção √(2·ln n). Sem isso o GA continuará promovendo buy-and-hold fantasiado — foi exatamente o que a geração 3 do motor simbólico produziu (quatro "descobertas" com Sharpe idêntico +0,89, todas `posição = +1 sempre`).

### 4. Regime preditivo em vez de tautológico
Rotular pelo **futuro** (vol realizada dos próximos 21 dias, ou drawdown dos próximos 63 dias, discretizados) em vez do presente. Aí a acurácia deixa de ser 1,000 e passa a significar alguma coisa. Se ficar em ~25% (aleatório para 4 classes), a resposta honesta é apagar o módulo.

### 5. Atribuição correta de P&L por estratégia
Sem isso, o fitness live é decorativo e o Learning Engine nunca acumulará dados discriminantes.

### 6. Meta-labeling
Em vez de o GA decidir **direção** (onde ele fracassou reiteradamente), usá-lo para decidir **tamanho** sobre um sinal primário já validado — por exemplo o TREND. É o uso do ML em que a literatura reporta os melhores resultados, porque a pergunta "quanto confiar neste sinal" tem muito mais dados de treino do que "para onde o mercado vai".

### 7. Git
Não adiciona inteligência, mas protege tudo que foi listado acima.

---

## Plano de ação sugerido

| # | Ação | Esforço | Risco de mexer |
|---|---|---|---|
| 1 | `.gitignore` + `git init` + commit | 15 min | nenhum |
| 2 | Apagar 219 MB de legado de 22/06 | 2 min | nenhum |
| 3 | Arquivar os 39 ativos órfãos | 10 min | nenhum |
| 4 | `periods=None` no motor de features | 1 min | **alto** — muda tudo que o GA vê |
| 5 | Nulo buy&hold + piso de seleção no fitness do GA | 1 h | médio |
| 6 | Logar os 9 `except: pass` do caminho crítico | 30 min | baixo |
| 7 | Unificar custos num só lugar | 20 min | baixo |
| 8 | Decidir o destino do Regime Engine | — | decisão sua |
| 9 | Atribuição de P&L por estratégia | 2 h | médio |
| 10 | Purged K-Fold no GA | 3 h | médio |

**Atenção ao item 4.** Ele é o mais valioso e o mais perigoso: ao mudar o que o GA enxerga, todo o Hall of Fame atual passa a ter sido evoluído sob outro regime de dados. O correto é **descartar o HOF e recomeçar a evolução do zero** — o que também zera o relógio do braço GA no forward. Os braços TREND, SYMB-1 e SPY não são afetados (têm ledger próprio e não dependem do GA).

---

## A conclusão que importa

Nada nesta auditoria cria edge. O sistema tem **muito mais maquinaria do que evidência**: um Transformer que copia um if-else, um GA que testa milhares de hipóteses contra dois anos de dados, um Learning Engine com 12 observações, um Regime Engine que emite uma constante.

Consertar os três críticos não vai fazer o sistema ganhar dinheiro. Vai fazer com que, quando ele disser que aprendeu algo, isso tenha chance de ser verdade. Que é a única coisa que este projeto se propôs a garantir.

E vale registrar o que a auditoria **não** encontrou: nenhum look-ahead nas features, nenhuma defasagem faltando na entrada do backtest, nenhum ledger corrompido, nenhum segredo vazado. As partes que foram construídas com desconfiança deliberada resistiram à inspeção. As que foram construídas por entusiasmo — o Transformer, o GA de 500 indivíduos, o Learning Engine — foram as que caíram.


---

# ADENDO — Correções aplicadas na mesma noite (2026-08-12, 22h)

**11 dos 13 achados corrigidos.** Git pulado a pedido do operador; CRIT3 parcial (explicado abaixo).

| # | O que mudou | Arquivo |
|---|---|---|
| CRIT1 | `get_ohlcv(periods=None)` = histórico completo. SPY 487 → 8.441 barras; BAC → 13.476. Razão candidatos:observações de 8,2:1 → ~0,5:1 | `data/ingestion.py`, `data/features.py` |
| CRIT1b | **Marcador de época de dados** (`full_history_v1`). Estratégias validadas sobre 487 barras não semeiam a nova evolução — o warm start é pulado uma vez. Nada foi apagado do banco | `evolution/genetic.py` |
| CRIT2 | Rótulo do regime vem do **futuro** (21 dias à frente), não das próprias features. Sequências construídas **por ativo** (antes atravessavam a fronteira entre ativos). Ramo morto eliminado. Split cronológico 80/20 e **veredito fora da amostra contra o baseline trivial** | `regime/gpu_model.py` |
| CRIT3 | Custo de giro descontado do fitness live (era cego a rotatividade) + procedência de cada marcação gravada em `live_marks_debug.jsonl` | `execution/signal_executor.py`, `core/live_performance.py` |
| GRAVE4 | 39 ativos órfãos movidos para `data_lake/_arquivado/` | — |
| GRAVE6 | `.gitignore` criado protegendo `.env` | — |
| GRAVE7 | 219 MB de legado em `_lixo_apagar_manualmente/` | — |
| MÉDIO8 | `config.cost_per_turnover` como fonte única — 8 bps em todo lugar | `core/config.py` |
| MÉDIO9 | 10 `except: pass` do caminho crítico agora logam a exceção suprimida | 4 arquivos |
| MÉDIO10 | Saída do backtest avaliada em `i-1` e executada em `i` — simétrica com a entrada | `backtest/engine.py` |
| MÉDIO11 | Alarme de concentração com throttle de 15 min | `risk/risk_engine.py` |
| MÉDIO12 | Evolução a cada 600 s (era 120 s, gerando 833 chamadas descartadas) | `orchestrator/hpc_orchestrator.py` |
| MÉDIO13 | `_bloomberg.html` e `_hybrid.backup.html` removidos | — |

## Ajustes de segunda ordem que a mudança exigiu

- **Timeout de features 60 s → 900 s** (`main.py` e job do orquestrador). Com 17-27× mais barras, o timeout antigo mataria o cálculo e o sistema subiria **sem features, degradado e em silêncio**. Medição real: ~1 s para as 157.594 barras — folga enorme, mas a margem é barata.
- **Timeout do kill switch mantido em 60 s.** Foi aumentado por engano junto com os demais e revertido no mesmo minuto. Liquidação de emergência não pode esperar 15 minutos. Comentário `NAO AUMENTAR` deixado no código.

## Por que o CRIT3 ficou parcial

A auditoria afirmou que sete estratégias com retorno idêntico (−2,0553%) provavam atribuição quebrada. **Fui verificar e nenhuma das duas hipóteses se sustentou:** esse valor não corresponde a variação de nenhum dos 67 ativos, em nenhuma janela de 1 a 15 pregões, comprado ou vendido. Não é "mesmo ativo, mesmo período" — e também não sei ainda o que é.

Consertar sem entender seria pior que não consertar. O que foi feito: (a) o defeito **certo** — cegueira a custo de giro — foi corrigido; (b) toda marcação agora grava ativo, preço de origem, preço de destino, posição e sinal em `live_marks_debug.jsonl`. Amanhã a origem do número aparece nos dados em vez de em especulação.

## Verificação

14 testes em memória, todos passaram: assinatura de `get_ohlcv`, cache respeitando `None`, quatro classes de regime alcançáveis (`{trend: 628, mean_reversion: 1833, vol_expansion: 150, liq_stress: 176}` no SPY), marcador de época, simetria do backtest, custo de giro, procedência, throttle, intervalo do GA, 10 `except` instrumentados, portão de mercado e guarda de barra liquidada intactos. **73/73 arquivos compilam.**

## O que esperar no primeiro boot

1. `EPOCA DE DADOS mudou (None -> full_history_v1): warm start PULADO` — esperado, uma vez só.
2. População 100% aleatória na primeira geração. **É o objetivo**: a evolução recomeça sobre 17× mais dados.
3. Features demorando mais para calcular na primeira passada.
4. `Regime FORA DA AMOSTRA: acc=X vs baseline Y -> ganho Z` — **este é o número que decide o destino do módulo.** Se o ganho for ≤ 0,01, o Transformer não carrega informação preditiva e o honesto é desligá-lo.

## O que NÃO mudou

Os ledgers do TREND, SYMB-1 e SPY, os portões pré-registrados das specs, o portão de horário de mercado e a guarda de barra liquidada. **A evidência do forward está intacta** — nada nesta noite tocou nos três relógios que carregam a única prova real do projeto.


---

# SEGUNDA PASSADA — 2026-08-14

*Áreas que a primeira auditoria não cobriu, verificação em produção dos consertos, e auto-auditoria do código que eu mesmo escrevi.*

## Verificado em produção (os consertos de 12-13/08 estão funcionando)

| Conserto | Evidência |
|---|---|
| Portão de perícia do regime | `conf=0.00` no log + `regime_skill.json {gain: -0.149}`. Segunda medição independente (ontem −0,136) — **o Transformer não tem perícia, reprodutivelmente** |
| Época de dados | `data_epoch.json` gravado, zero disparos repetidos |
| Telemetria de memória | 619 amostras, RSS 0,15–3,46 GB, sistema 39–51% |
| `kill_server.ps1` | `banco LIVRE - pode subir (persistencia garantida)` |
| Autostart | boot às 20:15 originado da tarefa; rede funcionou sob S4U |

---

## 🔴 NOVO — O Validator revalidava a mesma estratégia até 257 vezes

```
552 validações hoje | 12 estratégias DISTINTAS
  257x  multi_factor_6c9548
   95x  multi_factor_54f844
   88x  regime_filter_765263
```

`genetic.py:507` chama `validate_batch(elite[:20])` **a cada geração**, e a elite é estável por construção (é o ponto do elitismo). Cada validação roda walk-forward de 5 folds + teste por regime + injeção de ruído + **100 simulações Monte Carlo** — agora sobre 8.441 barras em vez de 487.

**97,8% de desperdício de CPU**, no recurso que faz falta para a evolução de verdade.

**Corrigido:** cache por `(fingerprint, época_de_dados)` em `validation/validator.py`. A época entra na chave de propósito — uma estratégia validada sobre 487 barras **não** está validada sobre 8.441 e precisa ser rejulgada uma vez.

## 🟡 NOVO — O `score` do Validator é uma constante disfarçada de métrica

`compute_score()` só é chamado depois de `result.passed = True`, que só ocorre se os 7 testes passarem (há `return` antecipado em cada falha). Logo o score **nunca pode ser diferente de 1,00**. Todo log de estratégia aprovada mostra `score=1.00` — parece nota de qualidade, carrega zero informação. Não é usado em nenhum outro lugar do sistema, então o dano é cosmético; mas é o mesmo tipo de ilusão do `acc=1.000` do Transformer.

## 🟡 NOVO — Taxa livre de risco de 5% fixa em 33 anos

`config.risk_free_rate = 0.05`. O engine subtrai isso do retorno antes do Sharpe, o que **reduz o Sharpe em 0,33 unidades** a 15% de vol. Dois problemas: (a) 5% constante é errado ao longo de 1993-2026 (as T-bills ficaram perto de zero em 2010-2021); (b) **os scripts de pesquisa em `lab/` calculam Sharpe SEM rf** — então o Sharpe do backtest e o Sharpe da pesquisa **não são comparáveis**, diferem sistematicamente por ~0,33. Mesma classe do problema de custo (8 bps vs 2 bps) já corrigido.

## 🔴 AUTO-AUDITORIA — bug meu, introduzido em 13/08

Ao trocar o dicionário Python por DataFrame compartilhado (para cortar 1,25 GB → 0,11 GB), deixei no worker:

```python
features.index = pd.to_datetime(features.index)   # MUTA o objeto COMPARTILHADO
```

O `ThreadPoolExecutor` compartilha memória: as 32 threads recebem **o mesmo objeto**. Funcionava porque o produtor já converte o índice antes de compartilhar — ou seja, **por acidente, não por desenho**. Se o índice chegasse em outro formato, todas as threads correriam para escrever no mesmo DataFrame.

**Corrigido:** o worker só usa o objeto direto quando já está pronto; se precisar converter, converte numa **cópia**.

## ✅ Verificado e correto

- **Sharpe**: `excess.mean()/vol*sqrt(252)` — fórmula padrão. Ressalva do rf acima.
- **Drawdown**: testado contra série de resposta conhecida (−50% exato) → correto.
- **Nenhuma operação in-place** no caminho do worker.

## ⏳ ABERTO — o braço GA não emitiu um único sinal desde o reset de época

176 leituras de sinal hoje, **todas 0,0**; zero ordens em 16 ciclos; `fills_cum` parado em 18 há 29 h. O `nav.csv` só se move por marcação a mercado de 3 posições legadas.

**Não declaro bug** porque o histórico mostra que o mecanismo funciona (32.065 leituras `signal=1.0` em sessões anteriores) e porque são apenas 16 ciclos com uma população de ~40 gerações sobre dados 17× maiores. Pode ser comportamento correto — estratégias evoluídas sobre 33 anos podem estar legitimamente fora do mercado no regime atual, já que a posição corrente é decidida na janela dos últimos 756 pregões.

**Teste que decide:** acompanhar por ~3 dias. Se continuar 100% flat com a evolução avançando, é regressão da mudança de janela de dados e precisa ser investigada no `_get_current_position_signal`. Se aparecerem sinais, era só o regime.


## Portfolio e Risco — auditado em 14/08/2026 (último módulo não coberto)

### ✅ HRP correto — testado contra propriedades conhecidas

| Teste | Esperado | Obtido |
|---|---|---|
| 4 ativos iid, mesma vol | pesos ~iguais (0,25) | desvio máximo **0,017** |
| 1 ativo com vol 5× | receber menos peso | **0,057** vs 0,314 dos outros |
| 2 pares redundantes | ~50% por **par**, não 25% por ativo | **0,535 / 0,465** |
| Soma dos pesos | 1,000000 | 1,000000 nos três casos |

O terceiro teste é o que realmente distingue HRP de vol-parity ingênua: alocar por **cluster**. Passou.

### ✅ Risk Engine correto — limites detectam e o kill switch aciona

`pre_trade_check`: aprova 10% e 49%, rejeita 51%, 200% e −80% (short avaliado pelo valor absoluto). `check_all` com kill switch injetado:

| Cenário | Kill switch |
|---|---|
| dia normal +0,5% | nenhum |
| perda diária −3% (limite 2%) | **L3** (flatten) |
| drawdown −13,6% (limite 10%) | **L3** |
| catástrofe −40% | **L3** |

*(No teste anterior o `kill_level` aparecia 0 porque eu não havia injetado o kill switch — artefato do teste, não do sistema.)*

### 🟡 O gatilho de 2% ao dia é agressivo e depende da exposição

Exceder 2% de perda diária é severidade 3 → **liquidação total**. A frequência disso depende de quanto da carteira está investido:

| Exposição | Movimento do SPY que dispara | Liquidações/ano (1993-2026) |
|---|---|---|
| 35% *(atual)* | −5,71% | **0,4** |
| 50% | −4,00% | 1,4 |
| 75% | −2,67% | 4,4 |
| **100%** | −2,00% | **10,1** |

Com 65% em caixa, o gatilho é praticamente inalcançável — precisa de um dia de −5,7% no SPY. **Mas se o portfólio chegar perto de 100% investido, o sistema passaria a liquidar tudo ~10 vezes por ano**, em dias de queda normal. Isso converte volatilidade rotineira em venda forçada no fundo — uma forma documentada de destruir retorno.

Não mexi no limite: ele é uma decisão de política de risco do operador, não de auditoria. Fica registrado que **o limite é seguro hoje pelo motivo errado** (excesso de caixa), não por calibragem.

---

## Encerramento da segunda passada

**Todos os módulos do sistema foram auditados.** Nada mais em aberto além do item de observação (braço GA sem sinal, decide em ~3 dias).

Estado dos braços após 14/08:

| Braço | Situação |
|---|---|
| GA | ativo, mas sem emitir sinal desde o reset de época — **em observação** |
| TREND | gravando |
| SYMB-1 | gravando |
| SPY | referência |
| ~~MANUAL~~ | **aposentado** — o operador não opera manualmente, não há decisão humana para medir |

**A consequência estratégica do fim do braço MANUAL:** todas as fontes restantes são **dado público analisado com método público**. Não existe, neste sistema, nenhuma vantagem informacional — por construção, não por defeito. Isso explica economicamente por que toda auditoria científica deu o mesmo resultado: GA morto, VRP morto, SYMB-1 abaixo do piso de seleção, TREND real mas sendo um prêmio de risco documentado desde 1880 e vendido empacotado por qualquer fundo de managed futures.


---

# TERCEIRA PASSADA — 2026-08-14, tarde

*Caça sistemática ao padrão que mais rendeu neste projeto: **valor decidido em dois lugares**.*

Três casos já haviam aparecido (custo 8 bps vs 2 bps; rf 5% vs 0%; e a janela de dados `periods=500` escondida num argumento default). Esta passada procurou o resto.

## 🔴 O `.env` sobrescreve 19 campos do `core/config.py` — em silêncio

Descoberto ao tentar corrigir a taxa livre de risco: editei `config.py` para `rf=0`, verifiquei, e o sistema continuou lendo **0,05**.

```
19 campos vem do .env, NAO do core/config.py:
  dashboard_port, evolution_mutation_rate, gpu_device, log_level,
  max_cpu_threads, max_daily_loss, max_portfolio_drawdown,
  max_position_size, risk_free_rate, system_mode  (+ chaves de API)
```

**Toda edição em `config.py` nesses campos é ignorada.** Quem lê o arquivo vê valores que não são os reais — inclusive os limites de risco. Na primeira auditoria eu li `max_position_size = 0.50` direto do `config.py` e reportei como verdade; só não deu errado porque o `.env` tinha o mesmo número. **Coincidência, não verificação.**

**Corrigido:** o valor foi ajustado no `.env` (onde de fato manda) e o boot agora **declara** quais parâmetros vieram de fora — nomes apenas, nunca valores, porque há chaves de API no mesmo arquivo:

```
[config] 19 parametros vem do .env, NAO do core/config.py: dashboard_port, ...
```

## 🟡 12 campos do config nunca são lidos por ninguém

De 70 campos definidos, **12 são inertes**. Dois importam:

**`evolution_max_generations = 100`** — a evolução **não tem teto**. Chegou à geração 88 hoje e continuou. O campo anuncia um limite que não existe.

**`data_history_years`** — nunca lido. Era o botão natural para controlar a janela de dados, exatamente o problema corrigido em 12/08. O campo existia; ninguém ligou nele; e o valor real ficava escondido num argumento default (`periods=500`). **A configuração que importava não estava na configuração.**

Os outros dez (`regime_retrain_days`, `regime_confidence_threshold`, `gpu_batch_size`, `data_assets_futures`, credenciais IBKR/Alpaca) são inertes sem dano. Todos ficaram documentados como tal no próprio `config.py`, para não enganarem a próxima leitura.

## Itens operacionais fechados nesta passada

| # | Correção |
|---|---|
| 1 | `QHOS_RESTART.bat` encerra a tarefa `QHOS-Server` **antes** do kill — era isso que criava dois servidores e derrubava o banco para RAM em 14/08 |
| 2 | **Retenção de 90 dias** no `backtest_results`. O `backtest_id` embute o timestamp, então `INSERT OR REPLACE` nunca substituía: ~34.600 linhas novas só hoje. Poda a cada 2.000 gravações |
| 3 | `rf = 0` como convenção única, ajustado no `.env`. A regra de decisão deste projeto é sempre "bater o SPY", nunca "bater o caixa" |
| 4 | Boot declara os campos sobrescritos pelo `.env` |

**Aviso sobre o item 3:** todo Sharpe já gravado no banco foi calculado com rf=5%; os novos virão ~0,33 maiores. **Não é melhora, é mudança de régua.** Não comparar os dois conjuntos.

## A lição que se repete

Cinco achados independentes, uma só forma: **algo parece estar decidido num lugar e na verdade é decidido em outro, sem aviso.**

- o custo estava em dois lugares com valores diferentes
- a taxa livre de risco estava em dois lugares com valores diferentes
- a janela de dados estava num argumento default, não na configuração
- 19 campos da configuração são sobrescritos por um arquivo que ninguém lê
- 12 campos da configuração não fazem nada

Nenhum deles quebrava o sistema. Todos faziam com que o sistema **medisse uma coisa e reportasse outra** — que é o único defeito que este projeto não pode tolerar, porque o produto dele é medição.


---

# TESTE 8 — a margem sobre comprar-e-segurar entra no GA (2026-08-14)

## O que faltava

Até hoje o GA pontuava contra **zero**. Uma estratégia que apenas ficava comprada num ativo que subiu era aprovada como descoberta. É a mesma falha que fez o motor simbólico produzir quatro "achados" com Sharpe idêntico +0,89 — todos eram `posição = +1 sempre`, o benchmark fantasiado. O motor simbólico ganhou o filtro em 12/08; **o GA não tinha**.

## O que foi implementado

Oitavo teste no `ValidationCluster`, com duas lâminas obrigatórias:

**(a) Bootstrap em blocos da diferença de Sharpe** contra buy & hold do próprio ativo. Blocos de 21 pregões, 800 reamostras — preserva autocorrelação, ao contrário do iid.

**(b) Piso de seleção** `√(2·ln n)·√(252/T)`: a vantagem que o melhor de *n* candidatos exibiria por puro acaso. Foi este piso que matou o SYMB-1 (margem +0,17 contra piso 0,83). `n` vem do tamanho do cache de validação — cresce conforme a busca avança, como deve.

Aprovação exige **P(margem ≤ 0) < 0,05 E margem ≥ piso**.

## Verificação com controles nos dois sentidos

| Série de teste | Margem | P(≤0) | Piso | Veredito |
|---|---|---|---|---|
| 6 estratégias aleatórias | −0,64 a −1,79 | 1,000 | — | **MORRE** (todas) |
| Idêntica ao buy & hold | +0,000 | 1,000 | 0,525 | **MORRE** |
| buy & hold + 2 bps/dia | +0,272 | 0,000 | 0,525 | **MORRE** |
| buy & hold + 8 bps/dia | +1,086 | 0,000 | 0,525 | **PASSA** |

O terceiro caso é o mais instrutivo: 2 bps por dia são ~5% de excesso ao ano — parece ótimo, mas com ~100 candidatos testados fica **abaixo do piso de ruído**. O teste recusa. É a resposta certa e conservadora.

**O teste é estrito e vai matar coisas que talvez fossem reais.** É a troca deliberada: dado o histórico deste projeto — Sharpe 5,25 que virou expectativa negativa, SYMB-1 que caiu sob o nulo correto —, errar para o lado de rejeitar é o único erro aceitável.

## Um no-op pego antes de entrar em produção

A primeira versão do Teste 8 procurava `result.returns`. Esse campo **não existe** no `BacktestResult` (tem `equity_curve`). O teste teria caído no ramo "sem série" e **aprovado tudo em silêncio** — exatamente a classe de defeito que esta auditoria vem eliminando. Corrigido antes do primeiro uso: agora deriva os retornos de `equity_curve`.

## O que NÃO foi feito, e por quê

**Purged K-Fold com embargo.** Estava na lista de recomendações da primeira auditoria e **não deve ser implementado**. Purging existe para quando se treina um modelo por fold e os rótulos vazam pela fronteira. Aqui as estratégias são regras **fixas**, avaliadas em janelas — não há ajuste por fold, e as features são todas retrospectivas. Adicionar purging seria sofisticação sem efeito: teatro estatístico.

E o embargo que importa **já existe e é respeitado**: `split_embargo` no validador reserva os últimos 20% do histórico, com auditoria final separada nesse held-out. Essa parte do sistema está bem construída.


## Recalibração do Teste 8 — o piso teórico estava ~20× rigoroso demais

Implementado o teste, fui medir se ele estava calibrado em vez de assumir. **Não estava.**

### O experimento

Gerar 900 estratégias aleatórias, selecionar a melhor de N **por Sharpe** (que é o que a evolução faz), medir a margem que ela exibe sobre o buy & hold **por puro acaso**, repetir 400 vezes, tirar o percentil 95.

| N testadas | piso EMPÍRICO (p95) | piso TEÓRICO √(2·ln N) |
|---|---|---|
| 10 | **−0,077** | +0,415 |
| 25 | **−0,063** | +0,491 |
| 50 | **−0,047** | +0,541 |
| 100 | **−0,029** | +0,587 |
| 200 | **−0,029** | +0,629 |

O viés de seleção **nesta estatística é praticamente zero** — a melhor-de-N por acaso nunca chega a bater o benchmark. O piso teórico pedia +0,42 a +0,63.

### Por que eu errei

`√(2·ln n)` corrige o **máximo de n Sharpes independentes contra um nulo zero**. A estatística do Teste 8 é a **diferença** contra buy & hold — e todos os candidatos são correlacionados com o próprio benchmark, então essa diferença quase não infla com a busca. Apliquei uma correção certa à estatística errada.

**Consequência se tivesse ficado:** o teste exigiria bater o índice por +0,6 de Sharpe — melhor que a maioria dos fundos profissionais. Rejeitaria qualquer edge real.

### A correção

Multiplicidade vai no **valor-p**, não no tamanho do efeito. Bonferroni sobre o p do bootstrap em blocos: `α = 0,05 / n`. Bootstrap subiu de 800 para 4.000 reamostras, porque Bonferroni exige resolução no p.

### Verificação depois da correção

| Série | Margem | P(≤0) | Limiar (n=100) | Veredito |
|---|---|---|---|---|
| Idêntica ao buy & hold | +0,000 | 1,0000 | 0,00050 | **morre** |
| buy & hold + 1 bp/dia (~2,5%/ano) | +0,139 | 0,0000 | 0,00050 | **PASSA** |
| buy & hold + 2 bps/dia (~5%/ano) | +0,278 | 0,0000 | 0,00050 | **PASSA** |
| buy & hold + 4 bps/dia (~10%/ano) | +0,555 | 0,0000 | 0,00050 | **PASSA** |
| buy & hold − 2 bps/dia | −0,278 | 1,0000 | 0,00050 | **morre** |

Antes da recalibração, o caso de **+2 bps/dia morria** (margem 0,272 < piso 0,525). Um edge real de 5% ao ano seria descartado. Agora passa, e o nulo e o negativo continuam morrendo.

**A lição é a mesma do resto da auditoria, aplicada a mim:** implementei um teste que *parecia* rigoroso, e só descobri que estava errado porque fui medir em vez de confiar na fórmula. Um piso de seleção mal calibrado não é conservadorismo — é um filtro que garante nunca encontrar nada, o que é indistinguível de não ter filtro nenhum.


## Bateria adversarial contra o Teste 8 — um furo encontrado e tapado

Depois de recalibrar, ataquei o teste em vez de confiar nele. Cinco ataques, um passou.

| Ataque | Sharpe | vol | Veredito original |
|---|---|---|---|
| A1 — caixa 99% + 40 acertos de +2% | +1,23 | 2,4% | morre |
| **A2 — caixa 94% + 400 acertos de +0,2%** | **+3,99** | **0,7%** | **PASSAVA — furo** |
| A3 — look-ahead (só os dias de alta) | +9,06 | 11,3% | passa (correto) |
| A4 — metade da exposição do índice | +0,60 | 9,1% | morre (correto) |
| A5 — um único dia de +60% | +0,19 | 11,6% | morre (correto) |

**O furo:** uma série que quase nunca arrisca nada e acumula ganhos minúsculos exibe Sharpe +3,99 e margem +3,385 sobre o benchmark. Passava folgado. Mas Sharpe a 0,7% de volatilidade é **economicamente vazio** — precisaria de ~20× de alavancagem para importar, e o edge não sobreviveria a custo nem a capacidade.

**A guarda:** volatilidade da estratégia ≥ 20% da volatilidade do benchmark (hoje: ≥ 3,6% contra os 18,2% do SPY). Abaixo disso o candidato é rejeitado antes do bootstrap.

### Estado final do Teste 8 — quatro propriedades verificadas

| Propriedade | Evidência |
|---|---|
| Rejeita ruído | 25 aleatórias, margem mediana −1,28 |
| Rejeita o próprio benchmark | idêntica ao buy & hold: margem +0,000, morre |
| Rejeita Sharpe alto sobre risco vazio | A1 e A2 barrados pela guarda de vol |
| **Aceita edge real modesto** | buy & hold + 1 bp/dia (~2,5%/ano): margem +0,139, **passa** |

O teste chegou aqui por três correções sucessivas — implementação, recalibração empírica, ataque adversarial. **Nenhuma delas veio de raciocinar sobre a fórmula; todas vieram de medir.**
