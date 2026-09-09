from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from .executor import DisabledLiveExecutor, LiveExecutor, request_from_event
from .models import AppConfig, EventResult, Mode, Side, TradeEvent
from .storage import SQLiteStore, SQLiteTransaction


@dataclass
class FollowerEngine:
    config: AppConfig
    store: SQLiteStore
    live_executor: LiveExecutor = field(default_factory=DisabledLiveExecutor)

    def _rule_for(self, trader_id: str):
        for rule in self.config.followed_traders:
            if rule.enabled and rule.trader_id == trader_id:
                return rule
        return None

    def _live_block_reason(self, event: TradeEvent, live_buy_usd: float | None) -> str | None:
        live = self.config.live
        if not live.enabled:
            return "live_execution_disabled"
        if live.kill_switch:
            return "live_kill_switch_enabled"
        if live.executor is None:
            return "live_executor_not_configured"
        if self.live_executor.name == "disabled":
            return "live_executor_not_available"

        executed_at = event.executed_at
        if executed_at.tzinfo is None:
            executed_at = executed_at.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - executed_at.astimezone(timezone.utc)).total_seconds()
        if age_seconds > live.stale_signal_seconds:
            return "stale_signal"

        if live.require_token_allowlist and event.token_address not in live.allowed_tokens:
            return "token_not_allowlisted"

        if event.side == Side.BUY:
            if live_buy_usd is None:
                return "live_buy_amount_not_configured"
            if live_buy_usd > live.max_order_usd:
                return "live_order_exceeds_max_order_usd"

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
                tx.insert_event(event, accepted=False, reason="trader_not_followed")
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

            if self.config.mode == Mode.LIVE:
                return self._live_trade(event, rule.live_buy_usd, tx)

            return self._paper_trade(event, rule.paper_buy_usd, tx)

    def _live_trade(
        self,
        event: TradeEvent,
        live_buy_usd: float | None,
        tx: SQLiteTransaction,
    ) -> EventResult:
        block_reason = self._live_block_reason(event, live_buy_usd)
        if block_reason is not None:
            tx.insert_event(event, accepted=False, reason=block_reason)
            tx.upsert_last_price(event)
            return EventResult(
                accepted=False,
                mode=self.config.mode,
                reason=block_reason,
                event=event,
                live_action={"status": "blocked"},
            )

        request = request_from_event(
            event,
            buy_notional_usd=live_buy_usd,
            max_slippage_bps=self.config.live.max_slippage_bps,
        )
        result = self.live_executor.execute(request)
        accepted = result.status in {"submitted", "filled"}
        reason = "live_order_submitted" if accepted else "live_order_failed"
        tx.insert_event(event, accepted=accepted, reason=reason)
        tx.upsert_last_price(event)

        return EventResult(
            accepted=accepted,
            mode=self.config.mode,
            reason=reason,
            event=event,
            live_action={
                "status": result.status,
                "provider": result.provider,
                "order_id": result.order_id,
                "tx_hash": result.tx_hash,
                "filled_quantity": result.filled_quantity,
                "filled_notional_usd": result.filled_notional_usd,
                "message": result.message,
            },
        )

    def _paper_trade(
        self,
        event: TradeEvent,
        paper_buy_usd: float,
        tx: SQLiteTransaction,
    ) -> EventResult:
        current = tx.get_position(event.trader_id, event.chain, event.token_address)
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

            tx.insert_trade(event=event, quantity=quantity, notional_usd=notional)
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
