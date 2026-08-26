"""A simulated portfolio that produces real PortfolioSnapshot objects.

This is the load-bearing decision of the whole backtest: because the snapshot
this builds is the same :class:`~src.models.PortfolioSnapshot` the live path
uses, ``StrategyContext.positions``/``.equity_krw``/``.to_krw`` and every
:class:`~src.execution.risk.RiskGate` rule run verbatim against it. Nothing
downstream - the strategy or the gate - can tell this is a simulation, which
is what makes replaying the real gate through history meaningful rather than
decorative.

Cash is tracked in USD only: the universe this strategy trades is US-listed,
and monthly contributions arrive in KRW and are converted on the day they
land (see :class:`~src.backtest.fills.ContributionSchedule`).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from src.models import SOURCE_MANUAL, ZERO, Position, PortfolioSnapshot
from src.strategy.base import SIDE_BUY


@dataclass
class Lot:
    quantity: Decimal
    avg_price: Decimal  # USD, cost basis per share


@dataclass
class Trade:
    """One realised sell, for win-rate and per-signal attribution."""

    date: object
    symbol: str
    quantity: Decimal
    entry_price: Decimal
    exit_price: Decimal
    pnl_usd: Decimal
    mode: str | None = None


class SimPortfolio:
    def __init__(self, cash_usd=ZERO):
        self.cash_usd = cash_usd
        self.lots = {}  # symbol -> Lot
        self.trades = []
        self.orders_today = 0
        self.notional_today_krw = ZERO
        self._current_day = None
        self.contributed_krw = ZERO

    def reset_daily(self, day):
        """Zero the daily counters at the start of a new simulated day.

        Mirrors ``store.daily_usage(day)`` in the live path - the risk gate's
        daily limits are meant to reset at midnight, not to persist forever.
        """
        if self._current_day != day:
            self._current_day = day
            self.orders_today = 0
            self.notional_today_krw = ZERO

    def record_intent(self, notional_krw):
        """Called once per approved signal, before its fill lands.

        The risk gate's daily-order and daily-notional checks read these
        counters *within the same simulated day*, so an approval has to be
        visible to the next signal evaluated that day - waiting until the
        fill (tomorrow) would let the gate approve far more than its own
        limits allow.
        """
        self.orders_today += 1
        self.notional_today_krw += notional_krw or ZERO

    def apply_fill(self, day, symbol, side, quantity, price, commission=ZERO, mode=None):
        """Settle one fill: update cash and the lot, and log a trade on a sell."""
        notional = quantity * price
        if side == SIDE_BUY:
            self.cash_usd -= notional + commission
            lot = self.lots.get(symbol)
            if lot is None:
                self.lots[symbol] = Lot(quantity, price)
            else:
                total_qty = lot.quantity + quantity
                new_avg = (lot.quantity * lot.avg_price + notional) / total_qty
                self.lots[symbol] = Lot(total_qty, new_avg)
            return

        self.cash_usd += notional - commission
        lot = self.lots.get(symbol)
        entry_price = lot.avg_price if lot else price
        pnl = quantity * (price - entry_price) - commission
        self.trades.append(
            Trade(
                date=day,
                symbol=symbol,
                quantity=quantity,
                entry_price=entry_price,
                exit_price=price,
                pnl_usd=pnl,
                mode=mode,
            )
        )
        if lot is not None:
            remaining = lot.quantity - quantity
            if remaining <= ZERO:
                del self.lots[symbol]
            else:
                self.lots[symbol] = Lot(remaining, lot.avg_price)

    def contribute(self, amount_krw, fx_rate):
        """Convert a KRW deposit to USD cash at the day's rate."""
        if not fx_rate:
            return
        self.cash_usd += amount_krw / fx_rate
        self.contributed_krw += amount_krw

    def held_quantity(self, symbol):
        lot = self.lots.get(symbol)
        return lot.quantity if lot else ZERO

    def snapshot(self, prices, fx_rate):
        """Build a real PortfolioSnapshot from the current lots and cash."""
        positions = []
        total_usd = self.cash_usd
        for symbol, lot in self.lots.items():
            price = prices.get(symbol, lot.avg_price)
            positions.append(
                Position(
                    symbol=symbol,
                    name=symbol,
                    market_country="US",
                    currency="USD",
                    quantity=lot.quantity,
                    last_price=price,
                    avg_purchase_price=lot.avg_price,
                    source=SOURCE_MANUAL,
                )
            )
            total_usd += lot.quantity * price
        return PortfolioSnapshot(
            positions=positions,
            exchange_rate=fx_rate,
            total_krw=total_usd * fx_rate,
            buying_power={"USD": self.cash_usd},
        )
