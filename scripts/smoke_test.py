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
    except ImportError:
        _warn("notion", "notion-client 가 설치되지 않았습니다.")
        return

    from src.notion import (
        PARENT_DATABASE,
        REQUIRED_PROPS_HINT,
        database_properties,
        resolve_parent,
    )

    client = Client(auth=config.notion.token)
    try:
        kind, detail = resolve_parent(client, config.notion.database_id)
    except Exception as exc:  # noqa: BLE001 - surface whatever Notion said
        _warn("notion", f"{exc}")
        return

    if kind is None:
        _warn("notion", detail)
        return

    if kind == PARENT_DATABASE:
        _ok("database", detail or config.notion.database_id)
        properties = database_properties(client, config.notion.database_id)
        for name, expected in REQUIRED_PROPS_HINT.items():
            actual = (properties.get(name) or {}).get("type")
            if actual == expected:
                _ok(f"속성 {name}", expected)
            elif actual:
                _warn(f"속성 {name}", f"타입이 {actual} 입니다 ({expected} 이어야 함)")
            else:
                available = ", ".join(properties) or "(없음)"
                _warn(f"속성 {name}", f"없습니다. 현재 속성: {available}")
    else:
        _ok("page", detail or config.notion.database_id)
        _ok("리포트 방식", "이 페이지의 하위 페이지로 생성됩니다")


def _check_analyst(config):
    """Confirm the Gemini key and model actually answer.

    A wrong key or a retired model name only shows up as a one-line apology
    inside the finished report, which is easy to miss. Ask for one token here
    instead so the failure is loud and attributable.
    """
    if not config.analyst.api_key:
        _warn(
            "GOOGLE_AI_API_KEY",
            ".env 미설정 — 리포트는 생성되지만 AI 분석 섹션은 비어 있습니다.",
        )
        return

    _ok("model", f"{config.analyst.model} (thinking={config.analyst.thinking_level})")
    try:
        from google import genai

        client = genai.Client(api_key=config.analyst.api_key)
        reply = client.models.generate_content(
            model=config.analyst.model,
            contents="Reply with the single word: OK",
        )
        text = (reply.text or "").strip()
        if text:
            _ok("generate_content", text[:40])
        else:
            _warn("generate_content", "응답에 텍스트가 없습니다.")
    except Exception as exc:  # noqa: BLE001 - a diagnostic reports, never raises
        _warn("generate_content", f"{type(exc).__name__}: {exc}")
        print(
            "    키는 https://aistudio.google.com/apikey 에서 발급합니다.\n"
            "    모델명이 폐기됐을 수도 있습니다 — config.yaml의 google_ai.model 확인.",
        )


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

        print("\n7) Gemini (AI 애널리스트)")
        _check_analyst(config)

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
