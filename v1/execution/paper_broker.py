"""
QUANT HPC OS — Paper Trading Broker
Simulação simplificada de fechamento com slippage, comissões e latência.

PERSISTÊNCIA (novo): o estado da carteira (caixa, posições, últimos preços,
capital inicial) é gravado atomicamente em <data_lake>/paper_broker_state.json
a cada fill e recarregado no boot. Antes, todo restart zerava o caixa para
$100k e apagava as posições — fragmentando o track-record forward em pedaços e
resetando a marcação diária. Agora um restart (proposital ou por queda de luz)
RETOMA a carteira de onde parou, mantendo o nav.csv contínuo e o total_return
ancorado no capital inicial VERDADEIRO. Depois de dias desligado, a primeira
marcação mostra o salto real de preço — igual a uma carteira mantida no fim de
semana, o que é mais honesto que fingir que voltou a $100k.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import numpy as np
from typing import Dict, List, Optional
from core.config import config
from core.logger import get_logger
from .abstract import AbstractBroker, Order, OrderSide, OrderType, OrderStatus, Fill

log = get_logger("PaperBroker")


class PaperBroker(AbstractBroker):
    """
    Broker de paper trading com modelo simplificado (nao reproduz fila/L2).
    - Slippage aleatório baseado em bid-ask spread
    - Comissão por trade
    - Latência de execução simulada
    - Rastreamento de P&L
    - Estado persistido em disco (sobrevive a restart)
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        slippage_bps: float = None,
        commission_bps: float = None,
        persist: bool = True,
        state_path=None,
    ):
        self.cash = initial_capital
        self.initial_capital = initial_capital
        self.positions: Dict[str, float] = {}       # symbol → quantity
        self.prices: Dict[str, float] = {}          # symbol → last price
        self.fills: List[Fill] = []
        self.orders: Dict[str, Order] = {}
        self.slippage_bps = (slippage_bps or config.backtest_slippage_bps) / 10000
        self.commission_bps = (commission_bps or config.backtest_commission_bps) / 10000
        self._pnl_history: List[float] = [0.0]
        self._fills_cum = 0  # contador CUMULATIVO de fills (persiste no restart)

        # ── Persistência de estado ────────────────────────────────
        self._persist = persist
        if state_path is not None:
            self._state_path = str(state_path)
        else:
            self._state_path = str(config.data_lake_dir / "paper_broker_state.json")
        if self._persist:
            self._load_state()

    # ─── Persistência ────────────────────────────────────────────────

    def _load_state(self):
        """Recarrega caixa/posições/preços/capital do disco, se existir."""
        try:
            if not os.path.exists(self._state_path):
                return
            with open(self._state_path, "r") as f:
                st = json.load(f)
            # capital inicial VERDADEIRO é preservado para o total_return ficar
            # ancorado na origem, não re-ancorado a cada boot.
            self.initial_capital = float(st.get("initial_capital", self.initial_capital))
            self.cash = float(st.get("cash", self.cash))
            self.positions = {k: float(v) for k, v in (st.get("positions") or {}).items()}
            self.prices = {k: float(v) for k, v in (st.get("prices") or {}).items()}
            self._fills_cum = int(st.get("fills_cum", 0))
            nav = self.cash + sum(
                q * self.prices.get(s, 0.0) for s, q in self.positions.items()
            )
            log.info(
                f"PaperBroker: estado retomado do disco — "
                f"caixa=${self.cash:,.2f}, {len(self.positions)} posições, "
                f"NAV≈${nav:,.2f} (capital inicial=${self.initial_capital:,.2f})"
            )
        except Exception as e:
            log.warning(f"PaperBroker: estado corrompido, começando limpo: {e}")

    def _save_state(self):
        """Grava o estado atomicamente (tmp + os.replace)."""
        if not self._persist:
            return
        try:
            st = {
                "initial_capital": self.initial_capital,
                "cash": self.cash,
                "positions": self.positions,
                "prices": self.prices,
                "fills_cum": self._fills_cum,
                "ts": time.time(),
            }
            tmp = self._state_path + ".tmp"
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(st, f)
            os.replace(tmp, self._state_path)  # atômico
        except Exception as e:
            log.warning(f"PaperBroker: falha ao gravar estado: {e}")

    async def submit_order(self, order: Order) -> Fill:
        """Simula execução com slippage e comissão."""
        # Latência simulada (5-50ms)
        await asyncio.sleep(np.random.uniform(0.005, 0.05))

        price = self.prices.get(order.symbol, 100.0)

        # Slippage adverso
        slippage = np.random.normal(self.slippage_bps, self.slippage_bps * 0.5)
        if order.side == OrderSide.BUY:
            fill_price = price * (1 + abs(slippage))
        else:
            fill_price = price * (1 - abs(slippage))

        # Comissão
        notional = abs(order.quantity * fill_price)
        commission = notional * self.commission_bps

        # Verifica cash disponível
        cost = notional + commission
        if order.side == OrderSide.BUY and cost > self.cash:
            # Reduz quantidade para caber no cash disponível
            adjusted_qty = (self.cash * 0.99) / (fill_price * (1 + self.commission_bps))
            if adjusted_qty < 0.001:
                order.status = OrderStatus.REJECTED
                raise ValueError(f"Cash insuficiente para {order.symbol}")
            order.quantity = adjusted_qty
            notional = adjusted_qty * fill_price
            commission = notional * self.commission_bps
            cost = notional + commission

        # Executa
        if order.side == OrderSide.BUY:
            self.cash -= cost
            self.positions[order.symbol] = self.positions.get(order.symbol, 0) + order.quantity
        else:
            # Venda pode reduzir long OU abrir/aumentar short. O backtest gera
            # sinais -1; limitar SELL ao estoque existente fazia a execucao
            # implementar outro sistema (long-only) sem declarar isso.
            sell_qty = order.quantity
            revenue = sell_qty * fill_price - commission
            self.cash += revenue
            self.positions[order.symbol] = self.positions.get(order.symbol, 0) - sell_qty

        # Remove posições zeradas
        if abs(self.positions.get(order.symbol, 0)) < 1e-6:
            self.positions.pop(order.symbol, None)

        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=fill_price,
            commission=commission,
            broker="paper",
        )
        self.fills.append(fill)
        self._fills_cum += 1
        order.status = OrderStatus.FILLED
        order.filled_price = fill_price
        order.filled_quantity = order.quantity
        order.filled_at = time.time()

        # Mantém preço atualizado para NAV correto
        self.prices[order.symbol] = fill_price

        # Persiste o estado após cada fill (JSON pequeno, throttle vem da banda)
        self._save_state()

        log.debug(
            f"Fill: {order.side.value} {order.quantity:.4f} {order.symbol} "
            f"@ {fill_price:.2f} (slip={slippage*10000:.1f}bps, "
            f"comm=${commission:.2f})"
        )
        return fill

    async def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order and order.status == OrderStatus.PENDING:
            order.status = OrderStatus.CANCELLED
            return True
        return False

    async def get_positions(self) -> Dict[str, float]:
        return dict(self.positions)

    async def get_account_value(self) -> float:
        """NAV = cash + posições a preço de mercado."""
        pos_value = sum(
            qty * self.prices.get(sym, 0)
            for sym, qty in self.positions.items()
        )
        return self.cash + pos_value

    async def flatten_all(self):
        """Fecha todas as posições a mercado."""
        log.warning(f"PaperBroker: FLATTEN ALL — {len(self.positions)} posições")
        for symbol in list(self.positions.keys()):
            qty = self.positions[symbol]
            if abs(qty) < 1e-6:
                continue
            side = OrderSide.SELL if qty > 0 else OrderSide.BUY
            order = Order(
                symbol=symbol,
                side=side,
                quantity=abs(qty),
                order_type=OrderType.MARKET,
                metadata={"reason": "flatten_all"},
            )
            try:
                await self.submit_order(order)
            except Exception as e:
                log.error(f"Erro ao fechar {symbol}: {e}")

    async def reduce_exposure(self, target_pct: float = 0.5):
        """Reduz exposição para target_pct do portfólio."""
        for symbol in list(self.positions.keys()):
            qty = self.positions[symbol]
            reduce_qty = abs(qty) * (1 - target_pct)
            if reduce_qty > 0.001:
                side = OrderSide.SELL if qty > 0 else OrderSide.BUY
                order = Order(
                    symbol=symbol,
                    side=side,
                    quantity=reduce_qty,
                    order_type=OrderType.MARKET,
                )
                try:
                    await self.submit_order(order)
                except Exception as _e:
                    log.debug(f"execution/paper_broker.py L242: falha silenciosa suprimida: {type(_e).__name__}: {_e}")

    def update_price(self, symbol: str, price: float):
        """Atualiza preço de mercado para cálculo de NAV."""
        self.prices[symbol] = price

    def get_pnl_summary(self) -> Dict:
        """Resumo de P&L."""
        nav = sum(
            qty * self.prices.get(sym, 0) for sym, qty in self.positions.items()
        ) + self.cash
        total_ret = (nav - self.initial_capital) / self.initial_capital
        return {
            "nav": nav,
            "cash": self.cash,
            "positions": len(self.positions),
            "total_return": total_ret,
            "n_fills": len(self.fills),
            "broker": "paper",
        }
