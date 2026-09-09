from datetime import datetime, timezone

from fomo_follower.database import Database
from fomo_follower.engine import FollowerEngine
from fomo_follower.models import AppConfig, FollowRule, Mode, Side, TradeEvent
from fomo_follower.storage import SQLiteStore


def event(
    event_id: str,
    trader: str,
    side: Side,
    sell_fraction=None,
    price_usd: float = 2.0,
):
    return TradeEvent(
        event_id=event_id,
        trader_id=trader,
        side=side,
        chain="solana",
        token_address="TOKEN",
        price_usd=price_usd,
        executed_at=datetime.now(timezone.utc),
        source="manual",
        sell_fraction=sell_fraction,
    )


def make_engine(tmp_path, config: AppConfig):
    database = Database(tmp_path / "test.sqlite3")
    store = SQLiteStore(database)
    store.initialize()
    return FollowerEngine(config, store), store


def test_unfollowed_trader_is_rejected_and_audited(tmp_path):
    engine, store = make_engine(
        tmp_path,
        AppConfig(mode=Mode.PAPER, followed_traders=[FollowRule(trader_id="alice")]),
    )
    result = engine.handle(event("1", "bob", Side.BUY))

    assert result.accepted is False
    assert result.reason == "trader_not_followed"
    rows = store.list_events()
    assert len(rows) == 1
    assert rows[0]["accepted"] is False
    assert rows[0]["reason"] == "trader_not_followed"


def test_duplicate_event_is_rejected(tmp_path):
    engine, store = make_engine(
        tmp_path,
        AppConfig(mode=Mode.PAPER, followed_traders=[FollowRule(trader_id="alice")]),
    )
    first = engine.handle(event("1", "alice", Side.BUY))
    second = engine.handle(event("1", "alice", Side.BUY))

    assert first.accepted is True
    assert second.accepted is False
    assert second.reason == "duplicate_event"
    assert len(store.list_trades()) == 1


def test_paper_buy_and_partial_sell_are_trader_scoped(tmp_path):
    config = AppConfig(
        mode=Mode.PAPER,
        followed_traders=[
            FollowRule(trader_id="alice", paper_buy_usd=20),
            FollowRule(trader_id="bob", paper_buy_usd=10),
        ],
    )
    engine, store = make_engine(tmp_path, config)

    engine.handle(event("a1", "alice", Side.BUY))
    engine.handle(event("b1", "bob", Side.BUY))
    engine.handle(event("a2", "alice", Side.SELL, sell_fraction=0.5))

    positions = {
        (row["trader_id"], row["chain"], row["token_address"]): row
        for row in store.list_positions()
    }
    alice = positions[("alice", "solana", "TOKEN")]
    bob = positions[("bob", "solana", "TOKEN")]

    assert alice["quantity"] == 5.0
    assert alice["cost_basis_usd"] == 10.0
    assert bob["quantity"] == 5.0
    assert bob["cost_basis_usd"] == 10.0


def test_alert_mode_never_creates_paper_position(tmp_path):
    engine, store = make_engine(
        tmp_path,
        AppConfig(mode=Mode.ALERT, followed_traders=[FollowRule(trader_id="alice")]),
    )
    result = engine.handle(event("1", "alice", Side.BUY))

    assert result.accepted is True
    assert result.review_action is not None
    assert store.list_positions() == []
    assert store.list_trades() == []
