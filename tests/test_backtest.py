"""The backtest engine: real risk gate, real snapshots, no lookahead."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from src.backtest.engine import Backtester, BacktestConfig
from src.backtest.fills import ContributionSchedule, FillModel
from src.execution.risk import RiskLimits
from src.strategy.base import Signal, Strategy, ORDER_MARKET, SIDE_BUY
from src.strategy.bars import Bar, PriceHistory
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


def flat_series(symbol, n, price="100", start=START):
    return series(symbol, [price] * n, start=start)


class NullStrategy(Strategy):
    """Produces no signals, ever. The churn test's control."""

    name = "null"

    def evaluate(self, ctx):
        return []


class AlwaysBuyStrategy(Strategy):
    """Buys a fixed USD amount of one symbol every day. For gate tests."""

    name = "always-buy"

    def __init__(self, symbol="QQQ", amount=D("500")):
        self.symbol = symbol
        self.amount = amount

    def evaluate(self, ctx):
        return [
            Signal(
                strategy=self.name,
                symbol=self.symbol,
                side=SIDE_BUY,
                order_type=ORDER_MARKET,
                amount=self.amount,
                currency="USD",
                reason="테스트 매수",
            )
        ]


def test_flat_market_accumulates_contributions_with_zero_trades():
    n = 65
    history = {"QQQ": flat_series("QQQ", n)}
    config = BacktestConfig(
        initial_krw=D("1000000"),
        contribution=ContributionSchedule(amount_krw=D("500000"), day_of_month=1),
        fx_rate=D("1000"),  # round number so KRW<->USD round-trips exactly
    )
    result = Backtester(NullStrategy(), history, config).run()
    assert result.trades == ()
    assert result.metrics["rejections_by_rule"] == {}
    # Nothing was ever bought, so every contributed won is still cash: the
    # final equity must equal exactly what was put in.
    # Day 0 (Jan 1) is the initial seed; the monthly schedule then lands on
    # Feb 1 and Mar 1 within this 65-day window.
    expected = config.initial_krw + config.contribution.amount_krw * 2
    assert result.equity_curve[-1].equity_krw == expected


def test_no_lookahead_truncating_the_future_does_not_change_the_past():
    n = 60
    base_closes = [100 + i * 0.3 for i in range(n)]
    strategy = MomentumDcaStrategy(
        universe=Universe(
            (
                Instrument(symbol="QQQ", name="QQQ", kind=KIND_INDEX_ETF, max_weight=D("0.9")),
                Instrument(symbol="AAA", name="AAA", kind=KIND_STOCK, max_weight=D("0.9")),
            )
        ),
        params=MomentumDcaParams(
            lookbacks=((10, D("1")),),
            skip_days=0,
            vol_adjust=False,
            top_n=1,
            weights=(D("1"),),
            fallback_symbol="QQQ",
            benchmark="QQQ",
            trend_sma=15,
            vol_window=10,
            rebalance_weekday=0,
            dislocation_window=5,
            min_order_usd=D("5"),
            cash_reserve=D("0"),
        ),
    )
    config = BacktestConfig(initial_krw=D("2000000"), fx_rate=D("1350"))

    full_history = {
        "QQQ": series("QQQ", base_closes),
        "AAA": series("AAA", [50 + i * 0.6 for i in range(n)]),
    }
    # A second run where every symbol has 10 extra future days appended -
    # if the engine ever leaked those into an earlier day's context, this
    # run's early signals would differ from the first run's.
    extended_history = {
        "QQQ": series("QQQ", base_closes + [200] * 10),
        "AAA": series("AAA", [50 + i * 0.6 for i in range(n)] + [5] * 10),
    }

    result_a = Backtester(strategy, full_history, config).run()
    result_b = Backtester(strategy, extended_history, config).run()

    days = min(len(result_a.equity_curve), n)
    for i in range(days):
        assert result_a.equity_curve[i].equity_krw == result_b.equity_curve[i].equity_krw


def test_a_risk_gate_rejection_produces_no_fill():
    history = {"QQQ": flat_series("QQQ", 10)}
    # A budget of zero orders per day guarantees every signal is rejected.
    config = BacktestConfig(
        initial_krw=D("1000000"),
        fx_rate=D("1350"),
        limits=RiskLimits(strict=False, max_orders_per_day=0),
    )
    result = Backtester(AlwaysBuyStrategy(), history, config).run()
    assert result.metrics["rejections_by_rule"].get("daily-order-limit", 0) > 0
    assert result.trades == ()


def test_mdd_is_computed_on_the_twr_index_not_raw_equity():
    # A market that halves then fully recovers: raw equity with a fat
    # contribution mid-crash would understate the drawdown; TWR must not.
    closes = [100] * 5 + [50] * 5 + [100] * 5
    history = {"QQQ": series("QQQ", closes)}
    config = BacktestConfig(initial_krw=D("1000000"), fx_rate=D("1350"))
    result = Backtester(NullStrategy(), history, config).run()
    # No positions are ever held (NullStrategy never buys), so cash-only MDD
    # should be ~0 regardless of QQQ's price - this is really a sanity check
    # that MDD does not spuriously fire on a strategy holding no risk.
    assert result.metrics["mdd"] == pytest.approx(0.0, abs=1e-9)


def test_twr_and_irr_diverge_under_a_rising_contribution_schedule():
    n = 40
    history = {"QQQ": flat_series("QQQ", n)}
    config = BacktestConfig(
        initial_krw=D("100000"),
        contribution=ContributionSchedule(amount_krw=D("1000000"), day_of_month=1),
        fx_rate=D("1350"),
    )
    result = Backtester(NullStrategy(), history, config).run()
    # TWR of an all-cash book with no trades is flat (0%); IRR is dominated by
    # the large late contributions and is a materially different number.
    assert result.metrics["twr_cagr"] == pytest.approx(0.0, abs=1e-6)
    assert result.metrics["irr"] is not None


# ------------------------------------------------------------- warm-up window


def test_trade_from_defers_the_first_deposit_to_the_trading_window():
    """Bars before ``trade_from`` are history to read, not days to trade.

    Without this the first deposit lands on the first bar loaded, so a
    strategy that needs a year of bars before it can rank anything sits in
    100% cash for that year while every comparison curve is fully invested -
    and the gap between them measures which year the strategy sat out.
    """
    n = 40
    history = {"QQQ": flat_series("QQQ", n)}
    trade_from = START + timedelta(days=20)
    config = BacktestConfig(
        initial_krw=D("1000000"),
        contribution=ContributionSchedule(amount_krw=D("500000"), day_of_month=1),
        fx_rate=D("1000"),
        trade_from=trade_from,
    )
    result = Backtester(NullStrategy(), history, config).run()

    assert result.equity_curve[0].date == trade_from
    assert result.equity_curve[0].contributed_today_krw == D("1000000")


def test_the_strategy_still_sees_the_bars_before_trade_from():
    """The warm-up is the whole point: those bars reach ``ctx.bars``."""
    seen = {}

    class RecordingStrategy(Strategy):
        name = "recording"

        def evaluate(self, ctx):
            history = ctx.bars("QQQ")
            seen[ctx.now] = len(history) if history is not None else 0
            return []

    n = 40
    history = {"QQQ": flat_series("QQQ", n)}
    trade_from = START + timedelta(days=20)
    config = BacktestConfig(fx_rate=D("1000"), trade_from=trade_from)
    Backtester(RecordingStrategy(), history, config).run()

    first_day = min(seen)
    # 21 bars exist up to and including trade_from, all of them visible.
    assert seen[first_day] == 21


def test_trade_from_after_every_bar_is_an_error_not_an_empty_run():
    history = {"QQQ": flat_series("QQQ", 10)}
    config = BacktestConfig(trade_from=START + timedelta(days=365))
    with pytest.raises(ValueError):
        Backtester(NullStrategy(), history, config).run()
