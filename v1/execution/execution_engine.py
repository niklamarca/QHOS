"""
QUANT HPC OS — Execution Engine
Coordena brokers registrados por mercado com risk gate obrigatório.
"""
from __future__ import annotations
import asyncio
import time
from typing import Dict, List, Optional, TYPE_CHECKING
from core.config import config
from core.events import EventBus, EventType, Event, event_bus
from core.logger import get_logger
from .abstract import AbstractBroker, Order, OrderSide, OrderType, Fill
from .paper_broker import PaperBroker

if TYPE_CHECKING:
    from risk.risk_engine import RiskEngine

log = get_logger("ExecutionEngine")


class ExecutionEngine:
    """
    Engine de execução com roteamento por mercado e risk gate.

    Arquitetura:
    - Broker selecionado automaticamente por mercado (stocks/crypto/futures)
    - Risk gate OBRIGATÓRIO antes de qualquer ordem
    - Slicing automático para ordens grandes (TWAP/VWAP)
    - Feedback de fills para o Risk Engine
    - Paper trading como default (nunca executa real sem configuração explícita)
    """

    def __init__(self):
        self.bus = event_bus
        self._brokers: Dict[str, AbstractBroker] = {}
        self._default_broker: AbstractBroker = PaperBroker()
        self._risk_engine: Optional["RiskEngine"] = None
        self._pending_orders: Dict[str, Order] = {}
        self._fill_history: List[Fill] = []
        # Modo efetivo vem do broker carregado, nunca de uma palavra no .env.
        # Hoje todos os mercados recebem PaperBroker em main.py.
        self._is_live = False
        if config.is_live:
            log.error("system_mode=live solicitado, mas nenhum adapter de broker real "
                      "foi registrado; execução FORÇADA para PAPER")
        self._signal_executor = None

    def inject_dependencies(self, risk_engine: "RiskEngine", signal_executor=None):
        self._risk_engine = risk_engine
        if signal_executor is not None:
            self._signal_executor = signal_executor

    def register_broker(self, market: str, broker: AbstractBroker):
        """Registra broker para um mercado específico."""
        self._brokers[market] = broker
        log.info(f"Broker registrado: {market} → {type(broker).__name__}")

    def _select_broker(self, symbol: str) -> AbstractBroker:
        """Seleciona broker pelo símbolo."""
        if "/" in symbol:  # Crypto
            return self._brokers.get("crypto", self._default_broker)
        if symbol in ["ES", "NQ", "RTY", "CL", "GC"]:  # Futures
            return self._brokers.get("futures", self._default_broker)
        return self._brokers.get("stocks", self._default_broker)  # Stocks

    # ─── Risk Gate ───────────────────────────────────────────────────

    def _risk_gate(self, order: Order) -> tuple:
        """
        Gate de risco PRÉ-EXECUÇÃO.
        OBRIGATÓRIO antes de qualquer ordem.
        Retorna (approved: bool, reason: str)
        """
        if not self._risk_engine:
            return False, "risk_engine_unavailable"

        direction = 1 if order.side == OrderSide.BUY else -1
        # Peso RESULTANTE do ativo apos a ordem (fracao do NAV) — unidade coerente
        # para a checagem de concentracao (antes usava quantity/1000, incoerente).
        broker = self._default_broker
        prices = getattr(broker, "prices", {}) or {}
        price = float(prices.get(order.symbol, 0.0) or 0.0)
        try:
            nav = float(getattr(broker, "cash", 0.0)) + sum(
                float(q) * float(prices.get(s, 0.0) or 0.0)
                for s, q in getattr(broker, "positions", {}).items()
            )
        except Exception:
            nav = 0.0
        if price <= 0 or nav <= 0:
            # sem preco/NAV confiavel: nao da pra computar peso — deixa passar (paper)
            return self._risk_engine.pre_trade_check(order.symbol, 0.0, direction)
        cur_qty = float(getattr(broker, "positions", {}).get(order.symbol, 0.0) or 0.0)
        new_qty = cur_qty + direction * float(order.quantity)
        resulting_weight = abs(new_qty * price) / nav
        return self._risk_engine.pre_trade_check(order.symbol, resulting_weight, direction)

    # ─── Auditoria da camada de integridade ──────────────────────────

    def _audit_integrity_event(self, event: str, symbol: str,
                               error: Exception,
                               include_traceback: bool = False) -> None:
        """Registra falha da camada de integridade sem jamais levantar.

        ``INTEGRITY_LAYER_FAULT`` (bug) e ``*_UNAVAILABLE`` (indisponibilidade
        operacional) são classes distintas de evento: lavar as duas na mesma
        mensagem foi o que manteve defeitos de código invisíveis no audit trail.
        """
        payload = {"symbol": symbol, "error_class": type(error).__name__,
                   "error": str(error)[:500]}
        if include_traceback:
            import traceback
            payload["traceback"] = traceback.format_exc()[-2000:]
        try:
            from governance.control import audit
            audit.record(event, payload, actor="execution_engine", subject=symbol)
        except Exception as audit_error:
            log.error(f"audit indisponível para {event}: {audit_error}")

    # ─── Order Submission ────────────────────────────────────────────

    async def submit(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        order_type: OrderType = OrderType.MARKET,
        strategy_id: Optional[str] = None,
        use_twap: bool = False,
    ) -> Optional[Fill]:
        """
        Submete uma ordem com risk gate obrigatório.
        """
        order = Order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            strategy_id=strategy_id,
        )

        # ── Reconciliação independente (fail-closed) ──────────────
        # Não confia no dashboard nem em estado em memória de outra camada.
        # Se broker e risco discordam, bloquear uma nova ordem é mais seguro
        # que ampliar uma posição que o motor de risco não enxerga.
        try:
            from governance.reconciliation import reconciliation
            reconciled, reconciliation_reason = reconciliation.execution_allowed(
                self._default_broker, self._risk_engine, self._signal_executor)
            if not reconciled:
                from governance.control import audit
                audit.record("ORDER_BLOCKED_BY_RECONCILIATION",
                             {"symbol": symbol, "side": side.value, "qty": quantity,
                              "reason": reconciliation_reason, "strategy_id": strategy_id},
                             actor="reconciliation", subject=symbol)
                log.error(f"Ordem bloqueada pela reconciliação: {reconciliation_reason}")
                return None
        except (OSError, TimeoutError, ValueError, KeyError) as _e:
            # Indisponibilidade OPERACIONAL da camada de integridade: esperada,
            # recuperável, bloqueia a ordem em silêncio disciplinado.
            self._audit_integrity_event("ORDER_BLOCKED_INTEGRITY_UNAVAILABLE",
                                        symbol, _e)
            log.error("Ordem bloqueada: reconciliação indisponível (%s): %s",
                      type(_e).__name__, _e)
            return None
        except Exception as _bug:
            # ImportError/AttributeError/TypeError aqui NÃO é decisão de risco,
            # é DEFEITO. A ordem continua bloqueada (fail-closed intacto), mas o
            # evento é de outra classe: precisa acordar alguém, não virar mais
            # uma linha anônima de "bloqueado".
            self._audit_integrity_event("INTEGRITY_LAYER_FAULT", symbol, _bug,
                                        include_traceback=True)
            log.critical(
                "DEFEITO na camada de integridade (%s: %s). Ordem bloqueada, "
                "mas a causa é BUG, não risco. Trate como incidente de código.",
                type(_bug).__name__, _bug, exc_info=True)
            return None

        # ── Gate de AUTONOMIA (antes de tudo) ────────────────────
        # MANUAL/COPILOT: o sistema NAO envia ordem sozinho. COPILOT registra a
        # proposta para o humano decidir; MANUAL bloqueia por completo.
        try:
            from governance.control import autonomy, audit
            if not autonomy.can_auto_execute():
                audit.record("ORDER_BLOCKED_BY_AUTONOMY",
                             {"symbol": symbol, "side": side.value, "qty": quantity,
                              "mode": autonomy.mode, "strategy_id": strategy_id},
                             actor="autonomy", subject=symbol)
                log.warning(f"Ordem NAO enviada — autonomia={autonomy.mode}: {symbol}")
                return None
        except (OSError, TimeoutError, ValueError, KeyError) as _e:
            self._audit_integrity_event("ORDER_BLOCKED_AUTONOMY_UNAVAILABLE",
                                        symbol, _e)
            log.error("Ordem bloqueada: governança de autonomia indisponível "
                      "(%s): %s", type(_e).__name__, _e)
            return None
        except Exception as _bug:
            self._audit_integrity_event("INTEGRITY_LAYER_FAULT", symbol, _bug,
                                        include_traceback=True)
            log.critical(
                "DEFEITO na governança de autonomia (%s: %s). Ordem bloqueada; "
                "causa é BUG, não decisão de governança.",
                type(_bug).__name__, _bug, exc_info=True)
            return None

        # ── Risk Gate (OBRIGATÓRIO) ──────────────────────────────
        approved, reason = self._risk_gate(order)
        if not approved:
            log.warning(f"Ordem rejeitada pelo risk gate: {symbol} — {reason}")
            try:
                from governance.control import audit
                audit.record("ORDER_REJECTED_BY_RISK",
                             {"symbol": symbol, "side": side.value, "qty": quantity,
                              "reason": reason, "strategy_id": strategy_id},
                             actor="risk_engine", subject=symbol)
            except Exception as _e:
                log.debug(f"execution/execution_engine.py L137: falha silenciosa suprimida: {type(_e).__name__}: {_e}")
            await self.bus.publish(Event(
                type=EventType.ORDER_CANCELLED,
                source="execution_engine",
                payload={"symbol": symbol, "reason": reason, "side": side.value}
            ))
            return None

        # ── Seleciona Broker ─────────────────────────────────────
        broker = self._select_broker(symbol)

        # ── Executa ──────────────────────────────────────────────
        try:
            self._pending_orders[order.order_id] = order
            await self.bus.publish(Event(
                type=EventType.ORDER_SUBMITTED,
                source="execution_engine",
                payload={
                    "order_id": order.order_id,
                    "symbol": symbol,
                    "side": side.value,
                    "quantity": quantity,
                    "strategy_id": strategy_id,
                }
            ))

            if use_twap and quantity > 1000:
                fills = await broker.submit_twap(order, duration_seconds=300, n_slices=10)
                fill = fills[0] if fills else None
            else:
                fill = await broker.submit_order(order)

            if fill:
                self._fill_history.append(fill)
                self._pending_orders.pop(order.order_id, None)

                # Notifica risk engine do fill
                if self._risk_engine:
                    pnl_est = (
                        fill.price * fill.quantity * (1 if side == OrderSide.BUY else -1)
                    )
                    if strategy_id:
                        self._risk_engine.update_strategy_pnl(strategy_id, pnl_est)

                await self.bus.publish(Event(
                    type=EventType.ORDER_FILLED,
                    source="execution_engine",
                    payload={
                        "order_id": order.order_id,
                        "symbol": symbol,
                        "fill_price": fill.price,
                        "quantity": fill.quantity,
                        "commission": fill.commission,
                        "broker": fill.broker,
                    }
                ))

                log.info(
                    f"Fill: {side.value.upper()} {quantity:.4f} {symbol} "
                    f"@ {fill.price:.2f} [{fill.broker}]"
                )
                try:
                    from governance.control import audit, journal, autonomy
                    ctx = {"symbol": symbol, "side": side.value, "qty": float(quantity),
                           "price": float(fill.price), "commission": float(fill.commission),
                           "broker": fill.broker, "strategy_id": strategy_id,
                           "mode": autonomy.mode, "order_id": order.order_id}
                    audit.record("ORDER_FILLED", ctx, actor="execution", subject=symbol)
                    journal.entry("fill", ctx)
                except Exception as _e:
                    log.debug(f"execution/execution_engine.py L207: falha silenciosa suprimida: {type(_e).__name__}: {_e}")
                return fill

        except Exception as e:
            log.error(f"Erro na execução de {symbol}: {e}")
            self._pending_orders.pop(order.order_id, None)

        return None

    # ─── Portfolio Management ─────────────────────────────────────────

    async def rebalance_to_weights(
        self,
        target_weights: Dict[str, float],
        total_nav: float,
    ):
        """
        Rebalanceia o portfólio para os pesos alvo.
        Calcula delta de posições e submete ordens necessárias.
        """
        log.info(f"Rebalanceando para {len(target_weights)} ativos...")

        broker = self._default_broker
        current_positions = await broker.get_positions()
        current_prices = getattr(broker, "prices", {})

        # Monta deltas por ativo, INCLUINDO saida de posicoes que sairam do
        # alvo (peso 0) — antes elas nunca eram vendidas. E ordena SELLs antes
        # de BUYs: a versao antiga rodava em ordem de dicionario, comprando e
        # gastando todo o caixa antes de qualquer venda -> "Cash insuficiente"
        # nos ultimos ativos, distorcendo a alocacao do HRP.
        # BANDA DE NAO-NEGOCIACAO (anti-churn): so negocia um ativo se o peso
        # mudou mais que a banda. O sistema estava fazendo ~150 trades/dia e o
        # custo comia ~1%/semana. Excecao: SEMPRE zera posicao que saiu do alvo
        # (tw=0), senao ficariamos presos em ativos indesejados abaixo da banda.
        _band = getattr(config, "rebalance_no_trade_band", 0.05)
        orders = []
        for symbol in set(target_weights) | set(current_positions):
            tw = float(target_weights.get(symbol, 0.0))
            price = float(current_prices.get(symbol, 0.0) or 0.0) or 100.0
            current_qty = float(current_positions.get(symbol, 0.0))
            current_w = (current_qty * price) / total_nav if total_nav > 0 else 0.0
            # dentro da banda e ainda no alvo -> nao mexe (economiza custo)
            if tw != 0 and abs(tw - current_w) < _band:
                continue
            target_qty = (total_nav * tw) / price if price > 0 else 0.0
            delta = target_qty - current_qty
            if abs(delta) < 0.001:
                continue
            orders.append((symbol, delta))

        # SELLs (delta<0) primeiro para liberar caixa; depois BUYs
        orders.sort(key=lambda o: o[1])
        for symbol, delta in orders:
            side = OrderSide.BUY if delta > 0 else OrderSide.SELL
            await self.submit(
                symbol=symbol,
                side=side,
                quantity=abs(delta),
                strategy_id="portfolio_rebal",
            )
            await asyncio.sleep(0.05)  # Rate limiting

    async def flatten_all(self):
        """FLATTEN TOTAL — fecha todas as posições."""
        log.critical("FLATTEN ALL acionado")
        await self._default_broker.flatten_all()

        for market, broker in self._brokers.items():
            if broker is not self._default_broker:
                try:
                    await broker.flatten_all()
                except Exception as e:
                    log.error(f"Erro ao flattener {market}: {e}")

    async def reduce_exposure(self, target_pct: float = 0.5):
        """Reduz exposição para % alvo."""
        await self._default_broker.reduce_exposure(target_pct)

    # ─── Account Info ─────────────────────────────────────────────────

    async def get_account_value(self) -> float:
        return await self._default_broker.get_account_value()

    async def get_positions(self) -> Dict[str, float]:
        return await self._default_broker.get_positions()

    def get_metrics(self) -> Dict:
        """Métricas de execução para o dashboard."""
        summary = {}
        if hasattr(self._default_broker, "get_pnl_summary"):
            summary = self._default_broker.get_pnl_summary()
        return {
            **summary,
            "n_fills": len(self._fill_history),
            "pending_orders": len(self._pending_orders),
            "mode": "paper" if isinstance(self._default_broker, PaperBroker) else "live_broker",
        }

    async def start(self):
        """Inicializa o engine."""
        actual = "PAPER" if isinstance(self._default_broker, PaperBroker) else "LIVE_BROKER"
        self._is_live = actual == "LIVE_BROKER"
        log.info(f"ExecutionEngine iniciado — modo efetivo: {actual}")

    async def stop(self):
        log.info("ExecutionEngine parado")
