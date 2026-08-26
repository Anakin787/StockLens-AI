"""Turning a backtest's equity curve and trade log into numbers worth reading.

One deliberate, scoped exception to the project's Decimal-only rule: the
aggregate statistics here (CAGR, IRR, annualised MDD) convert to ``float`` for
the arithmetic - a fractional exponent and Newton's method on Decimal buy
nothing but complexity for numbers that exist to be printed, not to size an
order. Every per-trade and per-signal figure upstream of this module - fills,
cash, positions - stays Decimal end to end; only the final summary rounds
through float.

Two return figures are reported, deliberately not one:

* **TWR (time-weighted return)** removes the effect of monthly contributions -
  it answers "how did the strategy do", independent of when money arrived.
* **IRR (money-weighted, XIRR)** uses the actual contribution dates and
  amounts - it answers "how did *this account* do", which is what the user
  actually experiences when contributions are rising over time.

A single "CAGR" figure, computed either way alone, would be wrong for what the
other question actually asks.
"""

from dataclasses import dataclass, field
from datetime import date


def _f(value):
    return float(value) if value is not None else 0.0


def twr_index(equity_curve):
    """Chain-linked return index, contributions removed. Starts at 1.0."""
    index = [1.0]
    previous_equity = None
    for point in equity_curve:
        if previous_equity is None or previous_equity <= 0:
            index.append(index[-1])
        else:
            daily_return = (_f(point.equity_krw) - _f(point.contributed_today_krw)) / previous_equity - 1
            index.append(index[-1] * (1 + daily_return))
        previous_equity = _f(point.equity_krw)
    return tuple(index[1:])  # drop the synthetic leading 1.0


def cagr_from_twr(index, days):
    if not index or days <= 0 or index[-1] <= 0:
        return None
    years = days / 365.0
    if years <= 0:
        return None
    return index[-1] ** (1 / years) - 1


def mdd_from_twr(index):
    """Max drawdown of the TWR index - never the raw equity curve.

    Raw equity understates drawdown when contributions keep arriving, and
    that is exactly the number a strategy holding 2x ETFs must not
    understate.
    """
    if not index:
        return None
    peak = index[0]
    worst = 0.0
    for value in index:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1)
    return -worst


def _xnpv(rate, cashflows):
    if rate <= -1:
        return float("inf")
    t0 = cashflows[0][0]
    return sum(
        amount / (1 + rate) ** ((when - t0).days / 365.0) for when, amount in cashflows
    )


def xirr(cashflows, tol=1e-7, max_iter=200):
    """Money-weighted return via bisection on the actual cashflow dates.

    ``cashflows`` is ``[(date, amount), ...]`` - negative for money going in
    (contributions), positive for the final liquidation value. Bisection
    rather than Newton because it never diverges, and this only ever runs
    once per backtest report.
    """
    if len(cashflows) < 2:
        return None
    if all(amount <= 0 for _, amount in cashflows) or all(
        amount >= 0 for _, amount in cashflows
    ):
        return None  # no sign change - not solvable

    low, high = -0.999, 10.0
    f_low, f_high = _xnpv(low, cashflows), _xnpv(high, cashflows)
    if f_low * f_high > 0:
        return None

    for _ in range(max_iter):
        mid = (low + high) / 2
        f_mid = _xnpv(mid, cashflows)
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


@dataclass(frozen=True)
class EquityPoint:
    date: date
    equity_krw: object
    equity_usd: object
    contributed_today_krw: object = 0


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: tuple = ()
    trades: tuple = ()
    decisions: tuple = ()
    metrics: dict = field(default_factory=dict)


def _trade_pnls(trades):
    return [_f(t.pnl_usd) for t in trades]


def summarize(equity_curve, trades, decisions, contributions):
    """Build the metrics dict from a completed run's raw logs."""
    index = twr_index(equity_curve)
    days = (equity_curve[-1].date - equity_curve[0].date).days if len(equity_curve) > 1 else 0

    cashflows = [(day, -_f(amount)) for day, amount in contributions]
    if equity_curve:
        cashflows.append((equity_curve[-1].date, _f(equity_curve[-1].equity_krw)))

    pnls = _trade_pnls(trades)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    rejections_by_rule = {}
    for decision in decisions:
        if not decision.approved:
            rule = decision.rejection.rule
            rejections_by_rule[rule] = rejections_by_rule.get(rule, 0) + 1

    attribution = {}
    for trade in trades:
        key = trade.mode or "unknown"
        bucket = attribution.setdefault(
            key, {"trade_count": 0, "pnl_usd": 0.0, "symbols": set()}
        )
        bucket["trade_count"] += 1
        bucket["pnl_usd"] += _f(trade.pnl_usd)
        bucket["symbols"].add(trade.symbol)
    for bucket in attribution.values():
        bucket["symbols"] = sorted(bucket["symbols"])

    return {
        "twr_cagr": cagr_from_twr(index, days),
        "irr": xirr(cashflows),
        "mdd": mdd_from_twr(index),
        "trade_count": len(trades),
        "win_rate": (len(wins) / len(pnls)) if pnls else None,
        "avg_win_usd": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_usd": (sum(losses) / len(losses)) if losses else None,
        "payoff": (
            (sum(wins) / len(wins)) / abs(sum(losses) / len(losses))
            if wins and losses
            else None
        ),
        "rejections_by_rule": rejections_by_rule,
        "attribution_by_mode": attribution,
        "final_equity_krw": equity_curve[-1].equity_krw if equity_curve else None,
        "total_contributed_krw": sum((amount for _, amount in contributions), 0),
    }
