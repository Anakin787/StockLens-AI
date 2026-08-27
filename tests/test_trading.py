"""order_body / conditional_order_body construction and TradingApi dispatch."""

from decimal import Decimal

import pytest

from src.toss.trading import TradingApi, TradingMode, conditional_order_body


class FakeClient:
    """Records every call TradingApi makes through it."""

    def __init__(self):
        self.calls = []

    def request(self, method, path, *, group=None, json_body=None, account_seq=None):
        self.calls.append(
            {"method": method, "path": path, "group": group, "body": json_body,
             "account_seq": account_seq}
        )
        return {"orderId": "T-1"}

    def get(self, path, *, group=None, params=None, account_seq=None):
        self.calls.append(
            {"method": "GET", "path": path, "group": group, "params": params,
             "account_seq": account_seq}
        )
        return {"status": "filled"}


def api(mode=TradingMode.LIVE):
    client = FakeClient()
    return TradingApi(client, account_seq=7, mode=mode), client


# ------------------------------------------------------------ dispatch


def test_place_conditional_order_posts_to_the_right_endpoint():
    trading, client = api()
    trading.place_conditional_order({"symbol": "005930"})
    call = client.calls[0]
    assert (call["method"], call["path"], call["group"]) == (
        "POST", "/api/v1/conditional-orders", "CONDITIONAL_ORDER",
    )
    assert call["account_seq"] == 7


def test_list_orders_is_a_read_and_works_on_a_paper_client():
    # PAPER holds a read-only client; get() must not raise the way a POST
    # would (test_write_is_blocked_by_default in test_client.py covers that).
    trading, client = api(mode=TradingMode.PAPER)
    trading.list_orders(params={"symbol": "005930"})
    call = client.calls[0]
    assert (call["method"], call["path"], call["group"]) == (
        "GET", "/api/v1/orders", "ORDER_HISTORY",
    )


# ---------------------------------------------------- conditional_order_body


def test_oco_body_has_two_sell_legs():
    body = conditional_order_body(
        "oco-1", "005930", Decimal("10"), Decimal("80000"), Decimal("65000"),
        "2026-09-10",
    )
    assert body["type"] == "OCO"
    assert body["first"]["orderSide"] == body["second"]["orderSide"] == "SELL"
    assert body["first"]["triggerPrice"] == "80000"  # take-profit, unmodified
    assert body["second"]["triggerPrice"] == "65000"  # stop-loss trigger, unmodified


def test_oco_stop_order_price_sits_below_its_trigger():
    # Without this gap a fast break can pass the order price before the limit
    # reaches the book and the stop simply never fills.
    body = conditional_order_body(
        "oco-1", "005930", Decimal("10"), Decimal("80000"), Decimal("65000"),
        "2026-09-10", stop_loss_slippage=Decimal("0.01"),
    )
    assert Decimal(body["second"]["orderPrice"]) < Decimal(body["second"]["triggerPrice"])
    assert body["second"]["orderPrice"] == "64350"  # 65000 * 0.99, rounded to the won


def test_krw_prices_round_to_the_won_usd_to_the_cent():
    krw = conditional_order_body(
        "o", "005930", Decimal("1"), Decimal("80000.4"), Decimal("65000.6"),
        "2026-09-10", currency="KRW",
    )
    assert krw["first"]["triggerPrice"] == "80000"

    usd = conditional_order_body(
        "o", "AAPL", Decimal("1"), Decimal("200.456"), Decimal("180.454"),
        "2026-09-10", currency="USD",
    )
    assert usd["first"]["triggerPrice"] == "200.46"


def test_oco_body_includes_the_idempotency_key_and_expiry():
    body = conditional_order_body(
        "oco-42", "005930", Decimal("5"), Decimal("80000"), Decimal("65000"),
        "2026-09-10",
    )
    assert body["clientOrderId"] == "oco-42"
    assert body["expireDate"] == "2026-09-10"
    assert body["quantity"] == "5"
