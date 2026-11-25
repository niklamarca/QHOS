"""
QUANT HPC OS — Paper Mark History
========================================
Registro persistente de marcações simuladas por estratégia. Ele fornece
observações paper-forward; não demonstra aprendizado e, por padrão, não altera
a seleção genética para preservar o período confirmatório.

Persistido em <data_lake>/live_performance.json (sobrevive a restart).
"""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path
import numpy as np
from core.config import config
from core.logger import get_logger

log = get_logger("LivePerformance")


class LivePerformance:
    def __init__(self, path=None, max_history: int = 252):
        self.path = Path(path) if path else (config.data_lake_dir / "live_performance.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_history = max_history
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r") as f:
                    return json.load(f)
            except Exception as e:
                log.warning(f"live_performance corrompido, recomecando: {e}")
        return {}

    def _save(self):
        tmp = str(self.path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f)
        os.replace(tmp, str(self.path))  # atomico

    def record(self, strategy_id: str, realized_return: float, ctx: dict | None = None):
        """Registra o retorno realizado de UM round-trip (fracao, ex: -0.012).

        `ctx` (opcional) grava a PROCEDENCIA da marcacao num arquivo separado
        (`live_marks_debug.jsonl`): ativo, preco de origem, preco de destino,
        posicao. Sem isso e impossivel auditar de onde saiu um numero — na
        auditoria de 12/08 sete estrategias apareceram com o retorno identico
        -2.0553%, que NAO corresponde a nenhuma variacao de nenhum dos 67
        ativos em janelas de 1 a 15 pregoes. Ate saber a origem, nao se mexe.
        """
        if strategy_id is None or not np.isfinite(float(realized_return)):
            return
        if ctx:
            try:
                import json as _j, time as _t
                _f = str(self.path).replace("live_performance.json", "live_marks_debug.jsonl")
                with open(_f, "a", encoding="utf-8") as _fh:
                    _fh.write(_j.dumps({"ts": _t.time(), "sid": str(strategy_id),
                                        "ret": float(realized_return), **ctx}) + "\n")
            except Exception:
                pass
        with self._lock:
            lst = self._data.setdefault(str(strategy_id), [])
            lst.append(float(realized_return))
            if len(lst) > self.max_history:
                self._data[str(strategy_id)] = lst[-self.max_history:]
            self._save()

    def stats(self, strategy_id: str):
        """Retorna (n_observacoes, retorno_medio, sharpe_anualizado) do paper."""
        r = self._data.get(str(strategy_id), [])
        if not r:
            return (0, 0.0, 0.0)
        a = np.asarray(r, dtype=float)
        n = len(a)
        mean = float(a.mean())
        if n > 1 and a.std() > 0:
            sharpe = float(mean / a.std() * np.sqrt(252))
        else:
            sharpe = 0.0
        return (n, mean, sharpe)

    def summary(self) -> dict:
        return {sid: len(r) for sid, r in self._data.items()}


# ─── Singleton compartilhado (signal_executor escreve, GA le) ──────────
_LIVE = None


def get_live_performance() -> LivePerformance:
    global _LIVE
    if _LIVE is None:
        _LIVE = LivePerformance()
    return _LIVE
