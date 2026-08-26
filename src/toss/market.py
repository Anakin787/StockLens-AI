"""Market data, stock master and market info endpoints.

These need only the bearer token - no account header - because they return
the same objective data for every caller.
"""

from src.toss.errors import TossNotFoundError

#: Toss accepts at most 200 comma-separated symbols per request.
MAX_SYMBOLS_PER_REQUEST = 200


def _chunk(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class MarketApi:
    def __init__(self, client):
        self.client = client

    def prices(self, symbols):
        """GET /api/v1/prices - MARKET_DATA (15 TPS). Returns {symbol: item}."""
        return self._by_symbol("/api/v1/prices", "MARKET_DATA", symbols)

    def stocks(self, symbols):
        """GET /api/v1/stocks - STOCK (5 TPS). Returns {symbol: item}."""
        return self._by_symbol("/api/v1/stocks", "STOCK", symbols)

    def _by_symbol(self, path, group, symbols):
        symbols = [s for s in dict.fromkeys(symbols or []) if s]
        if not symbols:
            return {}

        merged = {}
        for batch in _chunk(symbols, MAX_SYMBOLS_PER_REQUEST):
            result = self.client.get(
                path, group=group, params={"symbols": ",".join(batch)}
            )
            for item in result or []:
                symbol = item.get("symbol")
                if symbol:
                    merged[symbol] = item
        return merged

    def price_limits(self, symbols):
        """GET /api/v1/price-limits - MARKET_INFO (3 TPS). {symbol: item}.

        Today's upper and lower bound for each symbol. A limit price outside
        the band is rejected as ``price-out-of-range``; the risk gate checks
        it up front so a bad price is reported as a bad price.
        """
        return self._by_symbol("/api/v1/price-limits", "MARKET_INFO", symbols)

    def exchange_rate(self, base_currency="USD", quote_currency="KRW"):
        """GET /api/v1/exchange-rate - MARKET_INFO (3 TPS)."""
        return self.client.get(
            "/api/v1/exchange-rate",
            group="MARKET_INFO",
            params={"baseCurrency": base_currency, "quoteCurrency": quote_currency},
        )

    def market_calendar(self, country):
        """GET /api/v1/market-calendar/{KR|US} - MARKET_INFO (3 TPS)."""
        return self.client.get(
            f"/api/v1/market-calendar/{country.upper()}", group="MARKET_INFO"
        )

    def warnings(self, symbol):
        """GET /api/v1/stocks/{symbol}/warnings - STOCK (5 TPS).

        Returns None for an unknown symbol rather than raising: a warning
        lookup failing must never take the whole report down.
        """
        try:
            return self.client.get(
                f"/api/v1/stocks/{symbol}/warnings", group="STOCK"
            )
        except TossNotFoundError:
            return None
