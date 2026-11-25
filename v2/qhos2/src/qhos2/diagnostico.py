"""Pre-voo local sem rede, sem credenciais e sem leitura dos dados de mercado."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from importlib.metadata import version, PackageNotFoundError

from qhos2 import __version__, disco
from qhos2.config import Settings
from qhos2.coletor import PAGINA


def diagnosticar() -> dict:
    settings = Settings.from_environ()
    raiz = settings.data_root
    livre = disco.livre_bytes(raiz)
    try:
        dependencia = version("websockets")
    except PackageNotFoundError:
        dependencia = None
    pagina = PAGINA.read_bytes() if PAGINA.is_file() else None
    return {"schema": "qhos2.installation.v1", "version": __version__,
            "python": sys.version.split()[0], "venv": sys.prefix != sys.base_prefix,
            "websockets": dependencia,
            "terminal_included": pagina is not None,
            "terminal_sha256": hashlib.sha256(pagina).hexdigest() if pagina else None,
            "brain_installed": importlib.util.find_spec("brain_service") is not None,
            "data_root": str(raiz), "data_outside_git": True,
            "disk_free_bytes": livre,"credentials_read": False,
            "market_connected": False, "trading_ready": False}


def main(argv=None) -> int:
    try:
        result = diagnosticar()
    except (ValueError, OSError):
        print(json.dumps({"ok": False, "reason": "configuration_or_filesystem_invalid"}))
        return 2
    result["ok"] = bool(result["terminal_included"] and result["websockets"] and result["venv"])
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
