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

from decimal import Decimal
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


def _round_to_tick(price, currency):
    """Round a price to the precision Toss displays for its currency.

    Korean equities actually trade on a multi-band tick size (1 won up to
    2,000 won, rising in steps to 1,000 won above 500,000) that depends on
    the price level - a rule this project has no confirmed source for, so it
    is deliberately not applied here. Rounding to a whole won for KRW and two
    decimals for USD matches how the rest of the codebase displays these
    currencies; it narrows obviously-wrong prices (many decimal places from
    a slippage multiplication) without pretending to know the real tick
    table. A price that still lands off-tick comes back as
    ``invalid-tick-size`` - a known, named gap, not a silent one.
    """
    if currency == "KRW":
        return price.quantize(Decimal("1"))
    return price.quantize(Decimal("0.01"))


def conditional_order_body(
    client_order_id,
    symbol,
    quantity,
    take_profit_price,
    stop_loss_price,
    expire_date,
    currency="KRW",
    stop_loss_slippage=Decimal("0.005"),
):
    """Build an OCO bracket protecting a long position.

    Two SELL legs: one at ``take_profit_price``, one at ``stop_loss_price``.
    Filling either cancels the other - the mechanism design 2.3 calls "the
    single most important feature for individual automated trading", because
    Toss's own server watches the trigger rather than this process staying
    alive to poll for it.

    The stop leg's order price is set slightly below its trigger, the same
    gap the design's own example uses (trigger 295 / order 294.5) - without
    it, a fast-moving trigger can pass the order price before the limit order
    reaches the book, and the stop simply never fills.
    """
    stop_order_price = _round_to_tick(
        stop_loss_price * (Decimal("1") - stop_loss_slippage), currency
    )
    take_profit_price = _round_to_tick(take_profit_price, currency)
    stop_loss_price = _round_to_tick(stop_loss_price, currency)

    return {
        "symbol": symbol,
        "type": "OCO",
        "quantity": str(quantity),
        "orderType": "LIMIT",
        "clientOrderId": client_order_id,
        "expireDate": expire_date,
        "first": {
            "orderSide": "SELL",
            "triggerPrice": str(take_profit_price),
            "orderPrice": str(take_profit_price),
        },
        "second": {
            "orderSide": "SELL",
            "triggerPrice": str(stop_loss_price),
            "orderPrice": str(stop_order_price),
        },
    }


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

    def list_orders(self, params=None):
        """GET /api/v1/orders - ORDER_HISTORY (5 TPS).

        Design section 3.1 names this as the reconciler's polling source
        ("Reconciler <- GET /orders (폴링)"). The exact query parameters and
        response shape are not pinned down anywhere this project has a
        confirmed source for, so the reconciler that calls this parses the
        result defensively - by scanning for a matching clientOrderId rather
        than assuming a specific field layout - the same caution the market
        calendar and price-limit readers already apply to uncertain shapes.

        A read, so - like get_order - it works on a PAPER session's
        read-only client.
        """
        return self.client.get(
            "/api/v1/orders",
            group="ORDER_HISTORY",
            params=params,
            account_seq=self.account_seq,
        )

    def place_conditional_order(self, body):
        """POST /api/v1/conditional-orders - CONDITIONAL_ORDER (5 TPS)."""
        return self.client.request(
            "POST",
            "/api/v1/conditional-orders",
            group="CONDITIONAL_ORDER",
            json_body=body,
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
