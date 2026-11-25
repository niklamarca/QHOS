# Relatório Honesto — Quant HPC OS — 2026-08-19

> Regra de ouro deste relatório: **backtest não conta**. Só o forward real conta.
> Nenhuma recomendação de dinheiro real é feita aqui, em nenhuma circunstância.

---

## 1. Continuidade do sistema — ALERTA VERMELHO

O processo **está rodando agora** (log ativo às 11:05 de 19/08, ciclo de backtest 416/500).
Mas a continuidade está quebrada.

| Item | Valor |
|---|---|
| Log corrente | `logs/quant_hpc_2026-08-18.log` |
| Boots registrados em 18/08 | **11** |
| Boot da execução atual | 2026-08-18 23:39:01 |
| Tempo contínuo atual | ~11h30 |

Horários dos 11 boots em 18/08:

| # | Horário | # | Horário |
|---|---|---|---|
| 1 | 01:33:39 | 7 | 02:15:27 |
| 2 | 01:41:11 | 8 | 02:19:02 |
| 3 | 01:50:07 | 9 | 19:57:30 |
| 4 | 01:53:18 | 10 | 23:13:53 |
| 5 | 02:03:40 | 11 | 23:39:01 |

Oito reinícios em 46 minutos na madrugada (01:33–02:19) é loop de crash, não operação.
Cada restart fragmenta o track-record e reseta o aprendizado. **Isto é o problema nº 1 a resolver.**

### 1b. O track-record principal parou de ser gravado

`data_lake/track_record/nav.csv` tem como **última linha 2026-08-17**. Não há linha para
18/08 nem para 19/08, apesar de o sistema ter operado (39 fills em 18/08). O arquivo de
verdade deixou de ser escrito — o que significa que **estamos parcialmente cegos**.

---

## 2. Track-record FORWARD — segmentado por restart

Segmentação por reset de `n_fills_cum`. Cada segmento parte de $100.000.
**Os segmentos NÃO são somáveis** — somar seria inventar um número.

| Seg | Período | Dias | NAV final | Retorno vs $100k | SPY no período | Alfa |
|---|---|---|---|---|---|---|
| 1 | 22/06 → 30/06 | 7 | $99.016,16 | **-0,98%** | -0,16% | **-0,88%** |
| 2 | 08/07 → 15/07 | 6 | $99.989,68 | -0,01% | +0,51% | **-0,51%** |
| 3 | 22/07 → 28/07 | 5 | $98.705,42 | **-1,29%** ⚠ | -0,35% | **-0,63%** |
| 4 | 10/08 → 17/08 | 6 | $99.889,54 | -0,11% | +0,27% | **-0,27%** |

**Quatro segmentos. Quatro retornos negativos. Quatro derrotas para o SPY.** Zero exceções.

O segmento 3 rompeu o limite de -1% (alerta disparado). O segmento 4, o mais recente,
é o menos ruim — mas continua negativo e continua perdendo do índice.

Pelo log, o NAV do broker principal em 18/08 era **$99.859,63** (0 posições) — ou seja,
o segmento 4 piorou para **-0,14%** desde a última linha gravada no CSV.

### Braços paralelos (trend / symb1) — 6 sessões, ruído puro

| Braço | 11/08 → 18/08 | vs $100k | SPY | Alfa |
|---|---|---|---|---|
| TrendPaper | $99.924,88 | -0,08% | -0,40% | +0,33% |
| Symb1Paper | $100.230,82 | +0,23% | -0,40% | +0,63% |

Isto **não é evidência de nada**. Seis sessões de janela, uma delas com -0,95% e -0,33%
respectivamente no último dia. Com n=6 o desvio-padrão do estimador é maior que o próprio
sinal. Não trate estes números como edge.

---

## 3. Churn / custo de transação

| Segmento | Fills totais | Fills/dia | Avaliação |
|---|---|---|---|
| 1 | 1.438 | 205 | CENTENAS — over-trading |
| 2 | 566 | 94 | Centenas/dezenas altas |
| 3 | 1.169 | 234 | CENTENAS — pior de todos |
| 4 | 18 | 3,0 | Dezenas baixas — banda funcionou |

O anti-churn **funcionou no segmento 4**. Esse é o único progresso técnico real do período.

**Porém:** em 18/08 o sistema executou **39 fills num único dia**, voltando ao ritmo de dezenas.
E há um problema muito pior nesses fills — ver seção 4.

---

## 4. PROBLEMA GRAVE — fills a preço fantasma

Os fills de 18/08 foram executados a preço fictício:

```
2026-08-18 21:22:08 | PaperBroker | Fill: sell 10.0000 SPY @ 100.00 (slip=0.0bps, comm=$0.10)
2026-08-18 21:22:08 | PaperBroker | Fill: buy  10.0000 SPY @ 100.00 (slip=0.0bps, comm=$0.10)
```

**36 dos 39 fills saíram a `SPY @ $100.00`.** O SPY fechou 18/08 a **$767,45**.
O preço está hardcoded/placeholder — a marcação a mercado do broker principal está corrompida.

Consequências diretas:

- O P&L desses fills é **ficção**; só a comissão ($0,10/fill) é real → sangria pura.
- Slippage 0,0bps também é irreal — não existe execução sem slippage.
- Buy e sell no mesmo timestamp = round-trip instantâneo sem tese econômica.
- Qualquer número de forward que inclua 18/08 está contaminado.

Isso precisa ser corrigido **antes** de qualquer leitura de desempenho do período recente.

---

## 5. Aprendizado ao vivo

`data_lake/live_performance.json` existe, mas está **parado desde 2026-08-12** (7 dias sem atualização).

| Métrica | Valor |
|---|---|
| Estratégias registradas | 16 |
| Com ≥10 round-trips (limiar de ação do feedback) | **9** |
| Round-trips totais | 160 |
| Última atualização do arquivo | 12/08 |

O limiar foi atingido em 9 estratégias, então o laço de feedback *poderia* agir — mas o
arquivo não é escrito há uma semana. Somado ao `nav.csv` congelado desde 17/08, o padrão é
claro: **a camada de persistência está falhando**, provavelmente pelo mesmo motivo dos erros
de permissão da seção 6.

---

## 6. Erros reais (excluído o ruído conhecido de LLM/crédito de API)

| Erro | Ocorrências | Gravidade |
|---|---|---|
| `WinError 5 / PermissionError` gravando `reconciliation.json` | 5 (18/08, 11:49–23:05) | **Alta** — snapshot desatualizado |
| `RECONCILIACAO BLOCKED: unmarked_broker_position; orphan_broker_positions` | 1 (18/08 23:05) | **Alta** — posições órfãs no broker |
| `Binder Error: coluna "scientific_epoch" não existe` (Storage) | 1 (18/08 23:27) | **Média** — filtro de época devolve vazio, seleção evolutiva pode estar rodando sobre conjunto vazio |
| `RAW QUARENTENADO: MATIC/USDT — stale_final_bar:2024-09-10` | recorrente | Baixa — quarentena funcionando como deveria |

O `reconciliation.json` desatualizado + posições órfãs significa que **não há garantia de que
o estado interno bate com o estado do broker**. Junto com o preço fantasma da seção 4, a
confiabilidade do NAV reportado está comprometida.

Ignorado conforme instrução: erros de `LLMAgents / credit balance too low` (inofensivos).

---

## 7. Veredito

Ordem de prioridade para corrigir:

1. **Loop de restart** (11 boots em 18/08) — sem continuidade não há track-record.
2. **Preço fantasma `SPY @ 100.00`** no PaperBroker — sem preço real, todo número é ficção.
3. **Persistência quebrada** (`nav.csv` e `live_performance.json` congelados; `WinError 5`).
4. **Reconciliação bloqueada** / posições órfãs.
5. **`scientific_epoch`** ausente no schema do Storage.

Lembrete permanente: são necessárias **semanas a meses de forward contínuo** (sem restarts)
mais o **Deflated Sharpe Ratio** antes de qualquer conclusão sobre existência de edge.
Quatro segmentos de 5–7 dias cada não permitem conclusão nenhuma além de "não há evidência
de edge, e há evidência de custo". **Lucro garantido não existe.**

---

🔴 **SANGRANDO** — os 4 segmentos forward são negativos e os 4 perdem para o SPY; o segmento
atual ainda está negativo (-0,14%), o track-record parou de ser gravado em 17/08 e os fills de
18/08 saíram a preço fantasma, o que corrompe qualquer leitura do período recente.
