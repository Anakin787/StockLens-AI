"""Read-only connectivity check against the real Toss Open API.

Run this before main.py the first time, and any time something breaks:

    python scripts/smoke_test.py

It calls only GET endpoints through a client with allow_write=False, so it
cannot place an order even by accident.

Run it twice in a row to verify token caching: the second run must report
"cached" rather than issuing a new token. Toss invalidates the previous token
on every issuance, so needless refreshes are a real problem, not just waste.
"""

import os
import sys

# The Windows console is often cp949, which cannot encode characters like an
# em dash. Without this, a diagnostic script dies on its own status message.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.pipeline import PortfolioService  # noqa: E402
from src.toss.errors import (  # noqa: E402
    TossAuthError,
    TossConfigError,
    TossError,
    TossForbiddenError,
)


def _ok(label, value):
    print(f"  [ok]   {label}: {value}")


def _warn(label, value):
    print(f"  [warn] {label}: {value}")


#: Property names src/notion.py writes to. A database missing either of these
#: rejects the page at creation time.
REQUIRED_NOTION_PROPS = {"Report": "title", "Date": "date"}


def _check_notion(config):
    """Verify the token, the database connection and the schema.

    Two failures are easy to hit and hard to read from the API error alone:
    forgetting to connect the integration to the database (404 despite a valid
    token), and a database whose title/date properties are named differently.
    """
    if not config.notion.is_configured:
        _warn("notion", "미설정: .env 의 NOTION_TOKEN / NOTION_DATABASE_ID 를 채우세요.")
        return

    try:
        from notion_client import Client
        from notion_client.errors import APIResponseError
    except ImportError:
        _warn("notion", "notion-client 가 설치되지 않았습니다.")
        return

    try:
        database = Client(auth=config.notion.token).databases.retrieve(
            database_id=config.notion.database_id
        )
    except APIResponseError as exc:
        if getattr(exc, "code", "") == "object_not_found":
            _warn(
                "notion",
                "데이터베이스를 찾을 수 없습니다. DB 페이지의 [...] > Connections 에서 "
                "integration 을 연결했는지, database_id 가 맞는지 확인하세요.",
            )
        elif getattr(exc, "code", "") == "unauthorized":
            _warn("notion", "토큰이 유효하지 않습니다 (NOTION_TOKEN 확인).")
        else:
            _warn("notion", f"{exc}")
        return

    title = "".join(part.get("plain_text", "") for part in database.get("title", []))
    _ok("database", title or config.notion.database_id)

    properties = database.get("properties") or {}
    for name, expected in REQUIRED_NOTION_PROPS.items():
        actual = (properties.get(name) or {}).get("type")
        if actual == expected:
            _ok(f"속성 {name}", expected)
        elif actual:
            _warn(f"속성 {name}", f"타입이 {actual} 입니다 ({expected} 이어야 함)")
        else:
            available = ", ".join(properties) or "(없음)"
            _warn(f"속성 {name}", f"없습니다. 현재 속성: {available}")


def main():
    print("=== Toss Open API smoke test (read-only) ===\n")

    config = load_config()
    print(f"config: {config.toss}")
    print(f"manual holdings: {len(config.manual_holdings)}\n")

    service = PortfolioService(config)
    client = service.client

    try:
        print("1) GET /api/v1/accounts")
        accounts = service.account.list_accounts()
        if not accounts:
            _warn("accounts", "계좌가 없습니다.")
        for account in accounts:
            _ok(
                "account",
                f"no={account.get('accountNo')} seq={account.get('accountSeq')} "
                f"type={account.get('accountType')}",
            )
        _ok("token issued this run", client.token_issue_count)
        if client.token_issue_count == 0:
            _ok("token source", "cached (.toss_token.json 재사용)")

        print("\n2) GET /api/v1/holdings")
        holdings = service.account.holdings() or {}
        items = holdings.get("items") or []
        _ok("holdings count", len(items))
        for item in items:
            _ok(
                item.get("symbol"),
                f"{item.get('name')} qty={item.get('quantity')} "
                f"last={item.get('lastPrice')} {item.get('currency')}",
            )
        if not items:
            _warn("holdings", "토스 계좌에 보유 종목이 없습니다 (예상된 상태).")

        print("\n3) GET /api/v1/prices  (수기 입력 종목)")
        symbols = [h.symbol for h in config.manual_holdings if h.symbol]
        if symbols:
            prices = service.market.prices(symbols)
            for symbol in symbols:
                quote = prices.get(symbol)
                if quote:
                    _ok(symbol, f"{quote.get('lastPrice')} {quote.get('currency')}")
                else:
                    _warn(symbol, "시세를 찾을 수 없습니다 (심볼 표기 확인).")
        else:
            _warn("prices", "config에 수기 종목이 없어 건너뜁니다.")

        print("\n4) GET /api/v1/exchange-rate")
        rate = service.market.exchange_rate("USD", "KRW") or {}
        _ok("USD/KRW", rate.get("rate"))

        print("\n5) GET /api/v1/market-calendar")
        for country, calendar in service.market_status().items():
            _ok(country, "조회됨" if calendar else "조회 실패")

        print("\n6) Notion")
        _check_notion(config)

        print("\n=== 모든 호출 성공 ===")
        return 0

    except TossForbiddenError as exc:
        print(f"\n[403] {exc}", file=sys.stderr)
        print(
            "\n허용 IP 문제일 가능성이 높습니다.\n"
            "  토스 WTS > 설정 > Open API > 허용 IP 관리 에서 현재 공인 IP를 등록하세요.\n"
            "  (가정용 회선은 IP가 바뀌면 다시 등록해야 합니다.)",
            file=sys.stderr,
        )
        return 3
    except TossAuthError as exc:
        print(f"\n[401] {exc}", file=sys.stderr)
        print(
            "\n.env의 TOSS_CLIENT_ID / TOSS_CLIENT_SECRET을 확인하세요.",
            file=sys.stderr,
        )
        return 4
    except TossConfigError as exc:
        print(f"\n[config] {exc}", file=sys.stderr)
        return 5
    except TossError as exc:
        print(f"\n[error] {exc}", file=sys.stderr)
        return 6
    finally:
        service.close()


if __name__ == "__main__":
    sys.exit(main())
