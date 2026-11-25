"""
QUANT HPC OS — Control Tower Dashboard
Painel local de observabilidade via FastAPI + WebSocket.
Separa processo, dados, pesquisa, execução simulada e força da evidência.
"""
from __future__ import annotations
import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Set
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.registry import registry
from core.logger import get_logger

log = get_logger("Dashboard")


import os as _os_dash
_HTML_PATH = _os_dash.path.join(_os_dash.path.dirname(_os_dash.path.abspath(__file__)), "_hybrid.html")
try:
    with open(_HTML_PATH, "r", encoding="utf-8") as _f:
        DASHBOARD_HTML = _f.read()
except Exception:
    DASHBOARD_HTML = "<h1>QHOS</h1><p>dashboard HTML ausente (_bloomberg.html)</p>"


class ConnectionManager:
    """Gerencia conexões WebSocket ativas."""

    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, data: Dict):
        if not self.active:
            return
        msg = json.dumps(data, default=str)
        dead = set()
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self.active -= dead


class DashboardServer:
    """Servidor FastAPI do painel local de observabilidade."""

    def __init__(self):
        self.app = FastAPI(title="Quant HPC OS — Control Tower")
        self.manager = ConnectionManager()
        self.bus = event_bus
        self._state: Dict = {}
        self._logs: List[Dict] = []
        self._server = None

        # Injected references
        self.orchestrator = None
        self.risk_engine = None
        self.portfolio_engine = None
        self.evolution_engine = None
        self.regime_model = None

        self._setup_routes()
        self._setup_event_subscriptions()

    def inject_dependencies(self, **modules):
        for k, v in modules.items():
            setattr(self, k, v)

    def _setup_routes(self):
        @self.app.get("/api/export/{what}")
        async def export(what: str):
            """Baixa os ledgers forward como CSV (nav, trend, symb1, manual)."""
            import os as _oe
            from fastapi.responses import PlainTextResponse
            _m = {"nav": "nav.csv", "trend": "trend_nav.csv",
                  "symb1": "symb1_nav.csv", "manual": "manual_nav.csv",
                  "carry": "crypto_carry_nav.csv"}
            f = _m.get(what)
            if not f:
                return PlainTextResponse("arquivo inválido", status_code=400)
            _r = _oe.path.dirname(_oe.path.dirname(_oe.path.abspath(__file__)))
            _p = _oe.path.join(_r, "data_lake", "track_record", f)
            if not _oe.path.exists(_p):
                return PlainTextResponse("ainda não existe", status_code=404)
            return PlainTextResponse(open(_p, encoding="utf-8").read(),
                                     headers={"Content-Disposition": f"attachment; filename={f}"})

        @self.app.get("/api/learning")
        async def learning():
            try:
                from core.learning_engine import get_learning_engine
                return get_learning_engine().summary()
            except Exception as e:
                return {"error": str(e)[:120]}

        @self.app.get("/api/growth")
        async def growth():
            """Aprendizado real: geração, hipóteses, braços — sem NAV do GA."""
            try:
                from core.growth_metrics import build_growth_report
                return build_growth_report(force=True)
            except Exception as e:
                return {"error": str(e)[:200], "verdict": "TRAVADO"}

        @self.app.get("/api/governance")
        async def governance():
            try:
                from governance.control import autonomy, audit, journal
                from governance.promotion import promotion
                from governance.reconciliation import reconciliation
                from governance.experiment_registry import experiments
                return {"autonomy": autonomy.status(), "audit": audit.tail(30),
                        "journal": journal.tail(20),
                        "promotion": promotion.status(),
                        "reconciliation": reconciliation.latest(),
                        "experiments": {"backtests_recorded": experiments.count_backtests()}}
            except Exception as e:
                return {"error": str(e)[:120]}

        @self.app.post("/api/autonomy/{mode}")
        async def set_autonomy(mode: str):
            try:
                from governance.control import autonomy
                ok = autonomy.set_mode(mode.upper(), "dashboard")
                return {"ok": ok, "status": autonomy.status()}
            except Exception as e:
                return {"ok": False, "error": str(e)[:120]}

        @self.app.get("/api/chart/{sym}")
        async def chart(sym: str):
            import re as _re, glob as _gl2, os as _o2
            import pandas as _pd2
            if not _re.fullmatch(r"[A-Za-z0-9_\.\-]{1,20}", sym):
                return {"error": "bad_symbol"}
            _root2 = _o2.path.dirname(_o2.path.dirname(_o2.path.abspath(__file__)))
            hits = _gl2.glob(_o2.path.join(_root2, "data_lake", "raw", sym + "_1d.parquet"))
            if not hits:
                return {"error": "not_found"}
            try:
                df = _pd2.read_parquet(hits[0]).tail(160)
                return {"sym": sym, "ohlc": [[str(i.date()), float(r["open"]), float(r["high"]),
                                              float(r["low"]), float(r["close"])] for i, r in df.iterrows()]}
            except Exception as ex:
                return {"error": str(ex)[:80]}

        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            # le o HTML a cada request -> editar _bloomberg.html so precisa de refresh no browser
            try:
                with open(_HTML_PATH, "r", encoding="utf-8") as _f:
                    return _f.read()
            except Exception:
                return DASHBOARD_HTML

        @self.app.get("/api/status")
        async def status():
            return self._build_state()

        @self.app.get("/api/truth")
        async def truth():
            from governance.epistemics import load_current_state
            return load_current_state()

        @self.app.get("/api/health")
        async def health():
            from governance.epistemics import load_current_state
            truth = load_current_state()
            return {"status": truth.get("health", {}),
                    "process": truth.get("process", {}),
                    "modules": len(registry._modules),
                    "registry_healthy": registry.all_healthy()}

        @self.app.get("/api/debug")
        async def debug():
            """Diagnóstico: mostra estado bruto + info de threads."""
            try:
                import psutil as _p, threading as _t
                # Snapshot seguro do estado (evita RuntimeError por concurrent modification)
                state_snap = {}
                try:
                    state_snap = dict(self._state)
                except Exception:
                    pass
                logs_snap = []
                try:
                    logs_snap = list(self._logs[-5:])
                except Exception:
                    pass
                return {
                    "raw_state_keys": list(state_snap.keys()),
                    "raw_state": {k: str(v)[:300] for k, v in state_snap.items()},
                    "logs_count": len(self._logs),
                    "last_logs": logs_snap,
                    "orchestrator_running": bool(
                        self.orchestrator and getattr(self.orchestrator, "_running", False)
                    ),
                    "orchestrator_cycle": getattr(self.orchestrator, "_cycle_count", -1)
                        if self.orchestrator else -1,
                    "orchestrator_uptime_s": round(
                        time.time() - self.orchestrator._start_time, 1
                    ) if self.orchestrator and hasattr(self.orchestrator, "_start_time") else -1,
                    "live_cpu_pct": _p.cpu_percent(interval=None),
                    "live_ram_pct": _p.virtual_memory().percent,
                    "ws_connections": len(self.manager.active),
                    "active_threads": _t.active_count(),
                    "portfolio_nav": getattr(self.portfolio_engine, "_nav", None)
                        if self.portfolio_engine else None,
                    "portfolio_nav_history_len": len(
                        getattr(self.portfolio_engine, "_nav_history", [])
                    ) if self.portfolio_engine else 0,
                    "evolution_hof_size": len(
                        getattr(self.evolution_engine, "hall_of_fame", [])
                    ) if self.evolution_engine else 0,
                }
            except Exception as e:
                return {"error": str(e), "type": type(e).__name__}

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await self.manager.connect(websocket)
            first_send = True
            try:
                while True:
                    # Build + send — erros aqui NÃO desconectam; só logam
                    try:
                        state = self._build_state()
                        await websocket.send_text(json.dumps(state, default=str))
                        if first_send:
                            log.debug(f"[WS] Primeira mensagem enviada — keys: {list(state.keys())}")
                            first_send = False
                    except Exception as build_err:
                        log.warning(f"[WS] _build_state erro: {type(build_err).__name__}: {build_err}")
                    # Aguarda mensagem do cliente (2s timeout)
                    try:
                        msg = await asyncio.wait_for(
                            websocket.receive_text(), timeout=2.0
                        )
                        if msg == "ping":
                            await websocket.send_text('{"pong":1}')
                        elif msg:
                            await self._handle_ws_message(json.loads(msg))
                    except asyncio.TimeoutError:
                        pass  # normal — loop continua
            except Exception:
                pass  # WebSocketDisconnect ou erro de rede → desconecta
            finally:
                self.manager.disconnect(websocket)
    def _setup_event_subscriptions(self):
        """Subscreve a eventos do sistema para atualizar o dashboard."""
        self.bus.subscribe(EventType.METRICS_UPDATE, self._on_metrics)
        self.bus.subscribe(EventType.REGIME_PREDICTED, self._on_regime)
        self.bus.subscribe(EventType.GENERATION_COMPLETE, self._on_generation)
        self.bus.subscribe(EventType.STRATEGY_VALIDATED, self._on_strategy_event)
        self.bus.subscribe(EventType.STRATEGY_REJECTED, self._on_strategy_event)
        self.bus.subscribe(EventType.RISK_LIMIT_BREACH, self._on_risk_breach)
        self.bus.subscribe(EventType.KILL_SWITCH_L3, self._on_kill)

    async def _on_metrics(self, event: Event):
        payload = event.payload
        self._state.update(payload)
        # Log de heartbeat a cada ~60s (12 ciclos de 5s) para mostrar atividade
        cycle = payload.get("cycle", 0)
        if cycle % 12 == 0:
            uptime = payload.get("uptime", 0)
            h = int(uptime // 3600); m = int((uptime % 3600) // 60)
            res = payload.get("resources", {})
            cpu = res.get("cpu_percent", 0)
            q = payload.get("queue", {})
            qs = q.get("queue_size", 0)
            self._add_log("info", f"Sistema OK — {h}h{m}m | CPU {cpu:.1f}% | queue {qs}")

    async def _on_regime(self, event: Event):
        regime = event.payload.get("regime", "unknown")
        conf = event.payload.get("confidence", 0)
        prev = self._state.get("regime", "")
        self._state["regime"] = regime
        self._state["regime_conf"] = conf
        # Só loga se o regime mudou (evita spam de entradas idênticas)
        if regime != prev:
            self._add_log("info", f"Regime: {regime.upper()} (conf={conf:.0%})")

    async def _on_generation(self, event: Event):
        self._state["generation"] = event.payload.get("generation", 0)
        self._state["hof_size"] = event.payload.get("hall_of_fame_size", 0)
        best_sharpe = event.payload.get("best_oos_sharpe", 0)
        self._add_log("success", f"Gen {self._state['generation']} — best Sharpe={best_sharpe:.2f}")

    async def _on_strategy_event(self, event: Event):
        sid = event.payload.get("strategy_id", "?")
        if event.type == EventType.STRATEGY_VALIDATED:
            self._add_log("success", f"Testes internos aprovados: {sid}")
        else:
            reason = event.payload.get("reason", "?")
            self._add_log("warning", f"Estratégia rejeitada: {sid} — {reason}")

    async def _on_risk_breach(self, event: Event):
        breach = event.payload.get("breach_type", "?")
        severity = event.payload.get("severity", 1)
        level = "error" if severity >= 2 else "warning"
        self._add_log(level, f"RISK BREACH: {breach} (L{severity})")

    async def _on_kill(self, event: Event):
        self._add_log("error", "KILL SWITCH L3 — FLATTEN TOTAL ATIVADO!")

    async def _handle_ws_message(self, msg: Dict):
        """Processa comandos recebidos do dashboard."""
        action = msg.get("action")
        if action == "kill_switch" and self.orchestrator:
            level = msg.get("level", 1)
            ks = getattr(self.orchestrator, "kill_switch", None)
            if ks:
                if level == 1:
                    await ks.trigger_level1("dashboard")
                elif level == 2:
                    await ks.trigger_level2("dashboard")
                elif level == 3:
                    await ks.trigger_level3("dashboard")

    def _add_log(self, level: str, message: str):
        self._logs.append({
            "level": level,
            "message": message,
            "timestamp": time.time(),
        })
        if len(self._logs) > 100:
            self._logs = self._logs[-100:]

    def _build_state(self) -> Dict:
        """Constrói o estado completo para o dashboard. NUNCA lança exceção."""
        import psutil as _psutil

        # Copia o estado base (atualizado pelos eventos async)
        state = dict(self._state)
        try:
            from governance.epistemics import load_current_state
            state["truth"] = load_current_state()
        except Exception:
            state["truth"] = {"authoritative": False, "snapshot_stale": True}

        # Crescimento/aprendizado — independente do NAV paper do GA.
        try:
            from core.growth_metrics import build_growth_report
            state["growth"] = build_growth_report()
        except Exception as _gerr:
            state["growth"] = {
                "verdict": "TRAVADO",
                "verdict_label": "indisponível",
                "verdict_detail": str(_gerr)[:160],
                "error": True,
            }

        # ── Recursos ao vivo (leitura direta — não depende de eventos) ──
        try:
            state["resources"] = {
                "cpu_percent": _psutil.cpu_percent(interval=None),
                "ram_percent": _psutil.virtual_memory().percent,
                "gpu": self._state.get("resources", {}).get("gpu", {"load": 0, "memory_used": 0, "memory_total": 0, "temperature": 0}),
                "timestamp": time.time(),
            }
        except Exception:
            pass

        # ── Uptime direto do orchestrator ──
        try:
            if self.orchestrator and hasattr(self.orchestrator, "_start_time"):
                state["uptime"] = time.time() - self.orchestrator._start_time
                state["cycle"] = self.orchestrator._cycle_count
                state["kill_level"] = self.orchestrator._kill_level
        except Exception:
            pass

        # ── Queue direta do orchestrator ──
        try:
            if self.orchestrator and hasattr(self.orchestrator, "queue"):
                state["queue"] = self.orchestrator.queue.get_metrics()
        except Exception:
            pass

        # ── Portfolio metrics ──
        try:
            if self.portfolio_engine:
                state["portfolio"] = self.portfolio_engine.get_metrics()
        except Exception:
            pass

        # ── Risk metrics ──
        try:
            if self.risk_engine:
                state["risk"] = self.risk_engine.get_dashboard_metrics()
        except Exception:
            pass

        # ── Strategies — apenas validadas, sem duplicatas por strategy_id ──
        try:
            if self.evolution_engine and self.evolution_engine.hall_of_fame:
                state["strategies"] = []
                seen_ids: set = set()
                for s in list(self.evolution_engine.hall_of_fame):
                    try:
                        # Exclui rejeitadas (fitness=0) e duplicatas da mesma família
                        if s.fitness <= 0 or getattr(s, "status", "") != "validated":
                            continue
                        if s.strategy_id in seen_ids:
                            continue
                        seen_ids.add(s.strategy_id)
                        rt = s.rule_type.value if hasattr(s.rule_type, "value") else str(s.rule_type)
                        state["strategies"].append({
                            "id": s.strategy_id,
                            "name": s.name,
                            "rule_type": rt,
                            "oos_sharpe": s.oos_sharpe,
                            "fitness": s.fitness,
                            "status": s.status,
                        })
                        if len(state["strategies"]) >= 10:
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # ── Signal Executor ──
        try:
            if self.orchestrator:
                se = getattr(self.orchestrator, "signal_executor", None)
                if se:
                    state["signal_executor"] = se.get_status()
        except Exception:
            pass

        # ── Forward honesto (nav.csv vs SPY) + feedback + term-structure (gate VRP) ──
        # Cache TTL 60s: sem isto, cada ciclo do WebSocket abria os parquets e
        # segurava locks NTFS -> WinError 5 na ingestao ao salvar (tmp->parquet).
        _now_c = time.time()
        _cache = getattr(self, "_file_cache", None)
        if _cache is not None and _now_c - _cache.get("ts", 0) < 60:
            state["forward"] = _cache.get("forward", {})
            state["term_structure"] = _cache.get("term_structure", {})
            state["assets"] = _cache.get("assets", [])
            state["correlation"] = _cache.get("correlation", {})
            state["flow"] = _cache.get("flow", [])
            state["news"] = getattr(self, "_news", [])
            state["macro"] = getattr(self, "_macro", [])
            state["events"] = getattr(self, "_events", [])
            state["health"] = self._safe_health()
            try:
                from governance.control import autonomy as _au2
                state["autonomy"] = _au2.status()
            except Exception:
                pass
            state["recent_logs"] = list(self._logs[-10:])
            return state
        try:
            import os as _o, json as _j, glob as _gl
            import pandas as _pd
            _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
            _fwd = {}
            _navp = _o.path.join(_root, "data_lake", "track_record", "nav.csv")
            if _o.path.exists(_navp):
                _df = _pd.read_csv(_navp)
                if not _df.empty:
                    _ln = float(_df["nav"].iloc[-1])
                    _fwd["nav"] = _ln; _fwd["ret"] = _ln / 100000.0 - 1.0
                    # IDADE DO LEDGER. O painel mostrava a ultima linha do
                    # nav.csv como se fosse de hoje, sem checar a data. Em
                    # 19/08/2026 o braco GA estava parado em 17/08 (nada
                    # aprovado pelo Teste 8, portfolio vazio) e o dashboard
                    # exibia aquele NAV como atual. Numero velho apresentado
                    # como corrente e a pior especie de mentira num painel.
                    try:
                        from core.market_clock import last_settled_session as _lss
                        _dt_col = _df.columns[0]
                        _ult = str(_df[_dt_col].iloc[-1])[:10]
                        _fwd["nav_date"] = _ult
                        import datetime as _dtm
                        _atraso = (_lss() - _dtm.date.fromisoformat(_ult)).days
                        _fwd["nav_stale_days"] = int(max(_atraso, 0))
                    except Exception:
                        _fwd["nav_date"] = None; _fwd["nav_stale_days"] = None
                    _t = _df.tail(80)
                    _fwd["nav_series"] = [float(x) for x in _t["nav"].tolist()]
                    _sp = _t.dropna(subset=["spy_close"]) if "spy_close" in _t.columns else _t.iloc[0:0]
                    if len(_sp) > 1 and float(_sp["spy_close"].iloc[0]) > 0:
                        _s0 = float(_sp["spy_close"].iloc[0])
                        _fwd["spy_series"] = [100000.0 * float(x) / _s0 for x in _sp["spy_close"].tolist()]
                        _fwd["spy_ret"] = float(_sp["spy_close"].iloc[-1]) / _s0 - 1.0
                        _fwd["excess"] = _fwd["ret"] - _fwd["spy_ret"]
            _lp = _o.path.join(_root, "data_lake", "live_performance.json")
            if _o.path.exists(_lp):
                _d2 = _j.load(open(_lp))
                _fwd["fb_active"] = sum(1 for _v in _d2.values() if len(_v) >= 10)
                _fwd["fb_total"] = len(_d2)
            state["forward"] = _fwd
            _ts = {}
            _vh = _gl.glob(_o.path.join(_root, "data_lake", "raw", "VIX_1d.parquet"))
            _v3 = _gl.glob(_o.path.join(_root, "data_lake", "raw", "VIX3M_1d.parquet"))
            if _vh and _v3:
                _vx = float(_pd.read_parquet(_vh[0])["close"].iloc[-1])
                _v3v = float(_pd.read_parquet(_v3[0])["close"].iloc[-1])
                _ts = {"vix": _vx, "vix3m": _v3v, "ratio": _vx / _v3v, "contango": bool(_vx < _v3v)}
            state["term_structure"] = _ts
            _wl = ["SPY","QQQ","IWM","GLD","TLT","VIXY","BTC_USDT","ETH_USDT","VIX","VIX3M"]
            _assets = []
            for _a in _wl:
                _gg = _gl.glob(_o.path.join(_root, "data_lake", "raw", _a + "_1d.parquet"))
                if _gg:
                    try:
                        _cc = _pd.read_parquet(_gg[0])["close"]
                        _lx = float(_cc.iloc[-1]); _px = float(_cc.iloc[-2]) if len(_cc) > 1 else _lx
                        _assets.append({"sym": _a.replace("_USDT", "").replace("_", ""), "k": _a, "px": _lx, "chg": (_lx/_px - 1.0) if _px else 0.0})
                    except Exception:
                        pass
            state["assets"] = _assets
            _tn = _o.path.join(_root, "data_lake", "track_record", "trend_nav.csv")
            if _o.path.exists(_tn):
                try:
                    _tdf = _pd.read_csv(_tn)
                    if not _tdf.empty:
                        try:
                            from core.market_clock import last_settled_session as _lss2
                            import datetime as _dtm2
                            _u2 = str(_tdf[_tdf.columns[0]].iloc[-1])[:10]
                            _fwd["trend_date"] = _u2
                            _fwd["trend_stale_days"] = int(max(
                                (_lss2() - _dtm2.date.fromisoformat(_u2)).days, 0))
                        except Exception:
                            pass
                        _fwd["trend_nav"] = float(_tdf["nav"].iloc[-1])
                        _fwd["trend_ret"] = float(_tdf["nav"].iloc[-1]) / 100000.0 - 1.0
                        _fwd["trend_series"] = [float(x) for x in _tdf["nav"].tail(80).tolist()]
                        state["forward"] = _fwd
                except Exception:
                    pass
            _sn = _o.path.join(_root, "data_lake", "track_record", "symb1_nav.csv")
            if _o.path.exists(_sn):
                try:
                    _sdf = _pd.read_csv(_sn)
                    if not _sdf.empty:
                        _fwd["symb1_nav"] = float(_sdf["nav"].iloc[-1])
                        _fwd["symb1_ret"] = float(_sdf["nav"].iloc[-1]) / 100000.0 - 1.0
                        _fwd["symb1_series"] = [float(x) for x in _sdf["nav"].tail(80).tolist()]
                        state["forward"] = _fwd
                except Exception:
                    pass
            _cn = _o.path.join(_root, "data_lake", "track_record", "crypto_carry_nav.csv")
            if _o.path.exists(_cn):
                try:
                    _cdf0 = _pd.read_csv(_cn)
                    if not _cdf0.empty:
                        _fwd["carry_nav"] = float(_cdf0["nav"].iloc[-1])
                        _fwd["carry_ret"] = float(_cdf0["nav"].iloc[-1]) / 100000.0 - 1.0
                        _fwd["carry_series"] = [float(x) for x in _cdf0["nav"].tail(80).tolist()]
                        _fwd["carry_sessions"] = int(_cdf0["date"].nunique())
                        state["forward"] = _fwd
                except Exception:
                    pass
            _mn = _o.path.join(_root, "data_lake", "track_record", "manual_nav.csv")
            if _o.path.exists(_mn):
                try:
                    _mdf = _pd.read_csv(_mn)
                    if not _mdf.empty:
                        _e0 = float(_mdf["equity"].iloc[0])
                        _fwd["manual_eq"] = float(_mdf["equity"].iloc[-1])
                        _fwd["manual_ret"] = float(_mdf["equity"].iloc[-1]) / _e0 - 1.0 if _e0 else 0.0
                        _fwd["manual_series"] = [float(x) / _e0 * 100000.0 for x in _mdf["equity"].tail(80).tolist()] if _e0 else []
                        state["forward"] = _fwd
                except Exception:
                    pass
            # FLOW (PROXY — nao e order flow real): volume relativo vs media 20d
            # e |ret|/volume$ (Amihud, impacto por dolar). Derivado dos nossos parquets.
            try:
                _fl = []
                for _a3 in ["SPY", "QQQ", "IWM", "GLD", "TLT"]:
                    _g3 = _gl.glob(_o.path.join(_root, "data_lake", "raw", _a3 + "_1d.parquet"))
                    if not _g3:
                        continue
                    _d3 = _pd.read_parquet(_g3[0]).tail(30)
                    if len(_d3) < 21 or "volume" not in _d3.columns:
                        continue
                    _v = _d3["volume"].astype(float)
                    _rv = float(_v.iloc[-1] / (_v.tail(21).mean() + 1e-9))
                    _c3 = _d3["close"].astype(float)
                    _rr = float(abs(_c3.iloc[-1] / _c3.iloc[-2] - 1.0))
                    _amh = _rr / ((_v.iloc[-1] * _c3.iloc[-1]) / 1e9 + 1e-9)
                    _fl.append({"s": _a3, "rv": _rv, "im": _amh})
                if _fl:
                    state["flow"] = _fl
            except Exception:
                pass

            # matriz de correlacao rolante (63d) dos PROPRIOS dados — sem fonte externa
            try:
                _core = ["SPY", "QQQ", "IWM", "GLD", "TLT", "BTC_USDT"]
                _tails = {}
                for _a2 in _core:
                    _g2 = _gl.glob(_o.path.join(_root, "data_lake", "raw", _a2 + "_1d.parquet"))
                    if _g2:
                        _tails[_a2.replace("_USDT", "")] = _pd.read_parquet(_g2[0])["close"].astype(float).tail(64).reset_index(drop=True)
                if len(_tails) >= 3:
                    _cdf = _pd.DataFrame(_tails).pct_change().corr().round(2)
                    state["correlation"] = {"syms": list(_cdf.columns),
                                            "m": [[float(x) for x in row] for row in _cdf.values]}
            except Exception:
                pass
            self._file_cache = {
                "ts": _now_c,
                "forward": state.get("forward", {}),
                "term_structure": state.get("term_structure", {}),
                "assets": state.get("assets", []),
                "correlation": state.get("correlation", {}),
                "flow": state.get("flow", []),
            }
        except Exception:
            pass
        state["news"] = getattr(self, "_news", [])
        state["macro"] = getattr(self, "_macro", [])
        try:
            from governance.control import autonomy as _au
            state["autonomy"] = _au.status()
        except Exception:
            pass
        state["health"] = self._safe_health()
        state["events"] = getattr(self, "_events", [])
        state["recent_logs"] = list(self._logs[-10:])
        return state

    def _system_health(self) -> dict:  # nunca deve levantar excecao (roda no WS)
        """Saude por componente baseada em FATOS verificaveis (arquivos, idade,
        contadores) — nao em campos de payload que ficam vazios no boot."""
        import os as _oh, time as _th, glob as _gh
        R = _oh.path.dirname(_oh.path.dirname(_oh.path.abspath(__file__)))
        def fresh(path, max_min):
            try:
                return (_th.time() - _oh.path.getmtime(path)) / 60.0 <= max_min
            except Exception:
                return False
        h = {}
        raw = _gh.glob(_oh.path.join(R, "data_lake", "raw", "*.parquet"))
        try:
            # Nunca instanciar StorageEngine dentro do health loop. Cada
            # WebSocket chamava isto a cada 2s e abria novas conexoes DuckDB,
            # acumulando memoria/threads e disputando locks no NTFS.
            _storage = getattr(getattr(self, "orchestrator", None), "storage", None)
            if _storage is None:
                raise RuntimeError("storage compartilhado indisponivel")
            _dq = _storage.data_quality_summary()
            h["market_data"] = {
                "ok": bool(_dq.get("ok")),
                "info": (f"{_dq.get('approved', 0)}/{_dq.get('total', 0)} aprovadas · "
                         f"{_dq.get('partial', 0)} parciais · "
                         f"{_dq.get('quarantined', 0)} quarentenadas"),
            }
        except Exception:
            h["market_data"] = {"ok": len(raw) >= 10, "info": f"{len(raw)} séries (sem auditoria)"}
        feat = _gh.glob(_oh.path.join(R, "data_lake", "features", "*.parquet"))
        h["feature_engine"] = {"ok": len(feat) >= 5, "info": f"{len(feat)} features"}
        vix = _oh.path.join(R, "data_lake", "raw", "VIX_1d.parquet")
        h["state_engine"] = {"ok": _oh.path.exists(vix), "info": "VIX/VIX3M" if _oh.path.exists(vix) else "sem ^VIX"}
        try:
            gen = int(self._state.get("generation", 0) or 0)
        except Exception:
            gen = 0
        # vivo = tem populacao/HOF (a geracao 0 e valida!). Antes exigia gen>0,
        # o que marcava "aguardando" no primeiro minuto apos o boot.
        _pop = 0
        _hof = int(self._state.get("hof_size", 0) or 0)
        try:
            _ev = getattr(self, "evolution_engine", None)
            _pop = len(getattr(_ev, "population", []) or [])
            if not _hof:
                _hof = len(getattr(_ev, "hall_of_fame", []) or [])
        except Exception:
            pass
        # ROTULO CORRIGIDO (19/08/2026). Este painel dizia "aprovados {_hof}",
        # mas _hof e o TAMANHO DO HALL OF FAME — estrategias ordenadas por
        # fitness, SEM nenhum filtro de validacao. Enquanto isso o painel
        # Pesquisa filtra status=="validated" e mostrava lista vazia. O mesmo
        # dashboard exibia dois numeros contraditorios sobre a mesma coisa, e o
        # rotulo do primeiro estava errado. Com o Teste 8 ativo a diferenca
        # ficou gritante: HOF 50, validadas 0.
        _val = 0
        try:
            _val = sum(1 for _s in (getattr(_ev, "hall_of_fame", []) or [])
                       if getattr(_s, "status", "") == "validated"
                       and getattr(_s, "fitness", 0) > 0)
        except Exception:
            pass
        h["evolution"] = {
            "ok": (_pop > 0 or _hof > 0 or gen > 0),
            "info": f"gen {gen} · pop {_pop} · HOF {_hof} · VALIDADAS {_val}",
        }
        h["risk_engine"] = {"ok": getattr(self, "risk_engine", None) is not None,
                            "info": "kill L%d" % int(self._state.get("kill_level", 0) or 0)}
        # o dashboard nao recebe o execution_engine direto — checa via orchestrator
        _ex = getattr(self, "execution_engine", None) or getattr(getattr(self, "orchestrator", None), "execution_engine", None)
        h["execution"] = {"ok": _ex is not None, "info": "paper"}
        try:
            from governance.reconciliation import reconciliation
            _rec = reconciliation.latest()
            _rs = _rec.get("status", "UNKNOWN")
            h["reconciliation"] = {"ok": _rs == "OK", "info": _rs}
        except Exception:
            h["reconciliation"] = {"ok": False, "info": "indisponível"}
        nav = _oh.path.join(R, "data_lake", "track_record", "nav.csv")
        h["track_record"] = {"ok": _oh.path.exists(nav), "info": "nav.csv"}
        arms = sum(1 for f in ["trend_nav.csv", "symb1_nav.csv",
                               "crypto_carry_nav.csv", "manual_nav.csv"]
                   if _oh.path.exists(_oh.path.join(R, "data_lake", "track_record", f)))
        h["arms"] = {"ok": arms >= 2, "info": f"{arms}/4 braços"}
        hb = _oh.path.join(R, "data_lake", "server_heartbeat.txt")
        h["heartbeat"] = {"ok": fresh(hb, 3), "info": "servidor vivo" if fresh(hb, 3) else "parado"}
        try:
            from governance.control import autonomy as _a
            h["governance"] = {"ok": True, "info": _a.mode}
        except Exception:
            h["governance"] = {"ok": False, "info": "indisponível"}
        return h

    def _safe_health(self) -> dict:
        try:
            return self._system_health()
        except Exception as _e:
            log.debug(f"health falhou: {_e}")
            return {}

    async def start(self):
        """Inicia o servidor FastAPI em thread separada."""
        import threading

        # ── Feed de manchetes (WSJ Markets RSS) em thread propria, cache 15min ──
        self._news = getattr(self, "_news", [])
        def _news_loop():
            import urllib.request as _ur
            import xml.etree.ElementTree as _ET
            import time as _t2
            urls = ["https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
                    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"]
            while True:
                for u in urls:
                    try:
                        req = _ur.Request(u, headers={"User-Agent": "QHOS-Terminal/1.0"})
                        raw = _ur.urlopen(req, timeout=10).read()
                        root = _ET.fromstring(raw)
                        items = []
                        for it in root.iter("item"):
                            t = (it.findtext("title") or "").strip()
                            d = (it.findtext("pubDate") or "")[:22]
                            if t:
                                items.append({"t": t[:110], "d": d})
                            if len(items) >= 8:
                                break
                        if items:
                            self._news = items
                            break
                    except Exception:
                        continue
                _t2.sleep(900)
        threading.Thread(target=_news_loop, daemon=True).start()

        # ── MACRO: juros/dolar/oil/ouro via yfinance (dado real, 30min) ──
        self._macro = getattr(self, "_macro", [])
        def _macro_loop():
            import time as _t3
            MAP = [("^TNX", "US10Y", 1.0), ("DX-Y.NYB", "DXY", 1.0),
                   ("CL=F", "OIL", 1.0), ("GC=F", "GOLD", 1.0)]
            while True:
                out = []
                try:
                    import yfinance as _yf
                    for tk, lbl, mult in MAP:
                        try:
                            h = _yf.Ticker(tk).history(period="5d")
                            if h is None or h.empty or len(h) < 2:
                                continue
                            c = float(h["Close"].iloc[-1]); p0 = float(h["Close"].iloc[-2])
                            out.append({"l": lbl, "v": c * mult, "c": (c / p0 - 1.0) if p0 else 0.0,
                                        "u": "%" if lbl == "US10Y" else ""})
                        except Exception:
                            continue
                except Exception:
                    pass
                # fallback: se a rede/yfinance falhar, usa os parquets locais
                if not out:
                    try:
                        import glob as _g4, os as _o4
                        import pandas as _p4
                        _r4 = _o4.path.dirname(_o4.path.dirname(_o4.path.abspath(__file__)))
                        for _sym, _lbl in [("TLT", "TLT(20y)"), ("GLD", "GOLD"), ("VIX", "VIX")]:
                            _h4 = _g4.glob(_o4.path.join(_r4, "data_lake", "raw", _sym + "_1d.parquet"))
                            if not _h4:
                                continue
                            _c4 = _p4.read_parquet(_h4[0])["close"].astype(float)
                            if len(_c4) > 1:
                                out.append({"l": _lbl, "v": float(_c4.iloc[-1]),
                                            "c": float(_c4.iloc[-1] / _c4.iloc[-2] - 1.0)})
                    except Exception:
                        pass
                if out:
                    self._macro = out
                _t3.sleep(1800)
        threading.Thread(target=_macro_loop, daemon=True).start()

        # ── EVENTS: releases do Federal Reserve (RSS publico) ──
        self._events = getattr(self, "_events", [])
        def _events_loop():
            import urllib.request as _ur2
            import xml.etree.ElementTree as _ET2
            import time as _t4
            urls = ["https://www.federalreserve.gov/feeds/press_all.xml",
                    "https://www.federalreserve.gov/feeds/press_monetary.xml"]
            while True:
                for u in urls:
                    try:
                        rq = _ur2.Request(u, headers={"User-Agent": "QHOS-Terminal/1.0"})
                        rt = _ET2.fromstring(_ur2.urlopen(rq, timeout=10).read())
                        its = []
                        for it in rt.iter("item"):
                            t = (it.findtext("title") or "").strip()
                            d = (it.findtext("pubDate") or "")[:17]
                            if t:
                                its.append({"t": t[:90], "d": d})
                            if len(its) >= 6:
                                break
                        if its:
                            self._events = its
                            break
                    except Exception:
                        continue
                _t4.sleep(3600)
        threading.Thread(target=_events_loop, daemon=True).start()

        # ── CRESCIMENTO: acumula a série de aprendizado 24/7, mesmo sem
        #    navegador aberto. Sem isto, growth_snapshots.jsonl só cresce
        #    enquanto alguém tem o dashboard aberto, e o veredito
        #    CRESCENDO/LADO (que compara snapshots ao longo do tempo) fica
        #    cego. O append interno já é limitado a 1 a cada 5 min, então
        #    este loop apenas o mantém vivo — é leitura + append, sem DB.
        def _growth_loop():
            import time as _t5
            while True:
                try:
                    from core.growth_metrics import build_growth_report
                    build_growth_report(force=True)
                except Exception:
                    pass
                _t5.sleep(180)
        threading.Thread(target=_growth_loop, daemon=True).start()

        log.info(f"Dashboard iniciando em http://localhost:{config.dashboard_port}")

        def _run():
            try:
                uvicorn.run(
                    self.app,
                    host=config.dashboard_host,
                    port=config.dashboard_port,
                    log_level="warning",
                )
            except Exception as e:
                log.error(f"Dashboard erro: {e}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        import asyncio as _aio
        await _aio.sleep(1.0)
        log.info(f"Dashboard rodando em http://localhost:{config.dashboard_port}")

    async def stop(self):
        log.info("Dashboard parado")
