from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Query

from .config import load_config
from .database import Database
from .engine import FollowerEngine
from .models import EventResult, TradeEvent
from .storage import SQLiteStore


app = FastAPI(
    title="FOMO Follow Trader Assistant",
    version="0.3.0",
    description=(
        "Follower-filtered trade signals with persistent SQLite paper trading "
        "and a guarded live-execution boundary."
    ),
)

database = Database(
    Path(os.getenv("FOMO_FOLLOWER_DB_PATH", "data/fomo_follower.sqlite3"))
)
store = SQLiteStore(database)
store.initialize()
engine = FollowerEngine(load_config(), store)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": engine.config.mode.value,
        "persistence": "sqlite",
        "database_path": str(database.path),
        "fomo_profile_url": engine.config.fomo_profile_url,
        "followed_traders": [
            rule.trader_id for rule in engine.config.followed_traders if rule.enabled
        ],
        "live": {
            "enabled": engine.config.live.enabled,
            "executor_config": engine.config.live.executor,
            "executor_active": engine.live_executor.name,
            "kill_switch": engine.config.live.kill_switch,
            "max_order_usd": engine.config.live.max_order_usd,
            "max_daily_notional_usd": engine.config.live.max_daily_notional_usd,
            "max_slippage_bps": engine.config.live.max_slippage_bps,
            "stale_signal_seconds": engine.config.live.stale_signal_seconds,
            "require_token_allowlist": engine.config.live.require_token_allowlist,
        },
    }


@app.get("/events")
def events(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
    return store.list_events(limit=limit)


@app.post("/events", response_model=EventResult)
def receive_event(event: TradeEvent) -> EventResult:
    return engine.handle(event)


@app.get("/paper/positions")
def paper_positions(
    include_zero: bool = Query(default=True),
) -> list[dict]:
    return store.list_positions(include_zero=include_zero)


@app.get("/paper/trades")
def paper_trades(
    trader_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    return store.list_trades(trader_id=trader_id, limit=limit)
