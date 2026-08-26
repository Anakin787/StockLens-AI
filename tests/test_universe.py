"""The leverage policy: what this strategy may hold, enforced in code."""

from decimal import Decimal

import pytest

from src.models import SOURCE_TOSS, PortfolioSnapshot, Position
from src.strategy.universe import (
    DEFAULT_UNIVERSE,
    KIND_INDEX_ETF,
    KIND_SINGLE_STOCK_ETF,
    KIND_STOCK,
    Instrument,
    Universe,
    UniverseError,
    parse_universe,
)
from src.toss.errors import TossConfigError


def instrument(**overrides):
    base = dict(symbol="QQQ", name="Invesco QQQ", kind=KIND_INDEX_ETF)
    base.update(overrides)
    return Instrument(**base)


def position(symbol, **overrides):
    base = dict(
        symbol=symbol,
        name=symbol,
        market_country="US",
        currency="USD",
        quantity=Decimal("1"),
        last_price=Decimal("100"),
        avg_purchase_price=Decimal("90"),
        source=SOURCE_TOSS,
    )
    base.update(overrides)
    return Position(**base)


def test_3x_is_refused():
    with pytest.raises(UniverseError):
        instrument(symbol="TQQQ", leverage=Decimal("3"))


def test_leveraged_single_stock_etf_is_refused_by_kind():
    with pytest.raises(UniverseError):
        instrument(symbol="TSLL", kind=KIND_SINGLE_STOCK_ETF, leverage=Decimal("2"))


def test_leveraged_single_stock_etf_is_refused_by_underlying():
    with pytest.raises(UniverseError):
        instrument(
            symbol="NVDL",
            kind=KIND_INDEX_ETF,
            leverage=Decimal("2"),
            underlying="NVDA",
        )


def test_inverse_is_refused():
    with pytest.raises(UniverseError):
        instrument(symbol="SQQQ", leverage=Decimal("-1"))


def test_2x_index_etf_is_allowed():
    fund = instrument(symbol="QLD", leverage=Decimal("2"), max_weight=Decimal("0.15"))
    assert fund.is_leveraged
    assert fund.max_weight == Decimal("0.15")


def test_max_weight_must_be_a_fraction():
    with pytest.raises(UniverseError):
        instrument(max_weight=Decimal("1.5"))


def test_universe_rejects_a_duplicate_symbol():
    with pytest.raises(UniverseError):
        Universe((instrument(symbol="QQQ"), instrument(symbol="QQQ")))


def test_tradable_excludes_leverage_when_asked():
    universe = Universe(
        (
            instrument(symbol="QQQ", leverage=Decimal("1")),
            instrument(symbol="QLD", leverage=Decimal("2"), max_weight=Decimal("0.15")),
        )
    )
    assert {i.symbol for i in universe.tradable(allow_leverage=True)} == {"QQQ", "QLD"}
    assert {i.symbol for i in universe.tradable(allow_leverage=False)} == {"QQQ"}


def test_disabled_instruments_are_excluded_everywhere():
    universe = Universe((instrument(symbol="QQQ", enabled=False),))
    assert universe.symbols() == ()
    assert universe.tradable() == ()
    assert "QQQ" in universe  # still resolvable by symbol, just not offered


def test_audit_reports_held_symbols_outside_the_universe():
    universe = Universe((instrument(symbol="QQQ"),))
    snapshot = PortfolioSnapshot(positions=[position("QQQ"), position("TSLL")])
    assert universe.audit(snapshot) == ("TSLL",)


def test_parse_universe_rejects_an_unknown_key():
    with pytest.raises(TossConfigError):
        parse_universe([{"symbol": "QQQ", "leverage_factor": 3}])


def test_parse_universe_rejects_a_row_without_symbol():
    with pytest.raises(TossConfigError):
        parse_universe([{"name": "no symbol here"}])


def test_parse_universe_wraps_policy_violation_as_config_error():
    with pytest.raises(TossConfigError):
        parse_universe([{"symbol": "TQQQ", "leverage": 3}])


def test_parse_universe_builds_real_instruments():
    universe = parse_universe(
        [{"symbol": "QLD", "kind": KIND_INDEX_ETF, "leverage": "2", "max_weight": "0.15"}]
    )
    assert universe["QLD"].leverage == Decimal("2")


def test_empty_config_falls_back_to_the_default_universe():
    assert parse_universe([]) is DEFAULT_UNIVERSE
    assert parse_universe(None) is DEFAULT_UNIVERSE


def test_default_universe_has_no_3x_and_no_single_stock_leverage():
    for fund in DEFAULT_UNIVERSE:
        assert fund.leverage <= Decimal("2")
        if fund.is_leveraged:
            assert fund.kind != KIND_SINGLE_STOCK_ETF
            assert fund.underlying is None
