# Auditoria: a estrutura suporta a spec do Market OS?

*Verificação componente a componente contra o código real (não contra a intenção). Data: 2026-08-12.*

## Veredito curto

**O ciclo central da spec — `dados → estado → regime → estratégia → decisão → risco → execução → resultado → aprendizado → alocação` — está implementado e rodando.** As duas leis arquiteturais mais importantes são invioláveis no código: (a) nenhuma ordem chega ao broker sem passar pelo Risk Engine; (b) IA nunca fala com o broker. O que falta divide-se em *barato e valioso* (agora implementado) e *caro e prematuro* (deliberadamente não feito).

---

## Matriz de conformidade

| Componente da spec | Status | Onde vive |
|---|---|---|
| Event Bus desacoplado | ✅ | `core/events.py` (23 tipos de evento) |
| Market Data Layer + normalização | ✅ | `data/ingestion.py` (batch/incremental, drift de ajuste) |
| Validação de qualidade de dados | ✅ parcial | validação de parquet, dedup, filtro de volume |
| Feature Engine (versionado) | ✅ / ⚠️ sem versionamento formal | `data/features.py` (95 features, causais) |
| Market State Engine | ✅ parcial | features + `term_structure` + regime |
| Regime Engine (probabilístico) | ✅ | `regime/gpu_model.py` (RegimeTransformer) |
| **Scenario Engine** | ❌ ausente | — |
| AI Agents / Orchestrator | ⚠️ existe, sem crédito de API | `evolution/llm_agents.py` |
| Strategy Engine (hipótese) | ✅ | `evolution/strategy_dna.py` + specs pré-registradas |
| Bot Engine (instância operacional) | ✅ | 5 braços: GA, TREND, SYMB-1, MANUAL, SPY |
| **Bot Arena** | ✅ | aba *Braços* + `lab/monte_carlo_lab*.py` |
| Capital Allocation Engine | ⚠️ HRP fixo, não fitness-dinâmico | `portfolio/hrp.py`, `constructor.py` |
| **Risk Engine (autoridade)** | ✅ **obrigatório** | `risk/risk_engine.py` — `_risk_gate` em toda ordem |
| Kill Switch global (L0–L3) | ✅ | `risk/kill_switch.py` |
| **Execution Engine (única via broker)** | ✅ | `execution/execution_engine.py` |
| Broker Adapters | ✅ interface, 1 impl | `execution/abstract.py`, `paper_broker.py` |
| Máquina de estados de ordem | ⚠️ `OrderStatus` existe; transições agora auditadas | `execution/abstract.py` + audit |
| Position Engine | ✅ parcial | `paper_broker` (posições persistidas) |
| Portfolio Engine | ✅ | `portfolio/constructor.py` |
| Qualidade de execução (slippage/latência) | ⚠️ slippage sim, latência não medida | `paper_broker` |
| Performance Engine | ✅ parcial | `consolidation_scorecard.py` (DSR/PSR), `core/track_record.py` |
| Learning Engine | ✅ parcial | `core/live_performance.py` → fitness |
| Strategy Fitness Score | ✅ parcial | `compute_fitness` (multiplicativo + live) |
| Backtest = mesma arquitetura | ✅ | `backtest/engine.py` compartilhado |
| Execution Simulator | ✅ parcial | slippage/comissão; sem parciais/rejeições |
| **Replay / Digital Twin** | ❌ ausente | — |
| **Journal** | ✅ **novo** | `governance/control.py` → `journal.jsonl` |
| **Audit Trail** | ✅ **novo** | `governance/control.py` → `audit_*.jsonl` |
| **Modos de autonomia** | ✅ **novo** | `governance/control.py` + gate no ExecutionEngine |
| **System Health** | ✅ **novo** | payload `health` + aba Governança |
| Hot Path × Cold Path | ✅ de fato | LLM/pesquisa fora do caminho crítico |
| Frontend Next.js/React/TS | ❌ | HTML/JS puro servido por FastAPI |
| Kafka/NATS, Postgres/Timescale, Redis | ❌ | asyncio EventBus + DuckDB + parquet |
| Multi-broker, opções, intraday 1m–4h | ❌ | apenas diário, paper |

---

## O que foi implementado agora (governança)

1. **Modos de autonomia** — `MANUAL / COPILOT / SUPERVISED / AUTONOMOUS`. O gate roda **antes do Risk Engine**, dentro do `ExecutionEngine.submit()`. Em MANUAL/COPILOT nenhuma ordem sai; a tentativa é auditada. Persistido em `data_lake/autonomy.json` (sobrevive a restart). Alternável pela aba **Governança**.
2. **Audit trail append-only** — cada bloqueio por autonomia, rejeição por risco e fill grava `{seq, ts, evento, ator, sujeito, payload}` em JSONL diário. Reconstrói qualquer decisão.
3. **Journal automático** — entrada por operação com o contexto do momento.
4. **System Health** — estado por componente (market data, state engine, evolution, risk, execution, braços) no payload e na UI.

---

## O que deliberadamente NÃO foi feito, e por quê

**Next.js/React/TypeScript, Kafka/NATS, Postgres/TimescaleDB, Redis, microservices, multi-broker, cadeia de opções, intraday 1m–4h.**

Cada um exige reescrever a plataforma. E — o ponto que importa — **nenhum deles ataca o gargalo real, que continua sendo a ausência de edge demonstrado.** O sistema hoje: forward de lado, perdendo do SPY, Sharpe roll negativo. Trocar DuckDB por Timescale ou HTML por React não muda um único desses números; só aumenta a superfície de manutenção e o tempo até a próxima medição honesta.

A arquitetura atual é um **modular monolith com workers** — que é *exatamente* o que a própria spec recomenda como ponto de partida ("em vez de começar com dezenas de microservices"). As fronteiras estão nos lugares certos: se um dia for preciso trocar DuckDB→Timescale, EventBus→NATS ou HTML→Next.js, troca-se **atrás das interfaces existentes** (`StorageEngine`, `EventBus`, `/api/*` + WebSocket) sem tocar na lógica de decisão, risco ou execução.

**Suporta a spec?** A estrutura suporta — e o que falta de verdade não é infraestrutura: é uma hipótese com edge que sobreviva ao forward.
