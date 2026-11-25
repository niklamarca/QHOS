"""
QUANT HPC OS — Risk Engine Global
Controle de risco local multi-nível.
O Risk Engine pode sobrescrever qualquer outro módulo do sistema.
"""
from __future__ import annotations
import asyncio
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from data.storage import StorageEngine

log = get_logger("RiskEngine")


@dataclass
class RiskBreach:
    """Violação de limite de risco detectada."""
    breach_type: str
    severity: int                   # 1=alerta, 2=reduzir, 3=flatten
    current_value: float
    limit_value: float
    message: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RiskState:
    """Estado atual de risco do portfólio."""
    portfolio_drawdown: float = 0.0
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    total_exposure: float = 0.0
    max_position_size: float = 0.0
    strategy_correlations: float = 0.0
    portfolio_volatility: float = 0.0
    nav: float = 100.0
    peak_nav: float = 100.0
    cash_pct: float = 1.0

    # Estratégias por status
    n_active_strategies: int = 0
    n_paused_strategies: int = 0

    # Kill switch level
    kill_level: int = 0

    # Timestamp
    updated_at: float = field(default_factory=time.time)

    @property
    def is_critical(self) -> bool:
        return self.kill_level >= 3

    @property
    def drawdown_pct(self) -> float:
        return self.portfolio_drawdown * 100


class RiskEngine:
    """
    Engine de risco global do runtime local.

    Monitora continuamente:
    - Drawdown do portfólio (vs. limite global)
    - P&L diário
    - Exposição por ativo
    - Correlação entre estratégias
    - Volatilidade agregada
    - Concentração de posição

    O Risk Engine é soberano: pode pausar, reduzir ou encerrar
    qualquer operação do sistema sem permissão de outros módulos.
    """

    def __init__(self, storage: StorageEngine):
        self.storage = storage
        self.bus = event_bus
        self.state = RiskState()
        self._portfolio_positions: Dict[str, float] = {}  # asset → weight
        self._strategy_pnls: Dict[str, List[float]] = {}  # id → pnl history
        self._strategy_weights: Dict[str, float] = {}
        self._nav_history: List[float] = []
        self._daily_start_nav: float = 0.0
        self._active_breaches: List[RiskBreach] = []
        self._kill_switch = None  # injetado externamente

    def inject_kill_switch(self, ks):
        self._kill_switch = ks

    # ─── Main Check ──────────────────────────────────────────────────

    async def check_all(self):
        """Executa todos os checks de risco. Chamado pelo orchestrator como P0."""
        # Sincroniza o nivel REAL do kill switch no estado. Ninguem fazia isso —
        # state.kill_level ficava 0 e o pre_trade_check / is_critical nunca
        # enxergavam o kill switch ativo.
        if self._kill_switch is not None:
            self.state.kill_level = self._kill_switch.level

        breaches = []

        # 1. Drawdown do portfólio
        dd_breach = self._check_portfolio_drawdown()
        if dd_breach:
            breaches.append(dd_breach)

        # 2. P&L diário
        pnl_breach = self._check_daily_pnl()
        if pnl_breach:
            breaches.append(pnl_breach)

        # 3. Concentração de posição
        conc_breach = self._check_concentration()
        if conc_breach:
            breaches.append(conc_breach)

        # 4. Volatilidade do portfólio
        vol_breach = self._check_portfolio_volatility()
        if vol_breach:
            breaches.append(vol_breach)

        # 5. Correlação entre estratégias
        corr_breach = self._check_strategy_correlation()
        if corr_breach:
            breaches.append(corr_breach)

        # Processa violações — com DEDUP: so loga e publica quando o ESTADO de
        # breach MUDA (novo, alterado ou resolvido). Antes logava o mesmo alerta
        # a cada ciclo (alert fatigue: voce para de notar e perde um breach real).
        _sig = tuple(sorted((b.breach_type, b.severity) for b in breaches))
        _changed = (_sig != getattr(self, "_last_breach_sig", None))
        self._last_breach_sig = _sig

        if breaches:
            self._active_breaches = breaches
            max_severity = max(b.severity for b in breaches)

            if _changed:
                for breach in breaches:
                    log.warning(
                        f"Risk Breach: {breach.breach_type} "
                        f"(severity={breach.severity}) — {breach.message}"
                    )
                await self.bus.publish(Event(
                    type=EventType.RISK_LIMIT_BREACH,
                    source="risk_engine",
                    payload={
                        "breaches": [b.__dict__ for b in breaches],
                        "severity": max_severity,
                        "breach_type": breaches[0].breach_type,
                    }
                ))

            # Kill switch SEMPRE avaliado (idempotente), independente do dedup de
            # log. Em modo research sem NAV real, nao aciona L3 automatico.
            ks_eligible = (
                self._kill_switch is not None
                and self.state.nav > 0
                and self._nav_history  # pelo menos 1 update_nav recebido
            )
            if max_severity >= 3 and ks_eligible:
                await self._kill_switch.trigger_level3("risk_engine")
            elif max_severity >= 2 and ks_eligible:
                await self._kill_switch.trigger_level2("risk_engine")
            # severity == 1 = ALERTA: apenas registra (com dedup). NAO aciona L1.
        else:
            if _changed:
                log.info("Risk: breaches resolvidos")
            self._active_breaches = []

        # Atualiza estado
        self.state.updated_at = time.time()

        return {
            "breaches": len(breaches),
            "severity": max(b.severity for b in breaches) if breaches else 0,
            "state": self._get_state_dict(),
        }

    # ─── Individual Checks ───────────────────────────────────────────

    def _check_portfolio_drawdown(self) -> Optional[RiskBreach]:
        """Verifica drawdown do portfólio vs. limite global."""
        if self.state.nav <= 0:
            return None

        current_dd = (self.state.nav - self.state.peak_nav) / self.state.peak_nav
        self.state.portfolio_drawdown = current_dd

        if current_dd <= -config.max_portfolio_drawdown:
            severity = 3 if current_dd <= -config.max_portfolio_drawdown * 1.5 else 2
            return RiskBreach(
                breach_type="portfolio_drawdown",
                severity=severity,
                current_value=abs(current_dd),
                limit_value=config.max_portfolio_drawdown,
                message=(
                    f"Drawdown {abs(current_dd)*100:.1f}% excede limite "
                    f"{config.max_portfolio_drawdown*100:.1f}%"
                ),
            )
        return None

    def _check_daily_pnl(self) -> Optional[RiskBreach]:
        """Verifica P&L diário vs. limite de perda diária."""
        if self._daily_start_nav <= 0:
            return None

        daily_ret = (self.state.nav - self._daily_start_nav) / self._daily_start_nav
        self.state.daily_pnl_pct = daily_ret

        if daily_ret <= -config.max_daily_loss:
            severity = 3 if daily_ret <= -config.max_daily_loss * 1.5 else 2
            return RiskBreach(
                breach_type="daily_loss",
                severity=severity,
                current_value=abs(daily_ret),
                limit_value=config.max_daily_loss,
                message=(
                    f"Perda diária {abs(daily_ret)*100:.1f}% excede limite "
                    f"{config.max_daily_loss*100:.1f}%"
                ),
            )
        return None

    def _check_concentration(self) -> Optional[RiskBreach]:
        """Verifica concentração de posição por ativo."""
        if not self._portfolio_positions:
            return None

        max_pos = max(abs(v) for v in self._portfolio_positions.values())
        self.state.max_position_size = max_pos

        if max_pos > config.max_position_size:
            # THROTTLE: este alarme disparou 4.100 vezes sem UMA rejeicao de
            # ordem correspondente. Alarme que grita sem consequencia treina o
            # operador a ignorar alarmes. Loga no maximo 1x a cada 15 min.
            import time as _t
            _ag = _t.time()
            if _ag - getattr(self, "_ult_conc_log", 0.0) > 900:
                self._ult_conc_log = _ag
                self._conc_silenciados = 0
            else:
                self._conc_silenciados = getattr(self, "_conc_silenciados", 0) + 1
            return RiskBreach(
                breach_type="position_concentration",
                severity=1,
                current_value=max_pos,
                limit_value=config.max_position_size,
                message=(
                    f"Posição {max_pos*100:.1f}% excede limite "
                    f"{config.max_position_size*100:.1f}%"
                ),
            )
        return None

    def _check_portfolio_volatility(self) -> Optional[RiskBreach]:
        """Verifica volatilidade realizada do portfólio."""
        if len(self._nav_history) < 20:
            return None

        nav_arr = np.array(self._nav_history[-20:])
        returns = np.diff(nav_arr) / nav_arr[:-1]
        vol_daily = np.std(returns)
        vol_annual = vol_daily * np.sqrt(252)

        self.state.portfolio_volatility = float(vol_annual)

        # Alerta se vol muito alta (> 30% ao ano)
        if vol_annual > 0.30:
            return RiskBreach(
                breach_type="high_volatility",
                severity=1,
                current_value=vol_annual,
                limit_value=0.30,
                message=f"Vol do portfólio {vol_annual*100:.1f}% > 30% a.a.",
            )
        return None

    def _check_strategy_correlation(self) -> Optional[RiskBreach]:
        """Verifica correlação entre estratégias ativas."""
        pnl_series = {k: v for k, v in self._strategy_pnls.items() if len(v) >= 20}
        if len(pnl_series) < 2:
            return None

        df = pd.DataFrame(pnl_series)
        corr_matrix = df.corr()
        # Média das correlações off-diagonal
        n = len(corr_matrix)
        if n < 2:
            return None

        mask = ~np.eye(n, dtype=bool)
        avg_corr = float(corr_matrix.values[mask].mean())
        self.state.strategy_correlations = avg_corr

        if avg_corr > config.max_strategy_correlation:
            return RiskBreach(
                breach_type="strategy_correlation",
                severity=1,
                current_value=avg_corr,
                limit_value=config.max_strategy_correlation,
                message=(
                    f"Correlação média entre estratégias {avg_corr:.2f} > "
                    f"{config.max_strategy_correlation}"
                ),
            )
        return None

    # ─── Update Methods ──────────────────────────────────────────────

    def update_nav(self, nav: float):
        """Atualiza NAV do portfólio."""
        self.state.nav = nav
        self._nav_history.append(nav)
        if len(self._nav_history) > 252:
            self._nav_history = self._nav_history[-252:]

        # Seta baseline na primeira atualização real
        if self._daily_start_nav <= 0:
            self._daily_start_nav = nav

        # Atualiza pico
        if nav > self.state.peak_nav:
            self.state.peak_nav = nav

    def update_positions(self, positions: Dict[str, float]):
        """Atualiza posições do portfólio."""
        self._portfolio_positions = positions
        self.state.total_exposure = sum(abs(v) for v in positions.values())
        self.state.cash_pct = max(0, 1.0 - self.state.total_exposure)

    def update_strategy_pnl(self, strategy_id: str, pnl: float):
        """Atualiza P&L de uma estratégia."""
        if strategy_id not in self._strategy_pnls:
            self._strategy_pnls[strategy_id] = []
        self._strategy_pnls[strategy_id].append(pnl)
        if len(self._strategy_pnls[strategy_id]) > 252:
            self._strategy_pnls[strategy_id] = self._strategy_pnls[strategy_id][-252:]

    def reset_daily(self):
        """Reset diário dos contadores de P&L."""
        self._daily_start_nav = self.state.nav
        log.debug(f"Risk diário resetado. NAV base: {self._daily_start_nav:.2f}")

    def pre_trade_check(self, asset: str, size: float, direction: int) -> Tuple[bool, str]:
        """
        Gate de risco pré-trade.
        Retorna (aprovado, motivo).
        Chamado obrigatoriamente antes de qualquer execução.
        """
        # Kill switch ativo — LE O NIVEL REAL do KillSwitch (autoritativo).
        # Antes lia self.state.kill_level, que ninguem sincronizava (ficava 0),
        # entao o gate JAMAIS via o kill switch. flatten/reduce do kill switch
        # chamam o broker direto (nao passam aqui), entao bloquear novas ordens
        # aqui nao impede o de-risk. L1+ = para novas ordens (semantica documentada).
        ks_level = (
            self._kill_switch.level if self._kill_switch is not None
            else self.state.kill_level
        )
        if ks_level >= 1:
            return False, f"kill_switch_L{ks_level}_active"

        # Posição muito grande. `size` agora = PESO RESULTANTE do ativo (fracao
        # do NAV) ja computado pelo _risk_gate. Antes somava current_exposure
        # (peso 0-1) com size (=quantity/1000) -> unidades incoerentes (ex: 128.6%).
        resulting_weight = abs(size)
        if resulting_weight > config.max_position_size + 1e-6:
            return False, f"position_too_large ({resulting_weight:.2%})"

        # Drawdown crítico
        if self.state.portfolio_drawdown <= -config.max_portfolio_drawdown * 0.9:
            return False, "near_dd_limit"

        return True, "ok"

    def _get_state_dict(self) -> Dict:
        return {
            "nav": self.state.nav,
            "peak_nav": self.state.peak_nav,
            "portfolio_drawdown": self.state.portfolio_drawdown,
            "daily_pnl_pct": self.state.daily_pnl_pct,
            "total_exposure": self.state.total_exposure,
            "portfolio_volatility": self.state.portfolio_volatility,
            "kill_level": self.state.kill_level,
            "n_active_strategies": self.state.n_active_strategies,
            "active_breaches": len(self._active_breaches),
        }

    def get_dashboard_metrics(self) -> Dict:
        return self._get_state_dict()

    async def start(self):
        log.info("RiskEngine iniciado")
        log.info(
            f"  Limites: DD={config.max_portfolio_drawdown:.0%}, "
            f"DailyLoss={config.max_daily_loss:.0%}, "
            f"MaxPos={config.max_position_size:.0%}"
        )

    async def stop(self):
        log.info("RiskEngine parado")
