"""Prompt-building helpers in src/analyst.py - no network, no genai client."""

from decimal import Decimal

from src.analyst import Analyst, _keyword_news_str, _savings_str
from src.config import AnalystConfig, AppConfig, ManualHolding, NotionConfig, SavingsPlan, TossConfig


def test_savings_str_formats_each_plan():
    plans = [SavingsPlan(name="청년미래적금", monthly_krw=Decimal("500000"), kind="적금")]
    assert _savings_str(plans) == "- 청년미래적금 (적금): 500,000 KRW/month"


def test_savings_str_is_none_when_empty():
    assert _savings_str([]) == "None"
    assert _savings_str(None) == "None"


def test_keyword_news_str_lists_headlines_per_keyword():
    news_data = {
        "keywords": {
            "삼성전자": [{"title": "A"}, {"title": "B"}],
            "환율": [],  # empty sections are skipped
        }
    }
    result = _keyword_news_str(news_data)

    assert "- 삼성전자:" in result
    assert "  - A" in result
    assert "환율" not in result


def test_keyword_news_str_is_none_without_any_keyword_news():
    assert _keyword_news_str({}) == "None"
    assert _keyword_news_str({"keywords": {}}) == "None"


def _app_config(savings_plans=()):
    return AppConfig(
        toss=TossConfig(client_id="cid", client_secret="sec"),
        notion=NotionConfig(token="secret_real", database_id="db"),
        analyst=AnalystConfig(api_key=None),
        manual_holdings=[ManualHolding(symbol="AAPL", qty=Decimal("1"), avg_price=Decimal("1"))],
        savings_plans=list(savings_plans),
    )


def test_analyst_picks_up_savings_plans_from_app_config():
    config = _app_config(savings_plans=[SavingsPlan(name="청년미래적금", monthly_krw=Decimal("500000"))])
    analyst = Analyst(config)

    assert analyst.savings_plans[0].name == "청년미래적금"


def test_analyst_picks_up_savings_plans_from_a_raw_mapping():
    raw = {
        "google_ai": {},
        "portfolio": {"savings": [{"name": "청년미래적금", "monthly_krw": 500000}]},
    }
    analyst = Analyst(raw)

    assert analyst.savings_plans[0].name == "청년미래적금"
    assert analyst.savings_plans[0].monthly_krw == Decimal("500000")


def test_analyst_without_an_api_key_needs_no_savings_data_to_report_unconfigured():
    analyst = Analyst(_app_config())
    assert analyst.analyze_portfolio(snapshot=None, news_data={}) == (
        "AI Analyst is not configured (Missing API Key)."
    )
