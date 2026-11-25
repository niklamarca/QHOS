"""
QUANT HPC OS — Logger Centralizado
Logger estruturado com contexto de módulo, nível e saída para arquivo + console.
"""
from __future__ import annotations
import sys
from pathlib import Path
from loguru import logger
from .config import config


def setup_logging():
    """Configura o sistema de logging global."""
    logger.remove()  # Remove handlers padrão

    log_dir = config.logs_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    # Console — colorido, compacto
    logger.add(
        sys.stderr,
        level=config.log_level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[module]: <20}</cyan> | "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # Arquivo rotacionado — rotaciona por 100 MB ou 1 dia (o que vier primeiro)
    logger.add(
        str(log_dir / "quant_hpc_{time:YYYY-MM-DD}.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[module]} | {message}",
        rotation="100 MB",   # limite de tamanho — evita arquivos de 64 MB acumulados
        retention="30 days",
        compression="gz",
        enqueue=True,        # thread-safe — não bloqueia o event loop
    )

    # Arquivo de erros separado
    logger.add(
        str(log_dir / "errors.log"),
        level="ERROR",
        format="{time} | {level} | {extra[module]} | {message}\n{exception}",
        rotation="10 MB",
        retention="90 days",
        enqueue=True,
    )



def get_logger(module: str):
    """Retorna um logger com contexto de módulo."""
    return logger.bind(module=module)


# Setup na importação
setup_logging()
