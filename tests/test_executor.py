"""Order execution: idempotency, the PAPER guard, and the error policy."""

from datetime import datetime
from decimal import Decimal

import pytest

from src.execution.executor import (
    STATUS_FAILED,
    STATUS_SIMULATED,
    STATUS_SUBMITTED,
    STATUS_UNKNOWN,
    ExecutorBug,
    OrderExecutor,
)
from src.execution.risk import OrderIntent
from src.store.repo import Store
from src.strategy.base import SIDE_BUY, Signal
from src.toss.errors import TossApiError, TossWriteBlockedError
from src.toss.trading import TradingApi, TradingMode, order_body

CLOCK = lambda: datetime(2026, 8, 26, 10, 0)  # noqa: E731


def signal(**overrides):
    base = dict(
        strategy="test",
        symbol="005930",
        side=SIDE_BUY,
        reason="테스트",
        quantity=Decimal("10"),
        limit_price=Decimal("70000"),
    )
    base.update(overrides)
    return Signal(**base)


def intent(**overrides):
    source = overrides.pop("signal", None) or signal()
    base = dict(
        signal=source,
        symbol=source.symbol,
        side=source.side,
        order_type=source.order_type,
        currency=source.currency,
        quantity=source.quantity,
        amount=source.amount,
        limit_price=source.limit_price,
        notional_krw=Decimal("700000"),
    )
    base.update(overrides)
    return OrderIntent(**base)


class FakeTrading:
    """A TradingApi stand-in that records bodies and replays outcomes."""

    def __init__(self, mode=TradingMode.LIVE, outcomes=None):
        self.mode = mode
        self.bodies = []
        self.outcomes = list(outcomes or [])

    def place_order(self, body):
        self.bodies.append(body)
        outcome = self.outcomes.pop(0) if self.outcomes else {"orderId": "TOSS-1"}
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def executor(tmp_path, trading=None, **kwargs):
    trading = trading or FakeTrading()
    return OrderExecutor(trading, store(tmp_path), clock=CLOCK, **kwargs), trading


# ------------------------------------------------------------- paper mode


def test_paper_records_a_simulated_order_and_sends_nothing(tmp_path):
    engine, trading = executor(tmp_path, FakeTrading(mode=TradingMode.PAPER))
    record = engine.submit(intent())

    assert record.status == STATUS_SIMULATED
    assert record.sent
    assert trading.bodies == []  # no request was ever built

    row = engine.store.order_by_client_id(record.client_order_id)
    assert (row["status"], row["mode"]) == (STATUS_SIMULATED, "paper")


def test_paper_uses_a_client_that_physically_cannot_write():
    """The second safety layer, below the executor's mode check.

    Even if a bug called place_order in paper mode, the read-only client
    refuses the POST - so the guarantee does not rest on one branch.
    """
    from src.toss.client import TossClient

    client = TossClient("id", "secret", allow_write=False)
    api = TradingApi(client, account_seq=1, mode=TradingMode.PAPER)
    with pytest.raises(TossWriteBlockedError):
        api.place_order({"symbol": "005930"})


def test_executor_refuses_a_mode_that_contradicts_the_trading_api(tmp_path):
    with pytest.raises(ValueError):
        OrderExecutor(FakeTrading(mode=TradingMode.PAPER), store(tmp_path),
                      mode=TradingMode.LIVE)


# ------------------------------------------------------------ idempotency


def test_a_rerun_of_the_same_batch_places_no_second_order(tmp_path):
    """The property the derived id exists for.

    Two runs - two executors - over the same store and the same signal. The
    seq counter restarts each run, so the id comes out identical and the
    store recognises it.
    """
    shared = store(tmp_path)
    first_run = OrderExecutor(FakeTrading(), shared, clock=CLOCK)
    second_trading = FakeTrading()
    second_run = OrderExecutor(second_trading, shared, clock=CLOCK)

    first = first_run.submit(intent())
    second = second_run.submit(intent())

    assert first.client_order_id == second.client_order_id
    assert second.duplicate is True
    assert second_trading.bodies == []  # nothing left the process
    assert len(shared.recent_orders()) == 1


def test_two_distinct_signals_in_one_run_get_their_own_ids(tmp_path):
    # The seq is what allows a strategy to buy the same symbol twice in a
    # day; within one run these are two decisions, not a repeat of one.
    engine, trading = executor(tmp_path)
    first = engine.submit(intent())
    second = engine.submit(intent())

    assert first.client_order_id != second.client_order_id
    assert second.duplicate is False
    assert len(trading.bodies) == 2


def test_the_row_is_written_before_the_request(tmp_path):
    """An unanswered POST still has to leave a trace, or the next run re-orders."""
    seen = {}

    class Recording(FakeTrading):
        def place_order(self, body):
            seen["row"] = engine.store.order_by_client_id(body["clientOrderId"])
            raise TossApiError(500, "network-error", "끊김")

    engine, _ = executor(tmp_path, Recording())
    engine.submit(intent())
    assert seen["row"] is not None
    assert seen["row"]["status"] == "pending"


# --------------------------------------------------------------- live path


def test_live_submit_stores_the_broker_order_id(tmp_path):
    engine, trading = executor(tmp_path, FakeTrading(outcomes=[{"orderId": "T-99"}]))
    record = engine.submit(intent())

    assert (record.status, record.order_id) == (STATUS_SUBMITTED, "T-99")
    assert trading.bodies[0]["quantity"] == "10"
    assert trading.bodies[0]["price"] == "70000"


def test_high_value_confirmation_is_only_sent_when_the_gate_set_it(tmp_path):
    engine, trading = executor(tmp_path)
    engine.submit(intent())
    assert "confirmHighValueOrder" not in trading.bodies[0]

    engine2, trading2 = executor(tmp_path / "b")
    engine2.submit(intent(confirm_high_value=True))
    assert trading2.bodies[0]["confirmHighValueOrder"] is True


def test_amount_orders_send_orderAmount_instead_of_quantity(tmp_path):
    engine, trading = executor(tmp_path)
    engine.submit(
        intent(signal=signal(quantity=None, amount=Decimal("500000")))
    )
    body = trading.bodies[0]
    assert body["orderAmount"] == "500000"
    assert "quantity" not in body


# ------------------------------------------------------------ error policy


def error(code, status=422):
    return TossApiError(status, code, f"{code} 발생")


def test_terminal_rejection_is_not_retried(tmp_path):
    engine, trading = executor(
        tmp_path, FakeTrading(outcomes=[error("insufficient-buying-power")])
    )
    record = engine.submit(intent())

    assert record.status == STATUS_FAILED
    assert record.error_code == "insufficient-buying-power"
    assert record.terminal is True
    assert len(trading.bodies) == 1


def test_restricted_symbol_is_flagged_for_blacklisting(tmp_path):
    engine, _ = executor(tmp_path, FakeTrading(outcomes=[error("stock-restricted")]))
    assert engine.submit(intent()).blacklisted is True


def test_unknown_error_codes_are_treated_as_terminal(tmp_path):
    # Not recognised is not the same as safe to retry.
    engine, trading = executor(tmp_path, FakeTrading(outcomes=[error("who-knows")]))
    record = engine.submit(intent())
    assert record.status == STATUS_FAILED
    assert record.terminal is False  # not a known code, but still not retried
    assert len(trading.bodies) == 1


def test_price_out_of_range_retries_once_with_a_clamped_price(tmp_path):
    engine, trading = executor(
        tmp_path,
        FakeTrading(outcomes=[error("price-out-of-range"), {"orderId": "T-2"}]),
        price_limits={"005930": (Decimal("50000"), Decimal("69000"))},
    )
    record = engine.submit(intent())

    assert record.status == STATUS_SUBMITTED
    assert len(trading.bodies) == 2
    assert trading.bodies[0]["price"] == "70000"
    assert trading.bodies[1]["price"] == "69000"  # clamped to the upper bound


def test_price_retry_happens_exactly_once(tmp_path):
    engine, trading = executor(
        tmp_path,
        FakeTrading(
            outcomes=[error("price-out-of-range"), error("price-out-of-range")]
        ),
        price_limits={"005930": (Decimal("50000"), Decimal("69000"))},
    )
    record = engine.submit(intent())

    assert record.status == STATUS_FAILED
    assert len(trading.bodies) == 2  # original + one retry, no more


def test_price_error_with_no_band_is_not_retried(tmp_path):
    # Re-sending at an invented price is worse than failing.
    engine, trading = executor(
        tmp_path, FakeTrading(outcomes=[error("price-out-of-range")])
    )
    assert engine.submit(intent()).status == STATUS_FAILED
    assert len(trading.bodies) == 1


def test_the_band_in_the_error_envelope_wins(tmp_path):
    exc = TossApiError(422, "price-out-of-range", "범위 밖", data={"upperLimit": "68000"})
    engine, trading = executor(
        tmp_path,
        FakeTrading(outcomes=[exc, {"orderId": "T-3"}]),
        price_limits={"005930": (Decimal("50000"), Decimal("69000"))},
    )
    engine.submit(intent())
    assert trading.bodies[1]["price"] == "68000"


def test_in_flight_response_is_left_unknown_and_never_resent(tmp_path):
    engine, trading = executor(
        tmp_path, FakeTrading(outcomes=[error("request-in-progress", status=409)])
    )
    record = engine.submit(intent())

    assert record.status == STATUS_UNKNOWN
    assert len(trading.bodies) == 1
    # Not "failed": the order may well exist, and saying otherwise would
    # invite a duplicate on the next run.
    assert record.status != STATUS_FAILED


def test_idempotency_conflict_stops_the_run(tmp_path):
    engine, _ = executor(
        tmp_path, FakeTrading(outcomes=[error("idempotency-key-conflict")])
    )
    with pytest.raises(ExecutorBug):
        engine.submit(intent())


def test_idempotency_conflict_is_recorded_before_it_raises(tmp_path):
    engine, _ = executor(
        tmp_path, FakeTrading(outcomes=[error("idempotency-key-conflict")])
    )
    with pytest.raises(ExecutorBug):
        engine.submit(intent())
    row = engine.store.recent_orders()[0]
    assert row["status"] == STATUS_FAILED
    assert row["error_code"] == "idempotency-key-conflict"


# ------------------------------------------------------------------ body


def test_order_body_refuses_an_intent_with_no_id():
    with pytest.raises(ValueError):
        order_body(intent())
