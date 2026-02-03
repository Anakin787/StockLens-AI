import feedparser
import urllib.parse
from datetime import datetime

class NewsFetcher:
    def __init__(self, config):
        self.keywords = config.get('news', {}).get('keywords', [])

    def fetch_daily_news(self):
        """
        Fetches general economic news and keyword-specific news.
        Returns a dictionary or list of news items.
        """
        results = {
            "general": self._fetch_google_news("경제"),
            "keywords": {}
        }

        for keyword in self.keywords:
            results["keywords"][keyword] = self._fetch_google_news(keyword)

        return results

    def _fetch_google_news(self, query):
        encoded_query = urllib.parse.quote(query)
        # Google News RSS for Korea
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
        
        feed = feedparser.parse(url)
        news_items = []
        
        # Get top 5 items
        for entry in feed.entries[:5]:
            news_items.append({
                "title": entry.title,
                "link": entry.link,
                "published": entry.published,
                "source": entry.source.get('title', 'Unknown')
            })
            
        return news_items

if __name__ == "__main__":
    # Test run
    dummy_config = {"news": {"keywords": ["삼성전자", "환율"]}}
    fetcher = NewsFetcher(dummy_config)
    news = fetcher.fetch_daily_news()
    import json
    print(json.dumps(news, indent=2, ensure_ascii=False))
