"""The only module that writes. Orders, cancellations, order lookups.

Two things make this the single write path rather than merely the usual one:

*The mode is required and defaults to PAPER.* Constructing a TradingApi is a
decision about whether real money moves, so it cannot be made by accident or
inherited from a config file read three layers away.

*PAPER holds a read-only client.* ``build_trading_api`` gives a PAPER session
a client built with ``allow_write=False``, so a bug that reaches
:meth:`TradingApi.place_order` in paper mode raises TossWriteBlockedError
inside TossClient instead of placing an order. The safety property does not
depend on the executor's mode check being correct - it is enforced one layer
below, where the HTTP call is actually made.
"""

from enum import Enum

from src.toss.errors import TossConfigError


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"

    @classmethod
    def parse(cls, value):
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            raise TossConfigError(
                f"거래 모드는 'paper' 또는 'live'여야 합니다: {value!r}"
            ) from None


def order_body(intent):
    """Turn an approved OrderIntent into a POST /orders body.

    Only ever called with an intent, which is only ever produced by the risk
    gate - there is no path from a raw Signal to a request body.
    """
    body = {
        "clientOrderId": intent.client_order_id,
        "symbol": intent.symbol,
        "side": intent.side,
        "orderType": intent.order_type,
    }
    if not intent.client_order_id:
        raise ValueError("client_order_id 없이 주문 body를 만들 수 없습니다.")

    # The API rejects a body carrying both; the Signal already guarantees
    # exactly one is set, so this mirrors that rather than re-deciding it.
    if intent.amount is not None:
        body["orderAmount"] = str(intent.amount)
    else:
        body["quantity"] = str(intent.quantity)

    if intent.limit_price is not None:
        body["price"] = str(intent.limit_price)

    # Present only when the risk gate both found the order high-value and was
    # configured to permit it. Never defaulted on.
    if intent.confirm_high_value:
        body["confirmHighValueOrder"] = True

    return body


class TradingApi:
    def __init__(self, client, account_seq, mode=TradingMode.PAPER):
        self.client = client
        self.account_seq = account_seq
        self.mode = TradingMode.parse(mode)

    @property
    def is_live(self):
        return self.mode is TradingMode.LIVE

    def __repr__(self):
        return f"TradingApi(mode={self.mode.value}, account_seq={self.account_seq!r})"

    def place_order(self, body):
        """POST /api/v1/orders - ORDER (10 TPS)."""
        return self.client.request(
            "POST",
            "/api/v1/orders",
            group="ORDER",
            json_body=body,
            account_seq=self.account_seq,
        )

    def cancel_order(self, order_id):
        """DELETE /api/v1/orders/{orderId} - ORDER (10 TPS)."""
        return self.client.request(
            "DELETE",
            f"/api/v1/orders/{order_id}",
            group="ORDER",
            account_seq=self.account_seq,
        )

    def get_order(self, order_id):
        """GET /api/v1/orders/{orderId} - ORDER_INFO (6 TPS, 3 at the open).

        A read, so it works on a PAPER session's read-only client. That is
        deliberate: the executor has to be able to ask "did that actually go
        through?" after an ambiguous failure without holding write access.
        """
        return self.client.get(
            f"/api/v1/orders/{order_id}",
            group="ORDER_INFO",
            account_seq=self.account_seq,
        )


def build_trading_api(config, mode=TradingMode.PAPER, account_seq=None):
    """Assemble a TradingApi, granting write access only in LIVE mode."""
    from src.pipeline import build_client
    from src.toss.account import AccountApi

    mode = TradingMode.parse(mode)
    client = build_client(config, allow_write=(mode is TradingMode.LIVE))
    if account_seq is None:
        account_seq = AccountApi(client, account_no=config.toss.account_no).resolve_account_seq()
    return TradingApi(client, account_seq, mode=mode)
