# Inventário V1 → V2

Data: 2026-10-23. Base: snapshot `aab7dad` (runtime do WSL, marcado `NAO-VERIFICADO`).
Método: leitura estática (nomes, docstrings, contagem de linhas `.py`). **Nada foi executado.** A classificação
abaixo é julgamento, não prova.

## 1. O que o V1 é, segundo a documentação dele

Runtime local de pesquisa quantitativa sobre dados públicos (ETFs, cripto): busca evolutiva (GA), validador
interno, shadow, paper e dashboard (`CURRENT_ARCHITECTURE.md`, 19/08). Não executa capital real e não demonstrou
edge. Estado das hipóteses naquela data: VRP, CRYPTO-IMBALANCE, BASIS, FUNDING-DISPERSION e FUNDING-EXTREME
falsificadas; CRYPTO-CARRY em shadow desde 19/08; SYMB-1 e TREND em forward.

Estado em 2026-10-23 17:30 UTC (`data_lake/governance/CURRENT_STATE.json`): processo online; `data_integrity`,
`research_integrity` e `execution_readiness` **BLOQUEADOS** porque o ativo `^VIX` está em quarentena
(`stale_final_bar:2026-10-14`); 26 dos 27 ativos `PARTIAL` por `current_bar_excluded` (comportamento
desenhado). GA: geração 5863, 0 aprovadas em 4955 julgamentos, agentes LLM desligados (`no_api_key`).
Execução simulada, 0 posições, nenhum candidato autorizado. Evidência forward (mínimo de 60 sessões,
`INSUFFICIENT_SAMPLE`): TREND 33 sessões, SYMB-1 33, CRYPTO-CARRY 44; última sessão 2026-10-22.

Sobre fluxo de ordens, o próprio V1 conclui: "em tick/livro seria jogo de velocidade, que não é o nosso".

## 2. Decisão por área

| Área | Linhas `.py` | Decisão | Motivo |
|---|---:|---|---|
| `adapters/rithmic` | ver §3 | **Portar seletivamente** | Peças 4 a 6 da V2 |
| `main.py`, `orchestrator`, `dashboard`, `core` | 832 + 1.158 + 1.158 + 3.023 | **Manter no V1** | Runtime em operação. Ideias a portar: `core/atomic_io.py`, `core/restart_gate.py`, `core/runtime_health.py`, `data/storage.py::create_verified_backup` |
| `evolution` (GA, comitê, agentes LLM) | 3.593 | **Aposentar o GA** (`genetic.py`, `strategy_dna.py`, `admit.py`) quando houver ordem de parada | Só aumenta tentativas (ver calibração). `model_committee.py` fica como referência para o PAIR |
| `validation` | 3.382 | **Manter sem editar** `validator.py` | Editar muda a época científica. `block_bootstrap.py` é candidato a reimplementação com testes na V2 |
| `governance` | 1.903 | **Referência** | Registro de hipóteses e fingerprint de código servem de modelo à pré-registro da V2 |
| `strategies` | 641 | **Manter rodando** `crypto_carry_shadow`, `symb1_reversal`, `trend_following` | Experimentos forward dependem do relógio, sem crédito retroativo. `manual_mt5.py` não foi lido |
| `lab` | 2.154 | **Arquivar** (fica no git) | Evidência das hipóteses falsificadas |
| `data` | 8.203 | **Manter no V1** | Event lake, adaptador Databento (aguarda chave), features, observador de microestrutura |
| `execution`, `risk`, `portfolio`, `backtest`, `simulation`, `regime`, `monitoring` | ~5.500 | **Manter no V1** | `risk/kill_switch.py` e `risk/risk_engine.py` são referência para o motor de risco da V2 |
| `deploy` | 672 | **Referência** | Unidades systemd para a captura contínua (peça 5) |
| `tests` | 6.805 (49 arquivos) | **Ficam no V1** | A V2 tem os testes próprios |
| `research`, `scripts` | 1.053 + 71 | Não revisados | |

## 3. Rithmic, módulo a módulo

| Módulo | Linhas | Decisão |
|---|---:|---|
| `protocol.py`, `market_protocol.py`, `depth_protocol.py` | 102 + 40 + 38 | **Portar (peça 4).** Contrato do fio e lista branca de templates de saída, que bloqueia pedidos de ordem |
| `client.py`, `market_client.py`, `depth_client.py`, `connection_policy.py` | 257 + 408 + 72 + 19 | **Portar (peça 4)**, reescrevendo para Paper e processo único |
| `capture.py`, `recording_limits.py` | 354 + 16 | **Formato mantido** (o catálogo da V2 já o lê); escritor portado na peça 5 |
| `soak.py`, `recovery.py` | 798 + 309 | **Portar seletivo (peça 5):** rotação de segmentos, orçamento de bytes e disco, detecção de dado velho, recuperação após reinício |
| `depth_state.py`, `depth_reconstruction.py`, `book_qualification.py`, `depth_investigation.py` | 212 + 379 + 974 + 240 | **Referência (peça 6).** Política de alinhamento explícita, mas validada só contra Test (sintético). A reconstrução da captura Paper de 22/10 não foi verificada. Um teste do V1 (`reconstruction_change_unknown_id`, em `test_depth_investigation`) já falhava antes: investigar na peça 6 |
| `market_calendar.py` + `CME_SESSION_EXCEPTIONS.json` | 266 | **Portar e estender.** Cobre só índices de ações da CME; o ouro (COMEX) tem outro horário |
| `loopback_server.py` | 326 | **Portar** para testar a peça 4 sem rede |
| `history_client.py` | 258 | Não necessário agora (barras históricas; sem histórico MBO) |
| `observation_*` (5 arquivos), `feed_classification`, `market_state`, `price_depth` | ~1.700 | **Manter no V1** (alimentam consumidores do V1) |

## 4. Por que o V1 segue ligado

- Os experimentos forward continuam avançando mesmo com a pesquisa bloqueada (última sessão 2026-10-22):
  TREND e SYMB-1 em 33 de 60 sessões, CRYPTO-CARRY em 44 de 60. Parar o V1 abre buracos que não se recuperam.
- O que se pode parar antes: o **GA** (RAM e CPU), depois da captura contínua da V2 estar de pé e do tag
  `v1-running-20261022` existir.
- A parede de `n_test = 5001` (o cache em dente de serra toca o valor impossível) está descrita em
  `calibracao_validador/CALIBRACAO.md`.

## 5. O que não vai ao git

- `data_lake/` (capturas, bancos): ignorado por `.gitignore`. Dado licenciado nunca vai ao GitHub.
- Credenciais: nunca em arquivo; digitadas no prompt.
- Fora do repositório, em cópia única: `C:\Users\usuario\seguro_20261022` (governance, provenance, track_record),
  `C:\Users\usuario\tarefas_removidas` (4 XMLs), `C:\WSL\_arquivo\qhos-e1-aceite.tar`. **Recomendação:** cópia em
  disco externo.
- `vendor/rithmic/0.89.0.0/`: 31 `.proto` e `manifest.json` estão versionados desde o V1; o `.zip` do SDK está
  ignorado. Se versionar os `.proto` é permitido pela licença, **não foi verificado**: perguntar à Sales.

## 6. Fatos medidos na V2 (22 e 23/10/2026)

**COMEX.** O depth MBO de `GCZ6` na bolsa `COMEX` funciona no Paper: captura `9c083748` verificada, 3.807 quadros.

**Atraso** (relógio local menos carimbo remoto, template 160 = atualizações de livro, zero valores negativos):

| Captura | Mercado | n | mín (ms) | p50 | p90 | p99 | máx |
|---|---|---:|---:|---:|---:|---:|---:|
| `6be700be` | CME/ESZ6 | 1.808 | 71,9 | 73,6 | 74,7 | 77,4 | 82,1 |
| `9474726d` | CME/ESZ6 | 2.217 | 68,9 | 71,2 | 75,8 | 78,9 | 81,5 |
| `c7837cec` | CME/ESZ6 | 28.780 | 67,8 | 75,9 | 82,2 | 98,6 | 135,4 |
| `9c083748` | COMEX/GCZ6 | 2.516 | 75,1 | 79,7 | 89,7 | 101,2 | 110,8 |

Os três carimbos (`servidor`, `fonte`, `jop`) diferem entre si em no máximo ~0,2 ms.

Leitura (hipótese, não fato): o piso de ~70 ms é compatível com o trânsito São Paulo ↔ EUA; o relógio do WSL
parece alinhado (nenhum negativo, mínimos plausíveis), mas um desvio de dezenas de ms não está excluído. A latência
do caminho de **ordens não foi medida**. Consequência: sinais com meia-vida abaixo de ~200 ms são inexecutáveis daqui.

**Ritmo.** ES `c7837cec`: ~480 atualizações/s em 60 s. GC `9c083748`: ~42/s em 60 s (horários diferentes, não
comparável). O `pico_por_s` geral do catálogo (1.254 a 1.857) é dominado pela rajada do retrato inicial (1.253 a
1.317 quadros sem carimbo), não por atualizações: usar `pico_por_template`.

**Relógio.** O teste ao vivo do Databento de 09/09 registrou latência p50 de −1,4 s (relógio do WSL adiantado
naquela época); a medição de 23/10 não tem negativos. A sessão da V2 (peça 4) deve registrar o desvio de relógio
no início de cada captura.

## 7. Pendências

- Tag `v1-running-20261022` no commit `aab7dad`.
- Renovação da Rithmic: decidida em 23/10, 1 mês (US$ 200). Gate até 20/11: 5 pregões completos de captura e uma
  hipótese pré-registrada avaliada.
- `^VIX` em quarentena bloqueia a pesquisa do V1 (fora do escopo da V2).
- Aposentar o GA; networking espelhado do WSL e fase 3 do PAIR; perguntas à Sales (sessões, licença dos `.proto`).
