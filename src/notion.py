from datetime import datetime
from decimal import Decimal

from notion_client import Client

#: Property names used when the report target is a database.
TITLE_PROP = "Report"
DATE_PROP = "Date"

#: Columns a database target must have. Only checked for diagnostics -
#: a page target has no properties beyond its title.
REQUIRED_PROPS_HINT = {TITLE_PROP: "title", DATE_PROP: "date"}

PARENT_DATABASE = "database"
PARENT_PAGE = "page"


def _fmt_krw(value):
    if value is None:
        return "-"
    return f"{Decimal(value).quantize(Decimal('1')):,} KRW"


def _fmt_pct(rate):
    """Toss reports rates as ratios (0.1179), reports show percentages."""
    if rate is None:
        return "-"
    return f"{Decimal(rate) * 100:+.2f}%"


def _fmt_signed(value):
    if value is None:
        return "-"
    amount = Decimal(value).quantize(Decimal("1"))
    return f"{amount:+,} KRW"


def resolve_parent(client, target_id):
    """Work out whether ``target_id`` is a database or an ordinary page.

    Notion's UI gives both the same style of URL, and only a database link
    carries a ``?v=`` view parameter - which is easy to lose when copying.
    Probing here means either kind of link works, instead of a page id
    failing with a 404 that reads exactly like a missing integration
    connection.

    Returns (kind, title) or (None, error message).
    """
    from notion_client.errors import APIResponseError

    try:
        database = client.databases.retrieve(database_id=target_id)
        return PARENT_DATABASE, _plain_title(database.get("title"))
    except APIResponseError as exc:
        if getattr(exc, "code", "") not in ("object_not_found", "validation_error"):
            raise

    try:
        page = client.pages.retrieve(page_id=target_id)
    except APIResponseError:
        return None, (
            "해당 ID의 데이터베이스도 페이지도 찾을 수 없습니다. "
            "ID가 맞는지, 그리고 그 페이지에서 [...] > Connections 로 "
            "integration을 연결했는지 확인하세요."
        )

    title_prop = next(
        (
            value
            for value in (page.get("properties") or {}).values()
            if value.get("type") == "title"
        ),
        {},
    )
    return PARENT_PAGE, _plain_title(title_prop.get("title"))


def _plain_title(rich_text):
    return "".join(part.get("plain_text", "") for part in (rich_text or []))


class NotionReporter:
    def __init__(self, config, client=None):
        # Accepts either the AppConfig.notion dataclass or a raw mapping.
        notion_cfg = getattr(config, "notion", config)
        self.token = _get(notion_cfg, "token")
        self.database_id = _get(notion_cfg, "database_id")
        self.client = client or Client(auth=self.token)
        self.title_prefix = _get(notion_cfg, "page_title_prefix", "Financial Report")

        self.title_prop_name = TITLE_PROP
        self.date_prop_name = DATE_PROP
        self._parent_kind = None

    def parent_kind(self):
        """Cache the probe so a report run only checks once."""
        if self._parent_kind is None:
            kind, detail = resolve_parent(self.client, self.database_id)
            if kind is None:
                raise ValueError(detail)
            self._parent_kind = kind
        return self._parent_kind

    def _parent(self):
        if self.parent_kind() == PARENT_DATABASE:
            return {"database_id": self.database_id}
        return {"page_id": self.database_id}

    def _properties(self, title):
        """Databases carry Report/Date columns; a child page has only a title."""
        if self.parent_kind() == PARENT_PAGE:
            return {"title": {"title": [{"text": {"content": title}}]}}

        properties = {self.title_prop_name: {"title": [{"text": {"content": title}}]}}
        if self.date_prop_name:
            properties[self.date_prop_name] = {
                "date": {"start": datetime.now().replace(microsecond=0).isoformat()}
            }
        return properties

    def create_report(self, snapshot, news_data, ai_comment=None):
        """Create a page under the configured database or page.

        Returns {page_id, url, title}.
        """
        title = f"{self.title_prefix} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        children_blocks = []

        # 1. AI Analysis first - the reason to open the page at all.
        if ai_comment:
            children_blocks.append(self._create_heading_block("🧠 AI Analyst Insight"))
            comment = ai_comment
            if len(comment) > 2000:
                comment = comment[:1997] + "..."
            children_blocks.append(self._create_callout_block(comment))

        # 2. Summary
        children_blocks.append(self._create_heading_block("📊 Asset Summary"))
        children_blocks.extend(self._summary_blocks(snapshot))

        # 3. Holdings
        if snapshot.positions:
            children_blocks.append(self._create_subheading_block("Held Stocks"))
            for position in snapshot.positions:
                children_blocks.append(
                    self._create_bullet_block(_position_line(position))
                )

        # 4. Risk warnings surfaced by the API - facts, so they outrank the AI.
        if snapshot.warnings:
            children_blocks.append(self._create_heading_block("⚠️ Risk Warnings"))
            for warning in snapshot.warnings:
                children_blocks.append(self._create_bullet_block(warning))

        # 5. News
        children_blocks.append(self._create_heading_block("📰 Economic News"))
        children_blocks.append(self._create_subheading_block("General Economy"))
        for item in news_data.get("general", []):
            children_blocks.append(self._create_bullet_block(item["title"], item["link"]))

        for keyword, items in news_data.get("keywords", {}).items():
            if items:
                children_blocks.append(self._create_subheading_block(f"News: {keyword}"))
                for item in items:
                    children_blocks.append(
                        self._create_bullet_block(item["title"], item["link"])
                    )

        page = self.client.pages.create(
            parent=self._parent(),
            properties=self._properties(title),
            children=children_blocks,
        )
        print(f"[Notion] Successfully created report: {title}")
        return {"page_id": page.get("id"), "url": page.get("url"), "title": title}

    def _summary_blocks(self, snapshot):
        blocks = []
        lines = [
            f"💰 Total Assets: {_fmt_krw(snapshot.total_krw)}",
            f"📥 Invested: {_fmt_krw(snapshot.purchase_krw)}",
            f"📈 Total P&L: {_fmt_signed(snapshot.profit_krw)} "
            f"({_fmt_pct(snapshot.profit_rate)})",
        ]
        if snapshot.profit_rate_after_cost is not None:
            lines.append(
                f"🧾 After fees & tax: {_fmt_signed(snapshot.profit_after_cost_krw)} "
                f"({_fmt_pct(snapshot.profit_rate_after_cost)})"
            )
        if snapshot.daily_profit_krw:
            lines.append(
                f"📅 Today's P&L: {_fmt_signed(snapshot.daily_profit_krw)} "
                f"({_fmt_pct(snapshot.daily_profit_rate)})"
            )
        lines.append(f"💱 USD/KRW: {Decimal(snapshot.exchange_rate):,.2f}")
        blocks.append(self._create_paragraph_block("\n".join(lines)))

        if snapshot.has_unconverted_fx:
            # Without a purchase-time rate the return excludes FX gain/loss.
            # Saying so beats letting the number be read as something else.
            blocks.append(
                self._create_callout_block(
                    "환차손익 미반영 — 일부 해외 종목에 매수 시점 환율"
                    "(avg_exchange_rate)이 없어 해당 종목의 수익률은 주가 손익만 "
                    "반영합니다.",
                    emoji="⚠️",
                )
            )
        return blocks

    def _create_heading_block(self, text):
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    def _create_subheading_block(self, text):
        return {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    def _create_paragraph_block(self, text):
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }

    def _create_callout_block(self, text, emoji="🧠"):
        return {
            "object": "block",
            "type": "callout",
            "callout": {
                "rich_text": [{"type": "text", "text": {"content": text}}],
                "icon": {"type": "emoji", "emoji": emoji},
            },
        }

    def _create_bullet_block(self, text, url=None):
        rich_text = [{"type": "text", "text": {"content": text}}]
        if url:
            rich_text[0]["text"]["link"] = {"url": url}

        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rich_text},
        }


def _position_line(position):
    label = position.name or position.symbol or "?"
    badge = "토스" if position.source == "toss" else "수기"
    parts = [f"[{badge}] {label}: {position.quantity} @ {position.last_price:,} {position.currency}"]
    if position.profit_rate is not None:
        parts.append(_fmt_pct(position.profit_rate))
    return " · ".join(parts)


def _get(source, key, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)
