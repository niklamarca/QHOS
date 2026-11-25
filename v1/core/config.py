"""
QUANT HPC OS — Central Configuration
Única fonte de verdade para todos os parâmetros do sistema.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Literal, List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).parent.parent


class SystemConfig(BaseSettings):
    """Configuração central do Quant HPC OS."""

    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Identidade ──────────────────────────────────────────────────
    system_name: str = "Quant HPC OS"
    system_version: str = "1.0.0"
    system_mode: Literal["research", "paper", "live"] = "research"

    # ─── Caminhos ────────────────────────────────────────────────────
    # Raiz canonica do projeto. Todo caminho do sistema deriva daqui —
    # nenhum modulo deve recalcular o root com dirname(abspath(__file__)).
    root_dir: Path = ROOT_DIR
    data_lake_dir: Path = ROOT_DIR / "data_lake"
    strategies_dir: Path = ROOT_DIR / "strategies"
    logs_dir: Path = ROOT_DIR / "logs"

    # ─── LLM ─────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    llm_model_alpha: str = "claude-sonnet-4-6"
    llm_model_risk: str = "claude-haiku-4-5-20251001"
    llm_model_overfitting: str = "claude-haiku-4-5-20251001"
    llm_model_microstructure: str = "claude-haiku-4-5-20251001"

    # ─── Brokers ─────────────────────────────────────────────────────
    alpaca_api_key: str = ""



    binance_api_key: str = ""
    binance_secret_key: str = ""





    # ─── Hardware ────────────────────────────────────────────────────
    max_cpu_threads: int = 32
    gpu_device: str = "cuda:0"
    gpu_batch_size: int = 512

    # ─── Dashboard ───────────────────────────────────────────────────
    dashboard_port: int = 8000
    # Sem autenticacao, o dashboard deve ficar apenas no computador local.
    # Acesso remoto continua pelo watchdog Discord, sem expor endpoints de ordem.
    dashboard_host: str = "127.0.0.1"

    # ─── Logging ─────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ─── Risk Limits (Institucionais) ────────────────────────────────
    max_portfolio_drawdown: float = 0.10       # 10% máximo drawdown
    max_position_size: float = 0.50            # 50% — pesos HRP entre estratégias (não posições em ativos)
    max_daily_loss: float = 0.02               # 2% perda diária
    max_strategy_correlation: float = 0.65     # correlação máxima entre estratégias
    # TAXA LIVRE DE RISCO = 0 (convencao do sistema, decidida em 14/08/2026).
    #
    # Estava em 5% ao ano, subtraida do retorno antes do Sharpe no engine —
    # o que reduzia o Sharpe em ~0,33 unidades a 15% de vol. Dois problemas:
    #
    # (a) 5% CONSTANTE esta errado em 1993-2026. As T-bills ficaram perto de
    #     zero em 2010-2021 e perto de 5% nos anos 1990 e em 2023-2026. Uma
    #     taxa fixa penaliza uma decada e favorece outra.
    # (b) Os scripts de pesquisa em lab/ e os bracos forward calculam Sharpe
    #     SEM rf. Ou seja: Sharpe de backtest e Sharpe de pesquisa nao eram
    #     comparaveis, diferiam sistematicamente por ~0,33 — a mesma classe de
    #     incoerencia do custo (8 bps vs 2 bps) ja corrigida.
    #
    # A regra de decisao deste projeto NUNCA e "bater o caixa": e sempre
    # "bater o SPY". Com rf=0 todos os Sharpes do sistema passam a ser BRUTOS
    # e mutuamente comparaveis, e a comparacao contra o benchmark — a unica
    # que decide alguma coisa — fica limpa.
    risk_free_rate: float = 0.0

    # ─── Evolution Engine ─────────────────────────────────────────────
    evolution_population_size: int = 500       # estratégias por geração

    evolution_mutation_rate: float = 0.15
    evolution_crossover_rate: float = 0.70
    evolution_elite_fraction: float = 0.10     # top 10% sobrevivem direto
    evolution_llm_sample_rate: float = 0.05    # 5% das gerações consultam LLM

    # ─── Backtest ─────────────────────────────────────────────────────
    backtest_min_trades: int = 10              # mínimo de trades para validação
    backtest_walk_forward_folds: int = 5
    backtest_monte_carlo_trials: int = 1000
    backtest_slippage_bps: float = 5.0         # 5 basis points
    backtest_commission_bps: float = 3.0       # 3 basis points

    # CUSTO TOTAL POR UNIDADE DE GIRO — fonte unica da verdade.
    # Antes o backtest cobrava 8 bps e os bracos forward 2 bps: um Sharpe de
    # backtest nao era comparavel a um Sharpe de forward. Agora todo lugar
    # que cobra custo le daqui.
    @property
    def cost_per_turnover(self) -> float:
        return (self.backtest_slippage_bps + self.backtest_commission_bps) / 10000.0

    # ─── Validation ───────────────────────────────────────────────────
    validation_min_oos_sharpe: float = 0.5
    validation_min_regime_robustness: float = 0.6
    validation_max_complexity: int = 10        # max regras por estratégia
    validation_noise_trials: int = 100
    # Walk-forward gates (anti-overfitting)
    validation_wf_min_positive_folds: float = 0.6   # % de folds com Sharpe > 0
    validation_wf_min_worst_fold: float = 0.0       # pior fold não pode ser negativo
    validation_wf_min_stability: float = 0.2        # 1 - CV entre folds
    validation_wf_max_degradation: float = 0.4      # WF mean >= 40% do IS âncora
    validation_wf_degradation_ref_min: float = 0.5  # só aplica degradação se âncora > isto
    # Held-out embargado: últimos N% NUNCA usados em seleção/validação (só auditoria)
    embargo_fraction: float = 0.20
    # Diversidade por ATIVO no GA (0 = off). Cap de fracao da pop/HOF por ativo primario.
    evolution_max_per_asset_frac: float = 0.25
    # Shrinkage da correlacao no HRP (0 = amostral puro; 0.10 = regularizacao leve)
    hrp_shrinkage: float = 0.10
    # Banda de nao-negociacao no rebalance: so negocia se o peso do ativo mudar
    # mais que isto. Corta o over-trading (churn) que sangrava ~1%/semana em custo.
    rebalance_no_trade_band: float = 0.05
    # Marcas paper sao evidencia confirmatoria. Usa-las para selecionar a mesma
    # busca adapta ao ruido e invalida o relogio; so habilitar em experimento
    # online separado e pre-registrado.
    online_feedback_affects_selection: bool = False
    # Cap por LINHAGEM no HOF: max de instancias da MESMA linhagem (nome completo,
    # ex: volatility_8fdcd4). Impede o HOF de virar copias da mesma estrategia.
    evolution_max_per_lineage: int = 3

    # ─── Regime Model ─────────────────────────────────────────────────
    regime_n_classes: int = 4                  # trend, mr, vol_exp, liq_stress
    regime_lookback: int = 60                  # janela de contexto (dias)
    regime_retrain_days: int = 30              # retreina a cada 30 dias
    regime_retrain_days_no_skill: int = 7      # cooldown curto se ganho OOS <= 0


    # ─── Data ─────────────────────────────────────────────────────────
    data_refresh_interval_minutes: int = 5
    # Integridade de dados: pesquisa nunca pode receber fallback inventado.
    data_max_stale_equity_sessions: int = 3
    data_max_stale_crypto_days: int = 2
    # JANELA DE DADOS da pesquisa. 0 = HISTORICO COMPLETO (padrao).
    #
    # Este campo era INERTE: definido e nunca lido. E era exatamente o botao
    # que deveria controlar a janela — enquanto o valor real (500 barras)
    # ficava escondido num argumento default de get_ohlcv(), descartando 94%
    # dos dados. A configuracao que importava nao estava na configuracao.
    # Agora manda de verdade, e o padrao 0 preserva o comportamento atual.
    data_history_years: int = 0
    # Data plane institucional de eventos. Fica desligado ate existir chave,
    # entitlement e comparacao shadow aprovada. Nunca faz fallback silencioso.
    # Pode ficar ligado sem chave: nesse estado nao existe chamada de rede. Ao
    # detectar uma chave valida no proximo boot, executa somente o probe shadow.
    event_plane_enabled: bool = True
    databento_api_key: str = ""
    databento_max_request_usd: float = 5.0
    databento_daily_spend_cap_usd: float = 1.0
    databento_default_dataset: str = ""
    # A primeira fase converte um DBN limitado em memoria. Pedidos maiores
    # ficam bloqueados ate o leitor chunked/out-of-core ser promovido.
    event_plane_max_records_per_request: int = 1_000_000
    databento_bootstrap_shadow: bool = True
    databento_shadow_dataset: str = "EQUS.MINI"
    databento_shadow_schema: str = "ohlcv-1m"
    databento_shadow_symbol: str = "SPY"
    databento_shadow_use_qhos_universe: bool = True
    databento_shadow_limit: int = 50_000
    databento_bootstrap_max_usd: float = 0.25
    databento_shadow_poll_minutes: int = 60
    # Depois do probe, preenche uma janela curta e contigua para reconciliacao.
    # Cada sessao continua submetida ao teto financeiro individual acima.
    databento_backfill_enabled: bool = True
    databento_backfill_sessions: int = 20
    databento_backfill_max_sessions_per_cycle: int = 1
    # Bronze pago nunca e apagado automaticamente. Ao atingir qualquer limite,
    # novas compras sao bloqueadas e exigem decisao humana.
    event_plane_max_storage_gb: float = 200.0
    event_plane_min_free_gb: float = 50.0
    # Gate para uma futura troca da fonte de pesquisa. O padrao continua shadow:
    # a promocao exige cobertura, ajustes e reconciliacao, nao apenas download.
    databento_reconciliation_min_sessions: int = 20
    databento_reconciliation_min_symbol_coverage: float = 0.90
    databento_reconciliation_max_close_error: float = 0.005
    databento_min_regular_session_coverage: float = 0.90
    databento_promote_to_research: bool = False
    data_assets_stocks: List[str] = Field(default=[
        "SPY", "QQQ", "IWM", "GLD", "TLT", "VIXY",
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA",
        "JPM", "BAC", "GS", "XLF", "XLE", "XLK"
    ])
    data_assets_crypto: List[str] = Field(default=[
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",
        # MATIC/USDT foi aposentado: o provider devolve uma série encerrada
        # em 2024-09-10. Mantê-lo no universo fazia cada ciclo repetir uma
        # consulta sabidamente inválida. O parquet legado fica preservado
        # para auditoria, mas não entra em pesquisa nem em execução.
        "ADA/USDT", "AVAX/USDT", "LINK/USDT"
    ])


    # Series de REFERENCIA (dados-so, NAO negociaveis): indices de vol p/ o sinal
    # VRP (contango/backwardation via ^VIX/^VIX3M). Ingeridas, mas FORA de
    # all_assets e do universo do GA/execucao.
    data_assets_reference: List[str] = Field(default=[
        "^VIX", "^VIX3M"
    ])

    # BRACO MANUAL (MT5). APOSENTADO em 2026-08-14.
    # Ele existia para medir o trading humano do operador — a unica fonte de
    # informacao deste sistema que nao esta em paper publicado. O operador
    # confirmou que NAO opera manualmente em conta nenhuma. Sem dado de
    # entrada, o braco gravaria um NAV plano e mediria zero.
    # Nao foi apagado: basta ligar aqui e logar uma conta REAL com historico
    # de operacoes proprias. Ligar com uma conta demo vazia nao serve.
    manual_arm_enabled: bool = False

    # ─── Portfolio ─────────────────────────────────────────────────────
    portfolio_max_strategies: int = 20
    portfolio_rebalance_hours: int = 24
    portfolio_min_strategy_weight: float = 0.02
    # Governança de promoção: validado em backtest nunca entra automaticamente.
    promotion_min_forward_sessions: int = 60
    # Backup lógico do data lake: executado pelo servidor, nunca por cópia
    # externa do DuckDB em uso.
    verified_backup_hour_local: int = 2
    verified_backup_retention_days: int = 14

    def ensure_dirs(self):
        """Cria todos os diretórios necessários."""
        dirs = [
            self.data_lake_dir / "raw",
            self.data_lake_dir / "features",
            self.data_lake_dir / "regimes",
            self.data_lake_dir / "backtests",
            self.data_lake_dir / "portfolio",
            self.data_lake_dir / "logs",
            self.data_lake_dir / "provenance",
            self.data_lake_dir / "events",
            self.data_lake_dir / "events_native",
            self.data_lake_dir / "events_staging",
            self.data_lake_dir / "events_silver",
            self.data_lake_dir / "events_reference",
            self.data_lake_dir / "experiments",
            self.strategies_dir,
            self.logs_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

    @property
    def is_live(self) -> bool:
        return self.system_mode == "live"

    @property
    def is_paper(self) -> bool:
        return self.system_mode in ("paper", "live")

    @property
    def all_assets(self) -> List[str]:
        return self.data_assets_stocks + self.data_assets_crypto


# ─────────────────────────────────────────────────────────────────────
# NOTA DE CONFIGURACAO: o .env pode sobrescrever campos deste arquivo.
# O boot lista os NOMES sobrescritos sem expor segredos. Campos de Alpaca/IBKR
# sao apenas reserva de configuracao: nenhum adapter real esta registrado hoje.
# ─────────────────────────────────────────────────────────────────────

def _campos_sobrescritos_pelo_env() -> list:
    """Quais campos do config.py foram substituidos por variaveis de ambiente.

    Descoberto em 14/08/2026: o .env sobrescreve 19 campos — entre eles
    risk_free_rate, max_position_size, max_daily_loss e max_portfolio_drawdown.
    Editar core/config.py nesses campos NAO tem efeito nenhum, e quem le o
    arquivo ve valores que nao sao os reais. Falha silenciosa classica.
    Agora o boot declara quais vieram de fora. NUNCA imprime valores — ha
    chaves de API no mesmo arquivo.
    """
    import os
    achados = []
    for nome in SystemConfig.model_fields:
        if os.environ.get(nome.upper()) is not None:
            achados.append(nome)
            continue
        try:
            for l in open(".env", encoding="utf-8", errors="ignore"):
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    if l.split("=", 1)[0].strip().lower() == nome:
                        achados.append(nome)
                        break
        except Exception:
            pass
    return sorted(set(achados))


# Singleton global
config = SystemConfig()
config.ensure_dirs()
