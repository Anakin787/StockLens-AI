"""The momentum-weighted concentrated DCA strategy."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.models import SOURCE_TOSS, PortfolioSnapshot, Position
from src.strategy.bars import Bar, PriceHistory
from src.strategy.base import DailyUsage, SIDE_BUY, SIDE_SELL, StrategyContext
from src.strategy.momentum_dca import MomentumDcaParams, MomentumDcaStrategy
from src.strategy.universe import Instrument, KIND_INDEX_ETF, KIND_STOCK, Universe

D = Decimal
START = date(2025, 1, 1)


def series(symbol, closes, start=START):
    bars = [
        Bar(date=start + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(D(str(c)) for c in closes)
    ]
    return PriceHistory(symbol, tuple(bars))


def trending(symbol, n, daily_return, start_price=100, start=START):
    """A smooth compounding series - a clean, controllable momentum signal."""
    rate = D(str(daily_return))
    price = D(str(start_price))
    closes = []
    for _ in range(n):
        closes.append(price)
        price = price * (D("1") + rate)
    return series(symbol, closes, start=start)


def flat_then_move(symbol, flat_n, flat_price, moves, start=START):
    """``flat_n`` bars at ``flat_price``, then one multiplier per day in ``moves``."""
    closes = [D(str(flat_price))] * flat_n
    price = D(str(flat_price))
    for move in moves:
        price = price * D(str(move))
        closes.append(price)
    return series(symbol, closes, start=start)


def position(symbol, quantity="1", currency="USD", price="100"):
    return Position(
        symbol=symbol,
        name=symbol,
        market_country="US",
        currency=currency,
        quantity=D(quantity),
        last_price=D(price),
        avg_purchase_price=D(price),
        source=SOURCE_TOSS,
    )


def context(now, history, positions=(), buying_power=None, recent=(), total_krw="10000000"):
    snapshot = PortfolioSnapshot(
        positions=list(positions),
        exchange_rate=D("1350"),
        total_krw=D(total_krw),
    )
    return StrategyContext(
        now=now,
        snapshot=snapshot,
        prices={},
        buying_power=buying_power or {"USD": D("2000")},
        sellable={},
        price_limits={},
        sessions={},
        daily_usage=DailyUsage(),
        kill_switch=False,
        history=history,
        recent=tuple(recent),
    )


#: Small windows so a 40-50 bar fixture is enough - production defaults
#: (252-day lookback etc.) would need years of synthetic data for no extra
#: coverage.
SMALL_PARAMS = MomentumDcaParams(
    lookbacks=((10, D("1")),),
    skip_days=0,
    vol_adjust=False,
    top_n=2,
    weights=(D("0.65"), D("0.35")),
    fallback_symbol="QQQ",
    benchmark="QQQ",
    trend_sma=20,
    vol_window=10,
    leverage_max_vol=D("1"),  # wide open unless a test narrows it
    rebalance_weekday=0,  # Monday
    dislocation_window=5,
    dislocation_cooldown_days=3,
    min_order_usd=D("10"),
    cash_reserve=D("0"),
    stale_days=5,
)

UNIVERSE = Universe(
    (
        Instrument(symbol="QQQ", name="QQQ", kind=KIND_INDEX_ETF, max_weight=D("0.9")),
        Instrument(symbol="AAA", name="AAA", kind=KIND_STOCK, max_weight=D("0.9")),
        Instrument(symbol="BBB", name="BBB", kind=KIND_STOCK, max_weight=D("0.9")),
        Instrument(
            symbol="QLD",
            name="QLD",
            kind=KIND_INDEX_ETF,
            leverage=D("2"),
            max_weight=D("0.5"),
        ),
    )
)


def strategy(**param_overrides):
    from dataclasses import replace

    params = replace(SMALL_PARAMS, **param_overrides) if param_overrides else SMALL_PARAMS
    return MomentumDcaStrategy(universe=UNIVERSE, params=params)


# ---------------------------------------------------------------- fixtures


def _base_history(n=40):
    """QQQ in a mild uptrend (trend filter passes), two candidates ranked."""
    return {
        "QQQ": trending("QQQ", n, "0.001", start_price=100),
        "AAA": trending("AAA", n, "0.02", start_price=50),  # strong momentum
        "BBB": trending("BBB", n, "0.005", start_price=50),  # weaker momentum
    }


# --------------------------------------------------------------- determinism


def test_same_context_twice_gives_identical_signals():
    history = _base_history()
    monday = START + timedelta(days=39)
    monday = monday - timedelta(days=monday.weekday())  # snap to a Monday
    now = datetime(monday.year, monday.month, monday.day, 9, 0)
    ctx = context(now, history)
    s = strategy()
    first = s.evaluate(ctx)
    second = s.evaluate(ctx)
    assert first == second
    assert len(first) > 0


# ------------------------------------------------------------------ ranking


def test_top_ranked_symbol_gets_the_larger_weight():
    history = _base_history()
    monday = START + timedelta(days=39)
    monday = monday - timedelta(days=monday.weekday())
    now = datetime(monday.year, monday.month, monday.day)
    signals = strategy().evaluate(context(now, history))
    by_symbol = {s.symbol: s for s in signals if s.side == SIDE_BUY}
    assert "AAA" in by_symbol and "BBB" in by_symbol
    assert by_symbol["AAA"].amount > by_symbol["BBB"].amount


# -------------------------------------------------------------- quiet days


def test_no_signal_on_a_non_rebalance_non_dislocation_day():
    history = _base_history()
    # A Tuesday, no crash - neither trigger fires.
    tuesday = START + timedelta(days=39)
    tuesday = tuesday - timedelta(days=tuesday.weekday()) + timedelta(days=1)
    now = datetime(tuesday.year, tuesday.month, tuesday.day)
    signals = strategy().evaluate(context(now, history))
    assert signals == []


def test_empty_history_produces_no_signals():
    now = datetime(2026, 1, 5)  # a Monday
    assert strategy().evaluate(context(now, {})) == []


# --------------------------------------------------------------- dislocation


def test_dislocation_fires_on_a_sharp_drop_inside_an_uptrend():
    # Uptrend base, then a sharp single-day drop that also produces a
    # >= 8%-from-20-day-high drawdown (the default threshold; here the params
    # use the default dislocation_drawdown=0.08 from MomentumDcaParams()).
    base = flat_then_move(
        "QQQ",
        flat_n=25,
        flat_price=100,
        moves=[1.01] * 10 + [0.95],  # steady rise, then a sharp one-day drop
    )
    history = {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }
    last_day = START + timedelta(days=len(base) - 1)
    # Pick a non-Monday so a "weekly" rebalance can't explain the signal.
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    signals = strategy(dislocation_drawdown=D("0.03")).evaluate(context(now, history))
    assert any(s.side == SIDE_BUY for s in signals)
    assert all(s.meta.get("mode") == "dislocation" for s in signals if s.side == SIDE_BUY)


def test_dislocation_does_not_fire_below_the_trend_sma():
    # Same sharp drop, but QQQ's overall level is now below its own SMA -
    # trend is down, and dislocation_requires_trend defaults to True.
    base = flat_then_move(
        "QQQ", flat_n=25, flat_price=100, moves=[0.99] * 10 + [0.95]
    )
    history = {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }
    last_day = START + timedelta(days=len(base) - 1)
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    signals = strategy(dislocation_drawdown=D("0.03")).evaluate(context(now, history))
    assert not any(s.meta.get("mode") == "dislocation" for s in signals if s.side == SIDE_BUY)


def test_cooldown_suppresses_a_second_dislocation_buy():
    base = flat_then_move(
        "QQQ", flat_n=25, flat_price=100, moves=[1.01] * 10 + [0.95]
    )
    history = {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }
    last_day = START + timedelta(days=len(base) - 1)
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    recent = [
        {
            "ts": (last_day - timedelta(days=1)).isoformat(),
            "strategy": "momentum-dca",
            "symbol": "AAA",
            "meta": {"mode": "dislocation"},
        }
    ]
    signals = strategy(dislocation_drawdown=D("0.03")).evaluate(
        context(now, history, recent=recent)
    )
    assert not any(s.meta.get("mode") == "dislocation" for s in signals if s.side == SIDE_BUY)


# ------------------------------------------------------------ leverage gate


def test_leveraged_instrument_excluded_from_ranking_when_trend_is_down():
    down = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[0.99] * 15)
    history = {
        "QQQ": down,
        "AAA": trending("AAA", 40, "0.02", start_price=50),
        "QLD": trending("QLD", 40, "0.05", start_price=50),
    }
    monday = START + timedelta(days=len(down) - 1)
    monday = monday - timedelta(days=monday.weekday())
    now = datetime(monday.year, monday.month, monday.day)
    signals = strategy().evaluate(context(now, history))
    assert "QLD" not in {s.symbol for s in signals if s.side == SIDE_BUY}


def test_holding_is_sold_when_trend_breaks():
    down = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[0.99] * 15)
    history = {"QQQ": down}
    monday = START + timedelta(days=len(down) - 1)
    monday = monday - timedelta(days=monday.weekday())
    now = datetime(monday.year, monday.month, monday.day)
    ctx = context(now, history, positions=[position("QLD", quantity="3")])
    signals = strategy().evaluate(ctx)
    sells = [s for s in signals if s.side == SIDE_SELL and s.symbol == "QLD"]
    assert len(sells) == 1
    assert sells[0].quantity == D("3")
    assert sells[0].meta["mode"] == "trend-exit"


def test_non_leveraged_holding_is_never_sold_on_a_trend_signal():
    down = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[0.99] * 15)
    history = {"QQQ": down}
    monday = START + timedelta(days=len(down) - 1)
    monday = monday - timedelta(days=monday.weekday())
    now = datetime(monday.year, monday.month, monday.day)
    ctx = context(now, history, positions=[position("AAA", quantity="3")])
    signals = strategy().evaluate(ctx)
    assert not any(s.symbol == "AAA" and s.side == SIDE_SELL for s in signals)


# --------------------------------------------------------------- sizing caps


def test_amount_never_exceeds_the_budget():
    history = _base_history()
    monday = START + timedelta(days=39)
    monday = monday - timedelta(days=monday.weekday())
    now = datetime(monday.year, monday.month, monday.day)
    buying_power = {"USD": D("100")}
    signals = strategy().evaluate(context(now, history, buying_power=buying_power))
    total = sum(s.amount for s in signals if s.side == SIDE_BUY)
    assert total <= D("100")


def test_amount_respects_the_instrument_max_weight():
    history = _base_history()
    monday = START + timedelta(days=39)
    monday = monday - timedelta(days=monday.weekday())
    now = datetime(monday.year, monday.month, monday.day)
    tight_universe = Universe(
        (
            Instrument(symbol="QQQ", name="QQQ", kind=KIND_INDEX_ETF, max_weight=D("0.9")),
            Instrument(symbol="AAA", name="AAA", kind=KIND_STOCK, max_weight=D("0.01")),
            Instrument(symbol="BBB", name="BBB", kind=KIND_STOCK, max_weight=D("0.9")),
        )
    )
    s = MomentumDcaStrategy(universe=tight_universe, params=SMALL_PARAMS)
    ctx = context(now, history, buying_power={"USD": D("2000")}, total_krw="10000000")
    signals = s.evaluate(ctx)
    aaa = [sig for sig in signals if sig.symbol == "AAA"]
    if aaa:
        # 1% of 10,000,000 = 100,000 KRW = ~74 USD at rate 1350.
        assert aaa[0].amount <= D("75")
