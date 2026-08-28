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


def _base_history(n=41):
    """QQQ in a mild uptrend (trend filter passes), two candidates ranked.

    Default ``n`` lands the last bar on a Monday (START is a Wednesday), so
    the strategy - which now takes its session date from the benchmark's last
    bar, not the wall clock - sees a weekly-rebalance day.
    """
    return {
        "QQQ": trending("QQQ", n, "0.001", start_price=100),
        "AAA": trending("AAA", n, "0.02", start_price=50),  # strong momentum
        "BBB": trending("BBB", n, "0.005", start_price=50),  # weaker momentum
    }


# --------------------------------------------------------------- determinism


def test_same_context_twice_gives_identical_signals():
    history = _base_history()
    last = history["QQQ"].last_date  # a Monday - the weekly rebalance fires
    now = datetime(last.year, last.month, last.day, 9, 0)
    ctx = context(now, history)
    s = strategy()
    first = s.evaluate(ctx)
    second = s.evaluate(ctx)
    assert first == second
    assert len(first) > 0


# ------------------------------------------------------------------ ranking


def test_top_ranked_symbol_gets_the_larger_weight():
    history = _base_history()
    last = history["QQQ"].last_date  # a Monday - the weekly rebalance fires
    now = datetime(last.year, last.month, last.day)
    signals = strategy().evaluate(context(now, history))
    by_symbol = {s.symbol: s for s in signals if s.side == SIDE_BUY}
    assert "AAA" in by_symbol and "BBB" in by_symbol
    assert by_symbol["AAA"].amount > by_symbol["BBB"].amount


# -------------------------------------------------------------- quiet days


def test_no_signal_on_a_non_rebalance_non_dislocation_day():
    # n=40 lands the last bar on a Sunday (non-rebalance), no crash - neither
    # trigger fires.
    history = _base_history(40)
    last = history["QQQ"].last_date
    assert last.weekday() != 0
    now = datetime(last.year, last.month, last.day)
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


def _dislocation_history():
    base = flat_then_move(
        "QQQ", flat_n=25, flat_price=100, moves=[1.01] * 10 + [0.95]
    )
    return base, {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }


def test_cooldown_suppresses_a_second_dislocation_buy():
    base, history = _dislocation_history()
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


def test_cooldown_reads_meta_from_the_live_payload_dict():
    # The live store writes signal.meta straight through as a dict under the
    # "payload" key (store.repo.save_decision); recent_signals() returns it
    # unchanged. The cooldown must recognise a dislocation buy recorded that
    # way, not just the backtest's "meta" key.
    base, history = _dislocation_history()
    last_day = START + timedelta(days=len(base) - 1)
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    recent = [
        {
            "ts": (last_day - timedelta(days=1)).isoformat(),
            "strategy": "momentum-dca",
            "symbol": "AAA",
            "payload": {"mode": "dislocation"},
            "outcome": "accepted",
        }
    ]
    signals = strategy(dislocation_drawdown=D("0.03")).evaluate(
        context(now, history, recent=recent)
    )
    assert not any(s.meta.get("mode") == "dislocation" for s in signals if s.side == SIDE_BUY)


# ------------------------------------------------------------ leverage gate


def test_leveraged_instrument_excluded_from_ranking_when_trend_is_down():
    # 16 moves -> 41 bars -> last bar is a Monday, so the weekly rebalance
    # fires and there are real buy signals to check QLD's absence in.
    down = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[0.99] * 16)
    history = {
        "QQQ": down,
        "AAA": trending("AAA", 41, "0.02", start_price=50),
        "QLD": trending("QLD", 41, "0.05", start_price=50),
    }
    last = down.last_date
    assert last.weekday() == 0
    now = datetime(last.year, last.month, last.day)
    signals = strategy().evaluate(context(now, history))
    assert any(s.side == SIDE_BUY for s in signals)
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


def test_trend_exit_is_not_repeated_while_the_prior_sell_is_in_flight():
    # A downtrend that lasts several sessions: _exit_signals re-derives from
    # scratch each run, and the position still shows the shares until the
    # earlier sell settles (T+1..T+2). It must not re-propose the sell.
    down = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[0.99] * 15)
    history = {"QQQ": down}
    last = down.last_date
    now = datetime(last.year, last.month, last.day)
    recent = [
        {
            "ts": (last - timedelta(days=1)).isoformat(),
            "strategy": "momentum-dca",
            "symbol": "QLD",
            "payload": {"mode": "trend-exit"},
            "outcome": "accepted",
        }
    ]
    ctx = context(now, history, positions=[position("QLD", quantity="3")], recent=recent)
    signals = strategy().evaluate(ctx)
    assert not any(s.side == SIDE_SELL and s.symbol == "QLD" for s in signals)


def test_trend_exit_resumes_once_the_exit_cooldown_has_passed():
    down = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[0.99] * 15)
    history = {"QQQ": down}
    last = down.last_date
    now = datetime(last.year, last.month, last.day)
    recent = [
        {
            "ts": (last - timedelta(days=9)).isoformat(),  # older than exit_cooldown_days
            "strategy": "momentum-dca",
            "symbol": "QLD",
            "payload": {"mode": "trend-exit"},
            "outcome": "accepted",
        }
    ]
    ctx = context(now, history, positions=[position("QLD", quantity="3")], recent=recent)
    signals = strategy().evaluate(ctx)
    sells = [s for s in signals if s.side == SIDE_SELL and s.symbol == "QLD"]
    assert len(sells) == 1


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
    last = history["QQQ"].last_date  # a Monday - the weekly rebalance fires
    now = datetime(last.year, last.month, last.day)
    buying_power = {"USD": D("100")}
    signals = strategy().evaluate(context(now, history, buying_power=buying_power))
    total = sum(s.amount for s in signals if s.side == SIDE_BUY)
    assert total <= D("100")


def test_amount_respects_the_instrument_max_weight():
    history = _base_history()
    last = history["QQQ"].last_date  # a Monday - the weekly rebalance fires
    now = datetime(last.year, last.month, last.day)
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


# ------------------------------------------------------- bug-fix regressions


def test_dislocation_never_spends_below_the_cash_reserve():
    # A large cash pile with no weekly deploy cap should still leave the
    # reserve untouched, even under the dislocation multiplier - the
    # multiplier must narrow the deployable figure, never widen past it.
    base = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[1.01] * 10 + [0.95])
    history = {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }
    last_day = START + timedelta(days=len(base) - 1)
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    buying_power = {"USD": D("10000")}
    s = strategy(
        dislocation_drawdown=D("0.03"),
        cash_reserve=D("0.20"),
        max_deploy_per_week_usd=None,
    )
    signals = s.evaluate(context(now, history, buying_power=buying_power))
    spent = sum(sig.amount for sig in signals if sig.side == SIDE_BUY)
    assert spent <= D("10000") * (D("1") - D("0.20"))


def test_max_deploy_cap_bounds_a_large_cash_pile():
    history = _base_history()
    last = history["QQQ"].last_date  # a Monday - the weekly rebalance fires
    now = datetime(last.year, last.month, last.day)
    s = strategy(max_deploy_per_week_usd=D("50"), cash_reserve=D("0"))
    signals = s.evaluate(
        context(now, history, buying_power={"USD": D("100000")})
    )
    spent = sum(sig.amount for sig in signals if sig.side == SIDE_BUY)
    assert spent <= D("50")


def test_fallback_does_not_bypass_the_absolute_momentum_gate():
    # Every candidate has negative momentum - a genuine downturn, exactly the
    # case min_score exists to keep the strategy out of. The fallback must
    # not silently deploy the whole budget into an equally unvetted QQQ.
    history = {
        "QQQ": trending("QQQ", 41, "-0.01", start_price=100),
        "AAA": trending("AAA", 41, "-0.02", start_price=50),
        "BBB": trending("BBB", 41, "-0.015", start_price=50),
    }
    last = history["QQQ"].last_date
    assert last.weekday() == 0  # a rebalance day - so a buy is only withheld by min_score
    now = datetime(last.year, last.month, last.day)
    signals = strategy().evaluate(context(now, history))
    assert not any(s.side == SIDE_BUY for s in signals)


def test_cooldown_fails_closed_when_the_recent_log_does_not_reach_back_far_enough():
    # ctx.recent has entries, but none of them are old enough to prove there
    # was no dislocation buy just outside the window - a truncated log (the
    # live path passes a fixed-size window) must not read as a clean cooldown.
    base = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[1.01] * 10 + [0.95])
    history = {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }
    last_day = START + timedelta(days=len(base) - 1)
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    # The only entry in the log is from today - it does not reach back the
    # full dislocation_cooldown_days=3, so "no matching entry" is unverifiable.
    recent = [
        {
            "ts": last_day.isoformat(),
            "strategy": "some-other-strategy",
            "symbol": "XYZ",
            "meta": {"mode": "weekly"},
        }
    ]
    signals = strategy(dislocation_drawdown=D("0.03")).evaluate(
        context(now, history, recent=recent)
    )
    assert not any(s.meta.get("mode") == "dislocation" for s in signals if s.side == SIDE_BUY)


def test_cooldown_allows_a_dislocation_buy_when_the_log_is_genuinely_empty():
    # No recent-signal history at all (e.g. day one) is not the same as a
    # truncated log - there is nothing to be uncertain about, so this must
    # not be blocked.
    base = flat_then_move("QQQ", flat_n=25, flat_price=100, moves=[1.01] * 10 + [0.95])
    history = {
        "QQQ": base,
        "AAA": trending("AAA", 36, "0.02", start_price=50),
        "BBB": trending("BBB", 36, "0.005", start_price=50),
    }
    last_day = START + timedelta(days=len(base) - 1)
    while last_day.weekday() == 0:
        last_day += timedelta(days=1)
    now = datetime(last_day.year, last_day.month, last_day.day)
    signals = strategy(dislocation_drawdown=D("0.03")).evaluate(
        context(now, history, recent=())
    )
    assert any(s.meta.get("mode") == "dislocation" for s in signals if s.side == SIDE_BUY)


def test_from_config_rejects_a_universe_whose_max_weight_exceeds_the_gate():
    from src.config import TradingConfig
    from src.toss.errors import TossConfigError

    trading_config = TradingConfig(
        universe=[{"symbol": "QQQ", "kind": "INDEX_ETF", "max_weight": 0.60}],
        limits={},  # default max_position_weight=0.20, no override for QQQ
    )
    with pytest.raises(TossConfigError):
        MomentumDcaStrategy.from_config(trading_config)


def test_from_config_accepts_a_universe_with_a_matching_override():
    from src.config import TradingConfig

    trading_config = TradingConfig(
        universe=[{"symbol": "QQQ", "kind": "INDEX_ETF", "max_weight": 0.60}],
        limits={"max_position_weight_overrides": {"QQQ": D("0.60")}},
    )
    s = MomentumDcaStrategy.from_config(trading_config)
    assert s.universe["QQQ"].max_weight == D("0.60")


# ------------------------------------------------- holidays on the rebalance day

#: US Labor Day 2026 - a Monday the exchange is shut, which is exactly the
#: case a plain weekday comparison drops in silence.
LABOR_DAY = date(2026, 9, 7)


def sessions_between(start, end, holidays=(LABOR_DAY,)):
    """Weekdays from start to end inclusive, minus the given holidays."""
    days, day = [], start
    while day <= end:
        if day.weekday() < 5 and day not in holidays:
            days.append(day)
        day += timedelta(days=1)
    return days


def on_sessions(symbol, days, daily_return, start_price=100):
    """A trending series that exists only on real trading days."""
    rate = D(str(daily_return))
    price = D(str(start_price))
    bars = []
    for day in days:
        bars.append(Bar(date=day, open=price, high=price, low=price, close=price))
        price = price * (D("1") + rate)
    return PriceHistory(symbol, tuple(bars))


def calendar_history(end):
    days = sessions_between(date(2026, 7, 1), end)
    return {
        "QQQ": on_sessions("QQQ", days, "0.001", start_price=100),
        "AAA": on_sessions("AAA", days, "0.02", start_price=50),
        "BBB": on_sessions("BBB", days, "0.005", start_price=50),
    }


def test_a_closed_rebalance_day_buys_on_the_next_session():
    """The week still gets its buy when the exchange is shut on its weekday.

    Comparing weekdays alone would skip this week entirely and say nothing
    about it - the run would simply produce no signals, which is what a quiet
    market looks like too.
    """
    history = calendar_history(date(2026, 9, 8))  # Tuesday after Labor Day
    last = history["QQQ"].last_date
    assert last == date(2026, 9, 8) and last.weekday() == 1

    signals = strategy().evaluate(context(datetime(2026, 9, 8, 9, 0), history))

    assert [s.symbol for s in signals if s.side == SIDE_BUY]


def test_the_make_up_session_happens_once():
    """Only the first session after the closed weekday counts.

    Otherwise every remaining day of that week reads as the rebalance day and
    the week buys three or four times.
    """
    history = calendar_history(date(2026, 9, 9))  # Wednesday
    assert history["QQQ"].last_date.weekday() == 2

    signals = strategy().evaluate(context(datetime(2026, 9, 9, 9, 0), history))

    assert [s for s in signals if s.side == SIDE_BUY] == []


def test_an_open_rebalance_day_is_unaffected():
    """The ordinary case still fires on the weekday itself, not a day later."""
    history = calendar_history(date(2026, 8, 31))  # a Monday, exchange open
    assert history["QQQ"].last_date == date(2026, 8, 31)

    signals = strategy().evaluate(context(datetime(2026, 8, 31, 9, 0), history))

    assert [s.symbol for s in signals if s.side == SIDE_BUY]


def test_the_day_after_an_open_rebalance_day_does_not_buy_again():
    history = calendar_history(date(2026, 9, 1))  # Tuesday, Monday was open

    signals = strategy().evaluate(context(datetime(2026, 9, 1, 9, 0), history))

    assert [s for s in signals if s.side == SIDE_BUY] == []


def test_the_safe_bucket_is_not_a_momentum_candidate():
    """Adding safe assets for a different strategy must not change this one.

    momentum-dca has no notion of holding something to a target weight, so a
    safe asset can only reach it as a ranked candidate - and ranking an asset
    held for how it behaves when equities fall would buy and sell it on
    exactly the wrong weeks. Before the universe grew a SAFE bucket there was
    nothing here to skip.
    """
    from src.strategy.universe import BUCKET_SAFE, KIND_INDEX_ETF

    universe = Universe(
        (
            Instrument("QQQ", "Bench", kind=KIND_INDEX_ETF, max_weight=D("0.9")),
            Instrument("HOT", "Hot", kind=KIND_STOCK, max_weight=D("0.9")),
            Instrument(
                "SHY", "Treasuries", kind=KIND_INDEX_ETF,
                bucket=BUCKET_SAFE, max_weight=D("0.9"),
            ),
        )
    )
    # SHY has the strongest trend of the three, so a strategy that ranked it
    # would put this week's money there.
    history = {
        "QQQ": trending("QQQ", 41, "0.001"),
        "HOT": trending("HOT", 41, "0.004"),
        "SHY": trending("SHY", 41, "0.02"),
    }
    strategy = MomentumDcaStrategy(universe=universe, params=SMALL_PARAMS)
    signals = strategy.evaluate(context(datetime(2025, 3, 3, 10), history))

    assert "SHY" not in {s.symbol for s in signals}
    assert signals, "the other names should still be bought"
