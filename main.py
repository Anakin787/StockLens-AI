import yaml
import sys
import os
from src.kiwoom import KiwoomManager
from src.notion import NotionReporter
from src.news import NewsFetcher

def load_config():
    config_path = "config.yaml"
    if not os.path.exists(config_path):
        print("Config file not found. Please create 'config.yaml'.")
        sys.exit(1)
    
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def main():
    print(">>> Starting Financial Reporter...")
    
    # 1. Load Config
    config = load_config()
    
    # 2. Fetch Assets (Kiwoom)
    print(">>> Connecting to Kiwoom...")
    kiwoom = KiwoomManager(config)
    assets = kiwoom.get_assets()
    print(f"Summary: {assets.get('total_evaluation', 0)} KRW")
    
    # 3. Fetch News
    print(">>> Fetching News...")
    news_fetcher = NewsFetcher(config)
    news_data = news_fetcher.fetch_daily_news()
    print(f"Fetched {len(news_data.get('general', []))} general news items.")
    
    # 4. Report to Notion
    print(">>> Reporting to Notion...")
    if config['notion']['token'] == "secret_YOUR_NOTION_TOKEN_HERE":
        print("ERROR: Please set your valid Notion Token in config.yaml")
        return

    reporter = NotionReporter(config)
    reporter.create_report(assets, news_data)
    
    print(">>> Done.")

if __name__ == "__main__":
    main()
