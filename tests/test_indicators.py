"""Bars, the no-lookahead slice, and the indicators computed from them."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from src.strategy.bars import Bar, BarError, PriceHistory, as_date
from src.strategy.indicators import (
    atr,
    distance_from,
    drawdown_from_high,
    log_returns,
    pct_change,
    realized_vol,
    sma,
    total_return,
)


def bar(day, close, **overrides):
    close = Decimal(str(close))
    base = dict(
        date=date(2026, 1, day) if isinstance(day, int) else day,
        open=close,
        high=close,
        low=close,
        close=close,
    )
    base.update({k: Decimal(str(v)) for k, v in overrides.items()})
    return Bar(**base)


def history(closes, symbol="QQQ", start=date(2026, 1, 1)):
    bars = []
    for offset, close in enumerate(closes):
        bars.append(bar(date.fromordinal(start.toordinal() + offset), close))
    return PriceHistory(symbol, tuple(bars))


def D(value):
    return Decimal(str(value))


# --- bars ------------------------------------------------------------------


def test_as_date_accepts_the_three_shapes_callers_hold():
    assert as_date(date(2026, 1, 2)) == date(2026, 1, 2)
    assert as_date(datetime(2026, 1, 2, 15, 30)) == date(2026, 1, 2)
    assert as_date("2026-01-02T00:00:00+09:00") == date(2026, 1, 2)


def test_bar_rejects_a_float_price():
    with pytest.raises(BarError):
        Bar(date=date(2026, 1, 1), open=1.0, high=1.0, low=1.0, close=1.0)


def test_bar_rejects_low_above_high():
    with pytest.raises(BarError):
        Bar(
            date=date(2026, 1, 1),
            open=D(10),
            high=D(9),
            low=D(11),
            close=D(10),
        )


def test_history_rejects_out_of_order_bars():
    with pytest.raises(BarError):
        PriceHistory("QQQ", (bar(2, 10), bar(1, 10)))


def test_price_falls_back_to_adjusted_close_when_raw_is_absent():
    assert bar(1, 10).price == D(10)
    assert bar(1, 10, raw_close=D(100)).price == D(100)


def test_as_of_truncates_and_never_lengthens():
    series = history([1, 2, 3, 4, 5])
    assert len(series.as_of(date(2026, 1, 3))) == 3
    assert series.as_of(date(2026, 1, 3)).closes() == (D(1), D(2), D(3))
    # A cutoff past the end is the same history, not an error.
    assert series.as_of(date(2027, 1, 1)) is series
    assert len(series.as_of(date(2025, 1, 1))) == 0


def test_is_stale_when_the_feed_stopped_updating():
    series = history([1, 2, 3])
    assert not series.is_stale(date(2026, 1, 5), max_age_days=5)
    assert series.is_stale(date(2026, 1, 20), max_age_days=5)
    assert PriceHistory("QQQ").is_stale(date(2026, 1, 3))


# --- the None-on-short-window rule ----------------------------------------


@pytest.mark.parametrize(
    "call",
    [
        lambda closes: sma(closes, 10),
        lambda closes: total_return(closes, 10),
        lambda closes: realized_vol(closes, 10),
        lambda closes: drawdown_from_high(closes, 10),
    ],
)
def test_short_window_returns_none_not_a_partial_answer(call):
    assert call([D(1), D(2), D(3)]) is None


def test_atr_returns_none_on_a_short_window():
    assert atr([bar(1, 10), bar(2, 10)], window=14) is None


# --- values ----------------------------------------------------------------


def test_sma_is_the_mean_of_the_last_window():
    assert sma([D(1), D(2), D(3), D(10)], 3) == D(5)


def test_total_return_measures_first_to_last_of_the_window():
    # 5 bars back from the end: 100 -> 110 is +10%.
    closes = [D(100), D(101), D(102), D(103), D(104), D(110)]
    assert total_return(closes, 5) == D("0.1")


def test_total_return_skip_drops_the_most_recent_bars():
    closes = [D(100), D(110), D(200)]
    # Without skip the window ends at 200; with skip=1 it ends at 110.
    assert total_return(closes, 1, skip=1) == D("0.1")


def test_total_return_is_none_when_the_window_predates_the_series():
    assert total_return([D(1), D(2)], 5) is None


def test_pct_change_is_the_one_bar_return():
    assert pct_change([D(100), D(97)]) == D("-0.03")


def test_log_returns_refuses_a_non_positive_price():
    assert log_returns([D(10), D(0)]) is None


def test_realized_vol_of_a_flat_series_is_zero():
    assert realized_vol([D(100)] * 30, 20) == D(0)


def test_realized_vol_annualises_by_root_252():
    closes = [D(100)] * 30
    daily = realized_vol(closes, 20, annualize=False)
    annual = realized_vol(closes, 20, annualize=True)
    assert daily == D(0) and annual == D(0)
    # A moving series scales by exactly sqrt(252).
    alternating = []
    price = D(100)
    for step in range(40):
        price = price * (D("1.01") if step % 2 else D("0.99"))
        alternating.append(price)
    daily = realized_vol(alternating, 20, annualize=False)
    annual = realized_vol(alternating, 20, annualize=True)
    assert annual == daily * Decimal(252).sqrt()


def test_drawdown_from_high_is_a_positive_fraction():
    assert drawdown_from_high([D(100), D(120), D(110)], 3) == (D(120) - D(110)) / D(120)
    assert drawdown_from_high([D(100), D(110), D(110)], 3) == D(0)


def test_atr_averages_true_range_including_gaps():
    bars = [
        bar(1, 100, high=100, low=100),
        bar(2, 90, high=95, low=90),  # gap down: |90-100| = 10
    ]
    assert atr(bars, window=1) == D(10)  # max(95-90, |95-100|, |90-100|) = 10


def test_distance_from_keeps_the_size_of_the_gap():
    assert distance_from(D(110), D(100)) == D("0.1")
    assert distance_from(D(110), None) is None
    assert distance_from(None, D(100)) is None


# --- the float-leak canary -------------------------------------------------


def test_every_indicator_returns_decimal():
    closes = [D(100) + D(i) for i in range(300)]
    bars = [bar(date.fromordinal(date(2026, 1, 1).toordinal() + i), c) for i, c in enumerate(closes)]
    values = [
        sma(closes, 200),
        total_return(closes, 252, skip=5),
        realized_vol(closes, 63),
        drawdown_from_high(closes, 20),
        pct_change(closes),
        atr(bars, 14),
        distance_from(closes[-1], sma(closes, 200)),
    ]
    assert all(isinstance(v, Decimal) for v in values), values
