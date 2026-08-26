"""Signal validation and the context a strategy reads."""

from datetime import datetime
from decimal import Decimal

import pytest

from src.models import SOURCE_TOSS, PortfolioSnapshot, Position
from src.strategy.base import (
    ORDER_MARKET,
    SIDE_BUY,
    SIDE_SELL,
    Signal,
    SignalError,
    Strategy,
    StrategyContext,
)

RATE = Decimal("1400")


def position(**overrides):
    base = dict(
        symbol="AAPL",
        name="Apple",
        market_country="US",
        currency="USD",
        quantity=Decimal("10"),
        last_price=Decimal("200"),
        avg_purchase_price=Decimal("150"),
        source=SOURCE_TOSS,
    )
    base.update(overrides)
    return Position(**base)


def signal(**overrides):
    base = dict(
        strategy="test",
        symbol="005930",
        side=SIDE_BUY,
        reason="테스트 신호",
        quantity=Decimal("10"),
        limit_price=Decimal("70000"),
    )
    base.update(overrides)
    return Signal(**base)


# ------------------------------------------------------------ validation


def test_quantity_and_amount_are_mutually_exclusive():
    with pytest.raises(SignalError):
        signal(amount=Decimal("100000"))

    with pytest.raises(SignalError):
        signal(quantity=None, amount=None)


def test_limit_order_requires_a_price():
    with pytest.raises(SignalError):
        signal(limit_price=None)


def test_market_order_needs_no_price():
    assert signal(order_type=ORDER_MARKET, limit_price=None).limit_price is None


def test_reason_is_required():
    with pytest.raises(SignalError):
        signal(reason="   ")


def test_rejects_bad_side_and_nonpositive_size():
    with pytest.raises(SignalError):
        signal(side="HOLD")
    with pytest.raises(SignalError):
        signal(quantity=Decimal("0"))


# --------------------------------------------------------------- pricing


def test_limit_signal_prices_itself():
    assert signal().notional() == Decimal("700000")


def test_market_signal_borrows_the_market_price():
    market = signal(order_type=ORDER_MARKET, limit_price=None)
    assert market.notional() is None
    assert market.notional(market_price=Decimal("71000")) == Decimal("710000")


def test_amount_signal_is_its_own_notional():
    amount = signal(quantity=None, amount=Decimal("500000"))
    assert amount.uses_amount is True
    assert amount.notional() == Decimal("500000")


# --------------------------------------------------------------- context


def context(**overrides):
    snapshot = PortfolioSnapshot(
        positions=[position()],
        exchange_rate=RATE,
        total_krw=Decimal("10000000"),
    )
    base = dict(now=datetime(2026, 8, 26, 10, 0), snapshot=snapshot)
    base.update(overrides)
    return StrategyContext(**base)


def test_context_indexes_positions_by_symbol():
    ctx = context()
    assert ctx.position("AAPL").name == "Apple"
    assert ctx.position("005930") is None


def test_context_skips_positions_without_a_symbol():
    snapshot = PortfolioSnapshot(
        positions=[position(symbol=None, name="예금")], exchange_rate=RATE
    )
    assert context(snapshot=snapshot).positions == {}


def test_context_converts_to_krw_at_todays_rate():
    ctx = context()
    assert ctx.to_krw(Decimal("100"), "USD") == Decimal("140000")
    assert ctx.to_krw(Decimal("100"), "KRW") == Decimal("100")


# -------------------------------------------------------------- interface


def test_strategy_must_implement_evaluate():
    with pytest.raises(TypeError):
        Strategy()


def test_a_strategy_is_a_pure_function_of_its_context():
    class BuyDips(Strategy):
        name = "buy-dips"

        def evaluate(self, ctx):
            out = []
            for symbol, held in ctx.positions.items():
                if held.last_price < held.avg_purchase_price:
                    out.append(
                        Signal(
                            strategy=self.name,
                            symbol=symbol,
                            side=SIDE_BUY,
                            reason="평단 아래",
                            quantity=Decimal("1"),
                            limit_price=held.last_price,
                            currency=held.currency,
                        )
                    )
            return out

    strategy = BuyDips()
    assert strategy.evaluate(context()) == []

    snapshot = PortfolioSnapshot(
        positions=[position(last_price=Decimal("100"))], exchange_rate=RATE
    )
    signals = strategy.evaluate(context(snapshot=snapshot))
    assert [s.symbol for s in signals] == ["AAPL"]
    # Same input, same output - the property that makes backtesting possible.
    assert strategy.evaluate(context(snapshot=snapshot)) == signals


def test_sell_signal_carries_its_bracket_prices():
    exit_signal = signal(
        side=SIDE_SELL,
        stop_loss_price=Decimal("66000"),
        take_profit_price=Decimal("78000"),
    )
    assert exit_signal.is_buy is False
    assert exit_signal.stop_loss_price == Decimal("66000")
