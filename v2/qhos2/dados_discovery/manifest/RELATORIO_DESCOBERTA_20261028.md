# Descoberta de datasets para backtest — relatório de 28/10/2026

**Fase:** só descoberta. Nada foi baixado, não criei conta em lugar nenhum e não fiz login.
**Fontes:** quatro pesquisas paralelas na web (WebSearch e WebFetch), todas consultadas em 2026-10-28.
**Catálogo:** `catalogo_datasets_20261028.csv` (para Excel) e `.json`, os dois nesta pasta. Cada linha traz URL, licença, período, *point-in-time*, uso recomendado, limitações e uma etiqueta epistêmica.
**Gerador:** script reproduzível guardado no scratchpad da sessão.

Escala de classificação (seção 5 do prompt):

| Classe | Significado |
|---|---|
| **P** | Pendente: fonte oficial identificada e documentada, sem inspeção dos arquivos |
| **C** | Experimental: origem ou cobertura incompleta |
| **D** | Rejeitado |

Nenhum dataset chega a **A** ou **B** sem aquisição e validação, e isso é proposital.

---

## A. Resumo executivo

| Item | Quantidade | Etiqueta |
|---|---|---|
| Datasets catalogados (inclui os rejeitados) | **123** | CALCULATED |
| P, pendentes de validação | 75 | CALCULATED |
| C, experimentais | 15 | CALCULATED |
| D, rejeitados (pagos, descontinuados, sem proveniência ou com termos proibitivos) | 33 | CALCULATED |
| Acesso gratuito declarado na fonte e não rejeitado | 70 | CALCULATED, sobre o que as páginas declaram (OBSERVED/INFERRED) |
| Baixados | **0** | OBSERVED |
| Validados | **0** | OBSERVED |
| Prontos para backtest | **0** | OBSERVED |

Por categoria (P / C / D):

| Categoria | P | C | D |
|---|---|---|---|
| A. Futuros | 5 | 4 | 9 |
| B. Ações | 7 | 3 | 7 |
| C. FX | 4 | 2 | 1 |
| D. Macro | 22 | 0 | 2 |
| E. Juros e vol | 9 | 1 | 4 |
| F. Opções e COT | 6 | 0 | 5 |
| G. Cripto | 9 | 2 | 3 |
| H. Microestrutura | 5 | 1 | 2 |
| I. Complementares | 8 | 2 | 0 |

### Principais lacunas: o que não existe de graça e de forma legal (INFERRED, a partir de buscas negativas)

1. **Histórico diário por contrato de futuros CME** (GC, SI, CL, NG, HG, PL, ES, NQ, ZN, SR3, 6E), com settlement, volume e OI por vencimento.
   - A EIA parou em 2024-04-05, e só cobria energia em posições relativas 1–4.
   - O FTP de settlements da CME foi desligado em 2024; o arquivo histórico foi para o DataMine, que é pago.
   - O CHRIS do Nasdaq Data Link devolve 404.
   - Esta é a lacuna mais grave para o projeto.
2. **Intraday, tick e MBO de futuros CME:** nenhuma fonte aberta. O projeto já tem dados próprios (H06, H07 e H08, descritos abaixo).
3. **Metais preciosos diários com licença limpa:** a LBMA exige licença IBA, o FRED removeu as séries em 2022, e World Bank e IMF são mensais e spot.
4. **Cadeias históricas de opções** (SPX/SPY/QQQ/GLD e opções sobre futuros): só existem pagas.
5. **Consenso econômico pré-divulgação:** não há fonte gratuita e legal, exceto o Focus do BCB para o Brasil. Também não há **DXY oficial** gratuito (o proxy é o DTWEXBGS do Fed, com cesta diferente).
6. **Ações deslistadas e eventos corporativos** com licença permissiva: não existem de graça, o que deixa viés de sobrevivência sem solução gratuita.

### Fontes próprias do projeto (INFERRED a partir dos registros do projeto; conferir no inventário do lote 0)

| ID | Fonte | Situação |
|---|---|---|
| **H06** | MBO bruto da Databento herdado do V1 | 13 arquivos, 1,59 GB, com SHA256SUMS em `~/qhos-v2-data/arquivo_v1/` |
| **H07** | Capturas Rithmic do agendador V2 (ESZ6) | A partir de 28/10 |
| **H08** | History plant da Rithmic | HYPOTHESIS: pode cobrir a lacuna 1 dentro da assinatura atual. Depende da pergunta de licença non-display que já vai à CME. |

---

## B. Registro de datasets

O registro completo e filtrável está no CSV. Abaixo, os 25 mais úteis para o universo ouro, NQ e ES, todos **P** salvo indicação.

| dataset_id | Fonte | Granularidade | Período declarado | Acesso | Licença (resumo) | Uso recomendado |
|---|---|---|---|---|---|---|
| H07_LOCAL_RITHMIC_CAPTURA | captura própria | eventos | 2026-10-28+ | local | non-display pendente com a CME | microestrutura de ES e régua para as outras fontes |
| H06_LOCAL_DATABENTO_MBO | arquivo V1 | MBO (ns) | 13 arquivos | local | termos da Databento da época | microestrutura CME |
| H08_RITHMIC_HISTORY_PLANT | Rithmic | tick/barras | UNKNOWN | R\|API | UNKNOWN | histórico por contrato de GC/NQ/ES (HYPOTHESIS) |
| D02_ALFRED | St. Louis Fed | vintages | ~2006+ | API com chave | termos FRED | macro *point-in-time* |
| D03_PHIL_RTDSM | Philly Fed | vintages | NV | arquivo | UNKNOWN | conferir o ALFRED |
| E01_UST_CURVE | U.S. Treasury | diário (~15:30 ET) | 1990+ (INF) | CSV | governamental | juros nominais e reais |
| E06_FRED_BREAKEVEN | FRED | diário | 2003+ (INF) | API | citação exigida | expectativa de inflação |
| E04_GSW_TIPS | Fed | diário | 1999+ | CSV | Fed | curva real; ver se está parada |
| C02_FRED_DTWEXBGS | Fed H.10 | diário | 2006+ | API | domínio público, citação | proxy do dólar (≠ DXY) |
| C01_FED_H10 | Fed | diário | NV | FRED | Fed | FX de referência |
| B01_CBOE_VOL_IDX | Cboe | diário | VIX 1990+, GVZ, OVX, VVIX… | CSV | só disclaimer | regime de vol, *term structure*, skew |
| F01_CFTC_COT_FILES | CFTC | semanal | 1986+ / 2006+ | ZIP anual | governamental | posicionamento em GC, ES, NQ |
| F02_CFTC_COT_API | CFTC | semanal | até 2026-10-20 | Socrata | CC0 (terceiros) | atualização semanal |
| D06_FOMC_CAL | Fed | eventos | histórico | página | Fed | eventos de política monetária |
| D04_BLS / D05_BEA | BLS / BEA | mensal/trim. | — | API | governamental | CPI, payroll, PIB, PCE (valores) |
| E08_NFCI / E09_STLFSI4 | Chicago / St. Louis Fed | semanal | 1971+ / NV | FRED | Fed | condições financeiras |
| A03_EIA_SPOT_ENERGIA | EIA | diário | 1986/1997+ | FRED | citação | petróleo e gás **spot** |
| I05_EIA_API | EIA | semanal | — | API com chave | reuso permitido | estoques de petróleo |
| I07_WGC_ETF | World Gold Council | semanal/mensal | NV | Excel | só uso pessoal | fluxos de ETFs de ouro |
| I09_GPR / I10_EPU | acadêmico | diário/mensal | 1900+/1985+ | arquivo | citação / CC BY 4.0 | risco geopolítico e incerteza |
| D07/D08/C05 BCB | Banco Central do Brasil | diário | 1984–1986+ | API | ODbL | Selic, Focus, PTAX |
| B03_SEC_FSDS | SEC | trimestral | 2009+ | ZIP | governamental | fundamentos PIT (data `filed`) |
| G01_BINANCE_VISION | Binance | trade a mensal | 2017–2020+ | bulk + SHA-256 | CC BY-NC-SA, **não comercial** | cripto, funding, OI |
| I03_TZDB | IANA | regras | 1970+ | arquivo | domínio público (INF) | fusos e DST |
| H01_NASDAQ_ITCH_SAMPLES | Nasdaq | mensagem | dias soltos | HTTP | sem licença visível | testar parser e book (são **ações**) |

---

## C. Confiabilidade das fontes

Evidências de proveniência relevantes, todas OBSERVED em 2026-10-28 na URL do catálogo, salvo indicação:

**Fontes que mudaram ou estão sendo desligadas**
- **CME:** o FTP `/pub/settle` foi descontinuado (avisos Chadv23-272 e Chadv24-068) e o histórico migrou para o DataMine, que é pago. Os fetches em cmegroup.com deram timeout, então o que se sabe da CME vem de trechos de busca e do wiki público (INFERRED).
- **Fed:** o Data Download Program (DDP) deixa de montar pacotes na semana de 09/11/2026 e será aposentado. A ingestão de H.10 e H.15 deve passar pelo **FRED**.
- **IMF:** o IFS foi desmembrado em 2025-03-24 e os códigos antigos não resolvem mais (INFERRED).

**Restrições de licença e de histórico**
- **ICE BofA OAS no FRED:** desde abril de 2026, só 3 anos de histórico, e a reprodução exige permissão escrita da ICE. Rejeitado.
- **Yahoo e yfinance:** os termos proíbem coleta automatizada sem permissão prévia. Rejeitado; serve no máximo para conferência manual.
- **LBMA:** licença IBA obrigatória. Rejeitado.
- **Licenças não comerciais:** Binance Vision e Coin Metrics são **CC BY-NC**. Valem para pesquisa, não para uso comercial, e a Binance também proíbe uso em trading ao vivo.

**Divergências de metodologia entre fontes**
- **DXY:** o DTWEXBGS do Fed tem outra cesta e outros pesos. É proxy, não equivalente.
- **Treasury par curve:** muda de método em 2021-12-06 (monotone convex).
- **VIX:** antes de 2003 a metodologia era a do VXO. VIX9D, VIX3M, VIX6M e SKEW têm trechos retroprojetados.
- **COT Disaggregated e TFF:** 2006–2009/2010 é retroprojeção, não dado publicado na época.

**Conflitos ainda não resolvidos (HYPOTHESIS)**
- Data final da EIA NG: 05/04/2024 na EIA, 25/04/2024 numa fonte secundária.
- Licença da API do NY Fed para SOFR/EFFR.
- Rate limit da OECD: 60/h na página oficial, 20/h num espelho.
- Datas de "Last Update" das curvas GSW, que sugerem que as séries podem estar paradas.

**Ainda não feito:** nenhuma comparação numérica entre fontes. Isso depende da aquisição. Os pares previstos estão na seção I.

---

## D. Qualidade dos dados (achados preliminares, sem aquisição)

Os itens abaixo vêm só de inspeção de cabeçalho ou de páginas. São candidatos à quarentena, não conclusões.

| Dataset | Achado | Etiqueta |
|---|---|---|
| B01 VVIX | 2ª linha (2006-03-15) = 15,71, depois de 71,73 na 1ª, com intervalo de 06/03 a 15/03 | OBSERVED (cabeçalho do CSV) |
| B01 VIX6M | Início de 2008 com O=H=L=C, sinal de série só de fechamento retroprojetada | OBSERVED |
| F01/F02 COT | **Nenhum campo de data de divulgação.** Exige um calendário próprio. Usar a terça cria look-ahead de cerca de 3 dias (detalhe na seção F) | OBSERVED (81 campos do TFF) |
| F08 FINRA short volume | Correções saem como arquivo "Updated" em dia posterior, então é preciso versionar | OBSERVED |
| G01 Binance | Timestamps do spot passam a microssegundos em 2025-01-01. Arquivos de 2020 foram regravados em 2026-03-18 | OBSERVED |
| C06 HistData | Só bid, fuso UTC-5 fixo sem DST, origem não nomeada | OBSERVED |
| I10 EPU, E08 NFCI, I05 EIA | O histórico é revisado. O valor atual não é o valor que se conhecia na data | OBSERVED |
| B04 SEC frames | Usa o filing mais recente, o que gera look-ahead. Para PIT, usar FSDS com `filed` | OBSERVED |

**Testes previstos, a rodar no VALIDATED depois da aquisição:**
- **Estrutura:** schema e tipos, ordenação, duplicatas exatas e conflitantes, nulos e infinitos, encoding.
- **Tempo:** gaps contra calendário com feriados (via `exchange_calendars`, validado contra a bolsa), fuso e DST pelo tzdb, datas futuras.
- **Finanças:** H ≥ max(O, C), L ≤ min(O, C), H ≥ L, tick size, volume e OI ≥ 0, retornos extremos sinalizados e **não** removidos.
- **Estatística:** valores constantes por longos períodos, saltos de escala, quebras de metodologia já conhecidas (lista acima).

---

## E. Mapa de pesquisa entre ativos

Todas as relações abaixo são **HYPOTHESIS econômica**, nenhuma testada. A correlação será calculada sobre retornos ou diferenças, nunca sobre níveis, e só no período de sobreposição.

### Ouro (GC/MGC)

| Variável | Série gratuita | Frequência | Hipótese |
|---|---|---|---|
| Juros reais | E01 (real par), E04 (GSW TIPS), FRED DFII10 | diária | juro real ↑ → ouro ↓ |
| Dólar | C02 DTWEXBGS (proxy) | diária | dólar ↑ → ouro ↓ |
| Expectativa de inflação | E06 T10YIE | diária | breakeven ↑ → ouro ↑ |
| Expectativa do Fed | D06 (eventos), D02 (vintages); FedWatch pago | eventos | surpresas *hawkish* → ouro ↓ |
| Vol do ouro e do mercado | B01 GVZ, VIX | diária | regime de risco |
| Posicionamento | F01 COT GC (managed money) | semanal, válida a partir de sexta 15:30 ET | extremos de posição |
| Demanda via ETF | I07 WGC | semanal/mensal | fluxo |
| Geopolítica | I09 GPR | diária/mensal | choques |
| Prata, cobre, platina | **lacuna**: só mensal (A04, A05) | mensal | co-movimento de metais |
| Petróleo | A03 spot WTI/Brent | diária | inflação e commodities |

### NQ e ES

| Variável | Série gratuita | Frequência |
|---|---|---|
| Juros nominais e curva | E01, E02 | diária |
| Dólar | C02 | diária |
| Vol | B01 VIX, VIX9D, VIX3M, VVIX, SKEW; B02 VXN (NV) | diária |
| Condições financeiras | E08 NFCI, E09 STLFSI4 | semanal |
| Posicionamento | F01 COT TFF (ES, NQ) | semanal |
| Macro com PIT | D02 ALFRED, D04, D05 | eventos |

### Armadilhas de sincronização (INFERRED a partir das fontes)

Os "fechamentos diários" das séries acima acontecem em horários diferentes:

| Série | Horário de referência |
|---|---|
| Treasury | bid por volta das 15:30 ET |
| Cboe | fechamento por volta das 16:15 ET |
| BCE | fixing às 14:10 CET |
| Settlement COMEX GC | 13:30 ET (INFERRED) |
| EIA NYMEX | 14:30 ET |

Por isso nenhuma relação intradiária pode ser afirmada com essas séries diárias, e as correlações diárias contemporâneas exigem defasagem de segurança.

**Para cada par:** correlação contemporânea e defasada, correlação móvel, beta, estabilidade por regime (com quebras em 2008, 2020 e 2022), sensibilidade a outliers e teste de cointegração quando a hipótese for de nível.

---

## F. Prontidão para backtest

Situação atual: **nenhum dataset está BACKTEST_READY**, porque nada foi adquirido. A tabela diz o que cada um sustentaria depois de validado.

| Tipo de backtest | Datasets que sustentam | Datasets que NÃO sustentam |
|---|---|---|
| Diário e de regime | B01, E01, E06, C02, F01 (com calendário de divulgação), A03 (spot) | A01/A02 (param em 2024), A04/A05 (mensais) |
| Futuros diários por contrato (P&L real com rolagem) | **nenhum gratuito**; candidatos: H08 (HYPOTHESIS) e H06 | Yahoo, Stooq e contínuos sintéticos (rejeitados) |
| Intraday de futuros | H06, H07, H08 (se confirmados) | qualquer série diária |
| Estudo de eventos macro | D02 ALFRED, D03, D06, D08 Focus, D15 ECB RTD | D01 FRED "hoje", D04/D05 sem vintages, D_R02 consenso |
| Execução e microestrutura | H06/H07 (futuros), H01/H02 (ações) | C06/C07 (bid de dealer único), OHLCV de qualquer fonte |
| Opções | nenhum | F04/F05 (pagos), F06 (sem proveniência) |
| Cripto (não comercial) | G01, G03, G08 | G11 (agregado, 365 dias) |

**Regras obrigatórias para qualquer backtest com estes dados:**
1. Separar cronologicamente treino, validação e teste, com walk-forward.
2. Usar os dados só a partir da data de divulgação. Vale em especial para COT (divulgado na sexta, não na terça), macro (ALFRED) e fundamentos (`filed`).
3. Nunca executar no fechamento da própria barra.
4. Incluir custos de rolagem, spread e slippage.
5. Preservar as crises.
6. Controlar testes múltiplos.

---

## G. Manifesto de aquisição

Ainda não há aquisição, e os arquivos de RAW estão vazios. O plano por lote, com URLs, tamanhos estimados, licenças e comandos, está em `PLANO_AQUISICAO_20261028.md`. Cada lote só roda com aprovação do autor. A trilha de proveniência (URL, data e hora UTC, SHA-256, tamanho e versão) será gravada em `MANIFEST/aquisicoes.jsonl` no momento da aquisição.

---

## H. Rejeições

São 33 itens D no CSV, cada um com o motivo. Agrupados:

| Motivo | Itens |
|---|---|
| Pagos | CME DataMine, Cboe DataShop, IvyDB, CRSP, ICE DXY, LBMA, Tardis, FedWatch API, PMI, opções sobre futuros, CME BTC |
| Termos proibitivos ou dados indicativos | Yahoo e yfinance, Investing.com, FRED SP500 (pré-aprovação S&P), ICE BofA (reprodução proibida) |
| Descontinuados ou fora do ar | CHRIS (404), WIKI (congelado em 2018), FTP de settlements da CME, Oxford-Man (DNS), Deutsche Börse PDS |
| Sem proveniência | philippdubach/options-data (404), Kaggle futuros, GEX histórico |
| Insuficientes (cota ou histórico) | Alpha Vantage, EODHD, Massive Basic, CoinGecko Demo, FirstRate |
| Decisão do projeto | **A11 créditos Databento**: o autor apagou a conta em 09/10 e decidiu sair da Databento |

---

## I. Próximas ações (em ordem de valor)

1. **Lote 0, inventário só de leitura das fontes próprias** (H06 e H07): símbolos, datas e conferência do SHA256SUMS. Rodar fora da janela de captura.
2. **Lote 1, oficiais sem cadastro:** Cboe vol, Treasury, COT, BCB e GSW. É pequeno; ver o plano.
3. **Chaves gratuitas:** o autor cria as de FRED/ALFRED, EIA e BLS v2 (eu não crio contas). Elas ficam no Gerenciador de Credenciais do Windows, nunca em texto puro.
4. **Calendário de divulgação do COT** (sexta 15:30 ET, com as exceções de feriado de 2026 e o shutdown de 2025) e **tabela de eventos FOMC e BLS com horário**: são pré-requisitos de qualquer estudo de evento.
5. **Perguntar à Rithmic** se o history plant entrega histórico diário e tick por contrato de GC, NQ e ES, e com que profundidade. Isso pode fechar a lacuna 1 sem custo novo. Já existe e-mail pendente à Rithmic; dá para incluir a pergunta nele.
6. **Verificações pendentes:** CSV do VXN, termos do Stooq, URLs atuais da CME (deram timeout hoje), licença do Pink Sheet, horário do statement do FOMC e se as curvas GSW estão paradas.
7. **Comparações entre fontes planejadas:**
   - Treasury par contra H.15 e FRED (mesmos vencimentos).
   - PTAX contra FRED DEXBZUS.
   - COT por arquivo contra COT pela API.
   - Binance contra Kraken e Coinbase (BTC/USD × BTC/USDT, com a diferença de cotação explícita).
   - ES da captura Rithmic contra o settlement CME do dia.
