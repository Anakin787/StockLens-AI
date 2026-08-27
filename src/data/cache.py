"""Firestore cache for daily bars.

Kept as its own client helper rather than folded into
:class:`src.store.repo.Store` - historical bars are a concern the dashboard
and the report pipeline never touch, and growing ``Store`` for them would mix
two unrelated lifetimes into one class. It talks to the same Firestore
project, so one set of Firebase credentials still means one place all app
data lives.
"""

from datetime import date

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

#: Firestore caps a single batch at 500 writes.
_BATCH_SIZE = 500

from src.models import to_decimal
from src.strategy.bars import Bar, PriceHistory, as_date


def _text(value):
    return None if value is None else str(value)


def _bar_doc_id(symbol, bar_date):
    return f"{symbol}_{bar_date}"


def _doc_to_bar(doc):
    return Bar(
        date=doc.get("date"),
        open=to_decimal(doc.get("open")),
        high=to_decimal(doc.get("high")),
        low=to_decimal(doc.get("low")),
        close=to_decimal(doc.get("close")),
        raw_close=to_decimal(doc.get("raw_close")),
        volume=to_decimal(doc.get("volume")),
    )


class BarCache:
    """Reads and writes the ``daily_bars``/``bar_coverage`` collections."""

    def __init__(self, client=None):
        self.client = client or firestore.Client()

    def bars(self, symbol, start=None, end=None):
        """The cached PriceHistory for ``symbol``, filtered to ``[start, end]``."""
        query = self.client.collection("daily_bars").where(
            filter=FieldFilter("symbol", "==", symbol)
        )
        if start is not None:
            query = query.where(filter=FieldFilter("date", ">=", str(as_date(start))))
        if end is not None:
            query = query.where(filter=FieldFilter("date", "<=", str(as_date(end))))
        query = query.order_by("date")

        return PriceHistory(symbol, tuple(_doc_to_bar(doc) for doc in query.stream()))

    def upsert(self, symbol, bars, source, fetched_at=None):
        """Insert or replace bars for ``symbol``. Idempotent per (symbol, date)."""
        fetched_at = fetched_at or date.today().isoformat()
        bars = list(bars)
        if not bars:
            return 0

        collection = self.client.collection("daily_bars")
        for chunk_start in range(0, len(bars), _BATCH_SIZE):
            batch = self.client.batch()
            for bar in bars[chunk_start : chunk_start + _BATCH_SIZE]:
                batch.set(
                    collection.document(_bar_doc_id(symbol, bar.date)),
                    {
                        "symbol": symbol,
                        "date": str(bar.date),
                        "open": _text(bar.open),
                        "high": _text(bar.high),
                        "low": _text(bar.low),
                        "close": _text(bar.close),
                        "raw_close": _text(bar.raw_close),
                        "volume": _text(bar.volume),
                        "source": source,
                        "fetched_at": fetched_at,
                    },
                )
            batch.commit()

        dates = sorted(bar.date for bar in bars)
        first, last = str(dates[0]), str(dates[-1])
        coverage_ref = self.client.collection("bar_coverage").document(symbol)
        existing = coverage_ref.get()
        if existing.exists:
            first = min(first, existing.get("first_date"))
            last = max(last, existing.get("last_date"))
        coverage_ref.set(
            {
                "first_date": first,
                "last_date": last,
                "source": source,
                "fetched_at": fetched_at,
            }
        )
        return len(bars)

    def coverage(self, symbol):
        """``(first_date, last_date)`` as dates, or ``(None, None)`` if unseen."""
        doc = self.client.collection("bar_coverage").document(symbol).get()
        if not doc.exists or doc.get("first_date") is None:
            return (None, None)
        return (as_date(doc.get("first_date")), as_date(doc.get("last_date")))
