"""The bucket-targeted DCA strategy: shape, rotation, and what it never sells."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from src.models import SOURCE_TOSS, PortfolioSnapshot, Position
from src.strategy.bars import Bar, PriceHistory
from src.strategy.base import DailyUsage, SIDE_BUY, SIDE_SELL, StrategyContext
from src.strategy.bucket_dca import MODE_ROTATION, BucketDcaParams, BucketDcaStrategy
from src.strategy.universe import (
    BUCKET_CORE,
    BUCKET_GROWTH,
    BUCKET_SAFE,
    Instrument,
    KIND_INDEX_ETF,
    KIND_STOCK,
    Universe,
)
from src.toss.errors import TossConfigError

D = Decimal
START = date(2025, 1, 1)
MONDAY = date(2025, 3, 3)


def series(symbol, closes, start=START):
    bars = [
        Bar(date=start + timedelta(days=i), open=c, high=c, low=c, close=c)
        for i, c in enumerate(D(str(c)) for c in closes)
    ]
    return PriceHistory(symbol, tuple(bars))


def trending(symbol, n, daily_return, start_price=100, start=START):
    rate = D(str(daily_return))
    price = D(str(start_price))
    closes = []
    for _ in range(n):
        closes.append(price)
        price = price * (D("1") + rate)
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
        buying_power=buying_power or {"USD": D("5000")},
        sellable={},
        price_limits={},
        sessions={},
        daily_usage=DailyUsage(),
        kill_switch=False,
        history=history,
        recent=tuple(recent),
    )


UNIVERSE = Universe(
    (
        Instrument("SHY", "Treasuries", kind=KIND_INDEX_ETF, bucket=BUCKET_SAFE, max_weight=D("0.5")),
        Instrument("GLD", "Gold", kind=KIND_INDEX_ETF, bucket=BUCKET_SAFE, max_weight=D("0.5")),
        Instrument("AAA", "Core A", kind=KIND_STOCK, bucket=BUCKET_CORE, max_weight=D("0.5")),
        Instrument("BBB", "Core B", kind=KIND_STOCK, bucket=BUCKET_CORE, max_weight=D("0.5")),
        Instrument("CCC", "Core C", kind=KIND_STOCK, bucket=BUCKET_CORE, max_weight=D("0.5")),
        Instrument("DDD", "Core D", kind=KIND_STOCK, bucket=BUCKET_CORE, max_weight=D("0.5")),
        Instrument("GRW", "Growth", kind=KIND_STOCK, bucket=BUCKET_GROWTH, max_weight=D("0.5")),
        Instrument("QQQ", "Benchmark", kind=KIND_INDEX_ETF, bucket=BUCKET_CORE, max_weight=D("0.5")),
    )
)

SMALL = BucketDcaParams(
    bucket_weights=((BUCKET_SAFE, D("0.2")), (BUCKET_CORE, D("0.6")), (BUCKET_GROWTH, D("0.2"))),
    bucket_slots=((BUCKET_SAFE, 2), (BUCKET_CORE, 2), (BUCKET_GROWTH, 1)),
    rotation_buffer=1,
    lookbacks=((10, D("1")),),
    skip_days=0,
    vol_adjust=False,
    benchmark="QQQ",
    trend_sma=20,
    vol_window=10,
    leverage_max_vol=D("1"),
    rebalance_weekday=0,
    dislocation_enabled=False,
    min_order_usd=D("10"),
    cash_reserve=D("0"),
    stale_days=5,
)


def history_for(bars_by_symbol, n=41, start=START):
    """Every universe symbol gets a series; ranked ones get their own slope.

    ``n=41`` puts the last bar on a Monday (2025-02-10), which is the
    rebalance session - the strategy anchors every date to the benchmark's
    last bar, not to ``ctx.now``.
    """
    out = {}
    for instrument in UNIVERSE:
        symbol = instrument.symbol
        rate = bars_by_symbol.get(symbol, "0.001")
        out[symbol] = trending(symbol, n, rate, start=start)
    return out


def strategy(params=None):
    return BucketDcaStrategy(universe=UNIVERSE, params=params or SMALL)


# ------------------------------------------------------------------- params


def test_bucket_weights_that_do_not_sum_to_one_are_refused():
    with pytest.raises(Exception) as exc:
        BucketDcaParams(
            bucket_weights=(
                (BUCKET_SAFE, D("0.2")),
                (BUCKET_CORE, D("0.5")),
                (BUCKET_GROWTH, D("0.2")),
            )
        )
    assert "bucket_weights" in str(exc.value)


def test_a_bucket_with_weight_but_no_slots_is_refused():
    with pytest.raises(Exception):
        BucketDcaParams(
            bucket_slots=((BUCKET_SAFE, 0), (BUCKET_CORE, 6), (BUCKET_GROWTH, 2))
        )


def test_an_unknown_bucket_in_config_is_an_error_not_a_shrug():
    with pytest.raises(TossConfigError):
        BucketDcaParams.from_mapping({"bucket_weights": {"MOONSHOT": 1}})


# ------------------------------------------------------------------ shaping


def test_each_bucket_is_filled_toward_its_own_target_weight():
    history = history_for({"AAA": "0.02", "BBB": "0.015", "GRW": "0.01"})
    ctx = context(datetime(2025, 3, 3, 10), history)
    signals = strategy().evaluate(ctx)

    buys = [s for s in signals if s.side == SIDE_BUY]
    buckets = {s.meta["bucket"] for s in buys}
    assert buckets == {BUCKET_SAFE, BUCKET_CORE, BUCKET_GROWTH}


def test_the_safe_bucket_is_bought_even_though_it_never_ranks():
    """It is held for the weeks momentum is wrong, so momentum must not pick it."""
    history = history_for({"SHY": "0.0", "GLD": "-0.001", "AAA": "0.03"})
    ctx = context(datetime(2025, 3, 3, 10), history)
    buys = [s for s in strategy().evaluate(ctx) if s.side == SIDE_BUY]

    assert {"SHY", "GLD"} <= {s.symbol for s in buys}


def test_a_holding_already_at_its_target_is_not_topped_up():
    history = history_for({"AAA": "0.03"})
    # AAA at 3,000,000 KRW of a 10,000,000 KRW account is 30%, past its
    # CORE share (60% / 2 slots = 30%).
    held = position("AAA", quantity="2222", price="1")
    ctx = context(datetime(2025, 3, 3, 10), history, positions=[held])
    buys = [s for s in strategy().evaluate(ctx) if s.side == SIDE_BUY]

    assert "AAA" not in {s.symbol for s in buys}


# ----------------------------------------------------------------- rotation


def test_a_holding_that_falls_clear_of_its_slots_is_sold():
    history = history_for({"AAA": "0.03", "BBB": "0.02", "CCC": "0.01", "DDD": "-0.02"})
    held = position("DDD", quantity="10")
    ctx = context(datetime(2025, 3, 3, 10), history, positions=[held])

    sells = [s for s in strategy().evaluate(ctx) if s.side == SIDE_SELL]

    assert [s.symbol for s in sells] == ["DDD"]
    assert sells[0].meta["mode"] == MODE_ROTATION
    assert sells[0].quantity == D("10")


def test_a_holding_inside_the_buffer_is_left_alone():
    """Ranks near the cut trade places constantly.

    Selling on the first crossing would realise a gain, pay tax on it, and
    quite likely buy the same name back a week later.
    """
    history = history_for({"AAA": "0.03", "BBB": "0.02", "CCC": "0.015", "DDD": "-0.02"})
    # CCC is rank 3 with 2 slots + buffer 1 = keep the top 3.
    held = position("CCC", quantity="10")
    ctx = context(datetime(2025, 3, 3, 10), history, positions=[held])

    sells = [s for s in strategy().evaluate(ctx) if s.side == SIDE_SELL]
    assert "CCC" not in {s.symbol for s in sells}


def test_the_safe_bucket_is_never_rotated_out():
    """The weeks it looks worst are the weeks it is doing its job."""
    history = history_for({"SHY": "-0.005", "GLD": "-0.005", "AAA": "0.03", "BBB": "0.02"})
    ctx = context(
        datetime(2025, 3, 3, 10),
        history,
        positions=[position("SHY", quantity="10"), position("GLD", quantity="10")],
    )
    sells = [s for s in strategy().evaluate(ctx) if s.side == SIDE_SELL]

    assert not {"SHY", "GLD"} & {s.symbol for s in sells}


def test_a_rotation_sell_is_not_repeated_while_it_may_still_be_settling():
    """US equities settle T+1..T+2 and the position reads as sellable meanwhile."""
    history = history_for({"AAA": "0.03", "BBB": "0.02", "CCC": "0.01", "DDD": "-0.02"})
    recent = [
        {
            "ts": date(2025, 3, 2).isoformat(),
            "strategy": "bucket-dca",
            "symbol": "DDD",
            "meta": {"mode": MODE_ROTATION},
            "outcome": "accepted",
        }
    ]
    ctx = context(
        datetime(2025, 3, 3, 10),
        history,
        positions=[position("DDD", quantity="10")],
        recent=recent,
    )
    sells = [s for s in strategy().evaluate(ctx) if s.side == SIDE_SELL]
    assert "DDD" not in {s.symbol for s in sells}


def test_a_rejected_rotation_sell_is_retried():
    """A sell the gate refused did not happen; not retrying would strand it."""
    history = history_for({"AAA": "0.03", "BBB": "0.02", "CCC": "0.01", "DDD": "-0.02"})
    recent = [
        {
            "ts": date(2025, 3, 2).isoformat(),
            "strategy": "bucket-dca",
            "symbol": "DDD",
            "meta": {"mode": MODE_ROTATION},
            "outcome": "rejected",
        }
    ]
    ctx = context(
        datetime(2025, 3, 3, 10),
        history,
        positions=[position("DDD", quantity="10")],
        recent=recent,
    )
    sells = [s for s in strategy().evaluate(ctx) if s.side == SIDE_SELL]
    assert "DDD" in {s.symbol for s in sells}


# --------------------------------------------------------- unfilled buckets


def test_an_unfillable_bucket_hands_its_weight_over_rather_than_holding_cash():
    """GROWTH is short by design; its weight must not sit idle."""
    params = BucketDcaParams(
        **{
            **{f.name: getattr(SMALL, f.name) for f in SMALL.__dataclass_fields__.values()},
            "bucket_slots": ((BUCKET_SAFE, 2), (BUCKET_CORE, 2), (BUCKET_GROWTH, 1)),
        }
    )
    # GRW trends down, so it fails min_score and cannot fill its slot.
    history = history_for({"GRW": "-0.02", "AAA": "0.03", "BBB": "0.02"})
    ctx = context(datetime(2025, 3, 3, 10), history)
    buys = [s for s in strategy(params).evaluate(ctx) if s.side == SIDE_BUY]

    assert "GRW" not in {s.symbol for s in buys}
    core = [s for s in buys if s.meta["bucket"] == BUCKET_CORE]
    safe = [s for s in buys if s.meta["bucket"] == BUCKET_SAFE]
    core_usd = sum((s.amount for s in core), D("0"))
    safe_usd = sum((s.amount for s in safe), D("0"))
    # CORE now carries 0.6 + 0.2 = 0.8 against SAFE's 0.2.
    assert core_usd > safe_usd * 3
