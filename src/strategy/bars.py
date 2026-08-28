"""Daily bars, and the window of them a strategy is allowed to see.

A strategy computes its indicators from these rather than fetching them, which
is what keeps :meth:`~src.strategy.base.Strategy.evaluate` pure. The types live
here in ``strategy/`` rather than in ``data/`` because the strategy contract
refers to them: the data layer can be replaced - yfinance today, something else
later - without the strategy layer noticing.

:class:`PriceHistory` holds a tuple, not a list. The context that carries it is
frozen, and a mutable list on a frozen context is a frozen context in name
only - a strategy could sort or truncate the backtest's own data and every
later bar would be quietly wrong.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from src.models import ZERO


class BarError(ValueError):
    """A bar could never have come from a real session."""


def as_date(value):
    """Coerce a date, datetime or ISO string to a plain ``date``.

    Callers hand in whichever of the three they happen to hold - the cache
    reads TEXT, the backtest loop carries dates, the live path carries aware
    datetimes - and comparing a date against a datetime raises rather than
    misbehaving, so they are matched here instead.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise BarError(f"날짜로 읽을 수 없는 값입니다: {value!r}")


@dataclass(frozen=True)
class Bar:
    """One completed session for one symbol.

    ``close`` is split- and dividend-adjusted; ``raw_close`` keeps what the
    session actually printed. Indicators read the adjusted series - an
    unadjusted 10:1 split looks exactly like a 90% crash to a momentum rank -
    while anything that has to resemble a real order price reads the raw one.
    """

    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
    raw_close: Decimal | None = None

    def __post_init__(self):
        object.__setattr__(self, "date", as_date(self.date))
        for label in ("open", "high", "low", "close"):
            value = getattr(self, label)
            if not isinstance(value, Decimal):
                raise BarError(f"{label}는 Decimal이어야 합니다: {value!r}")
            if value <= ZERO:
                raise BarError(f"{label}는 0보다 커야 합니다: {value}")
        if self.low > self.high:
            raise BarError(f"low({self.low})가 high({self.high})보다 큽니다.")

    @property
    def price(self):
        """The price to treat as this bar's close for order sizing."""
        return self.raw_close if self.raw_close is not None else self.close


@dataclass(frozen=True)
class PriceHistory:
    """A symbol's bars in ascending date order.

    Nothing here fills gaps. A missing session is a missing session, and a
    synthesised one would let an indicator return a number where it should
    have returned None.
    """

    symbol: str
    bars: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "bars", tuple(self.bars))
        previous = None
        for bar in self.bars:
            if previous is not None and bar.date <= previous:
                raise BarError(
                    f"{self.symbol}: 봉이 날짜 오름차순이 아닙니다 "
                    f"({previous} 다음에 {bar.date})."
                )
            previous = bar.date

    @classmethod
    def _prefix(cls, symbol, bars):
        """A history built from bars already known to be ordered.

        ``as_of`` slices a validated history, so re-walking the slice to
        check the ordering it inherited is pure cost - and it is paid once
        per symbol per session, which in a ten-year backtest over forty names
        is a hundred million date comparisons that can only ever pass.
        """
        history = object.__new__(cls)
        object.__setattr__(history, "symbol", symbol)
        object.__setattr__(history, "bars", bars)
        return history

    def __len__(self):
        return len(self.bars)

    def __bool__(self):
        return bool(self.bars)

    def __iter__(self):
        return iter(self.bars)

    @property
    def dates(self):
        """Bar dates, oldest first. Built once and kept.

        This is read on every ``as_of`` and every staleness check, so
        rebuilding the tuple each time made the cost of looking at a history
        proportional to its length - the backtest's single largest expense.
        The object is immutable, so the answer cannot go stale.
        """
        cached = getattr(self, "_dates", None)
        if cached is None:
            cached = tuple(bar.date for bar in self.bars)
            object.__setattr__(self, "_dates", cached)
        return cached

    def closes(self, n=None):
        """Adjusted closes, oldest first. ``n`` takes the most recent n."""
        values = getattr(self, "_closes", None)
        if values is None:
            values = tuple(bar.close for bar in self.bars)
            object.__setattr__(self, "_closes", values)
        if n is None:
            return values
        return values[-n:] if n > 0 else ()

    def last(self):
        return self.bars[-1] if self.bars else None

    @property
    def last_date(self):
        last = self.last()
        return last.date if last else None

    def as_of(self, day):
        """The history as it stood at the close of ``day``.

        This is the whole no-lookahead mechanism, in one method. The backtest
        calls it once per bar; nothing downstream can see past ``day`` because
        nothing downstream is given anything past ``day``.
        """
        cutoff = as_date(day)
        index = bisect_right(self.dates, cutoff)
        if index == len(self.bars):
            return self
        return PriceHistory._prefix(self.symbol, self.bars[:index])

    def is_stale(self, now, max_age_days=5):
        """True when the last bar is too old to trade on.

        A feed that silently stops updating is indistinguishable from a market
        that stopped moving, and only one of those is a reason to keep sizing
        orders off the last price.
        """
        last = self.last_date
        if last is None:
            return True
        return (as_date(now) - last).days > max_age_days
