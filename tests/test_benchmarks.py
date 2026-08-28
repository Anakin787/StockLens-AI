"""The no-strategy comparison curves the backtest prints beside its own result."""

from datetime import date
from decimal import Decimal

from src.backtest.benchmarks import dca_curve, summarize_curve
from src.backtest.fills import ContributionSchedule
from src.strategy.bars import Bar, PriceHistory


def bar(day, close):
    close = Decimal(str(close))
    return Bar(date=day, open=close, high=close, low=close, close=close)


def history_of(**series):
    return {
        symbol: PriceHistory(symbol, tuple(bar(day, close) for day, close in bars))
        for symbol, bars in series.items()
    }


DAYS = [date(2026, 1, d) for d in (2, 5, 6)]
SCHEDULE = ContributionSchedule(amount_krw=Decimal("750000"), day_of_month=1)
FX = Decimal("1000")


def test_the_seed_buys_at_the_first_close_and_rides_the_price():
    history = history_of(AAA=[(DAYS[0], "100"), (DAYS[1], "200"), (DAYS[2], "200")])
    curve = dca_curve(history, ["AAA"], DAYS, SCHEDULE, Decimal("1000000"), FX)

    # 1,000,000원 / 1,000 = 1,000 USD at 100 = 10 shares.
    assert curve[0].equity_usd == Decimal("1000")
    assert curve[1].equity_usd == Decimal("2000")  # price doubled, no new cash
    assert curve[0].contributed_today_krw == Decimal("1000000")


def test_cash_splits_equally_and_skips_a_symbol_with_no_bar_that_day():
    history = history_of(
        AAA=[(DAYS[0], "100"), (DAYS[1], "100")],
        BBB=[(DAYS[1], "100")],  # not listed yet on day one
    )
    curve = dca_curve(history, ["AAA", "BBB"], DAYS[:2], SCHEDULE, Decimal("1000000"), FX)

    # Day one: all 1,000 USD into AAA alone. Day two adds nothing (same month).
    assert curve[0].equity_usd == Decimal("1000")
    assert curve[1].equity_usd == Decimal("1000")


def test_the_monthly_deposit_lands_once_per_month():
    days = [date(2026, 1, 2), date(2026, 1, 20), date(2026, 2, 3)]
    history = history_of(AAA=[(day, "100") for day in days])
    curve = dca_curve(history, ["AAA"], days, SCHEDULE, Decimal("1000000"), FX)

    deposits = [point.contributed_today_krw for point in curve]
    assert deposits == [Decimal("1000000"), Decimal("0"), Decimal("750000")]


def test_summarize_reports_twr_and_drawdown_not_raw_equity():
    days = [date(2026, 1, d) for d in (2, 5, 6, 7)]
    history = history_of(AAA=[(days[0], "100"), (days[1], "50"), (days[2], "50"), (days[3], "100")])
    stats = summarize_curve(
        dca_curve(history, ["AAA"], days, SCHEDULE, Decimal("1000000"), FX)
    )

    # MDD is reported as a positive magnitude, matching the strategy metrics.
    assert stats["mdd"] >= 0.5  # halved and recovered, on a flat deposit stream
    assert stats["final_equity_krw"] == Decimal("1000000")


def test_an_empty_curve_reports_nothing_rather_than_raising():
    assert summarize_curve([]) == {
        "twr_cagr": None,
        "mdd": None,
        "final_equity_krw": Decimal("0"),
    }


def test_dca_curve_accepts_an_fx_series_not_just_a_constant():
    """Both curves must price KRW deposits on the same rates.

    A constant here against a real series in the strategy run would put a
    decade of currency move on one side of the comparison only - the won went
    from ~1150 to ~1380 over the backtest span, and that lands in the reported
    return as if someone had earned it.
    """
    from datetime import date, timedelta
    from decimal import Decimal
    from src.backtest.benchmarks import dca_curve
    from src.backtest.fills import ContributionSchedule
    from src.strategy.bars import Bar, PriceHistory

    start = date(2026, 1, 1)
    days = [start + timedelta(days=i) for i in range(5)]
    bars = tuple(
        Bar(date=d, open=Decimal("100"), high=Decimal("100"),
            low=Decimal("100"), close=Decimal("100"))
        for d in days
    )
    history = {"QQQ": PriceHistory("QQQ", bars)}
    schedule = ContributionSchedule(amount_krw=Decimal("0"), day_of_month=1)

    flat = dca_curve(history, ["QQQ"], days, schedule, Decimal("1000000"), Decimal("1000"))
    # Same run, but the won weakens to 2000 by the last day.
    rates = {d: Decimal("1000") for d in days}
    rates[days[-1]] = Decimal("2000")
    varying = dca_curve(
        history, ["QQQ"], days, schedule, Decimal("1000000"), lambda d: rates[d]
    )

    assert flat[-1].equity_usd == varying[-1].equity_usd
    assert varying[-1].equity_krw == flat[-1].equity_krw * 2
