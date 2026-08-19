"""Config loading: symbol normalisation, v1 compatibility, credential order."""

import os
import textwrap
from decimal import Decimal

import pytest

from src.config import load_config, mask_secret, normalize_symbol
from src.toss.errors import TossConfigError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "env-id")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "env-secret")


def write_config(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("005930.KS", "005930"),
        ("005930.KQ", "005930"),
        ("005930.ks", "005930"),
        ("005930", "005930"),
        ("AAPL", "AAPL"),
        ("BRK.B", "BRK.B"),  # a real dot in a US ticker must survive
        (None, None),
    ],
)
def test_normalize_symbol(raw, expected):
    assert normalize_symbol(raw) == expected


def test_env_credentials_win_over_config(tmp_path):
    path = write_config(
        tmp_path,
        """
        toss:
          client_id: "file-id"
          client_secret: "file-secret"
        """,
    )
    config = load_config(path, load_env=False)

    assert config.toss.client_id == "env-id"
    assert config.toss.client_secret == "env-secret"


def test_missing_credentials_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("TOSS_CLIENT_SECRET")
    path = write_config(tmp_path, "toss: {}\n")

    with pytest.raises(TossConfigError) as excinfo:
        load_config(path, load_env=False)
    assert "TOSS_CLIENT_SECRET" in str(excinfo.value)


def test_config_secret_is_accepted_with_a_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("TOSS_CLIENT_SECRET")
    path = write_config(
        tmp_path,
        """
        toss:
          client_secret: "file-secret"
        """,
    )
    with pytest.warns(UserWarning, match="TOSS_CLIENT_SECRET"):
        config = load_config(path, load_env=False)
    assert config.toss.client_secret == "file-secret"


def test_v1_portfolio_stocks_still_loads(tmp_path):
    path = write_config(
        tmp_path,
        """
        portfolio:
          stocks:
            - symbol: "005930.KS"
              qty: 20
              avg_price: 70000
            - symbol: "AAPL"
              qty: 10
              avg_price: 180.0
              avg_exchange_rate: 1300.0
        """,
    )
    with pytest.warns(UserWarning, match="portfolio.manual"):
        config = load_config(path, load_env=False)

    assert [h.symbol for h in config.manual_holdings] == ["005930", "AAPL"]
    assert config.manual_holdings[0].qty == Decimal("20")
    assert config.manual_holdings[1].avg_exchange_rate == Decimal("1300.0")


def test_manual_schema_needs_no_warning(tmp_path, recwarn):
    path = write_config(
        tmp_path,
        """
        portfolio:
          manual:
            - symbol: "000660"
              qty: 10
              avg_price: 180000
        """,
    )
    config = load_config(path, load_env=False)

    assert config.manual_holdings[0].symbol == "000660"
    assert not [w for w in recwarn if "portfolio.manual" in str(w.message)]


def test_static_asset_without_symbol_requires_price(tmp_path):
    path = write_config(
        tmp_path,
        """
        portfolio:
          manual:
            - name: "금 현물"
              qty: 1
              avg_price: 5000000
        """,
    )
    with pytest.raises(TossConfigError, match="price"):
        load_config(path, load_env=False)


def test_static_asset_with_price_is_accepted(tmp_path):
    path = write_config(
        tmp_path,
        """
        portfolio:
          manual:
            - name: "금 현물"
              qty: 1
              avg_price: 5000000
              price: 5400000
              currency: "KRW"
        """,
    )
    holding = load_config(path, load_env=False).manual_holdings[0]

    assert holding.is_static
    assert holding.price == Decimal("5400000")


def test_amounts_are_decimal_not_float(tmp_path):
    path = write_config(
        tmp_path,
        """
        portfolio:
          manual:
            - symbol: "AAPL"
              qty: 3
              avg_price: 0.1
        """,
    )
    holding = load_config(path, load_env=False).manual_holdings[0]

    assert isinstance(holding.avg_price, Decimal)
    assert holding.avg_price * 3 == Decimal("0.3")


def test_missing_config_file_still_loads_from_env():
    config = load_config("does-not-exist.yaml", load_env=False)

    assert config.toss.client_id == "env-id"
    assert config.manual_holdings == []


def test_notion_placeholder_is_not_configured(tmp_path):
    path = write_config(
        tmp_path,
        """
        notion:
          token: "secret_YOUR_NOTION_TOKEN_HERE"
          database_id: "abc"
        """,
    )
    assert not load_config(path, load_env=False).notion.is_configured


def test_secret_masking():
    assert mask_secret("tssk_live_abcdefghijkl") == "tssk...ijkl"
    assert mask_secret("short") == "*****"
    assert mask_secret("") == ""


def test_toss_config_repr_hides_the_secret(tmp_path):
    config = load_config("does-not-exist.yaml", load_env=False)
    assert "env-secret" not in repr(config.toss)


def test_placeholder_credentials_are_reported_clearly(monkeypatch):
    """A freshly copied .env must fail with 'still a placeholder', not a 401."""
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "PASTE_YOUR_TOSS_CLIENT_SECRET")

    with pytest.raises(TossConfigError) as excinfo:
        load_config("does-not-exist.yaml", load_env=False)

    message = str(excinfo.value)
    assert "TOSS_CLIENT_SECRET" in message
    assert "플레이스홀더" in message


def test_placeholder_ai_key_is_treated_as_absent(monkeypatch):
    """The AI key is optional - a placeholder must not block the report."""
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "PASTE_YOUR_GOOGLE_AI_KEY")

    config = load_config("does-not-exist.yaml", load_env=False)

    assert config.analyst.api_key is None


def test_real_credentials_are_not_mistaken_for_placeholders(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "tssk_live_realvalue")

    config = load_config("does-not-exist.yaml", load_env=False)

    assert config.toss.client_secret == "tssk_live_realvalue"
