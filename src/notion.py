from datetime import datetime
from decimal import Decimal

from notion_client import Client


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


class NotionReporter:
    def __init__(self, config):
        # Accepts either the AppConfig.notion dataclass or a raw mapping.
        notion_cfg = getattr(config, "notion", config)
        self.token = _get(notion_cfg, "token")
        self.database_id = _get(notion_cfg, "database_id")
        self.client = Client(auth=self.token)
        self.title_prefix = _get(notion_cfg, "page_title_prefix", "Financial Report")

        # Hardcoded based on USER request: Date / Report
        self.title_prop_name = "Report"  # The title property
        self.date_prop_name = "Date"     # The date property

    def create_report(self, snapshot, news_data, ai_comment=None):
        """Create a page in the Notion database. Returns {page_id, url, title}."""
        date_str = datetime.now().replace(microsecond=0).isoformat()
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

        page_properties = {
            self.title_prop_name: {"title": [{"text": {"content": title}}]}
        }
        if self.date_prop_name:
            page_properties[self.date_prop_name] = {"date": {"start": date_str}}

        page = self.client.pages.create(
            parent={"database_id": self.database_id},
            properties=page_properties,
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
