"""
QUANT HPC OS — Abstract Broker Interface
Interface padronizada para todos os brokers: Paper, Alpaca, Binance, IBKR.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import time
import uuid


class OrderSide(Enum):
    BUY  = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT  = "limit"
    TWAP   = "twap"
    VWAP   = "vwap"


class OrderStatus(Enum):
    PENDING   = "pending"
    SUBMITTED = "submitted"
    PARTIAL   = "partial"
    FILLED    = "filled"
    CANCELLED = "cancelled"
    REJECTED  = "rejected"


@dataclass
class Order:
    """Ordem de trading padronizada."""
    symbol: str
    side: OrderSide
    quantity: float                          # em unidades (não USD)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    order_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    strategy_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    created_at: float = field(default_factory=time.time)
    filled_at: Optional[float] = None
    filled_price: Optional[float] = None
    filled_quantity: float = 0.0
    slippage_bps: float = 0.0
    commission: float = 0.0
    metadata: Dict = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def direction(self) -> int:
        return 1 if self.side == OrderSide.BUY else -1

    @property
    def notional(self) -> float:
        price = self.filled_price or self.limit_price or 0.0
        return abs(self.filled_quantity * price)


@dataclass
class Fill:
    """Confirmação de execução."""
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    commission: float = 0.0
    timestamp: float = field(default_factory=time.time)
    broker: str = "paper"

    @property
    def notional(self) -> float:
        return abs(self.quantity * self.price)


class AbstractBroker(ABC):
    """Interface abstrata para todos os brokers."""

    @abstractmethod
    async def submit_order(self, order: Order) -> Fill:
        """Submete uma ordem e retorna o fill."""
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancela uma ordem pendente."""
        ...

    @abstractmethod
    async def get_positions(self) -> Dict[str, float]:
        """Retorna posições atuais {symbol: quantity}."""
        ...

    @abstractmethod
    async def get_account_value(self) -> float:
        """Retorna valor total da conta em USD."""
        ...

    @abstractmethod
    async def flatten_all(self):
        """Fecha todas as posições imediatamente."""
        ...

    async def submit_twap(
        self,
        order: Order,
        duration_seconds: int = 300,
        n_slices: int = 10,
    ) -> List[Fill]:
        """TWAP: divide ordem em n_slices ao longo do tempo."""
        import asyncio
        slice_qty = order.quantity / n_slices
        interval = duration_seconds / n_slices
        fills = []

        for i in range(n_slices):
            slice_order = Order(
                symbol=order.symbol,
                side=order.side,
                quantity=slice_qty,
                order_type=OrderType.MARKET,
                strategy_id=order.strategy_id,
                metadata={"parent_id": order.order_id, "slice": i},
            )
            try:
                fill = await self.submit_order(slice_order)
                fills.append(fill)
            except Exception:
                pass
            if i < n_slices - 1:
                await asyncio.sleep(interval)

        return fills
