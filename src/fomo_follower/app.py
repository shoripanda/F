from __future__ import annotations

from fastapi import FastAPI

from .config import load_config
from .engine import FollowerEngine
from .models import EventResult, TradeEvent

app = FastAPI(
    title="FOMO Follow Trader Assistant",
    version="0.1.0",
    description="Follower-filtered trade signals with paper trading or manual-review output.",
)

engine = FollowerEngine(load_config())


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": engine.config.mode.value,
        "followed_traders": [
            rule.trader_id for rule in engine.config.followed_traders if rule.enabled
        ],
    }


@app.post("/events", response_model=EventResult)
def receive_event(event: TradeEvent) -> EventResult:
    return engine.handle(event)


@app.get("/paper/positions")
def paper_positions() -> list[dict]:
    rows: list[dict] = []
    for (trader_id, chain, token_address), position in engine.positions.items():
        rows.append(
            {
                "trader_id": trader_id,
                "chain": chain,
                "token_address": token_address,
                **position.model_dump(),
            }
        )
    return rows
