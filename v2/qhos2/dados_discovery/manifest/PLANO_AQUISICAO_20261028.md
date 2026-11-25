# Plano de aquisição por lote — 28/10/2026

**Regras deste plano:**
- Cada lote só roda com o "sim" do autor para aquele lote.
- Nada roda durante a janela de captura Rithmic (09:00–16:15 NY, 10:00–17:15 BRT em EDT) nem com uma suíte de testes em andamento.
- Tudo cai em `RAW/<fonte>/<AAAAMMDD>/`, no formato original e sem alteração.
- Cada arquivo gera uma linha em `MANIFEST/aquisicoes.jsonl` com: url, data e hora UTC, status HTTP, tamanho, SHA-256, `Last-Modified`/`ETag` e licença no momento da coleta.
- O que falhar vai para `QUARANTINE/` com o motivo.
- Tamanhos marcados com ~ são estimativas (INFERRED). Os reais entram no manifesto.

**Peça 1, antes de qualquer lote:** o script de aquisição `QHOS_DADOS/ferramentas/adquirir.py`.
- Dependências: só a biblioteca padrão do Python.
- Controle de carga: limite de requisições por fonte, 3 tentativas com espera exponencial, checkpoint para retomar, log.
- Chaves: lidas do Gerenciador de Credenciais do Windows ou de variável de ambiente, nunca de arquivo.
- Execução: com `python -I`.

A peça 1 é código novo e precisa de aprovação própria.

**Estado em 29/10 (autor aprovou "4. e 5." em 28/10 às 23:3x BRT):**
- Lote 0: FEITO (`inventario_lote0_20261028.md`).
- Peça 1: CONSTRUÍDA e revisada em 29/10 com a trava por item (12 testes, 4 mutações). Detalhes em `ferramentas/LEIA.md`.
- Lotes escritos por `ferramentas/lotes/gerar_lote1.py` e `gerar_lotes_2a5.py`; o cabeçalho de cada gerador diz o que foi conferido.
- Downloads feitos em 29/10 entre 02:45Z e 05:25Z, agendador em AGUARDANDO, tudo em `RAW/<fonte>/20261029/` e registrado em `MANIFEST/aquisicoes.jsonl` (6909 linhas):

| Lote | Resultado | Bytes |
|---|---|---|
| 1 | 200/200 ok | 131 MiB |
| 2 | NÃO RODADO: falta o autor criar as chaves FRED, EIA e BLS (`cmdkey /generic:QHOS_DADOS/<FONTE> /user:x /pass`) | — |
| 3 | 2079 ok, 33 falhas HTTP 403 | 663 MiB |
| 4 | 4584/4584 ok, todos com SHA-256 igual ao `.CHECKSUM` publicado | 767 MiB |
| 5 | 13/13 ok | 13 MiB |

- As 33 falhas do lote 3 são todas FINRA CNMS em feriados de bolsa dos EUA de 2022 em diante (Memorial Day, Juneteenth, 4 de julho, Labor Day, Thanksgiving, MLK, Presidents' Day e 09/01/2025). A FINRA não publica arquivo nesses dias. As datas vieram do `VIX_History.csv`, e esse é o achado: **o CSV da Cboe traz linhas com OHLC em feriados desde 2022** (OBSERVED; causa desconhecida). Os 33 registros ficam no manifesto e as notas em `QUARANTINE/finra/20261029/`; nada foi apagado.
- Fora de propósito: SEC FSDS (403 sem e-mail no User-Agent; a SEC exige contato declarado, decisão do autor) e WGC (só página HTML).
- **Peça 2 (validador) CONSTRUÍDA e RODADA em 29/10 10:06Z.** O relatório é `MANIFEST/validacao/validacao_20261029_20261029T100631Z.md`.
  - Integridade: 6876/6876 arquivos com SHA-256 igual ao manifesto.
  - Sem achados: Treasury, Focus, EPU (CSV diário), tzdb.
  - Principais achados:
    - **Binance spot:** o timestamp muda de ms para µs em 2025-01-01. Faltam 0,18% dos minutos.
    - **Binance metrics BTC:** de 01/09/2020 a 21/05/2021 (263 arquivos), cada linha aparece duas vezes, idêntica; são 75.255 linhas e nenhuma conflitante.
    - **CFTC:** o histórico financeiro 2006–2016 usa data 'MM/DD/AAAA hh:mm AM' numa coluna 'YYYY-MM-DD'. O legado é quinzenal até o início dos anos 1990.
    - **PTAX:** 10 dias repetidos e 16 cotações de compra acima da venda (1990).
    - **SGS 432:** traz datas futuras, a meta vigente até a próxima reunião do Copom.
    - **VIX:** 47 dias com OHLC inconsistente, de 1992 em diante, mais as linhas em feriados já registradas.
  - Não validados: XLS do GPR, valores dos XLSX do EPU, HTML do FOMC, blocos anuais do Fama-French.
- Próximos passos, cada um com "sim" próprio: (a) chaves do lote 2 e sua execução; (b) atualizar o catálogo (período/lacunas/método/hash) e propor a classe de cada dataset com base no relatório; (c) camada NORMALIZED (ex.: Binance em ms, linhas idênticas marcadas, datas CFTC em ISO), sempre sem tocar em RAW.

---

## Lote 0 — inventário das fontes próprias (só leitura, sem rede)

| Item | Onde | O que fazer |
|---|---|---|
| H06 MBO Databento | WSL `~/qhos-v2-data/arquivo_v1/` | `sha256sum -c SHA256SUMS`; listar símbolos, datas e tamanhos |
| H07 capturas Rithmic | WSL `~/qhos-v2-data/` | listar capturas, símbolos, janelas e tamanhos; não tocar no que o agendador está escrevendo |

- **Tamanho:** 0 bytes de rede.
- **Quando:** depois de 17:15 BRT, com o agendador em AGUARDANDO.

## Lote 1 — oficiais, sem cadastro, pequenos (~< 300 MB no total)

| dataset_id | URL | Tamanho | Licença |
|---|---|---|---|
| B01 Cboe vol | `https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{VIX,VIX9D,VIX3M,VIX6M,VVIX,SKEW,OVX,GVZ}_History.csv` | ~< 1 MB cada | só disclaimer |
| E01 Treasury | `https://home.treasury.gov/resource-center/data-chart-center/interest-rates/` (CSV anual: par nominal, par real, bills; 1990–2026) | ~< 20 MB | governamental |
| F01 CFTC COT | `https://www.cftc.gov/files/dea/history/` (`deacot{AAAA}.zip`, `fut_disagg_txt_{AAAA}.zip`, `fut_fin_txt_{AAAA}.zip`, 2006–2026, mais o histórico de 1986) | ~100–200 MB | governamental |
| E03/E04 GSW | `https://www.federalreserve.gov/data/yield-curve-tables/feds200628.csv` e o arquivo TIPS (a URL exata ainda precisa ser verificada) | ~< 30 MB | Fed |
| D07/D08/C05/D09 BCB | API SGS em blocos de 10 anos (Selic 11 e 432); Olinda Focus e PTAX | ~< 50 MB | ODbL |
| D06 FOMC | `fomccalendars.htm` e `fomc_historical.htm` (HTML, para montar a tabela de eventos) | ~< 5 MB | Fed |

Exemplo do formato de comando que o script vai registrar (não executar agora):

```bash
curl -fsS -A "QHOS-pesquisa (contato: QHOS)" -o RAW/cboe/20261029/VIX_History.csv https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
```

## Lote 2 — oficiais com chave gratuita (o autor cria as chaves)

| dataset_id | Cadastro | O que baixar |
|---|---|---|
| D02 ALFRED / D01 FRED | https://fred.stlouisfed.org/docs/api/api_key.html | lista curta de séries, cada uma com vintages (`output_type=4` e `realtime`): CPIAUCSL, PAYEMS, UNRATE, GDP, PCEPI, DFII10, DGS2, DGS10, T10YIE, T5YIFR, DTWEXBGS, DCOILWTICO, DCOILBRENTEU, DHHNGSP, NFCI, STLFSI4, SOFR, EFFR |
| I05 EIA | https://www.eia.gov/opendata/ | estoques semanais de petróleo e gás |
| D04 BLS v2 | https://data.bls.gov/registrationEngine/ | CPI e payroll (valores e datas) |

- **Tamanho:** ~< 200 MB.
- **Limites:** 500 consultas por dia na v2 do BLS; o limite do FRED não está verificado, então usar no máximo 1 requisição por segundo.

## Lote 3 — complementares e posicionamento

- I09 GPR e I10 EPU (arquivos, ~< 50 MB)
- I07 WGC ETFs (Excel; uso pessoal)
- F08 FINRA short volume CNMS 2018–2026 (~1 arquivo por dia, ~1–2 GB)
- I03 tzdb 2026f

## Lote 4 — cripto, só pesquisa não comercial

G01 Binance Vision, para BTCUSDT e ETHUSDT:
- spot: klines de 1m;
- USD-M: klines de 1m, fundingRate e metrics.

Arquivos mensais, cada um conferido pelo `.CHECKSUM` publicado.
- **Tamanho:** ~2–5 GB (UNKNOWN; o manifesto vai medir).
- **Licença:** CC BY-NC-SA. Não usar em trading ao vivo.

## Lote 5 — fundamentos e ações

- B03 SEC FSDS de 2009Q1 a 2026Q3: ~70 ZIPs de 57–122 MB cada, ~6 GB no total.
- B05 Fama-French.
- B16 composição do S&P 500 (fja05680), classificada como experimental.

## Fora deste plano (exigem decisão própria)

- H08: perguntar à Rithmic sobre o history plant. É e-mail; o autor envia.
- H01: um dia de Nasdaq ITCH (3,5–18 GB), só se o motor de book precisar de um banco de testes de ações.
- Qualquer fonte paga, inclusive a volta da Databento: não, salvo nova decisão do autor.
