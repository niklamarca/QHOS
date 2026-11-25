"""Portão explícito entre pesquisa, shadow e paper.

``validated`` significa apenas que a hipótese sobreviveu aos testes internos.
Não é autorização para receber capital nem mesmo paper. A promoção é humana,
append-only e exige evidência forward declarada.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Set

from core.config import config
from core.logger import get_logger

log = get_logger("Promotion")

RESEARCH, SHADOW, PAPER, REJECTED = "RESEARCH", "SHADOW", "PAPER", "REJECTED"
_ALLOWED = {RESEARCH, SHADOW, PAPER, REJECTED}


class PromotionGate:
    def __init__(self):
        root = Path(config.data_lake_dir) / "governance"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "strategy_promotions.jsonl"
        self._lock = threading.Lock()

    def _latest(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                out[str(row["strategy_id"])] = row
        except (OSError, json.JSONDecodeError):
            pass
        return out

    def stage(self, strategy_id: str) -> str:
        return self._latest().get(str(strategy_id), {}).get("stage", RESEARCH)

    @staticmethod
    def _internal_evidence(strategy_id: str) -> Dict[str, Any]:
        """Localiza a aprovacao interna na mesma versao cientifica."""
        from governance.epistemics import current_code_fingerprint, current_data_fingerprint
        root = Path(config.data_lake_dir) / "experiments"
        wanted_code = current_code_fingerprint()
        wanted_data = current_data_fingerprint()
        for path in sorted(root.glob("strategies_*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    row = json.loads(line)
                    payload = row.get("payload", {})
                    meta = payload.get("metadata", {})
                    if (str(payload.get("strategy_id")) == str(strategy_id)
                            and str(meta.get("status", "")).lower() == "validated"
                            and row.get("code_hash") == wanted_code
                            and row.get("data_fingerprint") == wanted_data):
                        return {"internal_tests_passed": True,
                                "code_hash": wanted_code, "data_hash": wanted_data,
                                "source": path.name}
                except (json.JSONDecodeError, TypeError):
                    continue
        return {"internal_tests_passed": False, "code_hash": wanted_code,
                "data_hash": wanted_data, "source": None}

    def promote(self, strategy_id: str, stage: str, evidence: Dict[str, Any],
                approved_by: str) -> Dict[str, Any]:
        """Registra promoção, recusando atalhos científicos."""
        stage = str(stage).upper()
        current = self.stage(strategy_id)
        if stage not in _ALLOWED:
            raise ValueError(f"estágio inválido: {stage}")
        if not approved_by or approved_by.strip().lower() in {"system", "auto"}:
            raise ValueError("promoção exige aprovação humana identificada")
        if stage == PAPER:
            if current != SHADOW:
                raise ValueError("PAPER exige estágio SHADOW anterior")
            # Recalcula no ledger append-only. Um dicionario fornecido pelo
            # chamador nao e evidencia e nao pode fabricar 60 sessoes.
            from execution.shadow_executor import shadow_evidence
            evidence = shadow_evidence(strategy_id)
            sessions = int(evidence.get("forward_sessions", 0) or 0)
            if sessions < config.promotion_min_forward_sessions:
                raise ValueError(
                    f"PAPER exige {config.promotion_min_forward_sessions} sessões forward; recebeu {sessions}"
                )
            if not bool(evidence.get("ledger_reconciled", False)):
                raise ValueError("PAPER exige ledger forward reconciliado")
        if stage == SHADOW:
            if current not in {RESEARCH, SHADOW}:
                raise ValueError(f"SHADOW não permitido a partir de {current}")
            evidence = self._internal_evidence(strategy_id)
            if not evidence.get("internal_tests_passed"):
                raise ValueError(
                    "SHADOW exige testes internos aprovados na versao atual de codigo e dados"
                )
        record = {
            "ts": time.time(), "strategy_id": str(strategy_id), "stage": stage,
            "previous_stage": current, "approved_by": approved_by.strip(),
            "evidence": dict(evidence or {}),
        }
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        try:
            from governance.control import audit
            audit.record("STRATEGY_PROMOTED", record, actor=approved_by.strip(), subject=str(strategy_id))
        except Exception:
            pass
        log.warning(f"PROMOÇÃO {strategy_id}: {current} -> {stage}")
        return record

    def paper_approved_ids(self) -> Set[str]:
        return {sid for sid, row in self._latest().items() if row.get("stage") == PAPER}

    def shadow_ids(self) -> Set[str]:
        return {sid for sid, row in self._latest().items() if row.get("stage") == SHADOW}

    def status(self) -> Dict[str, Any]:
        latest = self._latest()
        counts = {stage: 0 for stage in _ALLOWED}
        for row in latest.values():
            counts[row.get("stage", RESEARCH)] = counts.get(row.get("stage", RESEARCH), 0) + 1
        return {"counts": counts, "shadow": sorted(self.shadow_ids()),
                "paper_approved": sorted(self.paper_approved_ids())}


promotion = PromotionGate()
