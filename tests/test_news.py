"""portfolio_keywords() and NewsFetcher's keyword fan-out."""

from decimal import Decimal
from types import SimpleNamespace

from src.models import SOURCE_TOSS, PortfolioSnapshot, Position
from src.news import NewsFetcher, portfolio_keywords


def position(symbol, name):
    return Position(
        symbol=symbol,
        name=name,
        market_country="KR",
        currency="KRW",
        quantity=Decimal("1"),
        last_price=Decimal("1"),
        avg_purchase_price=Decimal("1"),
        source=SOURCE_TOSS,
    )


def test_portfolio_keywords_lists_distinct_held_names():
    snapshot = PortfolioSnapshot(positions=[position("005930", "삼성전자"), position("AAPL", "Apple Inc.")])
    assert portfolio_keywords(snapshot) == ["삼성전자", "Apple Inc."]


def test_portfolio_keywords_dedupes_and_skips_blank_names():
    snapshot = PortfolioSnapshot(
        positions=[position("005930", "삼성전자"), position("005930", "삼성전자"), position(None, "")]
    )
    assert portfolio_keywords(snapshot) == ["삼성전자"]


def test_portfolio_keywords_on_an_empty_portfolio():
    assert portfolio_keywords(PortfolioSnapshot(positions=[])) == []


class FakeEntry:
    def __init__(self, title, link, published, source_title):
        self.title = title
        self.link = link
        self.published = published
        self.source = {"title": source_title}


def test_fetch_daily_news_returns_a_section_per_keyword(monkeypatch):
    calls = []

    def fake_parse(url):
        calls.append(url)
        return SimpleNamespace(
            entries=[FakeEntry("한 종목 기사", "https://example.com/1", "2026-08-27", "Test Wire")]
        )

    monkeypatch.setattr("src.news.feedparser.parse", fake_parse)

    fetcher = NewsFetcher({"news": {"keywords": ["삼성전자", "Apple Inc."]}})
    result = fetcher.fetch_daily_news()

    # One call for "general" (경제) plus one per keyword.
    assert len(calls) == 3
    assert set(result["keywords"]) == {"삼성전자", "Apple Inc."}
    assert result["keywords"]["삼성전자"][0]["title"] == "한 종목 기사"
    assert result["keywords"]["삼성전자"][0]["source"] == "Test Wire"
