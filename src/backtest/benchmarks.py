"""What the same money would have done with no strategy at all.

A backtest that reports only its own CAGR answers the wrong question. The 39
names this strategy ranks were picked in 2026, knowing which of them worked;
any ranking over that list looks brilliant. The question that survives the
bias is comparative: *given the same universe and the same deposits, does the
ranking beat simply buying all of it?* That is what these curves are for, and
on 2026-08-27 they were the numbers that changed the conclusion.

Deliberately simple - equal weight, buy at the close, no fills model, no
commission. A benchmark exists to be beaten by a wide margin or not at all;
modelling its frictions to a basis point would only make the bar easier.
"""

from decimal import Decimal

from src.backtest.metrics import EquityPoint, cagr_from_twr, mdd_from_twr, twr_index
from src.models import ZERO


def dca_curve(history, symbols, dates, contribution, initial_krw, fx_rate):
    """Equity curve of an equal-weight DCA into ``symbols`` over ``dates``.

    Contributions follow the same schedule the strategy run used, so the two
    curves are comparable point for point. A symbol with no bar on a given day
    (a later listing, a data gap) simply does not take part in that day's
    split - it is not held, and it is not missed.

    ``fx_rate`` is either one Decimal for the whole span or a callable
    ``day -> Decimal``. It must be the *same* rate source the strategy run
    used: a constant here against a real series there would show up as a
    currency return on one curve only, and the comparison is the only thing
    these curves exist for.
    """
    rate_of = fx_rate if callable(fx_rate) else (lambda _day: fx_rate)
    closes = {
        symbol: {bar.date: bar.close for bar in history[symbol].bars}
        for symbol in symbols
        if symbol in history
    }
    shares = {symbol: ZERO for symbol in closes}
    curve = []
    contributed_month = None

    for i, day in enumerate(dates):
        fx = rate_of(day)
        cash_krw = ZERO
        month_key = (day.year, day.month)
        if i == 0:
            cash_krw = initial_krw
            contributed_month = month_key
        elif contribution.is_due(day, contributed_month == month_key):
            cash_krw = contribution.amount_krw
            contributed_month = month_key

        tradable = [symbol for symbol, series in closes.items() if day in series]
        if cash_krw and tradable:
            per_symbol_usd = (cash_krw / fx) / Decimal(len(tradable))
            for symbol in tradable:
                shares[symbol] += per_symbol_usd / closes[symbol][day]

        equity_usd = sum(
            (shares[symbol] * closes[symbol][day] for symbol in tradable), ZERO
        )
        curve.append(
            EquityPoint(
                date=day,
                equity_krw=equity_usd * fx,
                equity_usd=equity_usd,
                contributed_today_krw=cash_krw,
            )
        )
    return curve


def summarize_curve(curve):
    """``{twr_cagr, mdd, final_equity_krw}`` for a benchmark curve.

    The same TWR-based figures :mod:`src.backtest.metrics` reports for a
    strategy run - contributions removed from the return, drawdown measured on
    the TWR index rather than raw equity, so a rising deposit schedule cannot
    flatter either side of the comparison.
    """
    if not curve:
        return {"twr_cagr": None, "mdd": None, "final_equity_krw": ZERO}
    index = twr_index(curve)
    days = (curve[-1].date - curve[0].date).days
    return {
        "twr_cagr": cagr_from_twr(index, days),
        "mdd": mdd_from_twr(index),
        "final_equity_krw": curve[-1].equity_krw,
    }
