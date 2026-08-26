"""Errors for the historical-data layer.

Kept separate from ``src.toss.errors`` because a missing bar is not a Toss
failure - conflating the two would make a market-data gap look, in a log, like
the broker was unreachable, which points whoever is debugging it in the wrong
direction entirely.
"""


class MarketDataError(Exception):
    """Base class for anything in ``src.data``."""


class DataUnavailableError(MarketDataError):
    """A source or the cache could not produce what was asked for."""
