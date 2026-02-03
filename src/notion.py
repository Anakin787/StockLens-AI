from notion_client import Client
from datetime import datetime

class NotionReporter:
    def __init__(self, config):
        self.token = config['notion']['token']
        self.database_id = config['notion']['database_id']
        self.client = Client(auth=self.token)
        self.title_prefix = config['notion'].get('page_title_prefix', 'Financial Report')

    def create_report(self, asset_data, news_data):
        """
        Creates a new page in the Notion database with the given data.
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        title = f"{self.title_prefix} - {date_str}"
        
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
                port_text = "held Stocks:\n"
                for stock in asset_data['portfolio']:
                    port_text += f"- {stock['name']}: {stock['profit_rate']}%\n"
                children_blocks.append(self._create_paragraph_block(port_text))

        else:
            children_blocks.append(self._create_paragraph_block("No asset data available (Mock or Error)."))

        # 3. News Header
        children_blocks.append(self._create_heading_block("📰 Economic News"))

        # 4. General News
        children_blocks.append(self._create_subheading_block("General Economy"))
        for item in news_data.get('general', []):
            children_blocks.append(self._create_bullet_block(item['title'], item['link']))

        # 5. Keyword News
        for keyword, items in news_data.get('keywords', {}).items():
            if items:
                children_blocks.append(self._create_subheading_block(f"News: {keyword}"))
                for item in items:
                    children_blocks.append(self._create_bullet_block(item['title'], item['link']))

        # Create the page
        try:
            self.client.pages.create(
                parent={"database_id": self.database_id},
                properties={
                    "Name": {
                        "title": [{"text": {"content": title}}]
                    },
                    "Date": {
                        "date": {"start": date_str}
                    }
                },
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
