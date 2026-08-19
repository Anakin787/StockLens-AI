"""Assembles the Toss client, holding sources and aggregator.

Shared by main.py (report run) and the dashboard, so both read the portfolio
the same way and, importantly, through the same cached OAuth token.
"""

import dataclasses

from src.models import SOURCE_TOSS
from src.portfolio import HoldingsAggregator
from src.sources.manual_source import ManualSource
from src.sources.toss_source import TossSource
from src.toss.account import AccountApi
from src.toss.client import TossClient
from src.toss.errors import TossApiError
from src.toss.market import MarketApi

#: Warning flags Toss reports per stock. Anything true here is a fact about
#: the position and belongs above the AI commentary in any report.
WARNING_LABELS = {
    "liquidation": "정리매매",
    "shortTermOverheated": "단기과열",
    "investmentWarning": "투자경고",
    "investmentRisk": "투자위험",
    "investmentCaution": "투자주의",
    "volatilityInterruption": "변동성완화장치(VI) 발동",
    "preemptiveRight": "신주인수권",
}


def apply_name_overrides(snapshot, overrides):
    """Swap in user-edited display names.

    Applied to the snapshot rather than inside a source, so the Notion report
    and the dashboard show the same name for a ticker.
    """
    if not overrides:
        return snapshot
    snapshot.positions = [
        dataclasses.replace(position, name=overrides[position.symbol])
        if position.symbol in overrides
        else position
        for position in snapshot.positions
    ]
    return snapshot


def build_client(config, allow_write=False):
    return TossClient(
        config.toss.client_id,
        config.toss.client_secret,
        base_url=config.toss.base_url,
        token_cache=config.toss.token_cache,
        allow_write=allow_write,
    )


class PortfolioService:
    """One place that knows how to turn config + API into a snapshot."""

    def __init__(self, config, client=None):
        self.config = config
        self.client = client or build_client(config)
        self.account = AccountApi(self.client, account_no=config.toss.account_no)
        self.market = MarketApi(self.client)

    def snapshot(self, include_warnings=True, include_buying_power=True):
        aggregator = HoldingsAggregator(
            sources=[
                TossSource(self.account),
                ManualSource(self.market, self.config.manual_holdings),
            ],
            market_api=self.market,
        )
        snapshot = aggregator.build()

        if include_warnings:
            snapshot.warnings = self._collect_warnings(snapshot)
        if include_buying_power:
            snapshot.buying_power = self._collect_buying_power()
        return snapshot

    def _collect_warnings(self, snapshot):
        """Look up trading warnings for held symbols.

        Best effort - a failure here degrades the report, it does not fail it.
        """
        labels = []
        for position in snapshot.positions:
            if not position.symbol or position.market_country != "KR":
                continue
            try:
                result = self.market.warnings(position.symbol)
            except TossApiError:
                continue
            if not result:
                continue
            for key, label in WARNING_LABELS.items():
                if result.get(key):
                    name = position.name or position.symbol
                    labels.append(f"{name} · {label}")
        return labels

    def _collect_buying_power(self):
        powers = {}
        for currency in ("KRW", "USD"):
            try:
                result = self.account.buying_power(currency) or {}
                powers[currency] = result.get("cashBuyingPower")
            except TossApiError:
                powers[currency] = None
        return powers

    def market_status(self):
        """Open/closed per market for the dashboard header."""
        status = {}
        for country in ("KR", "US"):
            try:
                status[country] = self.market.market_calendar(country)
            except TossApiError:
                status[country] = None
        return status

    def close(self):
        self.client.close()
