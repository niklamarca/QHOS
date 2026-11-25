"""Espaco livre real do disco onde a V2 grava.

No WSL o disco do Linux e um arquivo .vhdx dentro de um disco do Windows: o `df` do Linux mostra o tamanho
maximo do vhdx (1 TB), mas ele so cresce ate o livre do disco do Windows que o guarda. O livre real e o menor
dos dois. `QHOS2_DISCO_HOSPEDEIRO` aponta a montagem desse disco (padrao `/mnt/c` no WSL; vazio desliga).
Se o disco hospedeiro nao responder, vale so o livre do Linux: a checagem nunca derruba uma captura sozinha.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional


def hospedeiro() -> Optional[Path]:
    valor = os.environ.get("QHOS2_DISCO_HOSPEDEIRO")
    if valor is not None:
        return Path(valor) if valor else None
    try:
        wsl = "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    except OSError:
        wsl = False
    return Path("/mnt/c") if wsl else None


def livre_bytes(caminho) -> int:
    """Bytes livres para gravar em `caminho` (ou no ancestral mais proximo que exista)."""
    caminho = Path(caminho)
    existente = next(p for p in (caminho, *caminho.parents) if p.exists())
    livre = shutil.disk_usage(existente).free
    disco_windows = hospedeiro()
    if disco_windows is not None:
        try:
            livre = min(livre, shutil.disk_usage(disco_windows).free)
        except OSError:
            pass
    return livre
