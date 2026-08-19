"""SQLite persistence for snapshots and report history.

The Portfolio Value chart cannot be reconstructed after the fact - Toss
reports today's numbers, not last month's - so every report run appends one
snapshot row. Starting this early is the whole point.
"""

import os
import sqlite3
from datetime import datetime, timedelta

from src.models import to_decimal

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

#: Ranges offered by the dashboard's chart selector.
RANGE_DAYS = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "ALL": None,
}


def _text(value):
    return None if value is None else str(value)


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self):
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
            schema = handle.read()
        with self._connect() as connection:
            connection.executescript(schema)

    # ------------------------------------------------------------- snapshots

    def save_snapshot(self, snapshot, ts=None):
        """Append one snapshot. Idempotent per timestamp."""
        ts = ts or datetime.now().replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    ts, total_krw, purchase_krw, profit_krw, profit_rate,
                    profit_after_cost_krw, profit_rate_after_cost,
                    daily_profit_krw, daily_profit_rate,
                    exchange_rate, has_unconverted_fx
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    _text(snapshot.total_krw),
                    _text(snapshot.purchase_krw),
                    _text(snapshot.profit_krw),
                    _text(snapshot.profit_rate),
                    _text(snapshot.profit_after_cost_krw),
                    _text(snapshot.profit_rate_after_cost),
                    _text(snapshot.daily_profit_krw),
                    _text(snapshot.daily_profit_rate),
                    _text(snapshot.exchange_rate),
                    1 if snapshot.has_unconverted_fx else 0,
                ),
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO position_snapshots (
                    ts, symbol, name, market_country, currency, quantity,
                    last_price, avg_price, market_value, profit_loss,
                    profit_rate, daily_profit_loss, source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        ts,
                        position.symbol,
                        position.name,
                        position.market_country,
                        position.currency,
                        _text(position.quantity),
                        _text(position.last_price),
                        _text(position.avg_purchase_price),
                        _text(position.evaluation),
                        _text(position.profit_loss),
                        _text(position.profit_rate),
                        _text(position.daily_profit_loss),
                        position.source,
                    )
                    for position in snapshot.positions
                ],
            )
        return ts

    def history(self, range_key="3M"):
        """Return snapshot rows for the chart, oldest first."""
        days = RANGE_DAYS.get(str(range_key).upper(), 90)
        query = "SELECT * FROM snapshots"
        params = ()
        if days is not None:
            since = (datetime.now() - timedelta(days=days)).replace(microsecond=0)
            query += " WHERE ts >= ?"
            params = (since.isoformat(),)
        query += " ORDER BY ts ASC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            {
                "ts": row["ts"],
                "total_krw": to_decimal(row["total_krw"], default=0),
                "profit_krw": to_decimal(row["profit_krw"], default=0),
                "profit_rate": to_decimal(row["profit_rate"], default=0),
            }
            for row in rows
        ]

    def latest_snapshot(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def snapshot_count(self):
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    # ------------------------------------------------------ name overrides

    def symbol_names(self):
        """Return {symbol: display name} for every override."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol, name FROM symbol_overrides"
            ).fetchall()
        return {row["symbol"]: row["name"] for row in rows}

    def set_symbol_name(self, symbol, name):
        """Store a display name, or clear it when name is blank."""
        name = (name or "").strip()
        with self._connect() as connection:
            if not name:
                connection.execute(
                    "DELETE FROM symbol_overrides WHERE symbol = ?", (symbol,)
                )
                return None
            connection.execute(
                """
                INSERT OR REPLACE INTO symbol_overrides (symbol, name, updated_at)
                VALUES (?,?,?)
                """,
                (symbol, name, datetime.now().replace(microsecond=0).isoformat()),
            )
        return name

    # --------------------------------------------------------------- reports

    def save_report(self, page_id, title=None, url=None, ai_comment=None, ts=None):
        ts = ts or datetime.now().replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reports (page_id, ts, title, url, ai_comment)
                VALUES (?,?,?,?,?)
                """,
                (page_id, ts, title, url, ai_comment),
            )
        return ts

    def recent_reports(self, limit=20):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reports ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
