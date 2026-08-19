"""Unified domain model for holdings, independent of where they came from.

Toss returns every monetary field as a string. Parsing those into Decimal at
the boundary - rather than float - keeps won-level sums exact and removes the
rounding drift the previous yfinance-based implementation carried.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")

SOURCE_TOSS = "toss"
SOURCE_MANUAL = "manual"


def to_decimal(value, default=None):
    """Parse an API value into Decimal, returning ``default`` when unusable."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def dig(mapping, *keys, default=None):
    """Walk nested dicts safely - Toss nests amounts several levels deep."""
    current = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


@dataclass(frozen=True)
class Position:
    """One holding, normalised across the Toss account and manual entries."""

    symbol: str | None
    name: str
    market_country: str  # "KR" | "US" | "" for static assets
    currency: str  # "KRW" | "USD"
    quantity: Decimal
    last_price: Decimal
    avg_purchase_price: Decimal
    source: str  # SOURCE_TOSS | SOURCE_MANUAL

    # Present for Toss positions, where the server already did the maths
    # including fees and tax. Manual positions derive what they can.
    market_value: Decimal | None = None
    purchase_amount: Decimal | None = None
    profit_loss: Decimal | None = None
    profit_loss_after_cost: Decimal | None = None
    profit_rate: Decimal | None = None
    profit_rate_after_cost: Decimal | None = None
    daily_profit_loss: Decimal | None = None
    daily_profit_rate: Decimal | None = None

    #: Only ever set from config. Toss does not report the exchange rate that
    #: applied when a foreign position was bought.
    avg_exchange_rate: Decimal | None = None

    @property
    def is_foreign(self):
        return self.currency != "KRW"

    @property
    def evaluation(self):
        """Market value in the position's own currency."""
        if self.market_value is not None:
            return self.market_value
        return self.quantity * self.last_price

    @property
    def cost_basis(self):
        """Purchase amount in the position's own currency."""
        if self.purchase_amount is not None:
            return self.purchase_amount
        return self.quantity * self.avg_purchase_price


@dataclass
class PortfolioSnapshot:
    """Everything the report and the dashboard need for one point in time."""

    positions: list = field(default_factory=list)
    exchange_rate: Decimal = ZERO

    # Totals converted to KRW
    total_krw: Decimal = ZERO
    purchase_krw: Decimal = ZERO
    profit_krw: Decimal = ZERO
    profit_rate: Decimal = ZERO
    profit_after_cost_krw: Decimal | None = None
    profit_rate_after_cost: Decimal | None = None
    daily_profit_krw: Decimal = ZERO
    daily_profit_rate: Decimal = ZERO

    # Per-currency subtotals, before conversion
    by_currency: dict = field(default_factory=dict)

    #: True when at least one foreign position has no avg_exchange_rate, so
    #: the return excludes FX gain/loss. Surfaced as a badge so the number is
    #: not read as something it is not.
    has_unconverted_fx: bool = False

    warnings: list = field(default_factory=list)
    buying_power: dict = field(default_factory=dict)

    @property
    def total_usd_equivalent(self):
        if not self.exchange_rate:
            return None
        return self.total_krw / self.exchange_rate

    def allocation(self, by="market"):
        """Group market value in KRW for the dashboard's donut chart."""
        buckets = {}
        for position in self.positions:
            if by == "currency":
                key = position.currency
            else:
                key = position.market_country or "OTHER"
            rate = self.exchange_rate if position.is_foreign else Decimal("1")
            buckets[key] = buckets.get(key, ZERO) + position.evaluation * rate
        return buckets
