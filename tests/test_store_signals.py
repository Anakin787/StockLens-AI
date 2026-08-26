"""The Phase 2 audit trail: signals, rejections, and the daily budget read."""

from decimal import Decimal

import pytest

from src.execution.risk import Rejection, RiskDecision
from src.store.repo import Store
from src.strategy.base import SIDE_BUY, Signal


def signal(**overrides):
    base = dict(
        strategy="test",
        symbol="005930",
        side=SIDE_BUY,
        reason="평단 아래로 내려옴",
        quantity=Decimal("10"),
        limit_price=Decimal("70000"),
        meta={"rsi": Decimal("28.4")},
    )
    base.update(overrides)
    return Signal(**base)


@pytest.fixture
def store(firestore_client):
    return Store(firestore_client)


def test_accepted_signal_is_recorded_without_a_rejection(store):
    store.save_decision(RiskDecision(signal=signal(), intent=object()))

    rows = store.recent_signals()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "accepted"
    assert rows[0]["reject_rule"] is None
    assert rows[0]["reason"] == "평단 아래로 내려옴"


def test_rejected_signal_keeps_the_rule_that_stopped_it(store):
    store.save_decision(
        RiskDecision(
            signal=signal(),
            rejection=Rejection("kill-switch", "KILL_SWITCH 활성"),
        )
    )

    row = store.recent_signals()[0]
    assert row["outcome"] == "rejected"
    assert row["reject_rule"] == "kill-switch"
    assert row["reject_detail"] == "KILL_SWITCH 활성"


def test_strategy_meta_survives_the_round_trip(store):
    store.save_decision(RiskDecision(signal=signal(), intent=object()))
    # Decimal isn't Firestore-storable, so it round-trips as text.
    assert store.recent_signals()[0]["payload"]["rsi"] == "28.4"


def test_daily_usage_sums_todays_orders(store):
    orders = store.client.collection("orders")
    orders.document("a").set(
        {"ts": "2026-08-26T09:10:00", "symbol": "005930", "side": "BUY",
         "order_type": "LIMIT", "notional_krw": "700000", "status": "submitted",
         "mode": "paper"}
    )
    orders.document("b").set(
        {"ts": "2026-08-26T13:00:00", "symbol": "AAPL", "side": "BUY",
         "order_type": "LIMIT", "notional_krw": "1300000", "status": "filled",
         "mode": "paper"}
    )
    # Yesterday - must not count.
    orders.document("c").set(
        {"ts": "2026-08-25T09:10:00", "symbol": "005930", "side": "BUY",
         "order_type": "LIMIT", "notional_krw": "5000000", "status": "filled",
         "mode": "paper"}
    )
    # Rejected - never reached the broker, so it spends no budget.
    orders.document("d").set(
        {"ts": "2026-08-26T14:00:00", "symbol": "005930", "side": "BUY",
         "order_type": "LIMIT", "notional_krw": "9000000", "status": "rejected",
         "mode": "paper"}
    )

    usage = store.daily_usage("2026-08-26")
    assert usage.order_count == 2
    assert usage.notional_krw == Decimal("2000000")


def test_daily_usage_is_zero_on_a_quiet_day(store):
    usage = store.daily_usage("2026-08-26")
    assert usage.order_count == 0
    assert usage.notional_krw == Decimal("0")
