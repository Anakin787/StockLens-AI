from notion_client import Client
from datetime import datetime

class NotionReporter:
    def __init__(self, config):
        self.token = config['notion']['token']
        self.database_id = config['notion']['database_id']
        self.client = Client(auth=self.token)
        self.title_prefix = config['notion'].get('page_title_prefix', 'Financial Report')
        
        # Hardcoded based on USER request: Date / Report
        self.title_prop_name = "Report"  # The title property
        self.date_prop_name = "Date"     # The date property

    def _resolve_schema(self):
        # Disabled dynamic resolution to rely on user-provided names
        pass

    def create_report(self, asset_data, news_data, ai_comment=None):
        """
        Creates a new page in the Notion database with the given data.
        """
        # Use ISO format to include time. Notion handles ISO 8601 strings.
        # truncating microseconds for cleaner look if desired, or just standard isoformat
        date_str = datetime.now().replace(microsecond=0).isoformat()
        title = f"{self.title_prefix} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        
        children_blocks = []

        # 1. Summary Header
        children_blocks.append(self._create_heading_block("📊 Asset Summary"))
        
        # 2. Asset Data (Account Balance)
        if asset_data:
            balance = asset_data.get('total_balance', 0)
            evaluation = asset_data.get('total_evaluation', 0)
            profit = asset_data.get('total_profit', 0)
            profit_rate = asset_data.get('profit_rate', 0.0)

            children_blocks.append(self._create_paragraph_block(
                f"💰 Total Balance: {balance:,} KRW\n"
                f"📈 Evaluation: {evaluation:,} KRW\n"
                f"💵 Profit: {profit:,} KRW ({profit_rate}%)"
            ))
            
            # Portfolio Table (Simplified)
            if 'portfolio' in asset_data:
                port_text = "Held Stocks:\n"
                for stock in asset_data['portfolio']:
                    port_text += f"- {stock['name']}: {stock['profit_rate']}%\n"
                children_blocks.append(self._create_paragraph_block(port_text))

        else:
            children_blocks.append(self._create_paragraph_block("No asset data available (Mock or Error)."))

        # 3. AI Analysis
        if ai_comment:
            children_blocks.append(self._create_heading_block("🤖 AI Analysis"))
            # Split by lines to avoid block size limits if necessary, though paragraph can hold a lot.
            # Truncate if insanely long, but Gemini output should be reasonable.
            if len(ai_comment) > 2000:
                ai_comment = ai_comment[:1997] + "..."
            children_blocks.append(self._create_paragraph_block(ai_comment))

        # 4. News Header
        children_blocks.append(self._create_heading_block("📰 Economic News"))

        # 5. General News
        children_blocks.append(self._create_subheading_block("General Economy"))
        for item in news_data.get('general', []):
            children_blocks.append(self._create_bullet_block(item['title'], item['link']))

        # 6. Keyword News
        for keyword, items in news_data.get('keywords', {}).items():
            if items:
                children_blocks.append(self._create_subheading_block(f"News: {keyword}"))
                for item in items:
                    children_blocks.append(self._create_bullet_block(item['title'], item['link']))

        # Create the page
        try:
            # Build properties dynamically based on resolved schema
            page_properties = {
                self.title_prop_name: {
                    "title": [{"text": {"content": title}}]
                }
            }
            
            # Only add Date if we have a valid date property name (or fallback)
            # If the DB doesn't have this column, this might fail, but that's expected.
            if self.date_prop_name:
                page_properties[self.date_prop_name] = {
                    "date": {"start": date_str}
                }

            self.client.pages.create(
                parent={"database_id": self.database_id},
                properties=page_properties,
                children=children_blocks
            )
            print(f"[Notion] Successfully created report: {title}")
        except Exception as e:
            print(f"[Notion] Error creating page: {e}")

    def _create_heading_block(self, text):
        return {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": text}}]}
        }

    def _create_subheading_block(self, text):
        return {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]}
        }

    def _create_paragraph_block(self, text):
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}
        }

    def _create_bullet_block(self, text, url=None):
        rich_text = [{"type": "text", "text": {"content": text}}]
        if url:
            rich_text[0]["text"]["link"] = {"url": url}
            
        return {
            "object": "block",
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": rich_text}
        }
