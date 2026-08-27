"""Daily report entry point.

Exits non-zero on failure so a scheduled run surfaces problems instead of
quietly reporting success, which the previous version did.
"""

import sys

# The Windows console is often cp949, which cannot encode characters like an
# em dash. Without this, a diagnostic script dies on its own status message.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

from src.analyst import Analyst
from src.config import load_config
from src.news import NewsFetcher, portfolio_keywords
from src.notion import NotionReporter
from src.pipeline import PortfolioService, apply_name_overrides
from src.store.repo import Store
from src.toss.errors import TossError


def run():
    print(">>> Starting Financial Reporter...")

    config = load_config()

    # 1. Portfolio: Toss account (automatic) + manual entries for holdings
    #    kept at other brokers, priced through the Toss market data API.
    print(">>> Fetching portfolio (Toss Open API)...")
    store = Store()
    service = PortfolioService(config)
    try:
        snapshot = service.snapshot()
    finally:
        service.close()

    # Display names edited in the dashboard apply to the report too, so a
    # ticker reads the same in both places.
    apply_name_overrides(snapshot, store.symbol_names())

    print(
        f"Summary: {snapshot.total_krw:,.0f} KRW "
        f"(P&L {snapshot.profit_krw:+,.0f}, {snapshot.profit_rate * 100:+.2f}%)"
    )
    if snapshot.daily_profit_krw:
        print(
            f"Today:   {snapshot.daily_profit_krw:+,.0f} KRW "
            f"({snapshot.daily_profit_rate * 100:+.2f}%)"
        )
    if snapshot.warnings:
        print(f"Warnings: {len(snapshot.warnings)} -> {'; '.join(snapshot.warnings)}")

    # 2. Persist before anything that can fail on a third-party service. The
    #    Portfolio Value chart cannot be backfilled, so the snapshot is worth
    #    keeping even if Notion or Gemini is down.
    ts = store.save_snapshot(snapshot)
    print(f">>> Snapshot saved ({ts}, total {store.snapshot_count()} rows)")

    # 3. News - the configured macro keywords, plus one per currently held
    #    stock, so the report also surfaces news about the actual portfolio
    #    and not only the general economy.
    print(">>> Fetching news...")
    keywords = list(config.news_keywords)
    for name in portfolio_keywords(snapshot):
        if name not in keywords:
            keywords.append(name)
    news_data = NewsFetcher({"news": {"keywords": keywords}}).fetch_daily_news()
    print(
        f"Fetched {len(news_data.get('general', []))} general news items, "
        f"{len(news_data.get('keywords', {}))} keyword sections ({', '.join(keywords)})."
    )

    # 4. AI analysis
    print(">>> AI Analyst is thinking...")
    ai_comment = Analyst(config).analyze_portfolio(snapshot, news_data)

    # 5. Notion
    if not config.notion.is_configured:
        print("ERROR: Please set your valid Notion Token in config.yaml")
        return 1

    print(">>> Reporting to Notion...")
    report = NotionReporter(config).create_report(snapshot, news_data, ai_comment)
    if report.get("page_id"):
        store.save_report(
            report["page_id"],
            title=report.get("title"),
            url=report.get("url"),
            ai_comment=ai_comment,
            ts=ts,
        )

    print(">>> Done.")
    return 0


def main():
    try:
        return run()
    except TossError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - top level guard for the batch job
        print(f"ERROR: 예기치 못한 오류 - {exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 3


if __name__ == "__main__":
    sys.exit(main())
