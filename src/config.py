"""Configuration loading for StockLens-AI.

Credentials come from the environment first and ``config.yaml`` only as a
fallback. From Phase 2 the same Toss credential carries order-placement
rights, so keeping it out of a file that sits next to the code matters more
than the convenience of having everything in one place.
"""

import os
import warnings
from dataclasses import dataclass, field
from decimal import Decimal

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional; env vars still work without it
    load_dotenv = None

from src.toss.errors import TossConfigError

DEFAULT_CONFIG_PATH = "config.yaml"
DEFAULT_DB_PATH = "stocklens.db"
DEFAULT_TOKEN_CACHE = ".toss_token.json"
DEFAULT_BASE_URL = "https://openapi.tossinvest.com"

#: Notion token placeholder shipped in the README/example config.
NOTION_TOKEN_PLACEHOLDER = "secret_YOUR_NOTION_TOKEN_HERE"

#: yfinance suffixes that Toss does not use. Toss identifies KR stocks by the
#: bare 6-digit code, so 005930.KS has to become 005930.
_YF_SUFFIXES = (".KS", ".KQ")

#: Placeholder prefix used by .env.example. Treated as "not set" so a
#: half-filled .env reports which key is missing instead of failing later
#: with a 401 that looks like a wrong credential.
_PLACEHOLDER_PREFIX = "PASTE_"


def is_placeholder(value):
    return bool(value) and str(value).startswith(_PLACEHOLDER_PREFIX)


def normalize_symbol(symbol):
    """Convert a yfinance-style ticker to Toss notation.

    005930.KS -> 005930; US tickers such as AAPL are unchanged. Lets an
    existing v1 config be read without hand-editing every entry.
    """
    if not symbol:
        return symbol
    text = str(symbol).strip()
    upper = text.upper()
    for suffix in _YF_SUFFIXES:
        if upper.endswith(suffix):
            return text[: -len(suffix)]
    return text


def mask_secret(value):
    """Render a secret as abcd...wxyz so logs stay useful but harmless."""
    if not value:
        return ""
    text = str(value)
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


@dataclass(frozen=True)
class ManualHolding:
    """A position held outside the Toss account, entered by hand.

    Only qty and avg_price are genuinely manual - the name, currency and
    current price are filled in from the Toss market-data endpoints. A
    holding with no symbol (cash, gold, ...) must supply price.
    """

    symbol: str | None
    qty: Decimal
    avg_price: Decimal
    name: str | None = None
    currency: str | None = None
    price: Decimal | None = None
    avg_exchange_rate: Decimal | None = None

    @property
    def is_static(self):
        """True when there is no symbol to look up a live price for."""
        return not self.symbol


@dataclass(frozen=True)
class TossConfig:
    client_id: str
    client_secret: str
    account_no: str | None = None
    base_url: str = DEFAULT_BASE_URL
    token_cache: str = DEFAULT_TOKEN_CACHE

    def __repr__(self):  # never let the secret reach a log or traceback
        return (
            f"TossConfig(client_id={mask_secret(self.client_id)!r}, "
            f"client_secret={mask_secret(self.client_secret)!r}, "
            f"account_no={self.account_no!r}, base_url={self.base_url!r})"
        )


@dataclass(frozen=True)
class NotionConfig:
    token: str
    database_id: str
    page_title_prefix: str = "Financial Report"

    @property
    def is_configured(self):
        return bool(self.token) and self.token != NOTION_TOKEN_PLACEHOLDER


@dataclass(frozen=True)
class AnalystConfig:
    api_key: str | None = None
    model: str = "gemini-1.5-flash"


@dataclass(frozen=True)
class AppConfig:
    toss: TossConfig
    notion: NotionConfig
    analyst: AnalystConfig
    manual_holdings: list = field(default_factory=list)
    news_keywords: list = field(default_factory=list)
    db_path: str = DEFAULT_DB_PATH


def _decimal(value, field_name):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise TossConfigError(
            f"'{field_name}' 값을 숫자로 읽을 수 없습니다: {value!r}"
        ) from exc


def _load_yaml(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _resolve_credentials(raw_toss):
    """Environment first, config file second."""
    env_secret = os.environ.get("TOSS_CLIENT_SECRET")
    client_id = os.environ.get("TOSS_CLIENT_ID") or raw_toss.get("client_id")
    client_secret = env_secret or raw_toss.get("client_secret")

    if not env_secret and raw_toss.get("client_secret"):
        warnings.warn(
            "client_secret을 config.yaml에서 읽었습니다. Phase 2부터 이 자격증명은 "
            "주문 실행 권한을 가지므로 .env의 TOSS_CLIENT_SECRET으로 옮기는 것을 권장합니다.",
            stacklevel=2,
        )

    pairs = (("TOSS_CLIENT_ID", client_id), ("TOSS_CLIENT_SECRET", client_secret))
    missing = [name for name, value in pairs if not value]
    unfilled = [name for name, value in pairs if is_placeholder(value)]

    if unfilled:
        raise TossConfigError(
            f".env의 {', '.join(unfilled)} 가 아직 플레이스홀더입니다. "
            "토스증권 WTS > 설정 > Open API 에서 발급받은 값으로 교체하세요."
        )
    if missing:
        raise TossConfigError(
            f"토스 자격증명이 없습니다: {', '.join(missing)}. "
            ".env 파일에 설정하거나 환경변수로 전달하세요."
        )
    return client_id, client_secret


def _parse_manual_holdings(raw_portfolio):
    """Read portfolio.manual, falling back to the v1 portfolio.stocks."""
    entries = raw_portfolio.get("manual")
    if entries is None:
        entries = raw_portfolio.get("stocks")
        if entries:
            warnings.warn(
                "config.yaml의 'portfolio.stocks'는 v1 스키마입니다. "
                "'portfolio.manual'로 이름을 바꿔주세요 (이번에는 자동 변환했습니다).",
                stacklevel=2,
            )
    entries = entries or []

    holdings = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TossConfigError(f"portfolio 항목 {index}가 매핑이 아닙니다: {entry!r}")

        symbol = normalize_symbol(entry.get("symbol"))
        qty = _decimal(entry.get("qty"), f"portfolio[{index}].qty")
        avg_price = _decimal(entry.get("avg_price"), f"portfolio[{index}].avg_price")
        price = _decimal(entry.get("price"), f"portfolio[{index}].price")

        if qty is None:
            raise TossConfigError(f"portfolio[{index}]에 'qty'가 없습니다.")
        if avg_price is None:
            raise TossConfigError(f"portfolio[{index}]에 'avg_price'가 없습니다.")
        if not symbol and price is None:
            raise TossConfigError(
                f"portfolio[{index}]에 'symbol'이 없으면 'price'를 직접 입력해야 합니다."
            )

        holdings.append(
            ManualHolding(
                symbol=symbol,
                qty=qty,
                avg_price=avg_price,
                name=entry.get("name"),
                currency=entry.get("currency"),
                price=price,
                avg_exchange_rate=_decimal(
                    entry.get("avg_exchange_rate"),
                    f"portfolio[{index}].avg_exchange_rate",
                ),
            )
        )
    return holdings


def load_config(path=DEFAULT_CONFIG_PATH, load_env=True):
    """Build an AppConfig from .env plus config.yaml."""
    if load_env and load_dotenv is not None:
        load_dotenv()

    raw = _load_yaml(path)
    raw_toss = raw.get("toss") or {}
    raw_notion = raw.get("notion") or {}
    raw_ai = raw.get("google_ai") or {}
    raw_portfolio = raw.get("portfolio") or {}
    raw_news = raw.get("news") or {}
    raw_report = raw.get("report") or {}

    client_id, client_secret = _resolve_credentials(raw_toss)

    toss = TossConfig(
        client_id=client_id,
        client_secret=client_secret,
        account_no=raw_toss.get("account_no"),
        base_url=raw_toss.get("base_url", DEFAULT_BASE_URL),
        token_cache=raw_toss.get("token_cache", DEFAULT_TOKEN_CACHE),
    )
    notion = NotionConfig(
        token=raw_notion.get("token", ""),
        database_id=raw_notion.get("database_id", ""),
        page_title_prefix=raw_notion.get("page_title_prefix", "Financial Report"),
    )
    # The AI key is optional - an unfilled placeholder just means no analysis,
    # which must not stop the report.
    ai_key = os.environ.get("GOOGLE_AI_API_KEY") or raw_ai.get("api_key")
    analyst = AnalystConfig(
        api_key=None if is_placeholder(ai_key) else ai_key,
        model=raw_ai.get("model", "gemini-1.5-flash"),
    )

    return AppConfig(
        toss=toss,
        notion=notion,
        analyst=analyst,
        manual_holdings=_parse_manual_holdings(raw_portfolio),
        news_keywords=list(raw_news.get("keywords") or []),
        db_path=raw_report.get("db_path", DEFAULT_DB_PATH),
    )
