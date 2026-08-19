"""Positions held outside the Toss account, entered by hand in config.yaml.

Only the quantity and the average purchase price are genuinely manual. The
name, currency, market and current price all come from the Toss market-data
endpoints, which is what shrank this from "maintain every field by hand" to
"maintain two numbers".

Assets Toss does not list at all - a deposit, physical gold - are supported
as static entries carrying their own ``price``.
"""

import warnings

from src.models import SOURCE_MANUAL, Position, to_decimal
from src.sources.base import HoldingSource


class ManualSource(HoldingSource):
    name = SOURCE_MANUAL

    def __init__(self, market_api, manual_holdings):
        self.market_api = market_api
        self.manual_holdings = list(manual_holdings or [])

    def fetch(self):
        if not self.manual_holdings:
            return []

        symbols = [h.symbol for h in self.manual_holdings if h.symbol]
        prices = self.market_api.prices(symbols) if symbols else {}
        masters = self.market_api.stocks(symbols) if symbols else {}

        positions = []
        for holding in self.manual_holdings:
            position = self._to_position(holding, prices, masters)
            if position is not None:
                positions.append(position)
        return positions

    @staticmethod
    def _to_position(holding, prices, masters):
        master = masters.get(holding.symbol) or {}
        quote = prices.get(holding.symbol) or {}

        currency = (
            holding.currency
            or quote.get("currency")
            or master.get("currency")
            or "KRW"
        )
        last_price = to_decimal(quote.get("lastPrice"))
        if last_price is None:
            # Either a static asset, or Toss does not list this symbol. The
            # config price is the documented fallback for both.
            last_price = holding.price
            if last_price is None:
                warnings.warn(
                    f"'{holding.symbol}'의 시세를 조회할 수 없어 이 종목을 건너뜁니다. "
                    "config에 price를 직접 입력하면 포함됩니다.",
                    stacklevel=2,
                )
                return None

        market_country = master.get("marketCountry") or ("KR" if currency == "KRW" else "US")
        if holding.symbol is None:
            market_country = ""

        # Toss computes profit for its own positions; for manual ones we
        # derive it here so both kinds show a return in the same column.
        # This is the native-currency return, matching what Toss reports per
        # item - FX gain/loss enters at the portfolio level, not per position.
        market_value = holding.qty * last_price
        purchase_amount = holding.qty * holding.avg_price
        profit_loss = market_value - purchase_amount
        profit_rate = profit_loss / purchase_amount if purchase_amount else None

        return Position(
            symbol=holding.symbol,
            name=holding.name or master.get("name") or holding.symbol or "",
            market_country=market_country,
            currency=currency,
            quantity=holding.qty,
            last_price=last_price,
            avg_purchase_price=holding.avg_price,
            source=SOURCE_MANUAL,
            market_value=market_value,
            purchase_amount=purchase_amount,
            profit_loss=profit_loss,
            profit_rate=profit_rate,
            avg_exchange_rate=holding.avg_exchange_rate,
        )
