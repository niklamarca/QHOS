"""
╔══════════════════════════════════════════════════════════════════════╗
║                     QUANT HPC OS v1.0.0                              ║
║           Runtime de Pesquisa Quantitativa Local                     ║
╠══════════════════════════════════════════════════════════════════════╣
║  Pipeline: DATA → FEATURES → EVOLUTION → BACKTEST → REGIME          ║
║            → VALIDATION → RISK → PORTFOLIO → EXECUTION              ║
╚══════════════════════════════════════════════════════════════════════╝

Entry point único do sistema integrado.
Inicializa todos os módulos na ordem correta e inicia o loop principal.
"""
from __future__ import annotations
import asyncio
import signal
import sys
import time
from pathlib import Path

# ── Setup de path ────────────────────────────────────────────────────
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# ── Core ─────────────────────────────────────────────────────────────
from core.config import config
from core.events import event_bus, EventType, Event
from core.registry import registry
from core.logger import get_logger

# ── Módulos ──────────────────────────────────────────────────────────
from data.storage import StorageEngine
from data.ingestion import DataIngestionEngine
from data.event_plane import EventPlane
from data.features import FeatureEngine

from regime.gpu_model import RegimeModelEngine

from evolution.genetic import GeneticEngine
from evolution.llm_agents import LLMAgentPool

from backtest.cpu_cluster import BacktestCluster
from validation.validator import ValidationCluster

from risk.risk_engine import RiskEngine
from risk.kill_switch import KillSwitch

from portfolio.constructor import PortfolioEngine
from execution.execution_engine import ExecutionEngine
from execution.paper_broker import PaperBroker
from execution.signal_executor import SignalExecutor

from orchestrator.hpc_orchestrator import HPCOrchestrator
from dashboard.control_tower import DashboardServer

log = get_logger("Main")


class QuantHPCOS:
    """
    Sistema Quantitativo de Alta Performance — Sistema Único Integrado.

    Instancia, conecta e orquestra todos os módulos do pipeline.
    """

    def __init__(self):
        log.info("Inicializando Quant HPC OS...")
        config.ensure_dirs()
        from governance.epistemics import current_code_fingerprint
        self._startup_code_hash = current_code_fingerprint()

        # ── Instanciar Módulos ────────────────────────────────────────

        # Data Layer
        self.storage         = StorageEngine()
        self.ingestion       = DataIngestionEngine(self.storage)
        self.event_plane     = EventPlane()
        self.feature_engine  = FeatureEngine(self.storage, self.ingestion)

        # Regime Model (GPU)
        self.regime_model    = RegimeModelEngine(self.storage)

        # Backtest + Validation
        self.backtest_cluster = BacktestCluster(self.storage, self.feature_engine)
        self.validator        = ValidationCluster(self.storage, self.feature_engine)

        # Evolution Engine
        self.llm_agents       = LLMAgentPool()
        self.evolution_engine = GeneticEngine(
            self.storage,
            self.backtest_cluster,
            self.validator,
        )
        self.evolution_engine.inject_dependencies(
            self.backtest_cluster,
            self.validator,
            self.llm_agents,
        )

        # Risk
        self.kill_switch = KillSwitch()
        self.risk_engine = RiskEngine(self.storage)
        self.risk_engine.inject_kill_switch(self.kill_switch)

        # Portfolio
        self.portfolio_engine = PortfolioEngine(self.storage)
        self.portfolio_engine.inject_dependencies(self.risk_engine, self.regime_model)

        # Execution
        self.execution_engine = ExecutionEngine()
        self.execution_engine.inject_dependencies(self.risk_engine)
        paper_broker = PaperBroker(initial_capital=100_000.0)
        self.execution_engine.register_broker("stocks", paper_broker)
        self.execution_engine.register_broker("crypto", paper_broker)
        # CRÍTICO: _default_broker deve ser o mesmo broker registrado
        # (o __init__ cria uma instância separada vazia — get_account_value leria dela)
        self.execution_engine._default_broker = paper_broker

        # Signal Executor — executa sinais reais das estratégias no paper broker
        self.signal_executor = SignalExecutor(self.storage, self.execution_engine)
        # A reconciliação de pré-ordem precisa enxergar também o executor;
        # sem esta ligação ela confundiria uma carteira ativa com posição órfã.
        self.execution_engine.inject_dependencies(self.risk_engine, self.signal_executor)
        from execution.shadow_executor import ShadowExecutor
        self.shadow_executor = ShadowExecutor(self.storage, self.signal_executor)

        # Braço forward independente: trend-following pré-registrado (paper).
        # Ledger e estado próprios; NÃO entra no GA/HRP. Spec congelada em
        # HIPOTESE_TREND_spec_falsificavel.md — mudar parâmetro = novo teste.
        try:
            from strategies.trend_following import TrendFollowingPaper
            self.trend_paper = TrendFollowingPaper()
        except Exception as _te:
            log.warning(f"TrendPaper indisponível: {_te}")
            self.trend_paper = None

        # Braço forward SYMB-1 (estrutura que passou dois holdouts historicos).
        # Spec congelada em HIPOTESE_SYMB1_spec_falsificavel.md.
        try:
            from strategies.symb1_reversal import Symb1Paper
            self.symb1_paper = Symb1Paper()
        except Exception as _se:
            log.warning(f"Symb1Paper indisponível: {_se}")
            self.symb1_paper = None

        # Braço SHADOW CRYPTO-CARRY (sobreviveu à bateria pré-registrada em
        # 19/08/2026 — primeira hipótese do projeto a sobreviver). SHADOW puro:
        # observa funding liquidado, sem ordem, sem fill. Protocolo congelado
        # em HIPOTESE_CRYPTO_spec_falsificavel.md §E.
        try:
            from strategies.crypto_carry_shadow import CryptoCarryShadow
            self.crypto_carry = CryptoCarryShadow()
        except Exception as _ce:
            log.warning(f"CryptoCarryShadow indisponível: {_ce}")
            self.crypto_carry = None

        # Braço MANUAL: mede o trading humano da conta MT5 (READ-ONLY, nunca
        # envia ordem). APOSENTADO em 2026-08-14 — ver core/config.py.
        # Ficava tentando conectar a cada 30 min e falhando com -6, poluindo o
        # log com um problema que nao tem solucao tecnica: nao ha operacao
        # humana para medir. Reativar so com conta REAL que tenha historico.
        self.manual_arm = None
        if config.manual_arm_enabled:
            try:
                from strategies.manual_mt5 import ManualMT5Arm
                self.manual_arm = ManualMT5Arm()
            except Exception as _me:
                log.warning(f"ManualArm indisponível: {_me}")
        else:
            log.info("Braço MANUAL desligado (manual_arm_enabled=False): "
                     "sem trading humano para medir. O experimento roda com "
                     "4 relógios — GA, TREND, SYMB-1 e SPY.")

        # Kill switch ← execution engine
        self.kill_switch.inject_dependencies(self.execution_engine, None)

        # Orchestrator (CEO do sistema)
        self.orchestrator = HPCOrchestrator()
        self.orchestrator.inject_modules(
            storage           = self.storage,
            data_engine       = self.ingestion,
            feature_engine    = self.feature_engine,
            regime_model      = self.regime_model,
            evolution_engine  = self.evolution_engine,
            backtest_cluster  = self.backtest_cluster,
            validator         = self.validator,
            risk_engine       = self.risk_engine,
            portfolio_engine  = self.portfolio_engine,
            execution_engine  = self.execution_engine,
            signal_executor  = self.signal_executor,
        )
        self.kill_switch.inject_dependencies(self.execution_engine, self.orchestrator)

        # Dashboard
        self.dashboard = DashboardServer()
        self.dashboard.inject_dependencies(
            orchestrator     = self.orchestrator,
            risk_engine      = self.risk_engine,
            portfolio_engine = self.portfolio_engine,
            evolution_engine = self.evolution_engine,
            regime_model     = self.regime_model,
        )

        # ── Registrar no Registry ─────────────────────────────────────
        modules = {
            "storage":          self.storage,
            "ingestion":        self.ingestion,
            "features":         self.feature_engine,
            "regime_model":     self.regime_model,
            "evolution":        self.evolution_engine,
            "llm_agents":       self.llm_agents,
            "backtest":         self.backtest_cluster,
            "validator":        self.validator,
            "risk_engine":      self.risk_engine,
            "kill_switch":      self.kill_switch,
            "portfolio":        self.portfolio_engine,
            "execution":        self.execution_engine,
            "orchestrator":     self.orchestrator,
            "dashboard":        self.dashboard,
        }
        for name, mod in modules.items():
            registry.register(name, mod)

    async def start(self):
        """Inicializa todos os módulos na ordem correta."""
        log.info("=" * 60)
        log.info(f"  {config.system_name} v{config.system_version}")
        log.info(f"  Modo: {config.system_mode.upper()}")
        log.info(f"  CPU: {config.max_cpu_threads} threads")
        log.info(f"  Dashboard: http://localhost:{config.dashboard_port}")
        log.info("=" * 60)

        # Inicia event bus
        await event_bus.start()

        # Ordem de inicialização (dependências primeiro)
        startup_order = [
            ("storage",       None),                            # sem start() async
            ("ingestion",     self.ingestion),
            ("features",      self.feature_engine),
            ("regime_model",  self.regime_model),
            ("backtest",      self.backtest_cluster),
            ("validator",     self.validator),
            ("evolution",     self.evolution_engine),
            ("llm_agents",    self.llm_agents),
            ("risk_engine",   self.risk_engine),
            ("kill_switch",   self.kill_switch),
            ("portfolio",     self.portfolio_engine),
            ("execution",     self.execution_engine),
            ("orchestrator",  self.orchestrator),
            ("dashboard",     self.dashboard),
        ]

        for name, module in startup_order:
            if module is None:
                continue
            try:
                if hasattr(module, "start"):
                    await module.start()
                log.info(f"  ✓ {name}")
            except Exception as e:
                log.error(f"  ✗ {name}: {e}")

        # ── Bootstrap de dados ────────────────────────────────────────
        log.info("Iniciando bootstrap de dados...")
        await self._bootstrap()

        await event_bus.publish(Event(
            type=EventType.SYSTEM_STARTED,
            source="main",
            payload={"mode": config.system_mode}
        ))

        log.info("")
        log.info("=" * 60)
        # Mantem o token OPERACIONAL para a vigia contar boots; seu significado
        # e estritamente liveness, nao qualidade de dados ou edge.
        log.info("  ✅ Quant HPC OS — PROCESS_ONLINE [OPERACIONAL=processo]")
        log.info(f"  Dashboard: http://localhost:{config.dashboard_port}")
        log.info("=" * 60)

    async def _bootstrap(self):
        """Bootstrap inicial: carrega dados e inicializa evolução."""
        assets = config.data_assets_stocks[:10]

        # PASSO 1: Inicializa população SEMPRE — independente de dados
        # (run_generation funciona mesmo sem features; sem isso population=0)
        # Declara quais parametros NAO vieram do core/config.py. Sem isto,
        # editar o arquivo em campos como risk_free_rate ou max_position_size
        # nao tem efeito e ninguem percebe (o .env vence, em silencio).
        try:
            from core.config import _campos_sobrescritos_pelo_env as _sob
            _c = _sob()
            if _c:
                log.info(f"  [config] {len(_c)} parametros vem do .env, NAO do "
                         f"core/config.py: {', '.join(n for n in _c if 'key' not in n and 'secret' not in n)}")
        except Exception:
            pass

        log.info("  [1/5] Inicializando população evolutiva...")
        try:
            self.evolution_engine.initialize_population(
                size=min(config.evolution_population_size, 200),
                available_assets=assets,
            )
        except Exception as e:
            log.error(f"Bootstrap: erro em initialize_population: {e}")

        # PASSO 2: Ingestão de dados antes das features
        # O bootstrap TAMBEM respeita o portao: bootar as 14h ET gravaria a
        # barra do dia AINDA INCOMPLETA no parquet, contaminando features e
        # marcacao. Se a janela estiver fechada, o dado ja no disco basta.
        from core import market_clock as _mc
        if _mc.should_ingest() or _mc.should_act():
            log.info("  [2/5] Ingestão de dados históricos (ciclo único)...")
            # Features precisam nascer de um snapshot completo. refresh_all()
            # coalesce caso o orquestrador ja tenha solicitado o mesmo ciclo.
            await self.ingestion.refresh_all()
        else:
            _d = _mc.describe()
            log.info(f"  [2/5] Ingestão ADIADA — {_d['label']} "
                     f"(barra do dia ainda não liquidada). Próxima abertura: {_d['next_open']}")

        # PASSO 3: O refresh acima ja terminou; cede o event loop para eventos.
        await asyncio.sleep(0)

        # PASSO 4: Features (pode falhar — não bloqueia evolução)
        log.info("  [3/5] Computando features...")
        try:
            # 60s bastavam para 487 barras/ativo. Com o historico completo sao
            # 17-27x mais dados — o timeout antigo mataria o calculo e o sistema
            # subiria SEM features, degradado e em silencio.
            await asyncio.wait_for(self.feature_engine.compute_all(), timeout=900.0)
        except asyncio.TimeoutError:
            log.warning("Bootstrap: compute_all timeout (>60s) — continuando sem features completas")
        except Exception as e:
            log.warning(f"Bootstrap: compute_all erro: {e} — continuando")

        # PASSO 5: Regime model e primeira geração (population já garantida)
        log.info("  [4/5] Iniciando regime model...")
        asyncio.create_task(self._train_regime_model())

        log.info("  [5/5] Executando geração 0 do engine evolutivo...")
        asyncio.create_task(self.evolution_engine.run_generation())
        asyncio.create_task(self._event_plane_loop())

    async def _event_plane_loop(self):
        """Acumula no maximo um snapshot shadow por sessao encerrada."""
        import asyncio as _asyncio
        while True:
            await self._bootstrap_event_plane_shadow()
            minutes = max(15, int(getattr(config, "databento_shadow_poll_minutes", 60)))
            await _asyncio.sleep(minutes * 60)

    async def _bootstrap_event_plane_shadow(self):
        """Ativa apenas o probe isolado quando a chave Databento existir."""
        if not bool(getattr(config, "event_plane_enabled", False)):
            log.info("EventPlane: DISABLED")
            return
        if not bool(getattr(config, "databento_bootstrap_shadow", True)):
            log.info("EventPlane: bootstrap shadow desativado")
            return
        if not self.event_plane.databento.configured:
            log.info("EventPlane: WAITING_FOR_KEY — zero chamadas de rede")
            return
        try:
            result = await asyncio.to_thread(self.event_plane.bootstrap_shadow)
            request = self.event_plane.shadow_request()
            log.info(
                "EventPlane: %s — %s/%s (%d simbolos)"
                % (result.get("status"), config.databento_shadow_dataset,
                   config.databento_shadow_schema, len(request.symbols))
            )
            backfill = await asyncio.to_thread(self.event_plane.backfill_step)
            log.info(
                "EventPlane backfill: %s — %d sessao(oes) neste ciclo, %d pendentes antes"
                % (backfill.get("status"),
                   len(backfill.get("completed_this_cycle", [])),
                   int(backfill.get("remaining_before_cycle", 0) or 0))
            )
        except Exception as exc:
            log.error(f"EventPlane shadow falhou fechado: {type(exc).__name__}: {exc}")
            # Falhas financeiras/de qualidade nao podem ficar escondidas no
            # terminal. Deduplica por tipo+motivo para nao avisar a cada hora.
            try:
                import hashlib as _hashlib, json as _json
                from datetime import date as _date
                marker = config.data_lake_dir / "events" / "_catalog" / "discord_alert.json"
                reason = f"{type(exc).__name__}: {str(exc)[:500]}"
                alert_id = _hashlib.sha256(reason.encode("utf-8")).hexdigest()
                previous = _json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {}
                if previous.get("alert_id") != alert_id or previous.get("date") != str(_date.today()):
                    from monitoring.discord_notifier import send, make_embed
                    await asyncio.to_thread(send, embeds=[make_embed(
                        "🔴 Databento bloqueado com segurança",
                        reason + "\n\nNenhuma nova compra será repetida silenciosamente. "
                        "Verifique o terminal antes de intervir.",
                        color=0xDC2626,
                    )])
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    temporary = marker.with_name(marker.name + ".tmp")
                    temporary.write_text(_json.dumps({"alert_id": alert_id,
                                                       "date": str(_date.today()),
                                                       "reason": reason},
                                                      ensure_ascii=False, indent=2),
                                         encoding="utf-8")
                    temporary.replace(marker)
            except Exception as alert_exc:
                log.warning(f"EventPlane: alerta Discord falhou: {alert_exc}")

    async def _train_regime_model(self):
        """Treina o regime model com dados disponíveis.

        `regime_retrain_days` era campo INERTE: o modelo retreinava a CADA
        BOOT, nao a cada N dias (auditoria de 14/08/2026). Com autostart e
        reinicios do Windows Update isso podia significar varios treinos por
        dia — GPU gasta para reaprender a mesma coisa.
        """
        await asyncio.sleep(30)  # Aguarda dados chegarem
        try:
            import os as _o, time as _t, json as _j
            _dias = int(getattr(config, "regime_retrain_days", 30) or 30)
            # O gate olhava só idade + existencia + flag. Um modelo com PERICIA
            # NEGATIVA comprovada (regime_skill.json, gravado ao lado) ficava
            # blindado por 30 dias de calendario. Calendario responde "faz
            # tempo?"; a pergunta certa e "isto vale alguma coisa?".
            _ganho = None
            try:
                _skill = _j.load(open(_o.path.join(
                    str(config.data_lake_dir), "regime_skill.json")))
                _ganho = float(_skill.get("gain"))
            except Exception:
                _ganho = None
            if _ganho is not None and _ganho <= 0:
                _dias = max(1, int(getattr(config, "regime_retrain_days_no_skill", 7) or 7))
            _marca = _o.path.join(str(config.data_lake_dir), "regime_last_train.json")
            if _o.path.exists(_marca):
                _idade = (_t.time() - _j.load(open(_marca)).get("ts", 0)) / 86400.0
                _tem_modelo = _o.path.exists(
                    str(config.data_lake_dir / "regimes" / "regime_transformer.pt"))
                if (_tem_modelo and _idade < _dias
                        and bool(getattr(self.regime_model, "_model_ready", False))):
                    if _ganho is not None and _ganho <= 0:
                        # Cooldown mais curto, e NUNCA em silencio: enquanto o
                        # ganho for <= 0 o regime nao condiciona nada, serve
                        # baseline e o log precisa dizer isso.
                        log.warning(
                            f"Regime: modelo SEM PERICIA (ganho {_ganho:+.3f}) — "
                            f"retreino adiado por cooldown curto ({_idade:.1f}d "
                            f"de {_dias}d). Ate la o regime serve BASELINE e nao "
                            f"informa decisao. Retreinar pode nao resolver: o "
                            f"problema pode ser ausencia de sinal nas features, "
                            f"nao modelo desatualizado."
                        )
                    else:
                        log.info(f"Regime: modelo treinado ha {_idade:.1f} dias "
                                 f"(limite {_dias}) — retreino ADIADO")
                    return
                if _ganho is not None and _ganho <= 0:
                    log.warning(
                        f"Regime: retreinando por PERICIA NEGATIVA (ganho "
                        f"{_ganho:+.3f}, {_idade:.1f}d desde o ultimo treino)"
                    )
        except Exception as _e:
            log.debug(f"marca de retreino: {_e}")
        try:
            features_dict = {}
            for asset in config.data_assets_stocks[:5]:
                df = self.storage.load_features(asset)
                if df is not None:
                    features_dict[asset] = df

            if features_dict:
                await self.regime_model.train(features_dict, epochs=10)
                try:
                    import os as _o2, time as _t2, json as _j2
                    _m = _o2.path.join(str(config.data_lake_dir), "regime_last_train.json")
                    _tmp = _m + ".tmp"
                    with open(_tmp, "w") as _f:
                        _j2.dump({"ts": _t2.time()}, _f)
                    _o2.replace(_tmp, _m)
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"Treino de regime model adiado: {e}")

    async def run(self):
        """Loop principal do sistema."""
        await self.start()

        # Feedback loop: atualiza NAV periodicamente
        asyncio.create_task(self._nav_update_loop())

        # Aguarda até o sistema ser parado
        try:
            while True:
                await asyncio.sleep(60)
                log.debug(f"Sistema rodando — ciclo {self.orchestrator._cycle_count}")
        except asyncio.CancelledError:
            await self.stop()

    async def _nav_update_loop(self):
        """Atualiza NAV e preços do broker a cada 30s."""
        import asyncio as _asyncio
        loop = _asyncio.get_running_loop()
        while True:
            try:
                # Atualiza preços das posições atuais com dados mais recentes do storage
                broker = self.execution_engine._default_broker
                positions = await broker.get_positions()
                if positions:
                    def _refresh_prices():
                        # SO marca com preco de barra LIQUIDADA. Durante o pregao
                        # o ultimo close das features e a vela inacabada — marcar
                        # com ela injeta preco intradiario no nav.csv, que e o
                        # ledger do braco GA.
                        try:
                            from core.market_clock import last_settled_session
                            _cut = last_settled_session()
                        except Exception:
                            _cut = None
                        for symbol in positions:
                            try:
                                df = self.storage.load_features(symbol)
                                if df is None or df.empty or "close" not in df.columns:
                                    continue
                                if _cut is not None:
                                    try:
                                        import pandas as _pd
                                        _idx = _pd.to_datetime(df.index)
                                        df = df[_idx.date <= _cut]
                                    except Exception:
                                        pass
                                if df.empty:
                                    continue
                                price = float(df["close"].iloc[-1])
                                if price > 0:
                                    broker.update_price(symbol, price)
                            except Exception:
                                pass
                    await loop.run_in_executor(None, _refresh_prices)

                nav = await self.execution_engine.get_account_value()
                self.portfolio_engine.update_nav(nav)
                self.risk_engine.update_nav(nav)
            except Exception:
                pass
            # TELEMETRIA DE MEMORIA. Em 13/08/2026 o servidor sumiu por 10h30
            # sem traceback, sem shutdown e sem nada escrito em disco. Nao foi
            # possivel provar se foi falta de memoria ou parada da maquina —
            # porque ninguem estava medindo. Agora fica registrado: se voltar a
            # acontecer, a serie mostra se a memoria estava subindo antes.
            try:
                import psutil as _ps, os as _o, json as _js, time as _tm
                _proc = _ps.Process(_o.getpid())
                _rss = _proc.memory_info().rss / 1e9
                _vm = _ps.virtual_memory()
                _reg = {"ts": _tm.time(), "rss_gb": round(_rss, 3),
                        "sys_usada_pct": _vm.percent,
                        "sys_livre_gb": round(_vm.available / 1e9, 2),
                        "threads": _proc.num_threads()}
                _mf = _o.path.join(str(config.data_lake_dir), "memoria.jsonl")
                with open(_mf, "a", encoding="utf-8") as _fh:
                    _fh.write(_js.dumps(_reg) + "\n")
                # alerta antes de morrer, nao depois
                if _vm.percent >= 90 and not getattr(self, "_avisou_ram", False):
                    self._avisou_ram = True
                    log.error(f"MEMORIA CRITICA: sistema em {_vm.percent:.0f}% "
                              f"({_vm.available/1e9:.1f} GB livres), este processo "
                              f"usando {_rss:.1f} GB. Risco de morte sem aviso.")
                    try:
                        from monitoring.discord_notifier import send, make_embed
                        send(embeds=[make_embed(
                            "🟠 Memória no limite",
                            f"O computador está com **{_vm.percent:.0f}%** da memória usada "
                            f"(só {_vm.available/1e9:.1f} GB livres). O QHOS está usando "
                            f"**{_rss:.1f} GB**.\n\n➡️ *Se passar de tudo, o Windows derruba "
                            "o processo sem aviso e sem deixar erro no log.*",
                            color=0xE67E22)])
                    except Exception:
                        pass
                elif _vm.percent < 80:
                    self._avisou_ram = False
            except Exception:
                pass

            # HEARTBEAT de liveness (a cada 30s): a vigia desacoplada usa a
            # IDADE deste arquivo para saber em segundos se o servidor esta vivo.
            try:
                import os as _os, time as _t
                # raiz canonica do config (root_dir) em vez de recalcular o
                # caminho a mao — era o ultimo campo inerte do config.
                _hb = _os.path.join(str(config.data_lake_dir), "server_heartbeat.txt")
                _os.makedirs(_os.path.dirname(_hb), exist_ok=True)
                _tmp = _hb + ".tmp"
                with open(_tmp, "w") as _f:
                    _f.write(str(_t.time()))
                _os.replace(_tmp, _hb)
            except Exception:
                pass
            # Snapshot autoritativo consumido por dashboard, Discord e agentes.
            # Falhar aqui degrada observabilidade, nunca derruba o servidor.
            try:
                import os as _truth_os
                from governance.epistemics import write_current_state
                write_current_state({
                    "pid": _truth_os.getpid(),
                    "code_hash": self._startup_code_hash,
                    "execution_mode": (
                        "LIVE_BROKER_FILL" if bool(getattr(self.execution_engine, "_is_live", False))
                        else "SIMULATED_CLOSE_FILL"
                    ),
                    "orchestrator_cycles": int(getattr(self.orchestrator, "_cycle_count", 0)),
                })
            except Exception as _truth_error:
                log.debug(f"CURRENT_STATE indisponivel: {_truth_error}")
            await asyncio.sleep(30)

    async def _trend_loop(self):
        """Passo dos braços a cada 30min — SÓ com a barra diária liquidada.

        Antes do portão os braços rodavam com o pregão aberto e gravavam no
        ledger uma vela inacabada (12/08/2026: spy_close 772.687 gravado vs
        772.490 liquidado). O ledger forward é a única evidência honesta do
        projeto; não pode ser escrito com dado que ainda vai mudar.
        """
        import asyncio as _aio
        from core import market_clock as mclock
        while True:
            try:
                # CRYPTO-CARRY roda ANTES do portão de pregão: cripto é 24/7 e
                # tem relógio próprio (dia UTC liquidado, §E da spec). O passo
                # é idempotente e barato; o portão de ações não o governa.
                if getattr(self, "crypto_carry", None) is not None:
                    loop = _aio.get_running_loop()
                    await loop.run_in_executor(None, self.crypto_carry.step)
                if not mclock.should_act():
                    await _aio.sleep(600)
                    continue
                if getattr(self, "trend_paper", None) is not None:
                    loop = _aio.get_running_loop()
                    await loop.run_in_executor(None, self.trend_paper.step)
                if getattr(self, "symb1_paper", None) is not None:
                    loop = _aio.get_running_loop()
                    await loop.run_in_executor(None, self.symb1_paper.step)
                if getattr(self, "manual_arm", None) is not None:
                    loop = _aio.get_running_loop()
                    await loop.run_in_executor(None, self.manual_arm.step)
                if getattr(self, "shadow_executor", None) is not None:
                    loop = _aio.get_running_loop()
                    await loop.run_in_executor(None, self.shadow_executor.step)
            except Exception as e:
                log.debug(f"trend_loop: {e}")
            await _aio.sleep(1800)

    async def stop(self):
        """Para o sistema graciosamente."""
        log.info("Desligando Quant HPC OS...")

        # Para em ordem inversa
        for mod in [
            self.dashboard, self.orchestrator, self.execution_engine,
            self.portfolio_engine, self.kill_switch, self.risk_engine,
            self.evolution_engine, self.validator, self.backtest_cluster,
            self.regime_model, self.feature_engine, self.ingestion,
        ]:
            try:
                if hasattr(mod, "stop"):
                    await mod.stop()
            except Exception:
                pass

        await event_bus.stop()
        self.storage.close()
        log.info("Sistema encerrado.")


_shutdown_event: asyncio.Event | None = None


def handle_signal(sig, frame):
    """Handler para SIGINT/SIGTERM — cancela o loop sem sys.exit() abrupto."""
    print(f"\n[SIGNAL] Recebido {sig} — encerrando...", flush=True)
    log.info(f"Sinal recebido: {sig} — iniciando shutdown limpo")
    # Sinaliza o evento de shutdown; o loop principal detecta e para.
    if _shutdown_event is not None:
        # call_soon_threadsafe é necessário pois signals podem vir de threads
        try:
            loop = asyncio.get_event_loop()
            loop.call_soon_threadsafe(_shutdown_event.set)
        except Exception:
            pass


async def main():
    """Ponto de entrada principal."""
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("[STARTUP] Iniciando Quant HPC OS...", flush=True)
    try:
        system = QuantHPCOS()
    except Exception as e:
        print(f"[STARTUP ERROR] Falha no __init__: {type(e).__name__}: {e}", flush=True)
        log.critical(f"Falha na inicialização: {type(e).__name__}: {e}")
        raise

    print("[STARTUP] Módulos instanciados — iniciando servi\u00e7os...", flush=True)
    try:
        await system.start()
    except Exception as e:
        print(f"[STARTUP ERROR] Falha no start(): {type(e).__name__}: {e}", flush=True)
        log.critical(f"Falha no start(): {type(e).__name__}: {e}")
        raise

    print("[STARTUP] Sistema operacional — aguardando shutdown...", flush=True)
    asyncio.create_task(system._nav_update_loop())
    asyncio.create_task(system._trend_loop())

    try:
        while not _shutdown_event.is_set():
            await asyncio.sleep(60)
            log.debug(f"Sistema rodando — ciclo {system.orchestrator._cycle_count}")
    except asyncio.CancelledError:
        pass
    finally:
        print("[SHUTDOWN] Encerrando sistema...", flush=True)
        await system.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[INTERRUPTED] Sistema interrompido pelo usuário.", flush=True)
        log.info("Sistema interrompido pelo usuário.")
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}", flush=True)
        log.critical(f"Erro fatal: {type(e).__name__}: {e}")
        raise
