"""SQLite cache for daily bars.

Kept as its own connection helper rather than folded into
:class:`src.store.repo.Store` - historical bars are a concern the dashboard
and the report pipeline never touch, and growing ``Store`` for them would mix
two unrelated lifetimes into one class. It shares the same database file, so
one ``--db-path`` still means one file for the whole app.
"""

import os
import sqlite3
from datetime import date

from decimal import Decimal

from src.models import to_decimal
from src.strategy.bars import Bar, PriceHistory, as_date

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "store", "schema.sql"
)


def _text(value):
    return None if value is None else str(value)


def _row_to_bar(row):
    return Bar(
        date=row["date"],
        open=to_decimal(row["open"]),
        high=to_decimal(row["high"]),
        low=to_decimal(row["low"]),
        close=to_decimal(row["close"]),
        raw_close=to_decimal(row["raw_close"]),
        volume=to_decimal(row["volume"]),
    )


class BarCache:
    """Reads and writes ``daily_bars``/``bar_coverage``."""

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
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as handle:
            schema = handle.read()
        with self._connect() as connection:
            connection.executescript(schema)

    def bars(self, symbol, start=None, end=None):
        """The cached PriceHistory for ``symbol``, filtered to ``[start, end]``."""
        query = "SELECT * FROM daily_bars WHERE symbol = ?"
        params = [symbol]
        if start is not None:
            query += " AND date >= ?"
            params.append(str(as_date(start)))
        if end is not None:
            query += " AND date <= ?"
            params.append(str(as_date(end)))
        query += " ORDER BY date ASC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return PriceHistory(symbol, tuple(_row_to_bar(row) for row in rows))

    def upsert(self, symbol, bars, source, fetched_at=None):
        """Insert or replace bars for ``symbol``. Idempotent per (symbol, date)."""
        fetched_at = fetched_at or date.today().isoformat()
        rows = [
            (
                symbol,
                str(bar.date),
                _text(bar.open),
                _text(bar.high),
                _text(bar.low),
                _text(bar.close),
                _text(bar.raw_close),
                _text(bar.volume),
                source,
                fetched_at,
            )
            for bar in bars
        ]
        if not rows:
            return 0

        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO daily_bars
                    (symbol, date, open, high, low, close, raw_close, volume,
                     source, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, raw_close=excluded.raw_close,
                    volume=excluded.volume, source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                rows,
            )
            dates = sorted(bar.date for bar in bars)
            connection.execute(
                """
                INSERT INTO bar_coverage (symbol, first_date, last_date, source, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    first_date = MIN(bar_coverage.first_date, excluded.first_date),
                    last_date = MAX(bar_coverage.last_date, excluded.last_date),
                    source = excluded.source, fetched_at = excluded.fetched_at
                """,
                (symbol, str(dates[0]), str(dates[-1]), source, fetched_at),
            )
        return len(rows)

    def coverage(self, symbol):
        """``(first_date, last_date)`` as dates, or ``(None, None)`` if unseen."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT first_date, last_date FROM bar_coverage WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None or row["first_date"] is None:
            return (None, None)
        return (as_date(row["first_date"]), as_date(row["last_date"]))
