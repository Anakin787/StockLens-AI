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


# ------------------------------------------------------- fills (Phase 2 [9])


def test_a_fill_is_recorded_and_readable_back_by_order(store):
    store.save_fill("TOSS-1", quantity=Decimal("10"), price=Decimal("69950"),
                     commission=Decimal("15"), tax=Decimal("3"))

    fills = store.fills_for_order("TOSS-1")
    assert len(fills) == 1
    assert fills[0]["quantity"] == "10"
    assert fills[0]["commission"] == "15"


def test_fills_for_a_different_order_are_not_mixed_in(store):
    store.save_fill("TOSS-1", quantity=Decimal("10"), price=Decimal("70000"))
    store.save_fill("TOSS-2", quantity=Decimal("5"), price=Decimal("71000"))

    assert len(store.fills_for_order("TOSS-1")) == 1
    assert len(store.fills_for_order("TOSS-2")) == 1


# ------------------------------------------------ conditional orders (OCO)


def test_a_conditional_order_is_recorded_and_readable(store):
    store.save_conditional_order(
        "oco-1", entry_client_order_id="entry-1", symbol="005930",
        quantity=Decimal("10"), take_profit_price=Decimal("80000"),
        stop_loss_price=Decimal("65000"), expire_date="2026-09-10",
        status="registered", mode="live",
    )

    row = store.conditional_order_by_client_id("oco-1")
    assert row["entry_client_order_id"] == "entry-1"
    assert row["status"] == "registered"


def test_a_second_save_with_the_same_id_does_not_overwrite(store):
    # Idempotency: a re-run of the reconciler must not clobber a bracket it
    # already placed, the same guarantee save_order gives entry orders.
    store.save_conditional_order(
        "oco-1", entry_client_order_id="entry-1", symbol="005930",
        quantity=Decimal("10"), take_profit_price=Decimal("80000"),
        stop_loss_price=Decimal("65000"), expire_date="2026-09-10",
        status="registered", mode="live",
    )
    store.save_conditional_order(
        "oco-1", entry_client_order_id="entry-1", symbol="005930",
        quantity=Decimal("999"), take_profit_price=Decimal("1"),
        stop_loss_price=Decimal("1"), expire_date="2099-01-01",
        status="pending", mode="live",
    )

    assert store.conditional_order_by_client_id("oco-1")["status"] == "registered"


def test_open_conditional_orders_only_lists_registered_ones(store):
    store.save_conditional_order(
        "oco-1", entry_client_order_id="e1", symbol="005930",
        quantity=Decimal("1"), take_profit_price=Decimal("1"),
        stop_loss_price=Decimal("1"), expire_date="2026-09-10",
        status="registered", mode="live",
    )
    store.save_conditional_order(
        "oco-2", entry_client_order_id="e2", symbol="AAPL",
        quantity=Decimal("1"), take_profit_price=Decimal("1"),
        stop_loss_price=Decimal("1"), expire_date="2026-09-10",
        status="failed", mode="live",
    )

    open_ones = store.open_conditional_orders()
    assert [row["client_order_id"] for row in open_ones] == ["oco-1"]


def test_an_accepted_signal_carries_its_bracket_prices_for_the_reconciler(store):
    bracketed = signal(
        stop_loss_price=Decimal("65000"), take_profit_price=Decimal("80000")
    )
    store.save_decision(RiskDecision(signal=bracketed, intent=object()))

    row = store.recent_signals()[0]
    assert row["stop_loss_price"] == "65000"
    assert row["take_profit_price"] == "80000"
