"""Positions held in the Toss account.

Toss already computes market value, profit and daily change - including the
fee/tax-adjusted variants - so this source copies those numbers rather than
recomputing them. Recomputing would only introduce a second, slightly
different answer to the same question.
"""

from src.models import SOURCE_TOSS, Position, dig, to_decimal
from src.sources.base import HoldingSource


class TossSource(HoldingSource):
    name = SOURCE_TOSS

    def __init__(self, account_api):
        self.account_api = account_api
        self.last_result = None

    def fetch(self):
        result = self.account_api.holdings() or {}
        self.last_result = result
        return [self._to_position(item) for item in result.get("items") or []]

    @staticmethod
    def _to_position(item):
        currency = item.get("currency") or "KRW"
        return Position(
            symbol=item.get("symbol"),
            name=item.get("name") or item.get("symbol") or "",
            market_country=item.get("marketCountry") or "",
            currency=currency,
            quantity=to_decimal(item.get("quantity"), default=0),
            last_price=to_decimal(item.get("lastPrice"), default=0),
            avg_purchase_price=to_decimal(item.get("averagePurchasePrice"), default=0),
            source=SOURCE_TOSS,
            market_value=to_decimal(dig(item, "marketValue", "amount")),
            purchase_amount=to_decimal(dig(item, "marketValue", "purchaseAmount")),
            profit_loss=to_decimal(dig(item, "profitLoss", "amount")),
            profit_loss_after_cost=to_decimal(dig(item, "profitLoss", "amountAfterCost")),
            profit_rate=to_decimal(dig(item, "profitLoss", "rate")),
            profit_rate_after_cost=to_decimal(dig(item, "profitLoss", "rateAfterCost")),
            daily_profit_loss=to_decimal(dig(item, "dailyProfitLoss", "amount")),
            daily_profit_rate=to_decimal(dig(item, "dailyProfitLoss", "rate")),
        )
