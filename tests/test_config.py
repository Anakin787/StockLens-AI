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


def test_notion_token_prefers_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "ntn_fromenv")
    monkeypatch.setenv("NOTION_DATABASE_ID", "db-from-env")
    path = write_config(
        tmp_path,
        """
        notion:
          token: "secret_fromfile"
          database_id: "db-from-file"
        """,
    )
    notion = load_config(path, load_env=False).notion

    assert notion.token == "ntn_fromenv"
    assert notion.database_id == "db-from-env"
    assert notion.is_configured


def test_notion_needs_both_token_and_database(tmp_path):
    path = write_config(
        tmp_path,
        """
        notion:
          token: "ntn_real"
          database_id: "YOUR_DATABASE_ID"
        """,
    )
    # A valid token pointing at nothing fails later with a confusing 404, so
    # treat it as unconfigured up front.
    assert not load_config(path, load_env=False).notion.is_configured


def test_notion_placeholder_token_is_not_configured(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "PASTE_YOUR_NOTION_TOKEN")
    monkeypatch.setenv("NOTION_DATABASE_ID", "abc")

    assert not load_config("does-not-exist.yaml", load_env=False).notion.is_configured


def test_modern_ntn_token_is_accepted(monkeypatch):
    monkeypatch.setenv("NOTION_TOKEN", "ntn_1234567890abcdef")
    monkeypatch.setenv("NOTION_DATABASE_ID", "2f1a8b3c4d5e6f7a8b9c0d1e2f3a4b5c")

    assert load_config("does-not-exist.yaml", load_env=False).notion.is_configured


# ------------------------------------------------------------ trading (Phase 2)


def test_trading_is_off_unless_enabled(tmp_path):
    from src.config import _parse_trading

    trading = _parse_trading({})
    assert trading.enabled is False
    assert trading.strategies == []
    # Adding the trading code to the tree must not, by itself, start trading.


def test_trading_limits_become_risk_limits_as_decimal(tmp_path):
    from decimal import Decimal

    from src.config import _parse_trading

    trading = _parse_trading(
        {
            "enabled": True,
            "limits": {
                "max_daily_notional_krw": 3000000,
                "max_position_weight": 0.15,
                "max_orders_per_day": 4,
            },
        }
    )
    limits = trading.risk_limits()
    assert limits.max_daily_notional_krw == Decimal("3000000")
    assert limits.max_orders_per_day == 4
    # Read through Decimal, not float - a float limit would put rounding
    # drift back into a pipeline built to avoid it.
    assert isinstance(limits.max_position_weight, Decimal)
    # Unset limits keep their defaults rather than becoming None.
    assert limits.strict is True


def test_an_unknown_limit_name_is_an_error_not_a_no_op():
    from src.config import _parse_trading
    from src.toss.errors import TossConfigError

    # A typo'd limit that is silently dropped reads, from the config file, as
    # a limit that is in force.
    with pytest.raises(TossConfigError):
        _parse_trading({"limits": {"max_order_per_day": 3}})


def test_a_non_numeric_limit_is_rejected():
    from src.config import _parse_trading
    from src.toss.errors import TossConfigError

    with pytest.raises(TossConfigError):
        _parse_trading({"limits": {"max_daily_notional_krw": "많이"}})


def test_weight_overrides_parse_into_decimals(tmp_path):
    from decimal import Decimal

    from src.config import _parse_trading

    trading = _parse_trading(
        {
            "limits": {
                "max_position_weight_overrides": {"QQQ": 0.6, "QLD": 0.2},
                "weight_check_min_equity_krw": 2000000,
            }
        }
    )
    limits = trading.risk_limits()
    assert limits.max_position_weight_overrides == {
        "QQQ": Decimal("0.6"),
        "QLD": Decimal("0.2"),
    }
    assert isinstance(limits.weight_check_min_equity_krw, Decimal)


def test_weight_overrides_must_be_a_mapping():
    from src.config import _parse_trading
    from src.toss.errors import TossConfigError

    with pytest.raises(TossConfigError):
        _parse_trading({"limits": {"max_position_weight_overrides": ["QQQ"]}})


def test_universe_rows_are_validated_at_load_time():
    from src.config import _parse_trading
    from src.toss.errors import TossConfigError

    # 3x is a policy violation the universe module raises on, wrapped here as
    # a config error so it fails at startup, before any signal is evaluated.
    with pytest.raises(TossConfigError):
        _parse_trading({"universe": [{"symbol": "TQQQ", "leverage": 3}]})


def test_valid_universe_rows_pass_through_to_trading_config():
    from src.config import _parse_trading

    rows = [{"symbol": "QLD", "leverage": 2, "max_weight": 0.15}]
    trading = _parse_trading({"universe": rows})
    assert trading.universe == rows


def test_strategy_params_pass_through_unvalidated():
    from src.config import _parse_trading

    trading = _parse_trading({"strategy_params": {"top_n": 2, "rebalance_weekday": 0}})
    assert trading.strategy_params == {"top_n": 2, "rebalance_weekday": 0}
