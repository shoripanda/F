from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import sqlite3
from typing import Iterator

from .database import Database
from .models import TradeEvent


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _decimal_text(value: Decimal | float | int | str) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    return format(Decimal(str(value)), "f")


def _optional_decimal_text(value: Decimal | float | int | str | None) -> str | None:
    if value is None:
        return None
    return _decimal_text(value)


@dataclass(frozen=True)
class PositionRecord:
    trader_id: str
    chain: str
    token_address: str
    quantity: Decimal
    cost_basis_usd: Decimal
    updated_at: str


class SQLiteTransaction:
    """Store operations bound to a single active SQLite transaction."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def event_exists(self, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM events WHERE event_id = ? LIMIT 1", (event_id,)
        ).fetchone()
        return row is not None

    def insert_event(
        self,
        event: TradeEvent,
        *,
        accepted: bool,
        reason: str,
        created_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO events (
                event_id, trader_id, side, chain, token_address,
                price_usd, executed_at, source, sell_fraction,
                accepted, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.trader_id,
                event.side.value,
                event.chain,
                event.token_address,
                _decimal_text(event.price_usd),
                _datetime_text(event.executed_at),
                event.source,
                _optional_decimal_text(event.sell_fraction),
                1 if accepted else 0,
                reason,
                created_at or _utc_now_iso(),
            ),
        )

    def get_position(
        self, trader_id: str, chain: str, token_address: str
    ) -> PositionRecord | None:
        row = self.conn.execute(
            """
            SELECT trader_id, chain, token_address, quantity, cost_basis_usd, updated_at
            FROM paper_positions
            WHERE trader_id = ? AND chain = ? AND token_address = ?
            """,
            (trader_id, chain, token_address),
        ).fetchone()
        if row is None:
            return None
        return PositionRecord(
            trader_id=row["trader_id"],
            chain=row["chain"],
            token_address=row["token_address"],
            quantity=Decimal(row["quantity"]),
            cost_basis_usd=Decimal(row["cost_basis_usd"]),
            updated_at=row["updated_at"],
        )

    def upsert_position(
        self,
        *,
        trader_id: str,
        chain: str,
        token_address: str,
        quantity: Decimal,
        cost_basis_usd: Decimal,
        updated_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO paper_positions (
                trader_id, chain, token_address, quantity, cost_basis_usd, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (trader_id, chain, token_address) DO UPDATE SET
                quantity = excluded.quantity,
                cost_basis_usd = excluded.cost_basis_usd,
                updated_at = excluded.updated_at
            """,
            (
                trader_id,
                chain,
                token_address,
                _decimal_text(quantity),
                _decimal_text(cost_basis_usd),
                updated_at or _utc_now_iso(),
            ),
        )

    def insert_trade(
        self,
        *,
        event: TradeEvent,
        quantity: Decimal,
        notional_usd: Decimal,
        cost_basis_released_usd: Decimal | None = None,
        realized_pnl_usd: Decimal | None = None,
        created_at: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO paper_trades (
                event_id, trader_id, side, chain, token_address,
                quantity, price_usd, notional_usd,
                cost_basis_released_usd, realized_pnl_usd,
                executed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.trader_id,
                event.side.value,
                event.chain,
                event.token_address,
                _decimal_text(quantity),
                _decimal_text(event.price_usd),
                _decimal_text(notional_usd),
                _optional_decimal_text(cost_basis_released_usd),
                _optional_decimal_text(realized_pnl_usd),
                _datetime_text(event.executed_at),
                created_at or _utc_now_iso(),
            ),
        )

    def upsert_last_price(self, event: TradeEvent) -> None:
        observed_at = _datetime_text(event.executed_at)
        self.conn.execute(
            """
            INSERT INTO token_prices (
                chain, token_address, price_usd, observed_at, source
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (chain, token_address) DO UPDATE SET
                price_usd = excluded.price_usd,
                observed_at = excluded.observed_at,
                source = excluded.source
            WHERE excluded.observed_at >= token_prices.observed_at
            """,
            (
                event.chain,
                event.token_address,
                _decimal_text(event.price_usd),
                observed_at,
                event.source,
            ),
        )


class SQLiteStore:
    """Persistence boundary for events, positions, trades, and observed prices."""

    def __init__(self, database: Database):
        self.database = database

    def initialize(self) -> None:
        self.database.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = self.database.connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[SQLiteTransaction]:
        with self.database.transaction() as conn:
            yield SQLiteTransaction(conn)

    def event_exists(self, event_id: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM events WHERE event_id = ? LIMIT 1", (event_id,)
            ).fetchone()
            return row is not None

    def list_positions(self, *, include_zero: bool = True) -> list[dict]:
        where = "" if include_zero else "WHERE CAST(quantity AS REAL) > 0"
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT trader_id, chain, token_address, quantity, cost_basis_usd, updated_at
                FROM paper_positions
                {where}
                ORDER BY trader_id, chain, token_address
                """
            ).fetchall()
        return [
            {
                "trader_id": row["trader_id"],
                "chain": row["chain"],
                "token_address": row["token_address"],
                "quantity": float(Decimal(row["quantity"])),
                "cost_basis_usd": float(Decimal(row["cost_basis_usd"])),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def list_trades(
        self, *, trader_id: str | None = None, limit: int = 100
    ) -> list[dict]:
        limit = max(1, min(limit, 1000))
        params: list[object] = []
        where = ""
        if trader_id is not None:
            where = "WHERE trader_id = ?"
            params.append(trader_id)
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, event_id, trader_id, side, chain, token_address,
                       quantity, price_usd, notional_usd,
                       cost_basis_released_usd, realized_pnl_usd,
                       executed_at, created_at
                FROM paper_trades
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._trade_row_to_dict(row) for row in rows]

    def list_events(self, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 1000))
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT event_id, trader_id, side, chain, token_address,
                       price_usd, executed_at, source, sell_fraction,
                       accepted, reason, created_at
                FROM events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            result.append(
                {
                    "event_id": row["event_id"],
                    "trader_id": row["trader_id"],
                    "side": row["side"],
                    "chain": row["chain"],
                    "token_address": row["token_address"],
                    "price_usd": float(Decimal(row["price_usd"])),
                    "executed_at": row["executed_at"],
                    "source": row["source"],
                    "sell_fraction": (
                        None
                        if row["sell_fraction"] is None
                        else float(Decimal(row["sell_fraction"]))
                    ),
                    "accepted": bool(row["accepted"]),
                    "reason": row["reason"],
                    "created_at": row["created_at"],
                }
            )
        return result

    def _trade_row_to_dict(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "trader_id": row["trader_id"],
            "side": row["side"],
            "chain": row["chain"],
            "token_address": row["token_address"],
            "quantity": float(Decimal(row["quantity"])),
            "price_usd": float(Decimal(row["price_usd"])),
            "notional_usd": float(Decimal(row["notional_usd"])),
            "cost_basis_released_usd": (
                None
                if row["cost_basis_released_usd"] is None
                else float(Decimal(row["cost_basis_released_usd"]))
            ),
            "realized_pnl_usd": (
                None
                if row["realized_pnl_usd"] is None
                else float(Decimal(row["realized_pnl_usd"]))
            ),
            "executed_at": row["executed_at"],
            "created_at": row["created_at"],
        }
