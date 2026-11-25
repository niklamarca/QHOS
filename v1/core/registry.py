"""
QUANT HPC OS — Module Registry
Registro central de todos os módulos do sistema com health check e status.
"""
from __future__ import annotations
import asyncio
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime
import time
from .logger import get_logger

log = get_logger("Registry")


class ModuleStatus(Enum):
    PENDING    = "pending"
    STARTING   = "starting"
    RUNNING    = "running"
    DEGRADED   = "degraded"    # funcionando com limitações
    ERROR      = "error"
    STOPPED    = "stopped"


@dataclass
class ModuleInfo:
    name: str
    instance: Any
    status: ModuleStatus = ModuleStatus.PENDING
    started_at: Optional[float] = None
    last_heartbeat: Optional[float] = None
    error_count: int = 0
    last_error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    @property
    def uptime(self) -> Optional[float]:
        if self.started_at:
            return time.time() - self.started_at
        return None

    @property
    def is_healthy(self) -> bool:
        if self.status not in (ModuleStatus.RUNNING, ModuleStatus.DEGRADED):
            return False
        if self.last_heartbeat:
            return (time.time() - self.last_heartbeat) < 60  # 60s timeout
        return self.status == ModuleStatus.RUNNING


class ModuleRegistry:
    """
    Registro global de módulos do sistema.
    Permite health checks, reinicialização e status consolidado.
    """

    def __init__(self):
        self._modules: Dict[str, ModuleInfo] = {}
        self._start_order: List[str] = []   # ordem de inicialização
        self._stop_order: List[str] = []    # ordem inversa para shutdown

    def register(
        self,
        name: str,
        instance: Any,
        start_order: int = 50,
        metadata: Optional[Dict] = None
    ):
        """Registra um módulo no sistema."""
        info = ModuleInfo(
            name=name,
            instance=instance,
            metadata=metadata or {}
        )
        self._modules[name] = info
        log.info(f"Módulo registrado: {name}")
        return info

    def get(self, name: str) -> Optional[Any]:
        """Retorna instância de um módulo."""
        info = self._modules.get(name)
        return info.instance if info else None

    def set_status(self, name: str, status: ModuleStatus, error: Optional[str] = None):
        """Atualiza status de um módulo."""
        if name in self._modules:
            self._modules[name].status = status
            if status == ModuleStatus.RUNNING:
                self._modules[name].started_at = time.time()
            if error:
                self._modules[name].last_error = error
                self._modules[name].error_count += 1

    def heartbeat(self, name: str):
        """Módulo sinaliza que está vivo."""
        if name in self._modules:
            self._modules[name].last_heartbeat = time.time()

    def get_status(self) -> Dict[str, Dict]:
        """Retorna status de todos os módulos."""
        return {
            name: {
                "status": info.status.value,
                "healthy": info.is_healthy,
                "uptime": info.uptime,
                "error_count": info.error_count,
                "last_error": info.last_error,
            }
            for name, info in self._modules.items()
        }

    def all_healthy(self) -> bool:
        """True se todos os módulos críticos estão saudáveis."""
        critical = ["risk_engine", "orchestrator", "data"]
        return all(
            self._modules.get(m, ModuleInfo("", None)).is_healthy
            for m in critical
            if m in self._modules
        )

    def degraded_modules(self) -> List[str]:
        return [
            name for name, info in self._modules.items()
            if not info.is_healthy and info.status != ModuleStatus.STOPPED
        ]

    async def start_all(self):
        """Inicializa todos os módulos registrados em ordem."""
        log.info(f"Iniciando {len(self._modules)} módulos...")
        for name, info in self._modules.items():
            try:
                self.set_status(name, ModuleStatus.STARTING)
                if hasattr(info.instance, "start"):
                    await info.instance.start()
                self.set_status(name, ModuleStatus.RUNNING)
                log.info(f"  ✓ {name}")
            except Exception as e:
                self.set_status(name, ModuleStatus.ERROR, str(e))
                log.error(f"  ✗ {name}: {e}")

    async def stop_all(self):
        """Para todos os módulos em ordem inversa."""
        log.info("Desligando módulos...")
        for name, info in reversed(list(self._modules.items())):
            try:
                if hasattr(info.instance, "stop"):
                    await info.instance.stop()
                self.set_status(name, ModuleStatus.STOPPED)
                log.info(f"  ✓ {name} parado")
            except Exception as e:
                log.error(f"  ✗ Erro ao parar {name}: {e}")


# Singleton global
registry = ModuleRegistry()
