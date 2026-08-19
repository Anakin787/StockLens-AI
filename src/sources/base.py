"""Holding sources.

A source turns some origin - the Toss account, the config file - into a list
of :class:`~src.models.Position`. Keeping the interface this narrow is what
lets the aggregator treat "held at Toss" and "held elsewhere" identically,
and is where a second brokerage would plug in later.
"""

from abc import ABC, abstractmethod


class HoldingSource(ABC):
    #: Value written to ``Position.source``.
    name = "unknown"

    @abstractmethod
    def fetch(self):
        """Return a list of Position objects. May be empty."""
        raise NotImplementedError
