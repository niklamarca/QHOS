"""Impede sleep S3 enquanto o processo do servidor estiver vivo.

Fechar o console mata o PID; dormir o PC congela o mesmo PID.
SetThreadExecutionState no thread do loop, limpo no atexit.
Nao exige admin e nao muda o plano de energia da maquina.
"""
from __future__ import annotations
import sys

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

_armed = False


def prevent_sleep() -> str:
    """'armed' | 'unsupported' | 'failed'."""
    global _armed
    if sys.platform != "win32":
        return "unsupported"
    try:
        import ctypes
        prev = ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        )
        if prev == 0:
            return "failed"
        _armed = True
        return "armed"
    except Exception:
        return "failed"


def allow_sleep() -> None:
    global _armed
    if not _armed:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass
    _armed = False


def run_sidecar(sleep_s: float = 300.0, *, once: bool = False) -> str:
    """Segura ES_SYSTEM_REQUIRED neste processo. O main.py so arma apos o restart.

    `once` e para teste: arma e devolve sem loop.
    """
    import time
    status = prevent_sleep()
    if status != "armed" or once:
        return status
    try:
        while True:
            time.sleep(sleep_s)
            prevent_sleep()
    finally:
        allow_sleep()
    return status


if __name__ == "__main__":
    raise SystemExit(0 if run_sidecar() == "armed" else 1)
