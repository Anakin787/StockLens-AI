import feedparser
import urllib.parse
from datetime import datetime


def portfolio_keywords(snapshot):
    """Distinct news-search keywords for currently held positions, in order.

    Feeds the report's news search with the account's actual stocks, not
    just the static macro keywords in config - "news about what I hold", not
    only "news about the economy in general".

    A position's ``underlying`` wins when set (a leveraged or single-stock
    product declares the one company it actually tracks -
    config.yaml's ``portfolio.manual[].underlying`` - e.g. TSLL -> "TSLA");
    searching a fund's full listed name ("DIREXION DAILY TSLA BULL 2X
    SHARES") returns almost nothing on a general news search, where the
    underlying company's own name does. Otherwise the display name is used
    rather than the ticker: "삼성전자" surfaces far more on Google News than
    the bare symbol "005930" would.
    """
    seen = []
    for position in getattr(snapshot, "positions", None) or []:
        keyword = getattr(position, "underlying", None) or getattr(position, "name", None) or ""
        keyword = keyword.strip()
        if keyword and keyword not in seen:
            seen.append(keyword)
    return seen


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
