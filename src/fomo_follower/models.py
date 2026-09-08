from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Mode(str, Enum):
    PAPER = "paper"
    ALERT = "alert"


class TradeEvent(BaseModel):
    """Normalized trade event supplied through an allowed/manual source."""

    event_id: str = Field(min_length=1, max_length=200)
    trader_id: str = Field(min_length=1, max_length=200)
    side: Side
    chain: str = Field(min_length=1, max_length=50)
    token_address: str = Field(min_length=1, max_length=200)
    price_usd: float = Field(gt=0)
    executed_at: datetime
    source: str = Field(default="manual", max_length=50)
    sell_fraction: float | None = Field(default=None, gt=0, le=1)

    @field_validator("source")
    @classmethod
    def reject_unsafe_sources(cls, value: str) -> str:
        blocked = {"scraper", "browser-bot", "credential-capture"}
        if value.lower() in blocked:
            raise ValueError("Automated/scraped FOMO sources are not supported")
        return value


class FollowRule(BaseModel):
    trader_id: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    paper_buy_usd: float = Field(default=10.0, gt=0, le=100_000)


class AppConfig(BaseModel):
    mode: Mode = Mode.PAPER
    followed_traders: list[FollowRule] = Field(default_factory=list)


class PaperPosition(BaseModel):
    token_address: str
    chain: str
    quantity: float = Field(ge=0)
    cost_basis_usd: float = Field(ge=0)


class EventResult(BaseModel):
    accepted: bool
    mode: Mode
    reason: str
    event: TradeEvent
    paper_action: dict | None = None
    review_action: dict | None = None
