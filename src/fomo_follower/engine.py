from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .models import AppConfig, EventResult, Mode, Side, TradeEvent
from .storage import SQLiteStore, SQLiteTransaction


@dataclass
class FollowerEngine:
    config: AppConfig
    store: SQLiteStore

    def _rule_for(self, trader_id: str):
        for rule in self.config.followed_traders:
            if rule.enabled and rule.trader_id == trader_id:
                return rule
        return None

    def handle(self, event: TradeEvent) -> EventResult:
        """Process one event atomically against persistent SQLite state."""
        with self.store.transaction() as tx:
            if tx.event_exists(event.event_id):
                return EventResult(
                    accepted=False,
                    mode=self.config.mode,
                    reason="duplicate_event",
                    event=event,
                )

            rule = self._rule_for(event.trader_id)
            if rule is None:
                tx.insert_event(
                    event, accepted=False, reason="trader_not_followed"
                )
                return EventResult(
                    accepted=False,
                    mode=self.config.mode,
                    reason="trader_not_followed",
                    event=event,
                )

            if self.config.mode == Mode.ALERT:
                reason = "followed_trader_signal_requires_user_review"
                tx.insert_event(event, accepted=True, reason=reason)
                tx.upsert_last_price(event)
                return EventResult(
                    accepted=True,
                    mode=self.config.mode,
                    reason=reason,
                    event=event,
                    review_action={
                        "trader_id": event.trader_id,
                        "side": event.side.value,
                        "chain": event.chain,
                        "token_address": event.token_address,
                        "observed_price_usd": event.price_usd,
                        "message": "Review this signal and place any real trade yourself.",
                    },
                )

            return self._paper_trade(event, rule.paper_buy_usd, tx)

    def _paper_trade(
        self,
        event: TradeEvent,
        paper_buy_usd: float,
        tx: SQLiteTransaction,
    ) -> EventResult:
        current = tx.get_position(
            event.trader_id, event.chain, event.token_address
        )
        current_quantity = current.quantity if current else Decimal("0")
        current_cost = current.cost_basis_usd if current else Decimal("0")
        price = Decimal(str(event.price_usd))

        tx.insert_event(event, accepted=True, reason="paper_trade_applied")
        tx.upsert_last_price(event)

        if event.side == Side.BUY:
            notional = Decimal(str(paper_buy_usd))
            quantity = notional / price
            updated_quantity = current_quantity + quantity
            updated_cost = current_cost + notional

            tx.insert_trade(
                event=event,
                quantity=quantity,
                notional_usd=notional,
            )
            tx.upsert_position(
                trader_id=event.trader_id,
                chain=event.chain,
                token_address=event.token_address,
                quantity=updated_quantity,
                cost_basis_usd=updated_cost,
            )
            action = {
                "side": "buy",
                "paper_notional_usd": float(notional),
                "paper_quantity": float(quantity),
                "position_quantity": float(updated_quantity),
                "position_cost_basis_usd": float(updated_cost),
            }
        else:
            if current_quantity <= 0:
                action = {
                    "side": "sell",
                    "paper_quantity": 0,
                    "paper_proceeds_usd": 0,
                    "position_quantity": 0,
                    "realized_pnl_usd": 0,
                    "note": "No paper position exists for this trader/token.",
                }
            else:
                fraction = Decimal(str(event.sell_fraction or 1.0))
                quantity = current_quantity * fraction
                proceeds = quantity * price
                released_cost = current_cost * fraction
                realized_pnl = proceeds - released_cost
                remaining_quantity = current_quantity - quantity
                remaining_cost = current_cost - released_cost

                tx.insert_trade(
                    event=event,
                    quantity=quantity,
                    notional_usd=proceeds,
                    cost_basis_released_usd=released_cost,
                    realized_pnl_usd=realized_pnl,
                )
                tx.upsert_position(
                    trader_id=event.trader_id,
                    chain=event.chain,
                    token_address=event.token_address,
                    quantity=remaining_quantity,
                    cost_basis_usd=remaining_cost,
                )
                action = {
                    "side": "sell",
                    "paper_quantity": float(quantity),
                    "paper_proceeds_usd": float(proceeds),
                    "cost_basis_released_usd": float(released_cost),
                    "realized_pnl_usd": float(realized_pnl),
                    "sell_fraction": float(fraction),
                    "position_quantity": float(remaining_quantity),
                    "position_cost_basis_usd": float(remaining_cost),
                }

        return EventResult(
            accepted=True,
            mode=self.config.mode,
            reason="paper_trade_applied",
            event=event,
            paper_action=action,
        )
