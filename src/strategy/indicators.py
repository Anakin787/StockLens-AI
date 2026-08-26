"""Indicators, in Decimal, computed from bars the caller already holds.

Every function here returns ``None`` when the window it was asked for is longer
than the data it was given. That is deliberate and it is the most important
rule in this module: a 200-day average computed from 40 bars is not a rough
200-day average, it is a 40-day average wearing its name, and it will rank a
newly listed symbol at the top of a momentum table on its second week of
trading.

No floats. ``Decimal`` has ``ln`` and ``sqrt``, so even the volatility figure -
the one place a float round-trip would be easy to excuse - stays exact to the
context's precision.
"""

from decimal import Decimal, InvalidOperation

from src.models import ZERO

ONE = Decimal("1")

#: Trading days in a year, for annualising a daily volatility.
TRADING_DAYS = Decimal("252")


def _window(values, size):
    """The last ``size`` values, or None when there are not that many."""
    if size <= 0:
        return None
    values = tuple(values)
    if len(values) < size:
        return None
    return values[-size:]


def sma(values, window):
    """Simple moving average of the last ``window`` values."""
    chunk = _window(values, window)
    if chunk is None:
        return None
    return sum(chunk) / Decimal(len(chunk))


def total_return(values, lookback, skip=0):
    """Return over ``lookback`` bars, ending ``skip`` bars before the last.

    ``skip`` exists because the most recent week of a momentum window tends to
    reverse rather than continue, so the convention is to measure 12-1 months
    rather than 12. Skipping is not free - it also discards a genuine breakout -
    which is why it is a parameter and not baked in.
    """
    if lookback <= 0 or skip < 0:
        return None
    values = tuple(values)
    end = len(values) - skip
    start = end - lookback - 1
    if start < 0 or end <= 0:
        return None
    first, last = values[start], values[end - 1]
    if first <= ZERO:
        return None
    return (last - first) / first


def log_returns(values):
    """Natural log returns, oldest first. Skips any non-positive price."""
    values = tuple(values)
    out = []
    for previous, current in zip(values, values[1:]):
        if previous <= ZERO or current <= ZERO:
            return None
        try:
            out.append((current / previous).ln())
        except (InvalidOperation, ValueError):
            return None
    return tuple(out)


def realized_vol(values, window, annualize=True):
    """Standard deviation of log returns over ``window`` bars.

    Sample standard deviation (n-1), because the window is a sample of the
    return process rather than the whole of it.
    """
    chunk = _window(values, window + 1)
    if chunk is None:
        return None
    returns = log_returns(chunk)
    if not returns or len(returns) < 2:
        return None
    count = Decimal(len(returns))
    mean = sum(returns) / count
    variance = sum((r - mean) ** 2 for r in returns) / (count - ONE)
    if variance < ZERO:
        return None
    vol = variance.sqrt()
    return vol * TRADING_DAYS.sqrt() if annualize else vol


def atr(bars, window=14):
    """Average true range - the plain mean of true ranges, not Wilder's.

    Wilder's smoothing needs a seed and a recursion; the plain mean over the
    same window is within a hair of it for the use here (sizing a stop) and is
    a pure function of its window, which the recursion is not.
    """
    bars = tuple(bars)
    if window <= 0 or len(bars) < window + 1:
        return None
    ranges = []
    for previous, current in zip(bars[-(window + 1) : -1], bars[-window:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    if not ranges:
        return None
    return sum(ranges) / Decimal(len(ranges))


def drawdown_from_high(values, window):
    """How far the last value sits below the highest of the last ``window``.

    Returned as a positive fraction: 0.08 means 8% below the window's high.
    """
    chunk = _window(values, window)
    if chunk is None:
        return None
    peak = max(chunk)
    if peak <= ZERO:
        return None
    return (peak - chunk[-1]) / peak


def pct_change(values, periods=1):
    """Simple return over the last ``periods`` bars."""
    return total_return(values, periods, skip=0)


def distance_from(value, reference):
    """``value`` relative to ``reference``, as a fraction of ``reference``.

    Used for "how far above its 200-day is the index" - a boolean crossing
    throws away the difference between 0.2% above and 15% above, and the two
    are not the same signal.
    """
    if value is None or reference is None or reference <= ZERO:
        return None
    return (value - reference) / reference
