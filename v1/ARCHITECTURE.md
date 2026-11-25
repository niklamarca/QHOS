# QUANT HPC OS — Arquitetura do Sistema

> **STATUS: SUPERADO.** Referência histórica. O estado vigente está em
> `CURRENT_ARCHITECTURE.md`, o vocabulário em `governance/EPISTEMIC_CONTRACT.md`
> e os fatos em `data_lake/governance/CURRENT_STATE.json`.

```
DATA → FEATURES → EVOLUTION → BACKTEST → REGIME → VALIDATION → RISK → PORTFOLIO → EXECUTION
                                                                              ↑
                                                                    FEEDBACK LOOP
```

## Módulos

| Módulo | Arquivo | Função |
|--------|---------|--------|
| HPC Orchestrator | `orchestrator/hpc_orchestrator.py` | CEO do sistema, gerencia ciclo de vida e kill switch |
| Job Queue | `orchestrator/job_queue.py` | Filas P0-P4, workers CPU/GPU/IO/Meta |
| Data Lake | `data/storage.py` | DuckDB + Parquet, catálogo de dados e estratégias |
| Data Ingestion | `data/ingestion.py` | yfinance (stocks), ccxt (crypto), dados sintéticos |
| Feature Engine | `data/features.py` | 80+ features: trend, momentum, vol, volume, MR |
| Regime Model | `regime/gpu_model.py` | Transformer PyTorch para 4 regimes de mercado |
| Strategy DNA | `evolution/strategy_dna.py` | Cromossomo genético de estratégias |
| Genetic Engine | `evolution/genetic.py` | Crossover, mutação, seleção, Hall of Fame |
| LLM Agents | `evolution/llm_agents.py` | 4 agentes Claude: alpha, risk, overfitting, micro |
| Backtest Engine | `backtest/engine.py` | Backtest vetorizado IS/OOS com custos |
| CPU Cluster | `backtest/cpu_cluster.py` | 32 threads paralelas de backtest |
| Walk-Forward | `backtest/walk_forward.py` | Validação em janelas deslizantes |
| Validator | `validation/validator.py` | 7 testes anti-overfitting obrigatórios |
| Risk Engine | `risk/risk_engine.py` | Drawdown, P&L diário, concentração, vol, correlação |
| Kill Switch | `risk/kill_switch.py` | L1 reduz / L2 desliga / L3 flatten total |
| HRP | `portfolio/hrp.py` | Hierarchical Risk Parity (López de Prado) |
| Portfolio Engine | `portfolio/constructor.py` | Seleção de estratégias + rebalanceamento |
| Paper Broker | `execution/paper_broker.py` | Simulação realista com slippage e comissão |
| Execution Engine | `execution/execution_engine.py` | Multi-broker com risk gate obrigatório |
| Dashboard | `dashboard/control_tower.py` | FastAPI + WebSocket, UI institucional |
| Main | `main.py` | Entry point único, integração completa |

## Setup Rápido

```bash
# 1. Clone e configure
cp .env.example .env
# Edite .env com sua ANTHROPIC_API_KEY

# 2. Instale dependências
pip install -r requirements.txt

# 3. Execute
python main.py

# 4. Dashboard
# http://localhost:8000
```

## Limites de Risco (configuráveis em .env)

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| MAX_PORTFOLIO_DRAWDOWN | 10% | Kill switch L2 automático |
| MAX_DAILY_LOSS | 2% | Limite de perda diária |
| MAX_POSITION_SIZE | 5% | Concentração máxima por ativo |
| MIN_OOS_SHARPE | 0.5 | Sharpe mínimo para validação |
| MIN_REGIME_ROBUSTNESS | 0.6 | Robustez cross-regime mínima |
