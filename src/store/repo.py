"""Firestore persistence for snapshots and report history.

The Portfolio Value chart cannot be reconstructed after the fact - Toss
reports today's numbers, not last month's - so every report run appends one
snapshot document. Starting this early is the whole point.

Monetary values are stored as strings, not Firestore's native double: a
double is a float and would reintroduce exactly the rounding drift the
Decimal pipeline removes. Read them back through Decimal.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore

from src.models import to_decimal
from src.strategy.base import DailyUsage

#: Ranges offered by the dashboard's chart selector.
RANGE_DAYS = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "ALL": None,
}


def _text(value):
    return None if value is None else str(value)


def _json_safe(value):
    """Recursively coerce a value to Firestore-storable types.

    ``signal.meta`` is free-form strategy output and often carries
    ``Decimal`` (e.g. an RSI reading) - a type Firestore's client rejects
    outright, unlike SQLite where it went through ``json.dumps(default=str)``.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _now(precise=False):
    now = datetime.now()
    return now.isoformat() if precise else now.replace(microsecond=0).isoformat()


class Store:
    def __init__(self, client=None):
        self.client = client or firestore.Client()

    # ------------------------------------------------------------- snapshots

    def save_snapshot(self, snapshot, ts=None):
        """Write one snapshot, keyed by ``ts`` so re-runs overwrite in place."""
        ts = ts or _now()
        doc = self.client.collection("snapshots").document(ts)
        doc.set(
            {
                "ts": ts,
                "total_krw": _text(snapshot.total_krw),
                "purchase_krw": _text(snapshot.purchase_krw),
                "profit_krw": _text(snapshot.profit_krw),
                "profit_rate": _text(snapshot.profit_rate),
                "profit_after_cost_krw": _text(snapshot.profit_after_cost_krw),
                "profit_rate_after_cost": _text(snapshot.profit_rate_after_cost),
                "daily_profit_krw": _text(snapshot.daily_profit_krw),
                "daily_profit_rate": _text(snapshot.daily_profit_rate),
                "exchange_rate": _text(snapshot.exchange_rate),
                "has_unconverted_fx": bool(snapshot.has_unconverted_fx),
            }
        )

        positions = doc.collection("positions")
        for existing in positions.stream():
            existing.reference.delete()
        for position in snapshot.positions:
            positions.document().set(
                {
                    "symbol": position.symbol,
                    "name": position.name,
                    "market_country": position.market_country,
                    "currency": position.currency,
                    "quantity": _text(position.quantity),
                    "last_price": _text(position.last_price),
                    "avg_price": _text(position.avg_purchase_price),
                    "market_value": _text(position.evaluation),
                    "profit_loss": _text(position.profit_loss),
                    "profit_rate": _text(position.profit_rate),
                    "daily_profit_loss": _text(position.daily_profit_loss),
                    "source": position.source,
                }
            )
        return ts

    def history(self, range_key="3M"):
        """Return snapshot rows for the chart, oldest first."""
        days = RANGE_DAYS.get(str(range_key).upper(), 90)
        query = self.client.collection("snapshots")
        if days is not None:
            since = (datetime.now() - timedelta(days=days)).replace(microsecond=0)
            query = query.where("ts", ">=", since.isoformat())
        query = query.order_by("ts")

        return [
            {
                "ts": doc.get("ts"),
                "total_krw": to_decimal(doc.get("total_krw"), default=0),
                "profit_krw": to_decimal(doc.get("profit_krw"), default=0),
                "profit_rate": to_decimal(doc.get("profit_rate"), default=0),
            }
            for doc in query.stream()
        ]

    def latest_snapshot(self):
        docs = list(
            self.client.collection("snapshots")
            .order_by("ts", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )
        if not docs:
            return None
        return docs[0].to_dict()

    def snapshot_count(self):
        return self.client.collection("snapshots").count().get()[0][0].value

    # ------------------------------------------------------ name overrides

    def symbol_names(self):
        """Return {symbol: display name} for every override."""
        return {
            doc.id: doc.get("name")
            for doc in self.client.collection("symbol_overrides").stream()
        }

    def set_symbol_name(self, symbol, name):
        """Store a display name, or clear it when name is blank."""
        name = (name or "").strip()
        doc = self.client.collection("symbol_overrides").document(symbol)
        if not name:
            doc.delete()
            return None
        doc.set({"name": name, "updated_at": _now()})
        return name

    # --------------------------------------------------------------- reports

    def save_report(self, page_id, title=None, url=None, ai_comment=None, ts=None):
        ts = ts or _now()
        self.client.collection("reports").document(page_id).set(
            {"ts": ts, "title": title, "url": url, "ai_comment": ai_comment}
        )
        return ts

    def recent_reports(self, limit=20):
        query = (
            self.client.collection("reports")
            .order_by("ts", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [{"page_id": doc.id, **doc.to_dict()} for doc in query.stream()]

    # --------------------------------------------------------------- signals

    def save_decision(self, decision, ts=None):
        """Record one risk-gate decision and return its Firestore doc id.

        Accepted and rejected signals go into the same collection so the
        audit trail reads in one pass; a rejection additionally carries the
        rule that stopped it, inlined on the same document. Called for every
        signal, not only the ones that trade.
        """
        signal = decision.signal
        ts = ts or _now(precise=True)

        data = {
            "ts": ts,
            "strategy": signal.strategy,
            "symbol": signal.symbol,
            "side": signal.side,
            "order_type": signal.order_type,
            "quantity": _text(signal.quantity),
            "amount": _text(signal.amount),
            "limit_price": _text(signal.limit_price),
            "currency": signal.currency,
            "reason": signal.reason,
            "payload": _json_safe(signal.meta) if signal.meta else None,
            "outcome": "accepted" if decision.approved else "rejected",
        }
        if decision.rejection is not None:
            data["rejection"] = {
                "rule": decision.rejection.rule,
                "detail": decision.rejection.detail,
            }

        _, doc_ref = self.client.collection("signals").add(data)
        return doc_ref.id

    def recent_signals(self, limit=50):
        """Signals newest first, each with the rule that rejected it, if any."""
        query = (
            self.client.collection("signals")
            .order_by("ts", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        rows = []
        for doc in query.stream():
            data = doc.to_dict()
            rejection = data.pop("rejection", None) or {}
            rows.append(
                {
                    "id": doc.id,
                    **data,
                    "reject_rule": rejection.get("rule"),
                    "reject_detail": rejection.get("detail"),
                }
            )
        return rows

    # ---------------------------------------------------------------- orders

    def daily_usage(self, day=None):
        """Today's order count and total notional, for the risk gate.

        Counts what was actually sent, so a rejected signal never consumes
        budget. Paper orders are counted too: a paper run that would have
        blown the daily limit should say so rather than look clean.

        Summed in Python rather than via a Firestore aggregation query - the
        order volume here is small enough that this is simpler, and it keeps
        the total on Decimal instead of a server-side float.
        """
        day = day or datetime.now().strftime("%Y-%m-%d")
        next_day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
        query = (
            self.client.collection("orders")
            .where("ts", ">=", day)
            .where("ts", "<", next_day)
        )

        count = 0
        total = Decimal(0)
        for doc in query.stream():
            data = doc.to_dict()
            if data.get("status") == "rejected":
                continue
            count += 1
            total += to_decimal(data.get("notional_krw"), default=0) or Decimal(0)

        return DailyUsage(order_count=count, notional_krw=total.quantize(Decimal("1")))

    def save_order(self, intent, signal_id=None, status="pending", mode="paper", ts=None):
        """Record an order before it is sent.

        Written ahead of the request on purpose: if the response never
        arrives, the attempt still left a trace, and the next run finds the
        client_order_id already taken rather than placing the order again.
        """
        ts = ts or _now(precise=True)
        doc = self.client.collection("orders").document(intent.client_order_id)
        try:
            doc.create(
                {
                    "signal_id": signal_id,
                    "ts": ts,
                    "strategy": intent.strategy,
                    "symbol": intent.symbol,
                    "side": intent.side,
                    "order_type": intent.order_type,
                    "quantity": _text(intent.quantity),
                    "amount": _text(intent.amount),
                    "price": _text(intent.limit_price),
                    "currency": intent.currency,
                    "notional_krw": _text(intent.notional_krw),
                    "status": status,
                    "mode": mode,
                    "order_id": None,
                    "error_code": None,
                    "updated_at": ts,
                }
            )
        except AlreadyExists:
            pass
        return ts

    def update_order(self, client_order_id, **fields):
        """Patch an order document. Unknown columns are refused, not ignored."""
        allowed = {"order_id", "status", "error_code", "signal_id"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"수정할 수 없는 컬럼입니다: {sorted(unknown)}")
        if not fields:
            return None

        fields["updated_at"] = _now(precise=True)
        self.client.collection("orders").document(client_order_id).update(fields)
        return fields["updated_at"]

    def order_by_client_id(self, client_order_id):
        doc = self.client.collection("orders").document(client_order_id).get()
        if not doc.exists:
            return None
        return {"client_order_id": doc.id, **doc.to_dict()}

    def recent_orders(self, limit=50):
        query = (
            self.client.collection("orders")
            .order_by("ts", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        return [{"client_order_id": doc.id, **doc.to_dict()} for doc in query.stream()]
