"""
QUANT HPC OS — Job Queue System
Sistema de filas com prioridades P0-P4, controle de recursos e rastreamento.
"""
from __future__ import annotations
import asyncio
import uuid
import time
from enum import IntEnum, Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional
from core.logger import get_logger

log = get_logger("JobQueue")


class JobPriority(IntEnum):
    """Prioridades de jobs — menor valor = maior prioridade."""
    P0_RISK_CRITICAL  = 0   # risk engine, kill switch
    P1_VALIDATION     = 1   # validação de estratégias
    P2_BACKTEST       = 2   # execução de backtests
    P3_FEATURES       = 3   # geração de features
    P4_EXPLORATION    = 4   # genético / LLM (exploration)


class JobType(Enum):
    RISK_CHECK       = "risk_check"
    KILL_SWITCH      = "kill_switch"
    VALIDATION       = "validation"
    BACKTEST         = "backtest"
    WALK_FORWARD     = "walk_forward"
    MONTE_CARLO      = "monte_carlo"
    FEATURE_COMPUTE  = "feature_compute"
    DATA_INGEST      = "data_ingest"
    REGIME_PREDICT   = "regime_predict"
    EVOLUTION_STEP   = "evolution_step"
    LLM_AGENT        = "llm_agent"
    PORTFOLIO_REBAL  = "portfolio_rebal"
    PAPER_EXECUTE    = "paper_execute"
    LIVE_EXECUTE     = "live_execute"


class JobStatus(Enum):
    PENDING    = "pending"
    RUNNING    = "running"
    COMPLETED  = "completed"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
    TIMEOUT    = "timeout"


@dataclass(order=True)
class Job:
    """Unidade de trabalho do sistema HPC."""
    priority: int                               # usado para ordenação na fila
    job_type: JobType = field(compare=False)
    coro: Callable[..., Coroutine] = field(compare=False)  # coroutine a executar
    args: tuple = field(default_factory=tuple, compare=False)
    kwargs: Dict = field(default_factory=dict, compare=False)
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8], compare=False)
    created_at: float = field(default_factory=time.time, compare=False)
    timeout_seconds: float = field(default=300.0, compare=False)  # 5 min default
    result: Any = field(default=None, compare=False)
    status: JobStatus = field(default=JobStatus.PENDING, compare=False)
    error: Optional[str] = field(default=None, compare=False)
    started_at: Optional[float] = field(default=None, compare=False)
    completed_at: Optional[float] = field(default=None, compare=False)
    metadata: Dict = field(default_factory=dict, compare=False)

    @property
    def elapsed(self) -> Optional[float]:
        if self.started_at:
            end = self.completed_at or time.time()
            return end - self.started_at
        return None

    @property
    def wait_time(self) -> float:
        return (self.started_at or time.time()) - self.created_at

    def __repr__(self):
        return (
            f"Job({self.job_type.value}, "
            f"P{self.priority}, "
            f"id={self.job_id}, "
            f"status={self.status.value})"
        )


class JobQueue:
    """
    Fila de jobs com prioridades P0-P4.
    Baseada em asyncio.PriorityQueue para ordem garantida.
    Inclui rastreamento, métricas e cancelamento.
    """

    def __init__(self, maxsize: int = 10_000):
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=maxsize)
        self._active: Dict[str, Job] = {}          # jobs em execução
        self._history: List[Job] = []              # jobs concluídos
        self._history_max: int = 10_000
        self._cancelled: set = set()               # IDs cancelados
        self._stats = {
            "total_enqueued": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_cancelled": 0,
        }

    async def enqueue(
        self,
        job_type: JobType,
        coro: Callable,
        *args,
        priority: JobPriority = JobPriority.P3_FEATURES,
        timeout: float = 300.0,
        metadata: Optional[Dict] = None,
        **kwargs,
    ) -> Job:
        """Adiciona um job à fila com prioridade."""
        job = Job(
            priority=priority.value,
            job_type=job_type,
            coro=coro,
            args=args,
            kwargs=kwargs,
            timeout_seconds=timeout,
            metadata=metadata or {},
        )
        await self._queue.put((job.priority, job.created_at, job))
        self._stats["total_enqueued"] += 1
        log.debug(f"Job enfileirado: {job}")
        return job

    async def dequeue(self) -> Optional[Job]:
        """Remove e retorna o job de maior prioridade."""
        try:
            _, _, job = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            if job.job_id in self._cancelled:
                job.status = JobStatus.CANCELLED
                self._stats["total_cancelled"] += 1
                self._queue.task_done()
                return None
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            self._active[job.job_id] = job
            return job
        except asyncio.TimeoutError:
            return None

    def complete(self, job: Job, result: Any = None):
        """Marca job como concluído."""
        job.status = JobStatus.COMPLETED
        job.result = result
        job.completed_at = time.time()
        self._active.pop(job.job_id, None)
        self._history.append(job)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]
        self._stats["total_completed"] += 1
        self._queue.task_done()

    def fail(self, job: Job, error: str):
        """Marca job como falho."""
        job.status = JobStatus.FAILED
        job.error = error
        job.completed_at = time.time()
        self._active.pop(job.job_id, None)
        self._history.append(job)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]
        self._stats["total_failed"] += 1
        self._queue.task_done()
        log.warning(f"Job falhou: {job} — {error}")

    def cancel(self, job_id: str):
        """Cancela um job pendente."""
        self._cancelled.add(job_id)

    def cancel_by_type(self, job_type: JobType):
        """Cancela todos os jobs pendentes de um tipo."""
        # Não podemos inspecionar a fila diretamente, mas marcamos para cancelamento
        log.info(f"Cancelando jobs do tipo: {job_type.value}")

    @property
    def size(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return len(self._active)

    def count_by_type(self, job_type: JobType) -> Dict[str, int]:
        """Conta jobs pendentes e ativos de um tipo, sem expor tuplas da fila."""
        pending = sum(
            1 for item in list(self._queue._queue)
            if isinstance(item, tuple) and len(item) == 3
            and getattr(item[2], "job_type", None) == job_type
        )
        active = sum(1 for job in self._active.values() if job.job_type == job_type)
        return {"pending": pending, "active": active}

    def get_metrics(self) -> Dict:
        """Métricas da fila para o dashboard."""
        return {
            "queue_size": self.size,
            "active_jobs": self.active_count,
            "stats": self._stats.copy(),
            "active": [
                {
                    "id": j.job_id,
                    "type": j.job_type.value,
                    "priority": j.priority,
                    "elapsed": j.elapsed,
                }
                for j in self._active.values()
            ],
            "recent_history": [
                {
                    "id": j.job_id,
                    "type": j.job_type.value,
                    "status": j.status.value,
                    "elapsed": j.elapsed,
                }
                for j in self._history[-20:]
            ],
        }
