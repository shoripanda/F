from datetime import datetime, timedelta, timezone

from fomo_follower.database import Database
from fomo_follower.engine import FollowerEngine
from fomo_follower.models import AppConfig, FollowRule, Mode, Side, TradeEvent
from fomo_follower.storage import SQLiteStore


def build_engine(db_path):
    database = Database(db_path)
    store = SQLiteStore(database)
    store.initialize()
    config = AppConfig(
        mode=Mode.PAPER,
        followed_traders=[FollowRule(trader_id="alice", paper_buy_usd=25)],
    )
    return FollowerEngine(config, store), store


def trade_event(
    event_id: str,
    side: Side,
    price_usd: float,
    *,
    sell_fraction=None,
    executed_at=None,
):
    return TradeEvent(
        event_id=event_id,
        trader_id="alice",
        side=side,
        chain="solana",
        token_address="TEST_TOKEN",
        price_usd=price_usd,
        executed_at=executed_at or datetime.now(timezone.utc),
        source="manual",
        sell_fraction=sell_fraction,
    )


def test_position_and_deduplication_survive_restart(tmp_path):
    db_path = tmp_path / "persistent.sqlite3"
    engine1, store1 = build_engine(db_path)

    buy = trade_event("test-buy-001", Side.BUY, 1.25)
    first = engine1.handle(buy)
    assert first.accepted is True
    assert store1.list_positions()[0]["quantity"] == 20.0
    assert store1.list_positions()[0]["cost_basis_usd"] == 25.0

    # Re-create Database/Store/Engine as if the server had restarted.
    engine2, store2 = build_engine(db_path)
    persisted = store2.list_positions()[0]
    assert persisted["quantity"] == 20.0
    assert persisted["cost_basis_usd"] == 25.0

    duplicate = engine2.handle(buy)
    assert duplicate.accepted is False
    assert duplicate.reason == "duplicate_event"
    assert len(store2.list_trades()) == 1


def test_partial_sell_persists_realized_pnl(tmp_path):
    db_path = tmp_path / "pnl.sqlite3"
    engine, store = build_engine(db_path)
    now = datetime.now(timezone.utc)

    engine.handle(
        trade_event("buy-001", Side.BUY, 1.25, executed_at=now)
    )
    sell = engine.handle(
        trade_event(
            "sell-001",
            Side.SELL,
            1.50,
            sell_fraction=0.5,
            executed_at=now + timedelta(minutes=10),
        )
    )

    assert sell.accepted is True
    assert sell.paper_action["paper_quantity"] == 10.0
    assert sell.paper_action["paper_proceeds_usd"] == 15.0
    assert sell.paper_action["cost_basis_released_usd"] == 12.5
    assert sell.paper_action["realized_pnl_usd"] == 2.5
    assert sell.paper_action["position_quantity"] == 10.0
    assert sell.paper_action["position_cost_basis_usd"] == 12.5

    position = store.list_positions()[0]
    assert position["quantity"] == 10.0
    assert position["cost_basis_usd"] == 12.5

    trades = store.list_trades()
    sell_trade = trades[0]
    assert sell_trade["side"] == "sell"
    assert sell_trade["notional_usd"] == 15.0
    assert sell_trade["cost_basis_released_usd"] == 12.5
    assert sell_trade["realized_pnl_usd"] == 2.5

    # Restart and verify the same persisted state is still available.
    _, restarted_store = build_engine(db_path)
    restarted_position = restarted_store.list_positions()[0]
    assert restarted_position["quantity"] == 10.0
    assert restarted_position["cost_basis_usd"] == 12.5
    assert restarted_store.list_trades()[0]["realized_pnl_usd"] == 2.5
