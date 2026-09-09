from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import Side, TradeEvent


@dataclass(frozen=True)
class LiveOrderRequest:
    event_id: str
    trader_id: str
    side: Side
    chain: str
    token_address: str
    notional_usd: float | None
    sell_fraction: float | None
    max_slippage_bps: int


@dataclass(frozen=True)
class LiveOrderResult:
    status: str
    provider: str
    order_id: str | None = None
    tx_hash: str | None = None
    filled_quantity: float | None = None
    filled_notional_usd: float | None = None
    message: str | None = None


class LiveExecutor(Protocol):
    """Officially permitted exchange/DEX/wallet execution boundary."""

    name: str

    def execute(self, request: LiveOrderRequest) -> LiveOrderResult:
        ...


class DisabledLiveExecutor:
    """Fail-closed executor used until an approved provider adapter is configured."""

    name = "disabled"

    def execute(self, request: LiveOrderRequest) -> LiveOrderResult:
        return LiveOrderResult(
            status="blocked",
            provider=self.name,
            message=(
                "Live execution is not configured. Connect an officially permitted "
                "exchange/DEX/wallet API adapter before enabling live mode."
            ),
        )


def request_from_event(
    event: TradeEvent,
    *,
    buy_notional_usd: float | None,
    max_slippage_bps: int,
) -> LiveOrderRequest:
    return LiveOrderRequest(
        event_id=event.event_id,
        trader_id=event.trader_id,
        side=event.side,
        chain=event.chain,
        token_address=event.token_address,
        notional_usd=buy_notional_usd if event.side == Side.BUY else None,
        sell_fraction=event.sell_fraction if event.side == Side.SELL else None,
        max_slippage_bps=max_slippage_bps,
    )
