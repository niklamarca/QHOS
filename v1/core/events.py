"""
QUANT HPC OS — Event Bus Assíncrono
Backbone de comunicação entre todos os módulos do sistema.
"""
from __future__ import annotations
import asyncio
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime
import time


class EventType(Enum):
    # ─── Data ──────────────────────────────────────────────
    DATA_UPDATED         = auto()   # novos dados disponíveis
    FEATURES_COMPUTED    = auto()   # features calculadas

    # ─── Regime ────────────────────────────────────────────
    REGIME_CHANGED       = auto()   # regime de mercado mudou
    REGIME_PREDICTED     = auto()   # nova predição de regime

    # ─── Evolution ─────────────────────────────────────────
    STRATEGY_GENERATED   = auto()   # nova estratégia criada
    GENERATION_COMPLETE  = auto()   # geração evolutiva finalizada

    # ─── Backtest ──────────────────────────────────────────
    BACKTEST_STARTED     = auto()
    BACKTEST_COMPLETE    = auto()
    BACKTEST_FAILED      = auto()

    # ─── Validation ────────────────────────────────────────
    STRATEGY_VALIDATED   = auto()
    STRATEGY_REJECTED    = auto()   # falhou na validação

    # ─── Risk ──────────────────────────────────────────────
    RISK_ALERT           = auto()   # alerta de risco
    RISK_LIMIT_BREACH    = auto()   # limite violado
    KILL_SWITCH_L1       = auto()   # redução de risco
    KILL_SWITCH_L2       = auto()   # desligamento de estratégias
    KILL_SWITCH_L3       = auto()   # flatten total

    # ─── Portfolio ─────────────────────────────────────────
    PORTFOLIO_UPDATED    = auto()
    WEIGHTS_REBALANCED   = auto()

    # ─── Execution ─────────────────────────────────────────
    ORDER_SUBMITTED      = auto()
    ORDER_FILLED         = auto()
    ORDER_CANCELLED      = auto()
    TRADE_CLOSED         = auto()

    # ─── System ────────────────────────────────────────────
    SYSTEM_STARTED       = auto()
    SYSTEM_SHUTDOWN      = auto()
    MODULE_ERROR         = auto()
    JOB_ENQUEUED         = auto()
    JOB_COMPLETE         = auto()
    JOB_FAILED           = auto()

    # ─── Dashboard ─────────────────────────────────────────
    METRICS_UPDATE       = auto()   # atualização de métricas para o dashboard


@dataclass
class Event:
    """Evento imutável que trafega no sistema."""
    type: EventType
    source: str                             # módulo que emitiu
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: f"{time.time_ns()}")

    @property
    def dt(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    def __repr__(self):
        return f"Event({self.type.name}, src={self.source}, t={self.dt.strftime('%H:%M:%S.%f')[:12]})"


Handler = Callable[[Event], Any]


class EventBus:
    """
    Bus de eventos pub/sub assíncrono.
    Thread-safe para uso com asyncio.
    """

    def __init__(self, maxsize: int = 10_000):
        self._subscribers: Dict[EventType, List[Handler]] = {}
        self._wildcard: List[Handler] = []                  # recebe todos os eventos
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._history: List[Event] = []
        self._history_max: int = 5000
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: EventType, handler: Handler):
        """Registra um handler para um tipo de evento."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)

    def subscribe_all(self, handler: Handler):
        """Handler recebe TODOS os eventos (útil para logger, dashboard)."""
        self._wildcard.append(handler)

    def unsubscribe(self, event_type: EventType, handler: Handler):
        if event_type in self._subscribers:
            try:
                self._subscribers[event_type].remove(handler)
            except ValueError:
                pass

    async def publish(self, event: Event):
        """Publica um evento no bus (non-blocking)."""
        await self._queue.put(event)

    def publish_sync(self, event: Event):
        """Publica evento de forma síncrona (usa loop de eventos corrente)."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(self.publish(event))
        else:
            loop.run_until_complete(self.publish(event))

    async def _dispatch(self, event: Event):
        """Despacha evento para todos os handlers registrados."""
        # Histórico
        self._history.append(event)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max:]

        # Handlers específicos
        handlers = self._subscribers.get(event.type, [])
        # Wildcard handlers
        all_handlers = handlers + self._wildcard

        if not all_handlers:
            return

        # Executa handlers de forma assíncrona
        tasks = []
        for handler in all_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    tasks.append(asyncio.create_task(handler(event)))
                else:
                    handler(event)
            except Exception as e:
                pass  # handlers não podem derrubar o bus

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        """Loop principal do event bus. Roda como task assíncrona."""
        self._running = True
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._dispatch(event)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

    async def start(self):
        """Inicia o event bus como task de background."""
        self._task = asyncio.create_task(self.run())

    async def stop(self):
        """Para o event bus graciosamente."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def get_history(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 100
    ) -> List[Event]:
        """Retorna histórico de eventos."""
        h = self._history
        if event_type:
            h = [e for e in h if e.type == event_type]
        return h[-limit:]

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()


# Singleton global
event_bus = EventBus()
