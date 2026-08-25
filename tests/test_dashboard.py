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
    assert "M7 Terminal" in response.text


def test_holdings_expose_cost_basis_and_krw_profit(client):
    """The invested amount must be visible next to the current value."""
    positions = {p["symbol"]: p for p in client.get("/api/holdings").json()["positions"]}

    krw = positions["005930"]
    assert krw["cost_krw"] == 6500000.0
    assert krw["value_krw"] == 7200000.0
    assert krw["profit_krw"] == pytest.approx(700000.0)

    # The USD position converts its cost at the purchase-time rate (1300),
    # not today's, so cost_krw reflects what was actually spent.
    usd = positions["AAPL"]
    assert usd["cost_krw"] == pytest.approx(1553 * 1300)


def test_holdings_expose_fx_pnl_for_foreign_positions_with_a_purchase_rate(client):
    positions = {p["symbol"]: p for p in client.get("/api/holdings").json()["positions"]}

    # KRW position - no currency exposure, no FX P&L.
    assert positions["005930"]["fx_pnl_krw"] is None

    # AAPL: bought at 1300, today's rate is 1342.5 - the won weakened, so the
    # unchanged 1553 USD cost basis is worth more in KRW now.
    assert positions["AAPL"]["fx_pnl_krw"] == pytest.approx(1553 * (1342.5 - 1300))


def test_overview_totals_the_fx_pnl_across_positions(client):
    overview = client.get("/api/overview").json()
    assert overview["fx_pnl_krw"] == pytest.approx(1553 * (1342.5 - 1300))


def test_position_costs_sum_to_the_portfolio_total(client):
    data = client.get("/api/holdings").json()["positions"]
    overview = client.get("/api/overview").json()

    assert sum(p["cost_krw"] for p in data) == pytest.approx(overview["purchase_krw"])
    assert sum(p["value_krw"] for p in data) == pytest.approx(overview["total_krw"])


def test_rename_persists_and_shows_up_immediately(client):
    """Renaming must bypass the 60s cache, or the edit looks like it failed."""
    assert client.get("/api/holdings").json()["positions"][0]["name"] == "삼성전자"

    response = client.put("/api/holdings/005930/name", json={"name": "삼성전자 (메인)"})
    assert response.status_code == 200
    assert response.json()["name"] == "삼성전자 (메인)"

    names = {p["symbol"]: p["name"] for p in client.get("/api/holdings").json()["positions"]}
    assert names["005930"] == "삼성전자 (메인)"


def test_blank_name_clears_the_override(client):
    client.put("/api/holdings/AAPL/name", json={"name": "애플"})
    assert client.put("/api/holdings/AAPL/name", json={"name": "  "}).json()["name"] is None

    names = {p["symbol"]: p["name"] for p in client.get("/api/holdings").json()["positions"]}
    assert names["AAPL"] == "Apple Inc."   # back to the API-supplied name


def test_rename_rejects_a_malformed_symbol(client):
    assert client.put("/api/holdings/..%2Fetc/name", json={"name": "x"}).status_code in (404, 422)


def test_rename_rejects_an_overlong_name(client):
    assert client.put("/api/holdings/005930/name",
                      json={"name": "x" * 200}).status_code == 422


def test_overrides_survive_a_new_service_on_the_same_db(service):
    """The Notion report reads the same overrides, so they must be in the DB."""
    service.store.set_symbol_name("005930", "삼성전자 (메인)")

    from src.store.repo import Store

    assert Store(service.config.db_path).symbol_names()["005930"] == "삼성전자 (메인)"
