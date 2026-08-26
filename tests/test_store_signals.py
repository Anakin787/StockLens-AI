"""The Phase 2 audit trail: signals, rejections, and the daily budget read."""

from decimal import Decimal

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


def store(tmp_path):
    return Store(str(tmp_path / "test.db"))


def test_accepted_signal_is_recorded_without_a_rejection(tmp_path):
    db = store(tmp_path)
    db.save_decision(RiskDecision(signal=signal(), intent=object()))

    rows = db.recent_signals()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "accepted"
    assert rows[0]["reject_rule"] is None
    assert rows[0]["reason"] == "평단 아래로 내려옴"


def test_rejected_signal_keeps_the_rule_that_stopped_it(tmp_path):
    db = store(tmp_path)
    db.save_decision(
        RiskDecision(
            signal=signal(),
            rejection=Rejection("kill-switch", "KILL_SWITCH 활성"),
        )
    )

    row = db.recent_signals()[0]
    assert row["outcome"] == "rejected"
    assert row["reject_rule"] == "kill-switch"
    assert row["reject_detail"] == "KILL_SWITCH 활성"


def test_strategy_meta_survives_the_round_trip(tmp_path):
    db = store(tmp_path)
    db.save_decision(RiskDecision(signal=signal(), intent=object()))
    # Stored as JSON so the numbers behind a past decision stay readable.
    assert "rsi" in db.recent_signals()[0]["payload"]


def test_daily_usage_sums_todays_orders(tmp_path):
    db = store(tmp_path)
    with db._connect() as connection:
        connection.executemany(
            """
            INSERT INTO orders (
                client_order_id, ts, symbol, side, order_type,
                notional_krw, status, mode
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            [
                ("a", "2026-08-26T09:10:00", "005930", "BUY", "LIMIT",
                 "700000", "submitted", "paper"),
                ("b", "2026-08-26T13:00:00", "AAPL", "BUY", "LIMIT",
                 "1300000", "filled", "paper"),
                # Yesterday - must not count.
                ("c", "2026-08-25T09:10:00", "005930", "BUY", "LIMIT",
                 "5000000", "filled", "paper"),
                # Rejected - never reached the broker, so it spends no budget.
                ("d", "2026-08-26T14:00:00", "005930", "BUY", "LIMIT",
                 "9000000", "rejected", "paper"),
            ],
        )

    usage = db.daily_usage("2026-08-26")
    assert usage.order_count == 2
    assert usage.notional_krw == Decimal("2000000")


def test_daily_usage_is_zero_on_a_quiet_day(tmp_path):
    usage = store(tmp_path).daily_usage("2026-08-26")
    assert usage.order_count == 0
    assert usage.notional_krw == Decimal("0")
