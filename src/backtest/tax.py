"""Korean capital-gains tax on overseas stock, as a backtest cost.

Why this exists at all: the older strategy never sold, so realised gains were
nil and leaving tax out of the model cost nothing. A strategy that rotates
realises gains by design, and every knob that changes turnover - the exit
rule, the incumbency margin, the buffer - changes this bill. Comparing those
knobs with tax set to zero would rank them on a cost the account does not
actually get to skip.

The rule modelled (양도소득세, 해외주식):

* Gains and losses realised in one calendar year are netted.
* A basic deduction (기본공제) is applied to the net gain.
* What remains is taxed at a flat rate.
* A net loss is not refunded and, in this model, is not carried forward -
  Korea does not allow a carry-forward for this income, so neither does this.
* The bill is settled the following May. The model deducts it from cash on
  the first session of the following year, which is close enough for a
  yearly-resolution backtest and errs toward charging it early.

The figures are defaults, not law that this module is asserting: rates and
the deduction change, and a reader should check the current year rather than
trust a constant compiled into a backtest. They live in one place here so
that check is a one-line edit.
"""

from dataclasses import dataclass
from decimal import Decimal

from src.models import ZERO

#: Flat rate applied to the net gain above the deduction. 20% capital gains
#: plus the 10%-of-that local surtax = 22%.
DEFAULT_RATE = Decimal("0.22")

#: Annual basic deduction, in KRW.
DEFAULT_DEDUCTION_KRW = Decimal("2500000")


@dataclass(frozen=True)
class CapitalGainsTax:
    """Yearly netting, a deduction, a flat rate, no carry-forward."""

    rate: Decimal = DEFAULT_RATE
    deduction_krw: Decimal = DEFAULT_DEDUCTION_KRW
    enabled: bool = True

    def bill_krw(self, net_gain_krw):
        """Tax owed on one year's netted gain. Never negative."""
        if not self.enabled or net_gain_krw is None:
            return ZERO
        taxable = net_gain_krw - self.deduction_krw
        if taxable <= ZERO:
            return ZERO
        return taxable * self.rate


class RealisedGainLedger:
    """Realised gains per calendar year, and what is owed on each.

    Gains are converted to KRW on the day of the sale, which is the currency
    the tax is actually assessed in - netting in USD and converting once at
    the end would quietly turn a decade of exchange-rate movement into part
    of the tax base.
    """

    def __init__(self, tax=None):
        self.tax = tax or CapitalGainsTax()
        self.by_year = {}
        self.paid_years = set()
        self.total_paid_krw = ZERO

    def record(self, day, pnl_usd, fx_rate):
        if not self.tax.enabled or not fx_rate:
            return
        year = day.year
        self.by_year[year] = self.by_year.get(year, ZERO) + pnl_usd * fx_rate

    def due_on(self, day):
        """KRW owed for any completed year not yet settled, as of ``day``."""
        if not self.tax.enabled:
            return ZERO
        owed = ZERO
        for year, net in self.by_year.items():
            if year >= day.year or year in self.paid_years:
                continue
            owed += self.tax.bill_krw(net)
            self.paid_years.add(year)
        self.total_paid_krw += owed
        return owed
