"""
QUANT HPC OS — Kill Switch Multi-Nível
Sistema de parada de emergência com 3 níveis de severidade.
Pode ser acionado por risco automático ou operador manual.
"""
from __future__ import annotations
import asyncio
import time
from typing import Optional, Callable
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger

log = get_logger("KillSwitch")


class KillSwitch:
    """
    Kill Switch com 3 níveis de severidade:
    L1 — Reduz exposição: para novos trades, cancela exploração
    L2 — Desliga estratégias: fecha posições gradualment
    L3 — Flatten total: fecha TODAS as posições imediatamente
    """

    def __init__(self):
        self.bus = event_bus
        self.level: int = 0                      # 0 = normal
        self._trigger_history: list = []
        self._execution_engine = None            # injetado
        self._orchestrator = None                # injetado

    def inject_dependencies(self, execution_engine, orchestrator):
        self._execution_engine = execution_engine
        self._orchestrator = orchestrator

    # ─── Trigger Methods ─────────────────────────────────────────────

    async def trigger_level1(self, source: str = "manual"):
        """L1: Reduz risco — para novas ordens, mantém posições existentes."""
        if self.level >= 1:
            return

        self.level = 1
        self._log_trigger(1, source)

        await self.bus.publish(Event(
            type=EventType.KILL_SWITCH_L1,
            source="kill_switch",
            payload={"level": 1, "source": source}
        ))
        log.warning(f"KILL SWITCH L1 ativado por: {source}")

    async def trigger_level2(self, source: str = "manual"):
        """L2: Fecha posições menores, para exploração."""
        if self.level >= 2:
            return
        self.level = max(self.level, 2)
        self._log_trigger(2, source)

        await self.bus.publish(Event(
            type=EventType.KILL_SWITCH_L2,
            source="kill_switch",
            payload={"level": 2, "source": source}
        ))
        log.error(f"KILL SWITCH L2 ativado por: {source}")

        # Fecha posições de baixa confiança
        if self._execution_engine:
            try:
                await self._execution_engine.reduce_exposure(target_pct=0.5)
            except Exception as e:
                log.error(f"Erro ao reduzir exposição: {e}")

    async def trigger_level3(self, source: str = "manual"):
        """L3: FLATTEN TOTAL — fecha TUDO imediatamente."""
        if self.level >= 3:
            return
        self.level = 3
        self._log_trigger(3, source)

        await self.bus.publish(Event(
            type=EventType.KILL_SWITCH_L3,
            source="kill_switch",
            payload={"level": 3, "source": source}
        ))
        log.critical(f"KILL SWITCH L3 — FLATTEN TOTAL — ativado por: {source}")

        if self._execution_engine:
            try:
                await self._execution_engine.flatten_all()
                log.critical("Portfólio flattenado com sucesso")
            except Exception as e:
                log.critical(f"ERRO ao flattener portfólio: {e}")

    # ─── Reset ───────────────────────────────────────────────────────

    async def reset(self, source: str = "operator"):
        """Reset manual — reativa o sistema."""
        old_level = self.level
        self.level = 0
        log.info(f"Kill switch resetado (era L{old_level}) por: {source}")
        self._log_trigger(0, source, "reset")

        if self._orchestrator:
            self._orchestrator.reset_kill_switch()

    # ─── Status ──────────────────────────────────────────────────────

    def _log_trigger(self, level: int, source: str, action: str = "trigger"):
        self._trigger_history.append({
            "action": action,
            "level": level,
            "source": source,
            "timestamp": time.time(),
        })

    @property
    def is_active(self) -> bool:
        return self.level > 0

    @property
    def status(self) -> dict:
        return {
            "level": self.level,
            "is_active": self.is_active,
            "description": {
                0: "Normal — sistema operacional",
                1: "L1 — Reduzindo risco",
                2: "L2 — Fechando posições",
                3: "L3 — FLATTEN TOTAL",
            }.get(self.level, "Unknown"),
            "trigger_history": self._trigger_history[-10:],
        }

    async def start(self):
        log.info("KillSwitch iniciado (nível 0 — normal)")

    async def stop(self):
        log.info("KillSwitch parado")
