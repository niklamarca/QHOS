"""Brain da V2: codigo observado separado dos dados persistentes.

Nao importa o V1 nem le QUANT_ROOT.txt/.env. Dados, memoria, logs e avaliacoes
ficam sob QHOS2_DATA_ROOT/brain. O codigo alvo e configuravel explicitamente.
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .config_contract import FLAGS
from qhos2.config import Settings


BRAIN_ROOT_DIR = Path(__file__).resolve().parent
log = logging.getLogger("Brain.Config")


def _violation(violations, key: str, reason: str, applied) -> None:
    """Rejected/degraded environment value: key, reason and applied value only (QHOS-AUD-273)."""
    violations.append({"key": key, "reason": reason, "applied": applied})
    log.warning("configuration %s: %s; applied %r", key, reason, applied)


def resolve_quant_root(
    brain_root: Optional[Path] = None, *, environ: Optional[Mapping[str, str]] = None,
) -> Path:
    root = Path(brain_root or BRAIN_ROOT_DIR).resolve()
    env = os.environ if environ is None else environ
    configured = str(env.get("QHOS2_BRAIN_CODE_ROOT", "")).strip()
    target = Path(configured).expanduser() if configured else root.parent / "qhos2"
    return (target if target.is_absolute() else root / target).resolve()


def _flag_value(name: str, spec: Mapping[str, Any], env: Mapping[str, str], violations=None) -> Any:
    default = spec["default"]
    key = name.upper()
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    text = raw.strip()
    violations = violations if violations is not None else []
    try:
        if spec["type"] == "bool":
            if text.lower() not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
                _violation(violations, key, "malformed_bool", default)
                return default
            return text.lower() in {"1", "true", "yes", "on"}
        if spec["type"] == "int":
            return int(text)
        if spec["type"] == "float":
            value = float(text)
            if not math.isfinite(value):
                _violation(violations, key, "non_finite_float", default)
                return default
            return value
        if text in spec.get("allowed", [text]):
            return text
        _violation(violations, key, "out_of_domain", default)
        return default
    except (ValueError, TypeError):
        _violation(violations, key, "malformed_" + str(spec["type"]), default)
        return default


class BrainConfig:
    """Mutable configuration object retained for existing Brain consumers/tests."""

    def __init__(
        self, brain_root: Optional[Path] = None, *, environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        env = os.environ if environ is None else environ
        self.brain_root_dir = Path(brain_root or BRAIN_ROOT_DIR).resolve()
        self.root_dir = resolve_quant_root(self.brain_root_dir, environ=env)
        self.quant_root_dir = self.root_dir
        dados = Settings.from_environ(env).data_root
        self.quant_data_lake_dir = dados
        self.quant_logs_dir = dados / "logs"
        self.brain_state_dir = dados / "brain" / "state"
        # BrainRuntime adds /brain to this root for memory/audit/state.json.
        self.data_lake_dir = dados
        self.logs_dir = dados / "brain" / "logs"
        self.configuration_violations: list = []
        for name, spec in FLAGS.items():
            setattr(self, name, _flag_value(name, spec, env, self.configuration_violations))
        timeout = getattr(self, "brain_llm_timeout_s", 20.0)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(float(timeout)) or not 0 < float(timeout) <= 120:
            if "BRAIN_LLM_TIMEOUT_S" in env and str(env.get("BRAIN_LLM_TIMEOUT_S", "")).strip():
                _violation(self.configuration_violations, "BRAIN_LLM_TIMEOUT_S", "out_of_range", 20.0)
            self.brain_llm_timeout_s = 20.0
        self.anthropic_api_key = str(
            env.get("BRAIN_ANTHROPIC_API_KEY") or env.get("ANTHROPIC_API_KEY") or ""
        ).strip()
        self.llm_model_alpha = self.brain_llm_model


config = BrainConfig()
# Historical public name means the repository being investigated.
ROOT_DIR = config.root_dir
