"""The risk gate: the last thing between a strategy's opinion and real money.

Every signal passes through :meth:`RiskGate.evaluate`, which either returns an
:class:`OrderIntent` - a signal that has been checked and normalised into
something the API will accept - or a :class:`Rejection` naming the rule that
stopped it. Nothing else is allowed to construct an OrderIntent.

Two deliberate design choices:

*Built before the order code.* Section 6 of the design puts step [7] ahead of
step [8] because once orders work, "let's add the limits later" becomes very
easy to say. There is no order path yet for this module to be bypassed by.

*Pure.* The gate does no I/O. Buying power, sellable quantity, market sessions
and today's usage are read by the caller and handed in on the context, so
every rule here is a function of its arguments and can be tested by
constructing a context rather than by mocking a broker.
"""

import os
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal

from src.models import ZERO
from src.strategy.base import ORDER_LIMIT, ORDER_MARKET, SIDE_BUY, DailyUsage

#: Toss requires ``confirmHighValueOrder`` at and above this notional.
HIGH_VALUE_THRESHOLD_KRW = Decimal("100000000")

#: Amount orders and fractional quantities stop being accepted this long
#: before the regular session closes (design 2.1).
AMOUNT_ORDER_CUTOFF_MINUTES = 60

_KILL_SWITCH_DEFAULT = "KILL_SWITCH"

_CURRENCY_COUNTRY = {"KRW": "KR", "USD": "US"}


def kill_switch_active(path=_KILL_SWITCH_DEFAULT):
    """True when the kill-switch file exists.

    The one piece of I/O in this module, kept as a free function so the gate
    itself stays pure - callers resolve it once and set ``ctx.kill_switch``.
    A file is used rather than a config flag on purpose: stopping the engine
    must not require editing code, restarting a process, or being able to log
    in to anything.
    """
    return os.path.exists(path)


@dataclass(frozen=True)
class RiskLimits:
    """Per-run trading budget. All limits are inclusive upper bounds."""

    max_orders_per_day: int = 10
    max_daily_notional_krw: Decimal = Decimal("5000000")

    #: Cap on any single holding's share of the portfolio, as a fraction.
    #: Checked against the position the order would *result in*, not the
    #: order alone - otherwise ten small buys quietly build one huge position.
    max_position_weight: Decimal = Decimal("0.20")

    #: Per-symbol exceptions to ``max_position_weight``, keyed by symbol. A
    #: concentrated strategy piling into one index ETF is a plan, not a bug -
    #: but that exception belongs in config, as a fact about that symbol, not
    #: in a strategy quietly assuming the gate will let it through.
    max_position_weight_overrides: dict = field(default_factory=dict)

    #: Below this equity, the weight check does not run at all. A strategy's
    #: first-ever order is, by definition, 100% of the account; without this
    #: exemption a concentrated strategy could never place one.
    weight_check_min_equity_krw: Decimal = Decimal("3000000")

    high_value_threshold_krw: Decimal = HIGH_VALUE_THRESHOLD_KRW

    #: Orders at or above the threshold need ``confirmHighValueOrder``. The
    #: design forbids setting that automatically: a bot that can wave through
    #: a 100M order is one bug away from doing it. Turning this on is an
    #: explicit, human decision recorded in config.
    allow_high_value: bool = False

    amount_order_cutoff_minutes: int = AMOUNT_ORDER_CUTOFF_MINUTES

    #: What to do when a check has no data to work with - no quote, no
    #: calendar, no buying-power reading.
    #:
    #: True (the default) rejects: an unverifiable order is not a safe order,
    #: and finding out from a 422 is exactly what section 3.3 item 5 says not
    #: to do. Backtests and early PAPER runs, where none of that reference
    #: data is wired up yet, pass False to check only what they can.
    strict: bool = True


@dataclass(frozen=True)
class Rejection:
    """Why a signal did not become an order. Persisted to ``rejections``."""

    rule: str
    detail: str = ""


@dataclass(frozen=True)
class OrderIntent:
    """A checked, normalised order. Only :class:`RiskGate` produces one."""

    signal: object
    symbol: str
    side: str
    order_type: str
    currency: str
    quantity: Decimal | None = None
    amount: Decimal | None = None
    limit_price: Decimal | None = None

    #: Order value in KRW at today's rate. None only when the gate was run in
    #: lenient mode against a MARKET signal with no quote.
    notional_krw: Decimal | None = None

    #: Set only when the order is genuinely high-value *and* the limits allow
    #: it; the executor copies this straight into the request body.
    confirm_high_value: bool = False

    #: Assigned by the executor in step [8], where the daily sequence lives.
    client_order_id: str | None = None

    @property
    def strategy(self):
        return self.signal.strategy

    def with_client_order_id(self, client_order_id):
        return replace(self, client_order_id=client_order_id)


@dataclass(frozen=True)
class RiskDecision:
    """The result of evaluating one signal."""

    signal: object
    intent: OrderIntent | None = None
    rejection: Rejection | None = None

    @property
    def approved(self):
        return self.intent is not None


def _reject(signal, rule, detail=""):
    return RiskDecision(signal=signal, rejection=Rejection(rule=rule, detail=detail))


def _is_fractional(quantity):
    return quantity is not None and quantity != quantity.to_integral_value()


class RiskGate:
    def __init__(self, limits=None):
        self.limits = limits or RiskLimits()

    def country_of(self, signal, ctx):
        """Which market this symbol trades on.

        The held position knows for certain; otherwise the currency is a
        reliable enough proxy for the two markets Toss offers.
        """
        position = ctx.position(signal.symbol)
        if position is not None and position.market_country:
            return position.market_country
        return _CURRENCY_COUNTRY.get(signal.currency, "")

    def evaluate(self, signal, ctx):
        """Return a :class:`RiskDecision` for one signal.

        Rules run cheapest-and-most-absolute first, so a killed engine or an
        exhausted daily budget short-circuits before anything is priced. The
        first failing rule wins - the rejection names one cause rather than a
        list, because the first one is the one that has to be fixed.
        """
        limits = self.limits
        strict = limits.strict

        # 1. Kill switch. Nothing gets past this, for any reason.
        if ctx.kill_switch:
            return _reject(
                signal, "kill-switch", "KILL_SWITCH가 활성화되어 모든 발주를 중단합니다."
            )

        usage = ctx.daily_usage or DailyUsage()

        # 2. Daily order count.
        if usage.order_count >= limits.max_orders_per_day:
            return _reject(
                signal,
                "daily-order-limit",
                f"오늘 주문 {usage.order_count}건으로 한도({limits.max_orders_per_day}건)에 도달했습니다.",
            )

        country = self.country_of(signal, ctx)
        price = ctx.price(signal.symbol)

        # 3. Fractional quantity: US MARKET SELL only (design 2.1).
        if _is_fractional(signal.quantity):
            if not (
                country == "US"
                and signal.order_type == ORDER_MARKET
                and not signal.is_buy
            ):
                return _reject(
                    signal,
                    "fractional-quantity-not-allowed",
                    f"소수점 수량({signal.quantity})은 미국 시장가 매도에만 허용됩니다 "
                    f"(현재: {country} {signal.order_type} {signal.side}).",
                )

        # 4. Market session.
        session_decision = self._check_session(signal, ctx, country, strict)
        if session_decision is not None:
            return session_decision

        # 5. Price band. Checked before sizing so an obviously bad price is
        #    reported as a bad price rather than as a balance problem.
        band_decision = self._check_price_band(signal, ctx, strict)
        if band_decision is not None:
            return band_decision

        # 6. Value the order. Everything below needs a number.
        notional = signal.notional(market_price=price)
        if notional is None:
            if strict:
                return _reject(
                    signal,
                    "price-unknown",
                    f"{signal.symbol}의 시세가 없어 주문 금액을 산정할 수 없습니다.",
                )
            notional_krw = None
        else:
            notional_krw = ctx.to_krw(notional, signal.currency)
            if signal.currency != "KRW" and not ctx.exchange_rate:
                if strict:
                    return _reject(
                        signal,
                        "exchange-rate-unknown",
                        "환율이 없어 외화 주문을 원화로 환산할 수 없습니다.",
                    )
                notional_krw = None

        if notional_krw is not None:
            budget_decision = self._check_budget(signal, ctx, notional_krw, limits, usage)
            if budget_decision is not None:
                return budget_decision

        # 7. Can the account actually do this?
        balance_decision = self._check_balance(signal, ctx, notional, strict)
        if balance_decision is not None:
            return balance_decision

        confirm_high_value = (
            notional_krw is not None and notional_krw >= limits.high_value_threshold_krw
        )
        if confirm_high_value and not limits.allow_high_value:
            return _reject(
                signal,
                "high-value-not-permitted",
                f"주문 금액 {notional_krw:,.0f}원이 고액 기준"
                f"({limits.high_value_threshold_krw:,.0f}원) 이상입니다. "
                "allow_high_value를 명시적으로 켠 경우에만 통과합니다.",
            )

        return RiskDecision(
            signal=signal,
            intent=OrderIntent(
                signal=signal,
                symbol=signal.symbol,
                side=signal.side,
                order_type=signal.order_type,
                currency=signal.currency,
                quantity=signal.quantity,
                amount=signal.amount,
                limit_price=signal.limit_price if signal.order_type == ORDER_LIMIT else None,
                notional_krw=notional_krw,
                confirm_high_value=confirm_high_value,
            ),
        )

    # ------------------------------------------------------------- rules

    def _check_session(self, signal, ctx, country, strict):
        session = ctx.sessions.get(country)
        if session is None:
            if strict:
                return _reject(
                    signal,
                    "market-session-unknown",
                    f"{country or '해당'} 시장의 운영시간 정보가 없습니다.",
                )
            return None

        if not session.is_open:
            return _reject(
                signal, "order-hours-closed", f"{country} 시장이 열려 있지 않습니다."
            )

        # Amount orders and fractional quantities are refused in the last hour
        # of the regular session even though the market is open.
        needs_early_cutoff = signal.uses_amount or _is_fractional(signal.quantity)
        if not needs_early_cutoff:
            return None

        close = session.regular_close
        if close is None:
            if strict:
                return _reject(
                    signal,
                    "market-session-unknown",
                    f"{country} 정규장 종료시각을 알 수 없어 금액/소수점 주문을 보류합니다.",
                )
            return None

        cutoff = close - timedelta(minutes=self.limits.amount_order_cutoff_minutes)
        if ctx.now >= cutoff:
            kind = "금액 지정" if signal.uses_amount else "소수점 수량"
            return _reject(
                signal,
                "amount-order-outside-regular-hours",
                f"{kind} 주문은 정규장 종료 "
                f"{self.limits.amount_order_cutoff_minutes}분 전({cutoff:%H:%M})까지만 접수됩니다.",
            )
        return None

    def _check_price_band(self, signal, ctx, strict):
        # A market order has no price to compare, and the band only ever
        # applies to a limit we chose ourselves.
        if signal.order_type != ORDER_LIMIT:
            return None

        band = ctx.price_limits.get(signal.symbol)
        if not band:
            if strict:
                return _reject(
                    signal,
                    "price-limits-unknown",
                    f"{signal.symbol}의 상·하한가 정보가 없습니다.",
                )
            return None

        low, high = band
        price = signal.limit_price
        if low is not None and price < low:
            return _reject(
                signal,
                "price-out-of-range",
                f"지정가 {price}가 하한가 {low} 아래입니다.",
            )
        if high is not None and price > high:
            return _reject(
                signal,
                "price-out-of-range",
                f"지정가 {price}가 상한가 {high} 위입니다.",
            )
        return None

    def _check_budget(self, signal, ctx, notional_krw, limits, usage):
        projected = usage.notional_krw + notional_krw
        if projected > limits.max_daily_notional_krw:
            return _reject(
                signal,
                "daily-notional-limit",
                f"오늘 누적 주문금액이 {projected:,.0f}원이 되어 "
                f"한도({limits.max_daily_notional_krw:,.0f}원)를 넘습니다.",
            )

        # Concentration is a buy-side concern: selling only ever reduces it.
        if not signal.is_buy:
            return None

        equity = ctx.equity_krw
        if equity <= ZERO:
            return None
        # A strategy's very first order is, by definition, 100% of the
        # account. Below this threshold there is nothing yet to concentrate,
        # so the check would only ever block the strategy from ever starting.
        if equity < limits.weight_check_min_equity_krw:
            return None

        position = ctx.position(signal.symbol)
        held_krw = ZERO
        if position is not None:
            held_krw = ctx.to_krw(position.evaluation, position.currency) or ZERO

        cap = limits.max_position_weight_overrides.get(
            signal.symbol, limits.max_position_weight
        )
        weight = (held_krw + notional_krw) / equity
        if weight > cap:
            return _reject(
                signal,
                "position-weight-limit",
                f"매수 후 {signal.symbol} 비중이 {weight:.1%}가 되어 "
                f"한도({cap:.1%})를 넘습니다.",
            )
        return None

    def _check_balance(self, signal, ctx, notional, strict):
        if signal.is_buy:
            available = ctx.buying_power.get(signal.currency)
            if available is None:
                if strict:
                    return _reject(
                        signal,
                        "buying-power-unknown",
                        f"{signal.currency} 주문가능금액을 확인할 수 없습니다.",
                    )
                return None
            if notional is None:
                return None
            if notional > available:
                return _reject(
                    signal,
                    "insufficient-buying-power",
                    f"필요 {notional} {signal.currency} > 주문가능 {available} {signal.currency}.",
                )
            return None

        # Sell: an amount-denominated sell still needs shares, but how many is
        # the broker's arithmetic, so only a share-count sell can be checked.
        sellable = ctx.sellable.get(signal.symbol)
        if sellable is None:
            if strict:
                return _reject(
                    signal,
                    "sellable-quantity-unknown",
                    f"{signal.symbol}의 매도가능수량을 확인할 수 없습니다.",
                )
            return None
        if signal.quantity is not None and signal.quantity > sellable:
            return _reject(
                signal,
                "insufficient-sellable-quantity",
                f"매도 요청 {signal.quantity}주 > 매도가능 {sellable}주.",
            )
        return None
