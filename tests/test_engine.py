from datetime import datetime, timezone

from fomo_follower.engine import FollowerEngine
from fomo_follower.models import AppConfig, FollowRule, Mode, Side, TradeEvent


def event(event_id: str, trader: str, side: Side, sell_fraction=None):
    return TradeEvent(
        event_id=event_id,
        trader_id=trader,
        side=side,
        chain="solana",
        token_address="TOKEN",
        price_usd=2.0,
        executed_at=datetime.now(timezone.utc),
        source="manual",
        sell_fraction=sell_fraction,
    )


def test_unfollowed_trader_is_rejected():
    engine = FollowerEngine(
        AppConfig(mode=Mode.PAPER, followed_traders=[FollowRule(trader_id="alice")])
    )
    result = engine.handle(event("1", "bob", Side.BUY))
    assert result.accepted is False
    assert result.reason == "trader_not_followed"


def test_duplicate_event_is_rejected():
    engine = FollowerEngine(
        AppConfig(mode=Mode.PAPER, followed_traders=[FollowRule(trader_id="alice")])
    )
    first = engine.handle(event("1", "alice", Side.BUY))
    second = engine.handle(event("1", "alice", Side.BUY))
    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "duplicate_event"


def test_paper_buy_and_partial_sell_are_trader_scoped():
    config = AppConfig(
        mode=Mode.PAPER,
        followed_traders=[
            FollowRule(trader_id="alice", paper_buy_usd=20),
            FollowRule(trader_id="bob", paper_buy_usd=10),
        ],
    )
    engine = FollowerEngine(config)

    engine.handle(event("a1", "alice", Side.BUY))
    engine.handle(event("b1", "bob", Side.BUY))
    engine.handle(event("a2", "alice", Side.SELL, sell_fraction=0.5))

    alice = engine.positions[("alice", "solana", "TOKEN")]
    bob = engine.positions[("bob", "solana", "TOKEN")]

    assert alice.quantity == 5.0
    assert bob.quantity == 5.0


def test_alert_mode_never_creates_paper_position():
    engine = FollowerEngine(
        AppConfig(mode=Mode.ALERT, followed_traders=[FollowRule(trader_id="alice")])
    )
    result = engine.handle(event("1", "alice", Side.BUY))
    assert result.accepted is True
    assert result.review_action is not None
    assert engine.positions == {}
