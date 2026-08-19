"""Dashboard API against a stubbed Toss backend - no credentials, no network.

Also covers the cache, which is a correctness requirement rather than an
optimisation: the ACCOUNT rate limit group allows one request per second.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.config import AnalystConfig, AppConfig, ManualHolding, NotionConfig, TossConfig
from src.dashboard import api as dashboard_api
from src.dashboard.service import DashboardService
from decimal import Decimal

HOLDINGS = {
    "totalPurchaseAmount": {"krw": "6500000", "usd": "1553"},
    "marketValue": {"amount": {"krw": "7200000", "usd": "1785"}},
    "profitLoss": {"amount": {"krw": "700000"}, "rate": "0.1179"},
    "items": [
        {
            "symbol": "005930", "name": "삼성전자", "marketCountry": "KR",
            "currency": "KRW", "quantity": "100", "lastPrice": "72000",
            "averagePurchasePrice": "65000",
            "marketValue": {"purchaseAmount": "6500000", "amount": "7200000",
                            "amountAfterCost": "7050000"},
            "profitLoss": {"amount": "700000", "amountAfterCost": "550000",
                           "rate": "0.1077", "rateAfterCost": "0.0846"},
            "dailyProfitLoss": {"amount": "100000", "rate": "0.0141"},
        }
    ],
}


class StubClient:
    """Counts calls so the cache can be asserted on."""

    def __init__(self):
        self.calls = []

    def get(self, path, *, group=None, params=None, account_seq=None):
        self.calls.append(path)
        if path == "/api/v1/accounts":
            return [{"accountNo": "12345678901", "accountSeq": 1, "accountType": "BROKERAGE"}]
        if path == "/api/v1/holdings":
            return HOLDINGS
        if path == "/api/v1/buying-power":
            currency = (params or {}).get("currency")
            return {"currency": currency,
                    "cashBuyingPower": "5000000" if currency == "KRW" else "3500.5"}
        if path == "/api/v1/exchange-rate":
            return {"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1342.5"}
        if path == "/api/v1/prices":
            return [{"symbol": "AAPL", "lastPrice": "178.5", "currency": "USD"}]
        if path == "/api/v1/stocks":
            return [{"symbol": "AAPL", "name": "Apple Inc.",
                     "marketCountry": "US", "currency": "USD"}]
        if path.endswith("/warnings"):
            return {"volatilityInterruption": True}
        if path.startswith("/api/v1/market-calendar"):
            return {"sessions": []}
        raise AssertionError(f"unexpected path {path}")

    def close(self):
        pass


@pytest.fixture
def service():
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    config = AppConfig(
        toss=TossConfig(client_id="cid", client_secret="sec"),
        notion=NotionConfig(token="secret_real", database_id="db"),
        analyst=AnalystConfig(api_key=None),
        manual_holdings=[
            ManualHolding(symbol="AAPL", qty=Decimal("10"), avg_price=Decimal("155.3"),
                          avg_exchange_rate=Decimal("1300"))
        ],
        db_path=db,
    )
    stub = StubClient()
    from src.pipeline import PortfolioService

    svc = DashboardService.__new__(DashboardService)
    svc.config = config
    from src.store.repo import Store

    svc.store = Store(db)
    svc.portfolio = PortfolioService(config, client=stub)
    from src.dashboard.service import _Cached, TTL_MARKET_STATUS, TTL_PORTFOLIO

    svc._snapshot = _Cached(TTL_PORTFOLIO)
    svc._status = _Cached(TTL_MARKET_STATUS)
    svc.last_sync = None
    svc.stub = stub
    yield svc
    svc.close()


@pytest.fixture
def client(service, monkeypatch):
    monkeypatch.setattr(dashboard_api, "_service", service)
    return TestClient(dashboard_api.app)


def test_overview_combines_both_sources(client):
    data = client.get("/api/overview").json()

    assert data["ready"]
    # 7,200,000 KRW + 1,785 USD * 1342.5
    assert data["total_krw"] == pytest.approx(7200000 + 1785 * 1342.5)
    assert data["exchange_rate"] == 1342.5
    assert data["buying_power"] == {"KRW": 5000000.0, "USD": 3500.5}


def test_after_cost_profit_is_exposed(client):
    data = client.get("/api/overview").json()

    assert data["profit_after_cost_krw"] == 550000.0
    assert data["profit_rate_after_cost"] is not None


def test_daily_profit_is_exposed(client):
    assert client.get("/api/overview").json()["daily_profit_krw"] == 100000.0


def test_warnings_are_surfaced(client):
    warnings = client.get("/api/overview").json()["warnings"]

    assert any("VI" in w for w in warnings)
    assert any("삼성전자" in w for w in warnings)


def test_holdings_carry_source_badges_and_weights(client):
    positions = client.get("/api/holdings").json()["positions"]

    by_symbol = {p["symbol"]: p for p in positions}
    assert by_symbol["005930"]["source"] == "toss"
    assert by_symbol["AAPL"]["source"] == "manual"
    assert sum(p["weight"] for p in positions) == pytest.approx(1.0)


def test_allocation_by_market_and_currency(client):
    market = client.get("/api/allocation?by=market").json()
    currency = client.get("/api/allocation?by=currency").json()

    assert {s["key"] for s in market["segments"]} == {"KR", "US"}
    assert {s["key"] for s in currency["segments"]} == {"KRW", "USD"}
    assert sum(s["share"] for s in market["segments"]) == pytest.approx(1.0)


def test_history_is_empty_before_any_report_run(client):
    data = client.get("/api/history?range=3M").json()

    assert data["points"] == []
    assert data["total_snapshots"] == 0


def test_history_returns_saved_snapshots(client, service):
    snapshot, _ = service.snapshot()
    service.store.save_snapshot(snapshot, ts="2026-08-19T10:00:00")

    data = client.get("/api/history?range=ALL").json()

    assert len(data["points"]) == 1
    assert data["points"][0]["ts"] == "2026-08-19T10:00:00"


def test_invalid_range_is_rejected(client):
    assert client.get("/api/history?range=99Y").status_code == 422


def test_cache_prevents_hammering_the_account_endpoint(client, service):
    """Several tabs polling must not multiply upstream calls - ACCOUNT is 1 TPS."""
    for _ in range(5):
        client.get("/api/overview")
        client.get("/api/holdings")

    assert service.stub.calls.count("/api/v1/holdings") == 1


def test_settings_masks_credentials(client):
    data = client.get("/api/settings").json()

    assert "sec" not in data["toss"]["client_secret"] or "..." in data["toss"]["client_secret"]
    assert data["toss"]["client_secret"] != "sec"


def test_health_reports_trading_disabled_in_phase_1(client):
    data = client.get("/api/health").json()

    assert data["connected"] is True
    assert data["trading_enabled"] is False


def test_index_page_is_served(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "StockLens AI" in response.text
