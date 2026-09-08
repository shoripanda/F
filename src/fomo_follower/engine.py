from __future__ import annotations

from dataclasses import dataclass, field

from .models import AppConfig, EventResult, Mode, PaperPosition, Side, TradeEvent


@dataclass
class FollowerEngine:
    config: AppConfig
    seen_event_ids: set[str] = field(default_factory=set)
    positions: dict[tuple[str, str, str], PaperPosition] = field(default_factory=dict)

    def _rule_for(self, trader_id: str):
        for rule in self.config.followed_traders:
            if rule.enabled and rule.trader_id == trader_id:
                return rule
        return None

    def handle(self, event: TradeEvent) -> EventResult:
        if event.event_id in self.seen_event_ids:
            return EventResult(
                accepted=False,
                mode=self.config.mode,
                reason="duplicate_event",
                event=event,
            )

        rule = self._rule_for(event.trader_id)
        if rule is None:
            return EventResult(
                accepted=False,
                mode=self.config.mode,
                reason="trader_not_followed",
                event=event,
            )

        self.seen_event_ids.add(event.event_id)

        if self.config.mode == Mode.ALERT:
            return EventResult(
                accepted=True,
                mode=self.config.mode,
                reason="followed_trader_signal_requires_user_review",
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

        return self._paper_trade(event, rule.paper_buy_usd)

    def _paper_trade(self, event: TradeEvent, paper_buy_usd: float) -> EventResult:
        key = (event.trader_id, event.chain, event.token_address)
        current = self.positions.get(
            key,
            PaperPosition(
                token_address=event.token_address,
                chain=event.chain,
                quantity=0,
                cost_basis_usd=0,
            ),
        )

        if event.side == Side.BUY:
            quantity = paper_buy_usd / event.price_usd
            updated = current.model_copy(
                update={
                    "quantity": current.quantity + quantity,
                    "cost_basis_usd": current.cost_basis_usd + paper_buy_usd,
                }
            )
            self.positions[key] = updated
            action = {
                "side": "buy",
                "paper_notional_usd": paper_buy_usd,
                "paper_quantity": quantity,
                "position_quantity": updated.quantity,
            }
        else:
            if current.quantity <= 0:
                action = {
                    "side": "sell",
                    "paper_quantity": 0,
                    "position_quantity": 0,
                    "note": "No paper position exists for this trader/token.",
                }
            else:
                fraction = event.sell_fraction or 1.0
                quantity = current.quantity * fraction
                remaining = current.quantity - quantity
                remaining_cost = current.cost_basis_usd * (1 - fraction)
                updated = current.model_copy(
                    update={"quantity": remaining, "cost_basis_usd": remaining_cost}
                )
                self.positions[key] = updated
                action = {
                    "side": "sell",
                    "paper_quantity": quantity,
                    "paper_proceeds_usd": quantity * event.price_usd,
                    "sell_fraction": fraction,
                    "position_quantity": remaining,
                }

        return EventResult(
            accepted=True,
            mode=self.config.mode,
            reason="paper_trade_applied",
            event=event,
            paper_action=action,
        )
