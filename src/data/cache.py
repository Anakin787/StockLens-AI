"""Local SQLite cache for daily bars.

Deliberately *not* in Firestore, unlike the rest of the persistence layer.
Bars are immutable reference data - one row per (symbol, session), never
revised - and a full universe backfill is ~50,000 writes, which is more than
a day of the Firestore free tier's write quota. Paying (in quota or in money)
to keep 16 years of OHLC in a cloud database buys nothing: no other machine
reads it, the dashboard never shows it, and ``--offline`` reproducibility is
better served by a file you can copy than by a collection you can only reach
online.

The live store (orders, signals, snapshots) stays in Firestore - that data is
small, mutable, and genuinely shared with the dashboard.

The file is git-ignored (``*.db``); delete it and the next ``--refresh``
rebuilds it from the source.
"""

import os
import sqlite3
from datetime import date
from pathlib import Path

from src.models import to_decimal
from src.strategy.bars import Bar, PriceHistory, as_date

#: Repository root - the cache sits next to the other local state.
_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "bars.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       TEXT,
    high       TEXT,
    low        TEXT,
    close      TEXT,
    raw_close  TEXT,
    volume     TEXT,
    source     TEXT,
    fetched_at TEXT,
    PRIMARY KEY (symbol, date)
);
"""


def _text(value):
    return None if value is None else str(value)


def _row_to_bar(row):
    return Bar(
        date=as_date(row["date"]),
        open=to_decimal(row["open"]),
        high=to_decimal(row["high"]),
        low=to_decimal(row["low"]),
        close=to_decimal(row["close"]),
        raw_close=to_decimal(row["raw_close"]),
        volume=to_decimal(row["volume"]),
    )


class BarCache:
    """Reads and writes the ``daily_bars`` table.

    ``path`` defaults to ``bars.db`` at the repository root, overridable with
    the ``M7_BAR_CACHE_PATH`` environment variable so a scheduled run and a
    manual backtest can share one file wherever it lives. Tests pass a
    ``tmp_path``.
    """

    def __init__(self, path=None):
        self.path = Path(path or os.getenv("M7_BAR_CACHE_PATH") or _DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

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
        query += " ORDER BY date"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return PriceHistory(symbol, tuple(_row_to_bar(row) for row in rows))

    def upsert(self, symbol, bars, source, fetched_at=None):
        """Insert or replace bars for ``symbol``. Idempotent per (symbol, date)."""
        fetched_at = fetched_at or date.today().isoformat()
        bars = list(bars)
        if not bars:
            return 0

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
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_bars "
                "(symbol, date, open, high, low, close, raw_close, volume, "
                " source, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        return len(bars)

    def coverage(self, symbol):
        """``(first_date, last_date)`` as dates, or ``(None, None)`` if unseen.

        Derived with MIN/MAX rather than kept in a side table: a stored range
        can drift from the rows it claims to describe, and this one cannot.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MIN(date) AS first, MAX(date) AS last "
                "FROM daily_bars WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None or row["first"] is None:
            return (None, None)
        return (as_date(row["first"]), as_date(row["last"]))
