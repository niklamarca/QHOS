"""
QUANT HPC OS — HPC Orchestrator
Controlador central do sistema. Gerencia todos os módulos, jobs e o ciclo de vida.
Equivalente ao kernel de um sistema operacional HPC.
"""
from __future__ import annotations
import asyncio
import time
import gc
import psutil
from datetime import datetime
from typing import Dict, Optional, TYPE_CHECKING
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.registry import ModuleRegistry, ModuleStatus, registry
from core.logger import get_logger
from .job_queue import JobQueue, Job, JobPriority, JobType, JobStatus

if TYPE_CHECKING:
    pass

log = get_logger("Orchestrator")


class ResourceMonitor:
    """Monitora uso de CPU/GPU/RAM para evitar overload."""

    def __init__(self):
        # Cache GPU stats para evitar subprocess bloqueante no event loop
        self._cached_gpu: Dict = {"load": 0, "memory_used": 0, "memory_total": 0, "temperature": 0}
        self._gpu_cache_time: float = 0
        self._GPU_CACHE_TTL: float = 5.0  # atualiza a cada 5s no máximo

    def get_cpu_percent(self) -> float:
        return psutil.cpu_percent(interval=None)

    def get_ram_percent(self) -> float:
        return psutil.virtual_memory().percent

    def get_gpu_stats(self) -> Dict:
        """Retorna stats de GPU com cache de 5s para não bloquear o event loop."""
        now = time.time()
        if now - self._gpu_cache_time < self._GPU_CACHE_TTL:
            return self._cached_gpu
        try:
            import GPUtil
            gpus = GPUtil.getGPUs()
            if gpus:
                g = gpus[0]
                self._cached_gpu = {
                    "load": g.load * 100,
                    "memory_used": g.memoryUsed,
                    "memory_total": g.memoryTotal,
                    "temperature": g.temperature,
                }
        except Exception:
            pass
        self._gpu_cache_time = now
        return self._cached_gpu

    def can_schedule_cpu(self) -> bool:
        """CPU disponível para novo job de backtest."""
        return self.get_cpu_percent() < 90.0

    def can_schedule_gpu(self) -> bool:
        """GPU disponível para regime model (usa cache — não bloqueia)."""
        stats = self.get_gpu_stats()
        return stats["load"] < 85.0

    def snapshot(self) -> Dict:
        return {
            "cpu_percent": self.get_cpu_percent(),
            "ram_percent": self.get_ram_percent(),
            "gpu": self.get_gpu_stats(),
            "timestamp": time.time(),
        }


class HPCOrchestrator:
    """
    Controlador central do Quant HPC OS.

    Responsabilidades:
    - Gerenciar ciclo de vida de todos os módulos
    - Controlar fila de jobs com prioridade
    - Escalonar workloads CPU/GPU/IO
    - Coordenar o pipeline completo de alpha generation
    - Responder a eventos de risco com preempção
    - Manter o sistema funcionando mesmo com falhas parciais
    """

    def __init__(self):
        self.queue = JobQueue()
        self.resources = ResourceMonitor()
        self.bus = event_bus
        self._running = False
        self._workers: list = []
        self._cycle_count: int = 0
        self._last_data_refresh: float = 0
        self._last_evolution_cycle: float = 0
        self._last_regime_update: float = 0
        self._last_portfolio_rebal: float = 0
        self._last_risk_check: float = 0
        self._last_signal_cycle: float = 0   # evita disparos múltiplos por data refresh
        self._last_verified_backup_day: str = ""
        self._kill_level: int = 0             # 0=normal, 1=reduz, 2=desliga, 3=flatten
        self._start_time: float = time.time()

        # Módulos (injetados pelo main)
        self.data_engine = None
        self.feature_engine = None
        self.regime_model = None
        self.evolution_engine = None
        self.backtest_cluster = None
        self.validator = None
        self.risk_engine = None
        self.portfolio_engine = None
        self.execution_engine = None
        self.dashboard = None

        # Subscreve a eventos críticos
        self.bus.subscribe(EventType.KILL_SWITCH_L1, self._on_kill_l1)
        self.bus.subscribe(EventType.KILL_SWITCH_L2, self._on_kill_l2)
        self.bus.subscribe(EventType.KILL_SWITCH_L3, self._on_kill_l3)
        self.bus.subscribe(EventType.RISK_LIMIT_BREACH, self._on_risk_breach)
        self.bus.subscribe(EventType.WEIGHTS_REBALANCED, self._on_weights_rebalanced)

    def inject_modules(self, **modules):
        """Injeta referências a todos os módulos do sistema."""
        for name, mod in modules.items():
            setattr(self, name, mod)
            log.debug(f"Módulo injetado: {name}")

    # ─── Lifecycle ───────────────────────────────────────────────────

    async def start(self):
        """Inicia o orchestrator e todos os workers."""
        log.info("=" * 60)
        log.info(f"  {config.system_name} v{config.system_version}")
        log.info(f"  Modo: {config.system_mode.upper()}")
        log.info(f"  CPU: {config.max_cpu_threads} threads")
        log.info("=" * 60)

        self._running = True

        # Inicia workers de jobs (CPU e GPU separados)
        n_cpu_workers = min(config.max_cpu_threads, 32)
        n_gpu_workers = 2
        n_io_workers = 4
        n_meta_workers = 2   # LLM, risk, orchestration

        for i in range(n_cpu_workers):
            w = asyncio.create_task(self._worker_cpu(f"cpu-{i}"))
            self._workers.append(w)

        for i in range(n_gpu_workers):
            w = asyncio.create_task(self._worker_gpu(f"gpu-{i}"))
            self._workers.append(w)

        for i in range(n_io_workers):
            w = asyncio.create_task(self._worker_io(f"io-{i}"))
            self._workers.append(w)

        for i in range(n_meta_workers):
            w = asyncio.create_task(self._worker_meta(f"meta-{i}"))
            self._workers.append(w)

        # Loop principal de orquestração
        asyncio.create_task(self._orchestration_loop())
        asyncio.create_task(self._metrics_loop())

        # Atrasa primeiros ciclos para dar tempo ao bootstrap completar
        _now = time.time()
        self._boot_time = _now  # marca o momento do boot
        self._last_evolution_cycle = _now
        self._last_regime_update = _now       # regime só roda após 5 min
        self._last_portfolio_rebal = _now     # rebalance só roda após 1h
        # O bootstrap calcula o primeiro snapshot de features depois do refresh.
        # Agendar outro job aqui fazia leitor e escritor disputarem o mesmo
        # parquet logo no boot; o proximo recompute periodico ocorre em 30 min.
        self._last_feature_compute = _now

        registry.set_status("orchestrator", ModuleStatus.RUNNING)
        await self.bus.publish(Event(
            type=EventType.SYSTEM_STARTED,
            source="orchestrator",
            payload={"mode": config.system_mode, "workers": len(self._workers)}
        ))
        log.info(f"Orchestrator iniciado com {len(self._workers)} workers.")

    async def stop(self):
        """Para o sistema graciosamente."""
        log.info("Desligando Quant HPC OS...")
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        await self.bus.publish(Event(
            type=EventType.SYSTEM_SHUTDOWN,
            source="orchestrator",
            payload={}
        ))

    # ─── Workers ─────────────────────────────────────────────────────

    async def _worker_cpu(self, worker_id: str):
        """Worker para jobs de backtest e feature computation (CPU-bound)."""
        log.debug(f"Worker CPU iniciado: {worker_id}")
        while self._running:
            try:
                # Só processa jobs CPU se recurso disponível
                if not self.resources.can_schedule_cpu():
                    await asyncio.sleep(0.5)
                    continue

                job = await self.queue.dequeue()
                if job is None:
                    await asyncio.sleep(0.1)
                    continue

                # Filtra por tipos CPU
                cpu_types = {
                    JobType.BACKTEST,
                    JobType.WALK_FORWARD,
                    JobType.MONTE_CARLO,
                    JobType.FEATURE_COMPUTE,
                    JobType.VALIDATION,
                }
                if job.job_type not in cpu_types:
                    # Devolve o MESMO job (não cria novo) para evitar multiplicação na fila
                    await self.queue._queue.put((job.priority, job.created_at, job))
                    await asyncio.sleep(0.1)
                    continue

                await self._execute_job(job, worker_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Worker CPU {worker_id} erro: {e}")
                await asyncio.sleep(1)

    async def _worker_gpu(self, worker_id: str):
        """Worker para jobs de GPU (regime model, etc.)."""
        while self._running:
            try:
                if not self.resources.can_schedule_gpu():
                    await asyncio.sleep(1.0)
                    continue

                job = await self.queue.dequeue()
                if job is None:
                    await asyncio.sleep(0.2)
                    continue

                gpu_types = {JobType.REGIME_PREDICT}
                if job.job_type not in gpu_types:
                    await self.queue._queue.put((job.priority, job.created_at, job))
                    await asyncio.sleep(0.1)
                    continue

                await self._execute_job(job, worker_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Worker GPU {worker_id} erro: {e}")
                await asyncio.sleep(1)

    async def _worker_io(self, worker_id: str):
        """Worker para jobs I/O (data ingestion, storage)."""
        while self._running:
            try:
                job = await self.queue.dequeue()
                if job is None:
                    await asyncio.sleep(0.1)
                    continue

                io_types = {JobType.DATA_INGEST, JobType.PAPER_EXECUTE, JobType.LIVE_EXECUTE}
                if job.job_type not in io_types:
                    await self.queue._queue.put((job.priority, job.created_at, job))
                    await asyncio.sleep(0.1)
                    continue

                await self._execute_job(job, worker_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Worker IO {worker_id} erro: {e}")
                await asyncio.sleep(1)

    async def _worker_meta(self, worker_id: str):
        """Worker para jobs meta (LLM, risk, portfolio, evolution)."""
        while self._running:
            try:
                job = await self.queue.dequeue()
                if job is None:
                    await asyncio.sleep(0.1)
                    continue

                meta_types = {
                    JobType.RISK_CHECK,
                    JobType.KILL_SWITCH,
                    JobType.EVOLUTION_STEP,
                    JobType.LLM_AGENT,
                    JobType.PORTFOLIO_REBAL,
                }
                if job.job_type not in meta_types:
                    await self.queue._queue.put((job.priority, job.created_at, job))
                    await asyncio.sleep(0.1)
                    continue

                await self._execute_job(job, worker_id)

            except asyncio.CancelledError:
                break

    async def _execute_job(self, job: Job, worker_id: str):
        """Executa um job com timeout e tratamento de erro."""
        try:
            log.debug(f"[{worker_id}] Executando {job}")
            result = await asyncio.wait_for(
                job.coro(*job.args, **job.kwargs),
                timeout=job.timeout_seconds
            )
            self.queue.complete(job, result)
            await self.bus.publish(Event(
                type=EventType.JOB_COMPLETE,
                source="orchestrator",
                payload={
                    "job_id": job.job_id,
                    "job_type": job.job_type.value,
                    "elapsed": job.elapsed,
                    "worker": worker_id,
                }
            ))
        except asyncio.TimeoutError:
            job.status = JobStatus.TIMEOUT
            self.queue.fail(job, f"Timeout após {job.timeout_seconds}s")
        except Exception as e:
            self.queue.fail(job, str(e))
            await self.bus.publish(Event(
                type=EventType.JOB_FAILED,
                source="orchestrator",
                payload={"job_id": job.job_id, "error": str(e)}
            ))

    # ─── Orchestration Loop ──────────────────────────────────────────

    async def _orchestration_loop(self):
        """
        Loop principal de orquestração.
        Coordena o pipeline DATA → EVOLUTION → BACKTEST → REGIME → RISK → PORTFOLIO.
        """
        log.info("Pipeline de orquestração iniciado.")
        from core import market_clock as mclock
        _last_sess = None
        INTERVALS = {
            "data":       config.data_refresh_interval_minutes * 60,
            "regime":     300,      # regime a cada 5 min
            # 833 chamadas foram descartadas com "run_generation ja em execucao"
            # porque o agendamento (120s) era mais rapido que uma geracao. Com o
            # historico completo (17-27x mais barras) a geracao demora ainda mais.
            "evolution":  600,      # evolução a cada 10 min
            "portfolio":  config.portfolio_rebalance_hours * 3600,
        }

        while self._running:
            now = time.time()
            self._cycle_count += 1

            # ─── PORTAO DE MERCADO ────────────────────────────────────
            # Hot path (dados/sinais/ordens) so roda quando faz sentido.
            # Cold path (evolucao, regime, risco) segue 24/7: e pesquisa
            # sobre historico, nao toca no mercado.
            _sess = mclock.session_state()
            _act_ok = mclock.should_act()
            # Janela normal de ingestao + RECUPERACAO: se o servidor subiu depois
            # que a janela fechou (PC desligado o dia todo, boot as 21h ET), ainda
            # e preciso buscar a barra do dia UMA vez — senao o pregao inteiro se
            # perde em silencio.
            _catchup = (_act_ok and self._last_data_refresh <= self._boot_time)
            _ingest_ok = mclock.should_ingest() or _catchup
            if _sess != _last_sess:
                _d = mclock.describe()
                log.info(f"[MERCADO] {_d['label']} ({_d['et']}) — "
                         f"ingestao={'ON' if _ingest_ok else 'OFF'} "
                         f"execucao={'ON' if _act_ok else 'OFF'} | "
                         f"ultima barra liquidada: {_d['last_settled']}")
                if _catchup:
                    log.info("[MERCADO] recuperacao: janela fechada mas barra do dia "
                             "ainda nao ingerida neste boot — buscando uma vez")
                if not _act_ok:
                    log.info(f"[MERCADO] proxima abertura: {_d['next_open']}")
                _last_sess = _sess

            try:
                # 1. Data ingestion — so na janela do pregao + liquidacao.
                #    Fora dela o provider nao tem nada novo e a barra do dia
                #    ainda esta INCOMPLETA (foi o que sujou o ledger em 12/08).
                if _ingest_ok and (_catchup or now - self._last_data_refresh >= INTERVALS["data"]):
                    await self._schedule_data_refresh()
                    self._last_data_refresh = now

                # 1b. Recompute periodico de FEATURES. compute_all busca dados
                # frescos e recalcula as features. SEM isto, as features ficam
                # congeladas no boot -> os precos que marcam o portfolio nao
                # mudam -> o NAV congela -> o track-record/scorecard ficam
                # inuteis (foi o que travou o NAV em $99.937 por 26h).
                if _ingest_ok and now - getattr(self, "_last_feature_compute", 0.0) >= 1800:  # 30 min
                    await self._schedule_feature_compute()
                    self._last_feature_compute = now

                # 2. Regime update
                if now - self._last_regime_update >= INTERVALS["regime"]:
                    await self._schedule_regime_update()
                    self._last_regime_update = now

                # 3. Evolution cycle (geração de novas estratégias)
                if (now - self._last_evolution_cycle >= INTERVALS["evolution"]
                        and self._kill_level < 2):
                    await self._schedule_evolution_cycle()
                    self._last_evolution_cycle = now

                # 4. Portfolio rebalancing
                # Rebalanceia a cada 1h OU imediatamente 2min após boot (DB pode ter validadas)
                _rebal_due = (now - self._last_portfolio_rebal >= 3600)  # 1h
                _first_rebal = (self._last_portfolio_rebal == self._boot_time
                                and now - self._boot_time > 120)  # 2min após boot
                # Trigger de oportunidade: portfolio vazio mas HOF tem estratégias validadas
                _port_empty = not bool(
                    getattr(self.portfolio_engine, "_active_strategies", None)
                )
                _hof_has_strats = bool(
                    self.evolution_engine
                    and getattr(self.evolution_engine, "hall_of_fame", None)
                )
                _rebal_cooldown = (now - self._last_portfolio_rebal) > 300  # mín 5min
                # _act_ok ENTRA AQUI, nao so na acao. Sem isto o gatilho ficava
                # True com o mercado fechado, logava, era bloqueado logo abaixo,
                # e como o rebalance nunca rodava o cooldown nunca reiniciava:
                # a mesma linha a cada 10s, ~8.600 por dia. Bug introduzido junto
                # com o portao de mercado em 12/08 e detectado em 14/08.
                _opportunity_rebal = (
                    _act_ok and _port_empty and _hof_has_strats
                    and _rebal_cooldown and self._kill_level < 1
                )
                if _opportunity_rebal:
                    log.info("Portfolio vazio com HOF disponível — rebalanceamento de oportunidade")
                if (_act_ok and (_rebal_due or _first_rebal or _opportunity_rebal)
                        and self._kill_level < 1):
                    await self._schedule_portfolio_rebal()
                    self._last_portfolio_rebal = now

                # 4b. Signal execution após data refresh (sinais com dados frescos)
                # Roda 1 vez por data refresh — não a cada 10s (throttle via _last_signal_cycle)
                _signal_executor = getattr(self, "signal_executor", None)
                _data_fresh = (now - self._last_data_refresh < 120)        # janela de 2min
                _signal_not_run = (self._last_signal_cycle < self._last_data_refresh)  # 1x por refresh
                if (_act_ok and _signal_executor and _data_fresh and _signal_not_run
                        and _signal_executor._active_strategies
                        and self._kill_level < 1):
                    try:
                        nav = await self.execution_engine.get_account_value()
                        await _signal_executor.run_daily_cycle(nav)
                        self._last_signal_cycle = now
                    except Exception as e:
                        log.error(f"Signal cycle pós-data-refresh: {e}")

                # 5. Risk check — máximo 1 a cada 30s (evita flood na fila)
                if now - self._last_risk_check >= 30:
                    await self._schedule_risk_check()
                    self._last_risk_check = now

                # 6. Backup diário no próprio processo do DuckDB. Só marca a
                # data após agendar; se falhar, o log/Discord torna o problema
                # observável em vez de declarar uma cópia inexistente como boa.
                local_now = datetime.now()
                today = local_now.strftime("%Y-%m-%d")
                if (local_now.hour == config.verified_backup_hour_local
                        and self._last_verified_backup_day != today
                        and getattr(self.data_engine, "storage", None) is not None):
                    self._last_verified_backup_day = today
                    try:
                        loop = asyncio.get_running_loop()
                        target = await loop.run_in_executor(
                            None, self.data_engine.storage.create_verified_backup)
                        log.warning(f"Backup diário verificado: {target}")
                    except Exception as backup_error:
                        self._last_verified_backup_day = ""
                        log.error(f"Backup diário FALHOU: {backup_error}")

                registry.heartbeat("orchestrator")

                # GC explícito a cada 100 ciclos (~16 min) — libera memória acumulada
                if self._cycle_count % 100 == 0:
                    collected = gc.collect()
                    log.debug(f"GC: {collected} objetos coletados (ciclo {self._cycle_count})")

            except Exception as e:
                log.error(f"Erro no loop de orquestração: {e}")

            await asyncio.sleep(10)  # ciclo a cada 10s

    async def _schedule_feature_compute(self):
        """Enfileira recomputo de features (compute_all: busca dados frescos +
        recalcula). Sem isto as features congelam no boot e o NAV para de se
        mover. O tipo de job e o worker ja existiam; so faltava enfileirar."""
        if not self.feature_engine:
            return
        try:
            # Features devem ler um snapshot estavel: se ha ingestao pendente
            # ou em curso, o loop tenta de novo no proximo ciclo de 10 segundos.
            ingest = self.queue.count_by_type(JobType.DATA_INGEST)
            if ingest["pending"] or ingest["active"]:
                log.debug("feature_compute adiado: data_ingest ainda ativo")
                return
            counts = self.queue.count_by_type(JobType.FEATURE_COMPUTE)
            if counts["pending"] or counts["active"]:
                return  # ja ha um recompute pendente ou em execucao
        except Exception:
            pass
        await self.queue.enqueue(
            JobType.FEATURE_COMPUTE,
            self.feature_engine.compute_all,
            priority=JobPriority.P3_FEATURES,
            timeout=300,
        )

    async def _schedule_data_refresh(self):
        """Enfileira job de atualização de dados."""
        if self.data_engine:
            # Nao enfileira se ja ha data_ingest pendente ou ativa. A fila guarda
            # tuplas (prioridade, timestamp, Job), portanto checar a tupla como
            # se fosse Job falhava em silencio e permitia downloads concorrentes.
            try:
                counts = self.queue.count_by_type(JobType.DATA_INGEST)
                if counts["pending"] or counts["active"]:
                    log.debug(
                        "data_ingest: "
                        f"{counts['pending']} pendente(s), {counts['active']} ativo(s) "
                        "— pulando enfileiramento"
                    )
                    return
            except Exception:
                pass
            await self.queue.enqueue(
                JobType.DATA_INGEST,
                self.data_engine.refresh_all,
                priority=JobPriority.P3_FEATURES,
                timeout=300,   # 120s era curto para 26 assets; aumentado para 5 min
            )

    async def _schedule_regime_update(self):
        """Enfileira predição de regime (GPU)."""
        if self.regime_model:
            await self.queue.enqueue(
                JobType.REGIME_PREDICT,
                self.regime_model.predict_current_regime,
                priority=JobPriority.P1_VALIDATION,
                timeout=900,   # historico completo: 17-27x mais barras que antes
            )

    async def _schedule_evolution_cycle(self):
        """Enfileira ciclo de evolução de estratégias."""
        if self.evolution_engine and self._kill_level == 0:
            # Limita a 1 evolution_step no total (pendente + executando)
            # asyncio.PriorityQueue armazena como (priority, ts, job) — usa _queue interno
            try:
                # Verifica ativos
                active = sum(
                    1 for j in self.queue._active.values()
                    if getattr(j, "job_type", None) == JobType.EVOLUTION_STEP
                )
                # Verifica pendentes: asyncio.PriorityQueue._queue é um heapq de tuplas (prio, ts, job)
                pending = sum(
                    1 for item in list(self.queue._queue._queue)
                    if isinstance(item, tuple) and len(item) == 3
                    and getattr(item[2], "job_type", None) == JobType.EVOLUTION_STEP
                )
                if pending + active >= 1:
                    log.debug(f"evolution_step: {active} ativo(s) + {pending} pendente(s) — pulando")
                    return
            except Exception as e:
                log.debug(f"evolution dedup check erro: {e} — enfileirando mesmo assim")
            await self.queue.enqueue(
                JobType.EVOLUTION_STEP,
                self.evolution_engine.run_generation,
                priority=JobPriority.P4_EXPLORATION,
                # 20min: com o Teste 8 a geracao faz backtest (500 estrategias,
                # ~180s) + validacao completa das 20 melhores. Os 600s antigos
                # matavam a geracao antes do commit, e o contador nunca subia —
                # o sistema repetia a mesma geracao para sempre.
                timeout=1200,
            )

    async def _schedule_portfolio_rebal(self):
        """Enfileira rebalanceamento de portfólio."""
        if self.portfolio_engine:
            await self.queue.enqueue(
                JobType.PORTFOLIO_REBAL,
                self.portfolio_engine.rebalance,
                priority=JobPriority.P1_VALIDATION,
                timeout=300,  # 5min — DuckDB no Windows pode ser lento com 1000+ estratégias
            )

    async def _schedule_risk_check(self):
        """Enfileira verificação de risco (P0 — sempre prioritário)."""
        if self.risk_engine:
            await self.queue.enqueue(
                JobType.RISK_CHECK,
                self.risk_engine.check_all,
                priority=JobPriority.P0_RISK_CRITICAL,
                timeout=30,
            )

    # ─── Kill Switch Handlers ────────────────────────────────────────

    async def _on_weights_rebalanced(self, event: Event):
        """Executa sinais reais das estratégias quando novos pesos HRP chegam."""
        if not self.execution_engine or self._kill_level > 0:
            return

        signal_executor = getattr(self, "signal_executor", None)
        if not signal_executor:
            return

        try:
            # Atualiza o portfolio do executor com as estratégias e pesos atuais
            weights = event.payload.get("weights", {})      # strategy_id → weight
            active_strats = []
            if self.portfolio_engine:
                active_strats = getattr(self.portfolio_engine, "_active_strategies", [])

            signal_executor.update_portfolio(active_strats, weights)

            # Executa sinais com dados mais recentes
            nav = await self.execution_engine.get_account_value()
            await signal_executor.run_daily_cycle(nav)

        except Exception as e:
            log.error(f"Erro ao executar sinais no paper broker: {e}")

    async def _on_kill_l1(self, event: Event):
        """Nível 1: Reduz exposição de risco."""
        self._kill_level = max(self._kill_level, 1)
        log.warning("KILL SWITCH L1 — Reduzindo risco...")
        # Cancela jobs de evolução/exploração
        self.queue.cancel_by_type(JobType.EVOLUTION_STEP)
        self.queue.cancel_by_type(JobType.LLM_AGENT)

    async def _on_kill_l2(self, event: Event):
        """Nível 2: Desliga novas estratégias."""
        self._kill_level = max(self._kill_level, 2)
        log.error("KILL SWITCH L2 — Desligando estratégias...")
        self.queue.cancel_by_type(JobType.EVOLUTION_STEP)
        self.queue.cancel_by_type(JobType.VALIDATION)

    async def _on_kill_l3(self, event: Event):
        """Nível 3: Flatten total — fecha todas as posições."""
        self._kill_level = 3
        log.critical("KILL SWITCH L3 — FLATTEN TOTAL DO PORTFÓLIO!")
        if self.execution_engine:
            await self.queue.enqueue(
                JobType.KILL_SWITCH,
                self.execution_engine.flatten_all,
                priority=JobPriority.P0_RISK_CRITICAL,
                timeout=60,    # NAO AUMENTAR: liquidacao de emergencia tem de ser rapida
            )

    async def _on_risk_breach(self, event: Event):
        """Responde a violação de limite de risco."""
        breach_type = event.payload.get("breach_type", "unknown")
        severity = event.payload.get("severity", 1)
        log.warning(f"Risk breach: {breach_type} (severity={severity})")

        if severity >= 3:
            await self._on_kill_l3(event)
        elif severity >= 2:
            await self._on_kill_l2(event)
        # severity == 1 = ALERTA (def. do RiskBreach: 1=alerta, 2=reduzir, 3=flatten).
        # Apenas registra. NAO aciona kill switch: o L1 cancela evolution_step/
        # llm_agent, e um alerta recorrente (ex: position_concentration 100% com
        # poucas estrategias no inicio) congelava a evolucao num loop a cada breach.
        else:
            log.info(f"  alerta de risco (severity 1, sem acao automatica): {breach_type}")

    def reset_kill_switch(self):
        """Reset manual do kill switch (operador)."""
        old_level = self._kill_level
        self._kill_level = 0
        log.info(f"Kill switch resetado (era nível {old_level})")

    # ─── Metrics Loop ────────────────────────────────────────────────

    async def _metrics_loop(self):
        """Publica métricas do sistema a cada 5s para o dashboard."""
        while self._running:
            try:
                # snapshot() usa cache de GPU — não bloqueia o event loop
                resources = self.resources.snapshot()
                queue_metrics = self.queue.get_metrics()
                module_status = registry.get_status()
                metrics = {
                    "cycle": self._cycle_count,
                    "uptime": time.time() - self._start_time,
                    "kill_level": self._kill_level,
                    "resources": resources,
                    "queue": queue_metrics,
                    "modules": module_status,
                }
                # Checagem independente: atualiza o snapshot mesmo em períodos
                # sem ordens, para que health e Discord não reportem carteira
                # saudável quando broker/risk divergem.
                if self.execution_engine:
                    try:
                        from governance.reconciliation import reconciliation
                        reconciliation.check(
                            getattr(self.execution_engine, "_default_broker", None),
                            self.risk_engine, getattr(self, "signal_executor", None),
                        )
                    except Exception as _re:
                        log.error(f"Reconciliação indisponível: {_re}")
                await self.bus.publish(Event(
                    type=EventType.METRICS_UPDATE,
                    source="orchestrator",
                    payload=metrics,
                ))
            except Exception as e:
                log.error(f"Erro em metrics loop: {type(e).__name__}: {e}")
            await asyncio.sleep(5)

    def status(self) -> Dict:
        """Status consolidado do orchestrator."""
        return {
            "running": self._running,
            "cycle": self._cycle_count,
            "uptime": time.time() - self._start_time,
            "kill_level": self._kill_level,
            "workers": len(self._workers),
            "queue": self.queue.get_metrics(),
            "resources": self.resources.snapshot(),
        }
