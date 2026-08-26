"""How an approved intent becomes a fill, and how cash arrives to be invested.

Fills happen at the *next* bar's open, not the bar the signal was computed
from. A signal is built from a day's close; filling at that same close would
let the backtest trade at a price the strategy could not have acted on when it
made the decision. A gap gets modelled as a gap, not smoothed away.
"""

from dataclasses import dataclass
from decimal import Decimal

from src.models import ZERO
from src.strategy.base import ORDER_LIMIT, ORDER_MARKET, SIDE_BUY

BPS = Decimal("10000")


@dataclass(frozen=True)
class FillModel:
    slippage_bps: Decimal = Decimal("5")
    commission_bps: Decimal = Decimal("7")

    def fill_price(self, intent, next_bar):
        """The price this intent fills at, using ``next_bar``'s open.

        A LIMIT order only fills if the limit sits inside the next bar's
        range - a buy limit above that day's low, or a sell limit below that
        day's high - otherwise it lapses (``None``), same as a real order that
        was never touched.
        """
        base = next_bar.open
        if intent.order_type == ORDER_LIMIT:
            if intent.limit_price is None:
                return None
            if intent.side == SIDE_BUY:
                if intent.limit_price < next_bar.low:
                    return None
                base = min(intent.limit_price, next_bar.open)
            else:
                if intent.limit_price > next_bar.high:
                    return None
                base = max(intent.limit_price, next_bar.open)
        elif intent.order_type != ORDER_MARKET:
            return None

        slip = base * self.slippage_bps / BPS
        return base + slip if intent.side == SIDE_BUY else base - slip

    def commission(self, notional):
        return notional * self.commission_bps / BPS


@dataclass(frozen=True)
class ContributionSchedule:
    """A monthly KRW deposit, applied on the first trading day at or after
    ``day_of_month`` each month - a deposit due on a weekend does not vanish,
    it lands on the next day the market is actually open."""

    amount_krw: Decimal = Decimal("750000")
    day_of_month: int = 1

    def is_due(self, day, already_contributed_this_month):
        return day.day >= self.day_of_month and not already_contributed_this_month
