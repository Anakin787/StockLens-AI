"""The bar cache and the offline-capable loader built on top of it."""

from datetime import date
from decimal import Decimal

import pytest

from src.data.cache import BarCache
from src.data.errors import DataUnavailableError
from src.data.loader import HistoryLoader
from src.strategy.bars import Bar, PriceHistory


def bar(day, close, volume="1000"):
    close = Decimal(str(close))
    return Bar(
        date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=Decimal(volume),
    )


class FakeSource:
    """A source whose fetches are scripted, so tests never touch a network."""

    name = "fake"

    def __init__(self, series=None, calls=None):
        self.series = series or {}
        self.calls = calls if calls is not None else []

    def fetch(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        if symbol not in self.series:
            raise DataUnavailableError(f"{symbol}: no data")
        bars = [b for b in self.series[symbol] if start <= b.date <= end]
        if not bars:
            raise DataUnavailableError(f"{symbol}: empty range")
        return PriceHistory(symbol, tuple(bars))


class RaisingSource:
    """A source that must never be called - proves offline mode is honoured."""

    name = "raising"

    def fetch(self, symbol, start, end):
        raise AssertionError("offline=True must never call the source")


@pytest.fixture
def cache(tmp_path):
    return BarCache(str(tmp_path / "bars.sqlite3"))


def test_decimal_round_trips_exactly_through_text(cache):
    original = bar(date(2026, 1, 2), "123.456789")
    cache.upsert("QQQ", [original], source="test")
    restored = cache.bars("QQQ").last()
    assert restored.close == original.close
    assert isinstance(restored.close, Decimal)


def test_upsert_is_idempotent_per_symbol_and_date(cache):
    cache.upsert("QQQ", [bar(date(2026, 1, 2), "100")], source="test")
    cache.upsert("QQQ", [bar(date(2026, 1, 2), "999")], source="test-again")
    history = cache.bars("QQQ")
    assert len(history) == 1
    assert history.last().close == Decimal("999")


def test_coverage_widens_across_upserts(cache):
    cache.upsert("QQQ", [bar(date(2026, 1, 2), "100")], source="test")
    cache.upsert("QQQ", [bar(date(2026, 1, 5), "100")], source="test")
    first, last = cache.coverage("QQQ")
    assert (first, last) == (date(2026, 1, 2), date(2026, 1, 5))


def test_bars_filters_to_the_requested_range(cache):
    for day in range(1, 6):
        cache.upsert("QQQ", [bar(date(2026, 1, day), "100")], source="test")
    history = cache.bars("QQQ", start=date(2026, 1, 2), end=date(2026, 1, 4))
    assert history.dates == (date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4))


# ------------------------------------------------------------- HistoryLoader


def test_offline_never_calls_the_source(cache):
    cache.upsert("QQQ", [bar(date(2026, 1, 2), "100")], source="test")
    loader = HistoryLoader(cache, source=RaisingSource(), offline=True)
    # Would raise AssertionError if the source were touched.
    loader.load(["QQQ"], date(2026, 1, 1), date(2026, 1, 10))


def test_offline_gap_is_silently_skipped_not_filled(cache):
    cache.upsert("QQQ", [bar(date(2026, 1, 2), "100")], source="test")
    loader = HistoryLoader(cache, source=RaisingSource(), offline=True)
    result = loader.load(["QQQ"], date(2026, 1, 1), date(2026, 1, 10))
    assert "QQQ" not in result


def test_refresh_raises_without_a_configured_source(cache):
    loader = HistoryLoader(cache, source=None, offline=False)
    with pytest.raises(DataUnavailableError):
        loader.refresh(["QQQ"], date(2026, 1, 1), date(2026, 1, 10))


def test_refresh_fetches_missing_range_and_populates_the_cache(cache):
    series = {"QQQ": [bar(date(2026, 1, d), "100") for d in range(1, 11)]}
    source = FakeSource(series)
    loader = HistoryLoader(cache, source=source, staleness_days=0)
    added = loader.refresh(["QQQ"], date(2026, 1, 1), date(2026, 1, 10))
    assert added["QQQ"] == 10
    assert len(cache.bars("QQQ")) == 10


def test_refresh_only_fetches_the_gap_since_last_coverage(cache):
    cache.upsert("QQQ", [bar(date(2026, 1, 1), "100")], source="test")
    series = {"QQQ": [bar(date(2026, 1, d), "100") for d in range(1, 6)]}
    source = FakeSource(series)
    loader = HistoryLoader(cache, source=source, staleness_days=0)
    loader.refresh(["QQQ"], date(2026, 1, 1), date(2026, 1, 5))
    fetched_symbol, fetched_start, fetched_end = source.calls[0]
    assert fetched_start == date(2026, 1, 2)  # the day after existing coverage


def test_refresh_skips_a_symbol_that_is_fresh_enough(cache):
    cache.upsert("QQQ", [bar(date(2026, 1, 5), "100")], source="test")
    loader = HistoryLoader(cache, source=RaisingSource(), staleness_days=30)
    added = loader.refresh(["QQQ"], date(2026, 1, 1), date(2026, 1, 6))
    assert added["QQQ"] == 0


def test_a_symbol_the_source_cannot_fetch_is_absent_not_fatal(cache):
    loader = HistoryLoader(cache, source=FakeSource({}), staleness_days=0)
    added = loader.refresh(["NOPE"], date(2026, 1, 1), date(2026, 1, 10))
    assert added["NOPE"] == 0
    assert "NOPE" not in loader.load(["NOPE"], date(2026, 1, 1), date(2026, 1, 10))
