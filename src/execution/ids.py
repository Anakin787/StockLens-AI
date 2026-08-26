"""Deterministic order identity.

``clientOrderId`` is the API's idempotency key, and design 3.3 item 3 asks
for it to be derived rather than random: if the same signal on the same day
always produces the same id, then a batch that runs twice, or a process that
restarts mid-run, re-sends an id the broker has already seen instead of
placing a second order. Randomness would make every retry a new order.
"""

import re

SEPARATOR = "-"

#: Anything outside this set is squashed, so a symbol or strategy name
#: carrying the separator cannot shift the field boundaries and collide with
#: a different signal's id.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.]+")

#: Toss accepts ids well beyond this, but a readable id is one a human can
#: match against a log line, so the variable parts are kept short.
MAX_PART = 24


def _clean(value, fallback="x"):
    cleaned = _UNSAFE.sub("_", str(value or "").strip())
    cleaned = cleaned.strip("_")[:MAX_PART]
    return cleaned or fallback


def make_client_order_id(strategy, symbol, day, seq):
    """Build the idempotency key for one order.

    ``day`` is a date or an ISO date string; ``seq`` distinguishes repeated
    orders for the same strategy and symbol within that day.
    """
    day = str(day)[:10]
    try:
        seq = int(seq)
    except (TypeError, ValueError):
        raise ValueError(f"seq는 정수여야 합니다: {seq!r}") from None
    if seq < 1:
        raise ValueError(f"seq는 1 이상이어야 합니다: {seq}")

    return SEPARATOR.join(
        (_clean(strategy, "strategy"), _clean(symbol, "symbol"), day, str(seq))
    )
