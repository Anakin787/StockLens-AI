"""Building a StrategyContext from the simulator instead of the network.

Field-for-field, this mirrors :func:`src.execution.context.build_context` -
the whole point of the backtest is that the strategy and the risk gate cannot
tell which builder produced their context.
"""

from datetime import datetime, time

from src.strategy.base import DailyUsage, MarketSession, StrategyContext

#: A daily-bar backtest has no real intraday clock, so ``now`` is pinned to a
#: fixed mid-session time on each simulated day - well inside regular hours
#: and well before the amount-order cutoff - rather than to the close. Pinning
#: it at the close would make every amount/fractional order fail the cutoff
#: check on every single day, which is not what the rule is for.
_SESSION_TIME = time(10, 0)
_SESSION_CLOSE = time(16, 0)


def build_backtest_context(day, sim, history, fx_rate, universe_symbols, recent=()):
    """Assemble the context a strategy and the risk gate both read, from `sim`.

    ``history`` is ``{symbol: PriceHistory}`` covering the *entire* backtest
    range - this function is where the no-lookahead cut happens, via
    ``PriceHistory.as_of(day)``. Nothing past ``day`` is ever handed to a
    caller.
    """
    now = datetime.combine(day, _SESSION_TIME)
    regular_close = datetime.combine(day, _SESSION_CLOSE)

    sliced_history = {}
    prices = {}
    for symbol in universe_symbols:
        full = history.get(symbol)
        if full is None:
            continue
        sliced = full.as_of(day)
        if not sliced:
            continue
        sliced_history[symbol] = sliced
        last = sliced.last()
        if last is not None and last.date == day:
            prices[symbol] = last.price

    sellable = {symbol: sim.held_quantity(symbol) for symbol in sim.lots}
    snapshot = sim.snapshot(
        {s: sliced_history[s].last().price for s in sliced_history if sliced_history[s]},
        fx_rate,
    )

    return StrategyContext(
        now=now,
        snapshot=snapshot,
        prices=prices,
        buying_power={"USD": sim.cash_usd},
        sellable=sellable,
        price_limits={},  # the US market has no daily band; strict=False skips this
        sessions={"US": MarketSession("US", is_open=True, regular_close=regular_close)},
        daily_usage=DailyUsage(
            order_count=sim.orders_today, notional_krw=sim.notional_today_krw
        ),
        kill_switch=False,
        history=sliced_history,
        recent=tuple(recent),
    )
