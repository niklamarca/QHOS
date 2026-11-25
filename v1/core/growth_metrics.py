"""Métricas de aprendizado/crescimento — o que o NAV do paper GA NÃO mede.

Fontes honestas:
  - geração evolutiva e hipóteses únicas (pesquisa)
  - sessões dos braços pré-registrados (SYMB-1, TREND, CARRY)
  - aprovadas internas na época corrente (quase sempre zero)
  - série temporal append-only em data_lake/growth_snapshots.jsonl

Propositadamente NÃO usa nav.csv do GA como prova de crescimento.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from core.config import config
from core.logger import get_logger

log = get_logger("Growth")

_MIN_FORWARD = 60
_SNAPSHOT_EVERY_S = 300.0
_CACHE_TTL_S = 20.0
_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}


def _lake() -> Path:
    return Path(config.data_lake_dir)


def _read_csv_series(path: Path, calendar: str = "equity") -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "sessions": 0, "available": False, "dates": [], "nav": [], "rets": [],
        "last_date": None, "last_nav": None, "return": None,
    }
    if not path.exists():
        return out
    try:
        import pandas as pd
        frame = pd.read_csv(path)
        if frame.empty or "date" not in frame.columns:
            out["available"] = True
            return out
        dates = pd.to_datetime(frame["date"], errors="coerce")
        if calendar == "crypto":
            valid = dates.notna()
        else:
            from core.market_clock import is_trading_day
            valid = dates.notna() & dates.dt.date.map(is_trading_day)
        frame = frame.loc[valid].copy()
        if frame.empty:
            out["available"] = True
            return out
        nav = frame["nav"].astype(float) if "nav" in frame.columns else None
        rets = frame["ret"].astype(float) if "ret" in frame.columns else None
        out.update({
            "available": True,
            "sessions": int(frame["date"].nunique()),
            "dates": [str(x)[:10] for x in frame["date"].tolist()],
            "nav": [float(x) for x in (nav.tolist() if nav is not None else [])],
            "rets": [float(x) for x in (rets.tolist() if rets is not None else [])],
            "last_date": str(frame["date"].iloc[-1])[:10],
            "last_nav": float(nav.iloc[-1]) if nav is not None and len(nav) else None,
            "return": (
                float(nav.iloc[-1] / nav.iloc[0] - 1.0)
                if nav is not None and len(nav) and float(nav.iloc[0])
                else None
            ),
        })
    except Exception as exc:
        out["error"] = type(exc).__name__
        log.debug(f"série {path.name}: {exc}")
    return out


def _hypotheses_by_day() -> List[Dict[str, Any]]:
    root = _lake() / "experiments"
    rows: List[Dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.glob("hypotheses_*.jsonl")):
        day = path.stem.replace("hypotheses_", "")
        try:
            n = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            n = 0
        rows.append({"date": day, "hypotheses": n})
    return rows


def _append_snapshot(payload: Dict[str, Any]) -> None:
    """Grava um ponto na série de crescimento no máximo a cada 5 min."""
    path = _lake() / "growth_snapshots.jsonl"
    try:
        last_ts = 0.0
        if path.exists() and path.stat().st_size > 0:
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - 4096))
                chunk = fh.read().decode("utf-8", errors="ignore")
            for line in reversed(chunk.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    last_ts = float(json.loads(line).get("ts") or 0)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
                break
        now = time.time()
        if now - last_ts < _SNAPSHOT_EVERY_S:
            return
        research = payload.get("research") or {}
        arms = payload.get("arms") or {}
        row = {
            "ts": now,
            "iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "generation": research.get("generation"),
            "unique_hypotheses": research.get("unique_hypotheses"),
            "internally_passed": research.get("internally_passed"),
            "symb1_sessions": (arms.get("symb1") or {}).get("sessions"),
            "trend_sessions": (arms.get("trend") or {}).get("sessions"),
            "carry_sessions": (arms.get("carry") or {}).get("sessions"),
            "verdict": payload.get("verdict"),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug(f"snapshot crescimento: {exc}")


def _load_snapshots(limit: int = 240) -> List[Dict[str, Any]]:
    path = _lake() / "growth_snapshots.jsonl"
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _verdict(research: Dict[str, Any], arms: Dict[str, Any],
             snapshots: List[Dict[str, Any]]) -> Dict[str, str]:
    """CRESCENDO | LADO | TRAVADO | DESCOBERTA — sem usar NAV do GA."""
    gen = int(research.get("generation") or 0)
    hyp = int(research.get("unique_hypotheses") or 0)
    passed = int(research.get("internally_passed") or 0)
    sess = [
        int((arms.get(k) or {}).get("sessions") or 0)
        for k in ("symb1", "trend", "carry")
    ]
    max_arm = max(sess) if sess else 0

    gen_delta = 0
    hyp_delta = 0
    if len(snapshots) >= 2:
        a, b = snapshots[0], snapshots[-1]
        try:
            gen_delta = int(b.get("generation") or 0) - int(a.get("generation") or 0)
            hyp_delta = int(b.get("unique_hypotheses") or 0) - int(a.get("unique_hypotheses") or 0)
        except (TypeError, ValueError):
            pass

    if passed > 0:
        return {
            "code": "DESCOBERTA",
            "label": "Candidata interna aprovada",
            "detail": (
                f"{passed} passaram nos testes internos nesta época — "
                "ainda não é edge nem autorização paper."
            ),
        }
    if gen_delta > 0 or hyp_delta > 0 or max_arm > 0:
        return {
            "code": "CRESCENDO",
            "label": "Aprendizado em curso",
            "detail": (
                f"Geração {gen} · {hyp} hipóteses únicas · "
                f"braços até {max_arm}/{_MIN_FORWARD} sessões. "
                "NAV do paper GA não entra neste veredito."
            ),
        }
    if gen > 0:
        return {
            "code": "LADO",
            "label": "Pesquisa ativa, sem avanço recente",
            "detail": (
                "Motor gira, mas a série de snapshots ainda não mostra "
                "subida clara de geração/hipóteses. Braços abaixo do mínimo."
            ),
        }
    return {
        "code": "TRAVADO",
        "label": "Sem sinal de aprendizado",
        "detail": "Geração/hipóteses ausentes e braços sem sessão.",
    }


def build_growth_report(force: bool = False) -> Dict[str, Any]:
    """Relatório consumido pelo dashboard (cache curto)."""
    now = time.time()
    if (not force and _cache["payload"] is not None
            and now - float(_cache["ts"]) < _CACHE_TTL_S):
        return _cache["payload"]  # type: ignore[return-value]

    lake = _lake()
    research: Dict[str, Any] = {
        "generation": 0,
        "population": 0,
        "unique_hypotheses": 0,
        "internally_passed": 0,
        "blockers": [],
    }
    try:
        state = json.loads(
            (lake / "governance" / "CURRENT_STATE.json").read_text(encoding="utf-8")
        )
        r = state.get("research") or {}
        research.update({
            "generation": int(r.get("generation") or 0),
            "population": int(r.get("population") or 0),
            "unique_hypotheses": int(r.get("unique_hypotheses_tested") or 0),
            "internally_passed": int(r.get("internally_passed") or 0),
            "blockers": list(r.get("research_blockers") or []),
            "activity": r.get("activity"),
        })
    except Exception:
        try:
            evo = json.loads((lake / "evolution_state.json").read_text(encoding="utf-8"))
            research["generation"] = int(evo.get("generation") or 0)
            research["population"] = len(evo.get("population") or [])
        except Exception:
            pass

    arms = {
        "symb1": _read_csv_series(lake / "track_record" / "symb1_nav.csv"),
        "trend": _read_csv_series(lake / "track_record" / "trend_nav.csv"),
        "carry": _read_csv_series(
            lake / "track_record" / "crypto_carry_nav.csv", calendar="crypto"
        ),
    }
    for name, arm in arms.items():
        arm["target"] = _MIN_FORWARD
        arm["progress"] = min(1.0, float(arm.get("sessions") or 0) / float(_MIN_FORWARD))
        arm["label"] = {"symb1": "SYMB-1", "trend": "TREND", "carry": "CARRY"}.get(name, name)

    hyp_days = _hypotheses_by_day()
    snapshots = _load_snapshots()

    payload: Dict[str, Any] = {
        "schema": "qhos_growth_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "disclaimer": (
            "Este painel mede aprendizado (busca + braços pré-registrados). "
            "O NAV do paper GA pode ficar estático sem invalidar o crescimento."
        ),
        "research": research,
        "arms": arms,
        "hypotheses_by_day": hyp_days,
        "snapshots": snapshots,
        "min_forward_sessions": _MIN_FORWARD,
        "ga_paper_nav_is_not_growth": True,
    }
    v = _verdict(research, arms, snapshots)
    payload["verdict"] = v["code"]
    payload["verdict_label"] = v["label"]
    payload["verdict_detail"] = v["detail"]

    _append_snapshot(payload)
    payload["snapshots"] = _load_snapshots()

    _cache["ts"] = now
    _cache["payload"] = payload
    return payload
