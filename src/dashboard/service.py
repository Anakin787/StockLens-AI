"""Data assembly and caching for the dashboard.

The cache is not an optimisation - it is a correctness requirement. The
ACCOUNT rate limit group allows one request per second, so a couple of
browser tabs polling every few seconds would trip 429 immediately. The server
owns one cached copy and every tab reads that.
"""

import threading
import time
from datetime import datetime
from decimal import Decimal

from src.pipeline import PortfolioService, apply_name_overrides
from src.store.repo import Store
from src.toss.errors import TossError

#: Seconds each kind of data stays fresh. Holdings move slowly enough that a
#: minute is imperceptible; the calendar changes a couple of times a day.
TTL_PORTFOLIO = 60
TTL_MARKET_STATUS = 300


class _Cached:
    def __init__(self, ttl):
        self.ttl = ttl
        self.value = None
        self.error = None
        self.fetched_at = 0.0
        self.lock = threading.Lock()

    def get(self, loader):
        with self.lock:
            if self.value is not None and time.monotonic() - self.fetched_at < self.ttl:
                return self.value, self.error
            try:
                self.value = loader()
                self.error = None
            except TossError as exc:
                # Keep serving the stale value; the UI shows the error banner
                # rather than going blank on one transient failure.
                self.error = str(exc)
            self.fetched_at = time.monotonic()
            return self.value, self.error

    def invalidate(self):
        with self.lock:
            self.fetched_at = 0.0


def _num(value):
    """JSON-safe number. Decimal is not serialisable and float loses won."""
    if value is None:
        return None
    return float(value)


class DashboardService:
    def __init__(self, config):
        self.config = config
        self.store = Store(config.db_path)
        self.portfolio = PortfolioService(config)
        self._snapshot = _Cached(TTL_PORTFOLIO)
        self._status = _Cached(TTL_MARKET_STATUS)
        self.last_sync = None

    def _load_snapshot(self):
        snapshot = self.portfolio.snapshot()
        apply_name_overrides(snapshot, self.store.symbol_names())
        self.last_sync = datetime.now()
        return snapshot

    def rename_symbol(self, symbol, name):
        """Set or clear a display name, then refresh so the UI shows it."""
        stored = self.store.set_symbol_name(symbol, name)
        self._snapshot.invalidate()
        return stored

    def snapshot(self):
        return self._snapshot.get(self._load_snapshot)

    def overview(self):
        snapshot, error = self.snapshot()
        status, _ = self._status.get(self.portfolio.market_status)

        if snapshot is None:
            return {"error": error or "포트폴리오를 불러오지 못했습니다.", "ready": False}

        buying_power = snapshot.buying_power or {}
        return {
            "ready": True,
            "error": error,
            "total_krw": _num(snapshot.total_krw),
            "total_usd_equivalent": _num(snapshot.total_usd_equivalent),
            "purchase_krw": _num(snapshot.purchase_krw),
            "profit_krw": _num(snapshot.profit_krw),
            "profit_rate": _num(snapshot.profit_rate),
            "profit_after_cost_krw": _num(snapshot.profit_after_cost_krw),
            "profit_rate_after_cost": _num(snapshot.profit_rate_after_cost),
            "daily_profit_krw": _num(snapshot.daily_profit_krw),
            "daily_profit_rate": _num(snapshot.daily_profit_rate),
            "exchange_rate": _num(snapshot.exchange_rate),
            "has_unconverted_fx": snapshot.has_unconverted_fx,
            "fx_pnl_krw": _num(snapshot.total_fx_pnl_krw),
            "buying_power": {
                "KRW": _num(_decimal_or_none(buying_power.get("KRW"))),
                "USD": _num(_decimal_or_none(buying_power.get("USD"))),
            },
            "warnings": snapshot.warnings,
            "market_status": _market_status(status),
        }

    def holdings(self):
        snapshot, error = self.snapshot()
        if snapshot is None:
            return {"error": error, "positions": []}

        total = snapshot.total_krw or Decimal("1")
        positions = []
        for position in snapshot.positions:
            value_krw = snapshot.evaluation_krw(position)
            cost_krw = snapshot.cost_krw(position)
            positions.append(
                {
                    "symbol": position.symbol,
                    "name": position.name,
                    "market_country": position.market_country,
                    "currency": position.currency,
                    "quantity": _num(position.quantity),
                    "last_price": _num(position.last_price),
                    "avg_price": _num(position.avg_purchase_price),
                    "value_krw": _num(value_krw),
                    "cost_krw": _num(cost_krw),
                    "profit_krw": _num(value_krw - cost_krw),
                    "fx_pnl_krw": _num(snapshot.fx_pnl_krw(position)),
                    "profit_loss": _num(position.profit_loss),
                    "profit_rate": _num(position.profit_rate),
                    "daily_profit_rate": _num(position.daily_profit_rate),
                    "weight": _num(value_krw / total) if total else 0,
                    "source": position.source,
                }
            )
        positions.sort(key=lambda item: item["value_krw"] or 0, reverse=True)
        return {"error": error, "positions": positions}

    def history(self, range_key="3M"):
        rows = self.store.history(range_key)
        return {
            "range": range_key,
            "points": [
                {
                    "ts": row["ts"],
                    "total_krw": _num(row["total_krw"]),
                    "profit_rate": _num(row["profit_rate"]),
                }
                for row in rows
            ],
            "total_snapshots": self.store.snapshot_count(),
        }

    def allocation(self, by="market"):
        snapshot, error = self.snapshot()
        if snapshot is None:
            return {"error": error, "segments": []}

        buckets = snapshot.allocation(by)
        total = sum(buckets.values()) or Decimal("1")
        segments = [
            {
                "key": key,
                "label": _allocation_label(key, by),
                "value_krw": _num(value),
                "share": _num(value / total),
            }
            for key, value in buckets.items()
        ]
        segments.sort(key=lambda item: item["value_krw"], reverse=True)
        return {"by": by, "segments": segments}

    def reports(self, limit=20):
        return {"reports": self.store.recent_reports(limit)}

    def health(self):
        snapshot, error = self.snapshot()
        return {
            "connected": snapshot is not None and not error,
            "error": error,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "snapshot_count": self.store.snapshot_count(),
            # Phase 2 wires these up; the UI greys the controls out until then.
            "trading_enabled": False,
        }

    def settings(self):
        """Read-only view of the effective config, with secrets masked."""
        from src.config import mask_secret

        return {
            "toss": {
                "client_id": mask_secret(self.config.toss.client_id),
                "client_secret": mask_secret(self.config.toss.client_secret),
                "account_no": self.config.toss.account_no,
                "base_url": self.config.toss.base_url,
            },
            "notion": {
                "configured": self.config.notion.is_configured,
                "database_id": mask_secret(self.config.notion.database_id),
            },
            "analyst": {
                "model": self.config.analyst.model,
                "configured": bool(self.config.analyst.api_key),
            },
            "manual_holdings": len(self.config.manual_holdings),
            "db_path": self.config.db_path,
        }

    def close(self):
        self.portfolio.close()


def _decimal_or_none(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _allocation_label(key, by):
    if by == "currency":
        return key
    return {"KR": "KRX (Domestic)", "US": "US (Foreign)"}.get(key, key or "Other")


#: Calendar keys that denote a tradable session, mapped to the kind reported
#: to the UI. Ordered by precedence: the windows overlap at the boundary (KR
#: afterMarket starts the minute regularMarket ends), and the more significant
#: session should win.
_SESSION_KINDS = (
    ("regularMarket", "regular"),
    ("dayMarket", "day"),
    ("preMarket", "pre"),
    ("afterMarket", "after"),
)


def _market_status(status):
    """Reduce the calendar payload to the live session per market."""
    result = {}
    for country in ("KR", "US"):
        calendar = (status or {}).get(country)
        session = _live_session(calendar)
        result[country] = {
            # "open" stays regular-session-only so anything reading it keeps
            # meaning the main session; extended hours are reported via
            # "session" instead.
            "open": session == "regular",
            "session": session,
            "known": calendar is not None,
        }
    return result


def _live_session(calendar):
    """Which session is live right now: regular/day/pre/after, or None.

    The calendar shape varies by market and nests sessions arbitrarily deep
    (KR wraps them in "integrated"; a US session can start on
    previousBusinessDay and still be live now in KST), so this recurses
    through the whole tree rather than assuming a fixed layout.
    """
    if not calendar:
        return None

    now = datetime.now().astimezone()
    found = set()

    def scan(node):
        if not isinstance(node, (dict, list)):
            return
        if isinstance(node, list):
            for item in node:
                scan(item)
            return
        for key, value in node.items():
            if isinstance(value, dict):
                kind = dict(_SESSION_KINDS).get(key)
                if kind and _contains(value, now):
                    found.add(kind)
            scan(value)

    scan(calendar)

    for _, kind in _SESSION_KINDS:
        if kind in found:
            return kind
    return None


def _contains(session, now):
    """True when ``now`` falls inside this session's window."""
    start = _parse_dt(
        session.get("startTime") or session.get("startDateTime") or session.get("start")
    )
    end = _parse_dt(
        session.get("endTime") or session.get("endDateTime") or session.get("end")
    )
    return bool(start and end and start <= now <= end)


def _parse_dt(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed
