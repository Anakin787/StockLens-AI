"""Client-side rate limiting for the Toss Securities Open API.

Toss meters requests per (client x API group) in requests-per-second. The
published limits are listed below; the API also reports the live allowance in
the ``X-RateLimit-Limit`` response header, so ``RateLimiter.observe`` narrows
or widens a bucket as the server tells us about it.

Staying under the limit locally is cheaper than discovering it through 429s,
especially for ``ACCOUNT`` which allows only one request per second.
"""

import threading
import time

#: Published per-second limits, keyed by the Rate Limits Group that each
#: endpoint's documentation names. Order/conditional-order groups are listed
#: for completeness; Phase 1 is read-only and never touches them.
GROUP_LIMITS = {
    "AUTH": 5,
    "ACCOUNT": 1,
    "ASSET": 5,
    "STOCK": 5,
    "STOCK_ALL": 1,
    "STOCK_TRADING_TREND": 10,
    "MARKET_INFO": 3,
    "MARKET_DATA": 15,
    "MARKET_DATA_CHART": 20,
    "RANKING": 5,
    "MARKET_INDICATOR_PRICE": 10,
    "MARKET_INDICATOR": 10,
    "MARKET_INDICATOR_CHART": 5,
    "ORDER": 10,
    "ORDER_HISTORY": 5,
    "ORDER_INFO": 6,
    "CONDITIONAL_ORDER": 5,
    "CONDITIONAL_ORDER_HISTORY": 10,
}

#: Fallback for a group we do not know about (a new endpoint, say). Deliberately
#: conservative - one request per second will not trip anything.
DEFAULT_LIMIT = 1


class TokenBucket:
    """A single bucket refilling at ``rate`` tokens per second."""

    def __init__(self, rate, capacity=None, clock=time.monotonic, sleep=time.sleep):
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else rate)
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self):
        now = self._clock()
        elapsed = now - self._updated
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._updated = now

    def acquire(self):
        """Block until a token is available, then consume it."""
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                deficit = 1 - self._tokens
                wait = deficit / self.rate if self.rate > 0 else 1.0
            self._sleep(wait)

    def resize(self, rate):
        """Adjust the refill rate after the server reported its real limit."""
        rate = float(rate)
        if rate <= 0:
            return
        with self._lock:
            self._refill()
            self.rate = rate
            self.capacity = rate
            self._tokens = min(self._tokens, self.capacity)


class RateLimiter:
    """Holds one :class:`TokenBucket` per API group."""

    def __init__(self, limits=None, clock=time.monotonic, sleep=time.sleep):
        self._limits = dict(GROUP_LIMITS if limits is None else limits)
        self._clock = clock
        self._sleep = sleep
        self._buckets = {}
        self._lock = threading.Lock()

    def _bucket(self, group):
        with self._lock:
            bucket = self._buckets.get(group)
            if bucket is None:
                rate = self._limits.get(group, DEFAULT_LIMIT)
                bucket = TokenBucket(rate, clock=self._clock, sleep=self._sleep)
                self._buckets[group] = bucket
            return bucket

    def acquire(self, group):
        if group:
            self._bucket(group).acquire()

    def observe(self, group, headers):
        """Re-size a bucket from the ``X-RateLimit-Limit`` response header.

        Toss states the published numbers may change without notice, so the
        header is more trustworthy than our table.
        """
        if not group or not headers:
            return
        raw = headers.get("X-RateLimit-Limit")
        if raw is None:
            return
        try:
            limit = float(raw)
        except (TypeError, ValueError):
            return
        if limit > 0:
            self._bucket(group).resize(limit)
