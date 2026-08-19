"""KRW conversion, FX handling and duplicate merging."""

from decimal import Decimal

from src.models import SOURCE_MANUAL, SOURCE_TOSS, Position
from src.portfolio import HoldingsAggregator, _merge_duplicates, _summarise

RATE = Decimal("1342.5")


def krw_position(**overrides):
    base = dict(
        symbol="005930",
        name="삼성전자",
        market_country="KR",
        currency="KRW",
        quantity=Decimal("100"),
        last_price=Decimal("72000"),
        avg_purchase_price=Decimal("65000"),
        source=SOURCE_TOSS,
        market_value=Decimal("7200000"),
        purchase_amount=Decimal("6500000"),
    )
    base.update(overrides)
    return Position(**base)


def usd_position(**overrides):
    base = dict(
        symbol="AAPL",
        name="Apple",
        market_country="US",
        currency="USD",
        quantity=Decimal("10"),
        last_price=Decimal("178.5"),
        avg_purchase_price=Decimal("155.3"),
        source=SOURCE_MANUAL,
    )
    base.update(overrides)
    return Position(**base)


def test_krw_only_portfolio():
    snapshot = _summarise([krw_position()], RATE)

    assert snapshot.total_krw == Decimal("7200000")
    assert snapshot.purchase_krw == Decimal("6500000")
    assert snapshot.profit_krw == Decimal("700000")
    assert not snapshot.has_unconverted_fx


def test_usd_converts_at_current_rate():
    snapshot = _summarise([usd_position()], RATE)

    # 10 * 178.5 = 1785 USD
    assert snapshot.total_krw == Decimal("1785") * RATE


def test_purchase_rate_makes_fx_gain_visible():
    """With avg_exchange_rate the cost side converts at the purchase-time
    rate, so a weaker won shows up as extra return."""
    with_rate = _summarise([usd_position(avg_exchange_rate=Decimal("1300"))], RATE)
    without_rate = _summarise([usd_position()], RATE)

    assert with_rate.purchase_krw == Decimal("1553") * Decimal("1300")
    assert without_rate.purchase_krw == Decimal("1553") * RATE
    # The won weakened from 1300 to 1342.5, so FX adds to the return.
    assert with_rate.profit_rate > without_rate.profit_rate


def test_missing_purchase_rate_raises_the_badge():
    assert _summarise([usd_position()], RATE).has_unconverted_fx
    assert not _summarise(
        [usd_position(avg_exchange_rate=Decimal("1300"))], RATE
    ).has_unconverted_fx


def test_krw_positions_never_raise_the_fx_badge():
    assert not _summarise([krw_position()], RATE).has_unconverted_fx


def test_after_cost_totals_only_when_reported():
    plain = _summarise([krw_position()], RATE)
    assert plain.profit_rate_after_cost is None

    with_cost = _summarise(
        [krw_position(profit_loss_after_cost=Decimal("550000"))], RATE
    )
    assert with_cost.profit_after_cost_krw == Decimal("550000")


def test_daily_change_rate_uses_yesterdays_base():
    snapshot = _summarise([krw_position(daily_profit_loss=Decimal("100000"))], RATE)

    assert snapshot.daily_profit_krw == Decimal("100000")
    # 100000 / (7200000 - 100000)
    assert snapshot.daily_profit_rate == Decimal("100000") / Decimal("7100000")


def test_same_symbol_across_sources_merges_with_weighted_average():
    toss = krw_position(quantity=Decimal("100"), avg_purchase_price=Decimal("65000"))
    manual = krw_position(
        quantity=Decimal("50"),
        avg_purchase_price=Decimal("71000"),
        source=SOURCE_MANUAL,
        market_value=None,
        purchase_amount=None,
    )

    merged = _merge_duplicates([toss, manual])

    assert len(merged) == 1
    assert merged[0].quantity == Decimal("150")
    # (100*65000 + 50*71000) / 150
    assert merged[0].avg_purchase_price == Decimal("67000")
    assert merged[0].source == "toss+manual"


def test_different_symbols_are_not_merged():
    assert len(_merge_duplicates([krw_position(), usd_position()])) == 2


def test_decimal_precision_survives_the_pipeline():
    """Exactly the drift the float-based v1 implementation accumulated."""
    positions = [
        krw_position(
            symbol=f"00{i}",
            quantity=Decimal("1"),
            last_price=Decimal("0.01"),
            avg_purchase_price=Decimal("0.01"),
            market_value=Decimal("0.01"),
            purchase_amount=Decimal("0.01"),
        )
        for i in range(10)
    ]
    assert _summarise(positions, RATE).total_krw == Decimal("0.10")


def test_empty_portfolio_does_not_divide_by_zero():
    snapshot = _summarise([], RATE)

    assert snapshot.total_krw == 0
    assert snapshot.profit_rate == 0
    assert snapshot.daily_profit_rate == 0


class FakeMarket:
    def __init__(self, rate="1342.5"):
        self.rate = rate

    def exchange_rate(self, base="USD", quote="KRW"):
        return {"rate": self.rate}


class FakeSource:
    def __init__(self, positions):
        self._positions = positions

    def fetch(self):
        return self._positions


def test_aggregator_combines_sources():
    aggregator = HoldingsAggregator(
        sources=[FakeSource([krw_position()]), FakeSource([usd_position()])],
        market_api=FakeMarket(),
    )
    snapshot = aggregator.build()

    assert len(snapshot.positions) == 2
    assert snapshot.exchange_rate == RATE
    assert set(snapshot.by_currency) == {"KRW", "USD"}


def test_allocation_groups_by_market_in_krw():
    aggregator = HoldingsAggregator(
        sources=[FakeSource([krw_position(), usd_position()])],
        market_api=FakeMarket(),
    )
    allocation = aggregator.build().allocation("market")

    assert allocation["KR"] == Decimal("7200000")
    assert allocation["US"] == Decimal("1785") * RATE


class FakeMarketApi:
    def __init__(self, prices=None, stocks=None):
        self._prices = prices or {}
        self._stocks = stocks or {}

    def prices(self, symbols):
        return self._prices

    def stocks(self, symbols):
        return self._stocks


def test_manual_positions_get_a_profit_rate():
    """Manual holdings must show a return like Toss holdings do, not a dash."""
    from src.config import ManualHolding
    from src.sources.manual_source import ManualSource

    source = ManualSource(
        FakeMarketApi(
            prices={"AAPL": {"symbol": "AAPL", "lastPrice": "178.5", "currency": "USD"}},
            stocks={"AAPL": {"symbol": "AAPL", "name": "Apple Inc.", "marketCountry": "US"}},
        ),
        [ManualHolding(symbol="AAPL", qty=Decimal("10"), avg_price=Decimal("155.3"))],
    )
    position = source.fetch()[0]

    assert position.profit_loss == Decimal("1785") - Decimal("1553")
    assert position.profit_rate == Decimal("232") / Decimal("1553")
    assert position.name == "Apple Inc."


def test_manual_static_asset_uses_its_config_price():
    from src.config import ManualHolding
    from src.sources.manual_source import ManualSource

    source = ManualSource(
        FakeMarketApi(),
        [ManualHolding(symbol=None, name="금 현물", qty=Decimal("1"),
                       avg_price=Decimal("5000000"), price=Decimal("5400000"),
                       currency="KRW")],
    )
    position = source.fetch()[0]

    assert position.last_price == Decimal("5400000")
    assert position.profit_loss == Decimal("400000")
    assert position.market_country == ""


def test_unpriced_manual_symbol_is_skipped_with_a_warning():
    from src.config import ManualHolding
    from src.sources.manual_source import ManualSource

    source = ManualSource(
        FakeMarketApi(),
        [ManualHolding(symbol="NOPE", qty=Decimal("1"), avg_price=Decimal("100"))],
    )
    import pytest as _pytest

    with _pytest.warns(UserWarning, match="시세"):
        assert source.fetch() == []
