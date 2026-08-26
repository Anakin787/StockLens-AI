"""SQLite persistence for snapshots and report history.

The Portfolio Value chart cannot be reconstructed after the fact - Toss
reports today's numbers, not last month's - so every report run appends one
snapshot row. Starting this early is the whole point.
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

from src.models import to_decimal
from src.strategy.base import DailyUsage

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

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


class Store:
    def __init__(self, db_path):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self):
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_schema(self):
        parent = os.path.dirname(os.path.abspath(self.db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(SCHEMA_PATH, "r", encoding="utf-8") as handle:
            schema = handle.read()
        with self._connect() as connection:
            connection.executescript(schema)

    # ------------------------------------------------------------- snapshots

    def save_snapshot(self, snapshot, ts=None):
        """Append one snapshot. Idempotent per timestamp."""
        ts = ts or datetime.now().replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    ts, total_krw, purchase_krw, profit_krw, profit_rate,
                    profit_after_cost_krw, profit_rate_after_cost,
                    daily_profit_krw, daily_profit_rate,
                    exchange_rate, has_unconverted_fx
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    _text(snapshot.total_krw),
                    _text(snapshot.purchase_krw),
                    _text(snapshot.profit_krw),
                    _text(snapshot.profit_rate),
                    _text(snapshot.profit_after_cost_krw),
                    _text(snapshot.profit_rate_after_cost),
                    _text(snapshot.daily_profit_krw),
                    _text(snapshot.daily_profit_rate),
                    _text(snapshot.exchange_rate),
                    1 if snapshot.has_unconverted_fx else 0,
                ),
            )
            connection.executemany(
                """
                INSERT OR REPLACE INTO position_snapshots (
                    ts, symbol, name, market_country, currency, quantity,
                    last_price, avg_price, market_value, profit_loss,
                    profit_rate, daily_profit_loss, source
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        ts,
                        position.symbol,
                        position.name,
                        position.market_country,
                        position.currency,
                        _text(position.quantity),
                        _text(position.last_price),
                        _text(position.avg_purchase_price),
                        _text(position.evaluation),
                        _text(position.profit_loss),
                        _text(position.profit_rate),
                        _text(position.daily_profit_loss),
                        position.source,
                    )
                    for position in snapshot.positions
                ],
            )
        return ts

    def history(self, range_key="3M"):
        """Return snapshot rows for the chart, oldest first."""
        days = RANGE_DAYS.get(str(range_key).upper(), 90)
        query = "SELECT * FROM snapshots"
        params = ()
        if days is not None:
            since = (datetime.now() - timedelta(days=days)).replace(microsecond=0)
            query += " WHERE ts >= ?"
            params = (since.isoformat(),)
        query += " ORDER BY ts ASC"

        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            {
                "ts": row["ts"],
                "total_krw": to_decimal(row["total_krw"], default=0),
                "profit_krw": to_decimal(row["profit_krw"], default=0),
                "profit_rate": to_decimal(row["profit_rate"], default=0),
            }
            for row in rows
        ]

    def latest_snapshot(self):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def snapshot_count(self):
        with self._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    # ------------------------------------------------------ name overrides

    def symbol_names(self):
        """Return {symbol: display name} for every override."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol, name FROM symbol_overrides"
            ).fetchall()
        return {row["symbol"]: row["name"] for row in rows}

    def set_symbol_name(self, symbol, name):
        """Store a display name, or clear it when name is blank."""
        name = (name or "").strip()
        with self._connect() as connection:
            if not name:
                connection.execute(
                    "DELETE FROM symbol_overrides WHERE symbol = ?", (symbol,)
                )
                return None
            connection.execute(
                """
                INSERT OR REPLACE INTO symbol_overrides (symbol, name, updated_at)
                VALUES (?,?,?)
                """,
                (symbol, name, datetime.now().replace(microsecond=0).isoformat()),
            )
        return name

    # --------------------------------------------------------------- reports

    def save_report(self, page_id, title=None, url=None, ai_comment=None, ts=None):
        ts = ts or datetime.now().replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reports (page_id, ts, title, url, ai_comment)
                VALUES (?,?,?,?,?)
                """,
                (page_id, ts, title, url, ai_comment),
            )
        return ts

    def recent_reports(self, limit=20):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reports ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    # --------------------------------------------------------------- signals

    def save_decision(self, decision, ts=None):
        """Record one risk-gate decision and return its ``signals.id``.

        Accepted and rejected signals go into the same table so the audit
        trail reads in one pass; a rejection additionally writes the rule that
        stopped it. Called for every signal, not only the ones that trade.
        """
        signal = decision.signal
        ts = ts or datetime.now().replace(microsecond=0).isoformat()
        outcome = "accepted" if decision.approved else "rejected"

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO signals (
                    ts, strategy, symbol, side, order_type,
                    quantity, amount, limit_price, currency,
                    reason, payload, outcome
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    ts,
                    signal.strategy,
                    signal.symbol,
                    signal.side,
                    signal.order_type,
                    _text(signal.quantity),
                    _text(signal.amount),
                    _text(signal.limit_price),
                    signal.currency,
                    signal.reason,
                    json.dumps(signal.meta, ensure_ascii=False, default=str)
                    if signal.meta
                    else None,
                    outcome,
                ),
            )
            signal_id = cursor.lastrowid

            if decision.rejection is not None:
                connection.execute(
                    "INSERT INTO rejections (signal_id, rule, detail) VALUES (?,?,?)",
                    (
                        signal_id,
                        decision.rejection.rule,
                        decision.rejection.detail,
                    ),
                )
        return signal_id

    def recent_signals(self, limit=50):
        """Signals newest first, each with the rule that rejected it, if any."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, r.rule AS reject_rule, r.detail AS reject_detail
                FROM signals s
                LEFT JOIN rejections r ON r.signal_id = s.id
                ORDER BY s.ts DESC, s.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ---------------------------------------------------------------- orders

    def daily_usage(self, day=None):
        """Today's order count and total notional, for the risk gate.

        Counts what was actually sent, so a rejected signal never consumes
        budget. Paper orders are counted too: a paper run that would have
        blown the daily limit should say so rather than look clean.
        """
        day = day or datetime.now().strftime("%Y-%m-%d")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS n, COALESCE(SUM(CAST(notional_krw AS REAL)), 0) AS total
                FROM orders
                WHERE substr(ts, 1, 10) = ? AND status != 'rejected'
                """,
                (day,),
            ).fetchone()

        # SUM has to go through REAL - SQLite cannot add TEXT - so the total is
        # re-quantised to whole won here rather than being carried as a float.
        # Won-level precision is all a budget check needs.
        total = to_decimal(row["total"], default=0) or Decimal(0)
        return DailyUsage(
            order_count=row["n"], notional_krw=total.quantize(Decimal("1"))
        )

    def save_order(self, intent, signal_id=None, status="pending", mode="paper", ts=None):
        """Record an order before it is sent.

        Written ahead of the request on purpose: if the response never
        arrives, the attempt still left a trace, and the next run finds the
        client_order_id already taken rather than placing the order again.
        """
        ts = ts or datetime.now().replace(microsecond=0).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO orders (
                    client_order_id, signal_id, ts, strategy, symbol, side,
                    order_type, quantity, amount, price, currency,
                    notional_krw, status, mode, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    intent.client_order_id,
                    signal_id,
                    ts,
                    intent.strategy,
                    intent.symbol,
                    intent.side,
                    intent.order_type,
                    _text(intent.quantity),
                    _text(intent.amount),
                    _text(intent.limit_price),
                    intent.currency,
                    _text(intent.notional_krw),
                    status,
                    mode,
                    ts,
                ),
            )
        return ts

    def update_order(self, client_order_id, **fields):
        """Patch an order row. Unknown columns are refused, not ignored."""
        allowed = {"order_id", "status", "error_code", "signal_id"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"수정할 수 없는 컬럼입니다: {sorted(unknown)}")
        if not fields:
            return None

        fields["updated_at"] = datetime.now().replace(microsecond=0).isoformat()
        assignments = ", ".join(f"{column} = ?" for column in fields)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE orders SET {assignments} WHERE client_order_id = ?",
                (*fields.values(), client_order_id),
            )
        return fields["updated_at"]

    def order_by_client_id(self, client_order_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return dict(row) if row else None

    def recent_orders(self, limit=50):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM orders ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]
