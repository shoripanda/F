from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


SCHEMA_VERSION = "1"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    trader_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    executed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    sell_fraction TEXT,
    accepted INTEGER NOT NULL CHECK (accepted IN (0, 1)),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    trader_id TEXT NOT NULL,
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    quantity TEXT NOT NULL,
    cost_basis_usd TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (trader_id, chain, token_address)
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    trader_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    quantity TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    notional_usd TEXT NOT NULL,
    cost_basis_released_usd TEXT,
    realized_pnl_usd TEXT,
    executed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE TABLE IF NOT EXISTS token_prices (
    chain TEXT NOT NULL,
    token_address TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (chain, token_address)
);

CREATE INDEX IF NOT EXISTS idx_events_trader_executed
    ON events (trader_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_token_executed
    ON events (chain, token_address, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_trader_executed
    ON paper_trades (trader_id, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_token_executed
    ON paper_trades (chain, token_address, executed_at DESC);
"""


class Database:
    """Low-level SQLite connection, schema, and transaction manager."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        self._ensure_parent()
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def initialize(self) -> None:
        """Create the schema idempotently and verify its version."""
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA_SQL)
            row = conn.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO schema_meta (key, value) VALUES (?, ?)",
                    ("schema_version", SCHEMA_VERSION),
                )
            elif row["value"] != SCHEMA_VERSION:
                raise RuntimeError(
                    "Unsupported SQLite schema version: "
                    f"{row['value']} (expected {SCHEMA_VERSION})"
                )
            conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write unit atomically on one SQLite connection."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
