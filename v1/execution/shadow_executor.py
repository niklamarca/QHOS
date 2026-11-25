"""Observador forward sem ordens para candidatos em estagio SHADOW.

Cada candidato recebe uma serie propria, marcada uma vez por sessao liquidada.
O ledger e evidência de comportamento fora da selecao, nao simulacao de fill e
nao autorizacao de capital. A promocao PAPER consulta este arquivo diretamente.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

from core.config import config
from core.logger import get_logger
from core.market_clock import last_settled_session
from evolution.strategy_dna import StrategyDNA
from governance.epistemics import current_code_fingerprint, current_data_fingerprint
from governance.promotion import promotion

log = get_logger("ShadowExecutor")
_LOCK = threading.Lock()


def _paths() -> tuple[Path, Path]:
    root = Path(config.data_lake_dir) / "governance"
    root.mkdir(parents=True, exist_ok=True)
    return root / "shadow_ledger.jsonl", root / "shadow_state.json"


def shadow_evidence(strategy_id: str) -> Dict[str, Any]:
    """Recalcula a evidencia do ledger; nunca confia em contagem declarada."""
    ledger, _ = _paths()
    rows = []
    malformed = 0
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if str(row.get("strategy_id")) == str(strategy_id):
                    rows.append(row)
            except (json.JSONDecodeError, TypeError):
                malformed += 1
    except OSError:
        pass
    keys = [(str(r.get("strategy_id")), str(r.get("session"))) for r in rows]
    unique = len(set(keys))
    duplicate_rows = len(keys) - unique
    hashes_present = all(r.get("code_hash") and r.get("data_hash") for r in rows)
    returns_valid = all(
        isinstance(r.get("net_return"), (int, float)) and math.isfinite(float(r["net_return"]))
        for r in rows
    )
    return {
        "source": "shadow_ledger.jsonl",
        "forward_sessions": unique,
        "ledger_rows": len(rows),
        "duplicate_rows": duplicate_rows,
        "malformed_rows": malformed,
        "hashes_present": hashes_present,
        "ledger_reconciled": bool(rows and duplicate_rows == 0 and malformed == 0
                                  and hashes_present and returns_valid),
        "first_session": min((r.get("session") for r in rows), default=None),
        "last_session": max((r.get("session") for r in rows), default=None),
        "execution_evidence": "SHADOW_MARK",
    }


class ShadowExecutor:
    """Marca candidatos SHADOW sem tocar no broker ou no portfolio."""

    def __init__(self, storage: Any, signal_executor: Any):
        self.storage = storage
        self.signal_executor = signal_executor
        self.ledger_path, self.state_path = _paths()
        self.state = self._load_state()
        self.cost_rate = (
            float(config.backtest_slippage_bps) + float(config.backtest_commission_bps)
        ) / 10000.0

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self) -> None:
        tmp = str(self.state_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def _strategies(self) -> Dict[str, StrategyDNA]:
        frame = self.storage.get_validated_strategies(min_sharpe=0.0)
        out: Dict[str, StrategyDNA] = {}
        for _, row in frame.iterrows():
            try:
                raw = row.get("metadata", "{}")
                meta = json.loads(raw) if isinstance(raw, str) else dict(raw)
                strategy = StrategyDNA.from_dict(meta)
                out[str(strategy.strategy_id)] = strategy
            except Exception as exc:
                log.debug(f"SHADOW metadata ignorada: {type(exc).__name__}: {exc}")
        return out

    def step(self) -> Dict[str, Any]:
        session = last_settled_session().isoformat()
        wanted = promotion.shadow_ids()
        if not wanted:
            return {"session": session, "marked": 0, "eligible": 0}
        strategies = self._strategies()
        marked = 0
        code_hash = current_code_fingerprint()
        data_hash = current_data_fingerprint()
        with _LOCK:
            for sid in sorted(wanted):
                strategy = strategies.get(sid)
                if strategy is None or not strategy.target_assets:
                    log.warning(f"SHADOW {sid}: candidato aprovado internamente nao encontrado")
                    continue
                prev = dict(self.state.get(sid, {}))
                if prev.get("last_session") == session:
                    continue
                asset = strategy.target_assets[0]
                features = self.storage.load_features(asset)
                if features is None or features.empty or "close" not in features:
                    continue
                try:
                    features = features[features.index.date <= last_settled_session()]
                except Exception:
                    pass
                if features.empty:
                    continue
                price = float(features["close"].iloc[-1])
                signal = float(self.signal_executor._get_current_position_signal(strategy, features))
                old_signal = float(prev.get("position", 0.0) or 0.0)
                old_price = float(prev.get("price", price) or price)
                gross_ret = old_signal * (price / old_price - 1.0) if old_price > 0 else 0.0
                cost = abs(signal - old_signal) * self.cost_rate
                net_ret = gross_ret - cost
                nav = float(prev.get("nav", 1.0) or 1.0) * (1.0 + net_ret)
                row = {
                    "ts": time.time(), "session": session, "strategy_id": sid,
                    "asset": asset, "previous_position": old_signal,
                    "position": signal, "price": price, "gross_return": gross_ret,
                    "turnover_cost": cost, "net_return": net_ret, "nav": nav,
                    "execution_evidence": "SHADOW_MARK", "code_hash": code_hash,
                    "data_hash": data_hash,
                }
                with open(self.ledger_path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                self.state[sid] = {
                    "last_session": session, "position": signal, "price": price,
                    "nav": nav, "updated_at": time.time(),
                }
                marked += 1
            if marked:
                self._save_state()
        return {"session": session, "marked": marked, "eligible": len(wanted)}


__all__ = ["ShadowExecutor", "shadow_evidence"]
