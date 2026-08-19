from decimal import Decimal

import google.generativeai as genai


def _pct(rate):
    if rate is None:
        return "-"
    return f"{Decimal(rate) * 100:+.2f}%"


class Analyst:
    def __init__(self, config):
        analyst_cfg = getattr(config, "analyst", None)
        if analyst_cfg is not None:
            self.api_key = analyst_cfg.api_key
            self.model_name = analyst_cfg.model
        else:  # raw mapping, kept for the module's standalone use
            google_ai = config.get("google_ai", {})
            self.api_key = google_ai.get("api_key")
            self.model_name = google_ai.get("model", "gemini-1.5-flash")

        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
        else:
            self.model = None

    def analyze_portfolio(self, snapshot, news_data):
        """Analyze the portfolio and news using Gemini to generate insights."""
        if not self.model:
            return "AI Analyst is not configured (Missing API Key)."

        try:
            holdings_str = ""
            for position in snapshot.positions:
                label = position.name or position.symbol
                holdings_str += (
                    f"- {label}: {position.quantity} shares, "
                    f"Profit: {_pct(position.profit_rate)}, "
                    f"Current Price: {position.last_price} {position.currency}\n"
                )

            news_str = ""
            for news in news_data.get("general", [])[:5]:
                news_str += f"- {news['title']}\n"

            warnings_str = "\n".join(f"- {w}" for w in snapshot.warnings) or "None"

            prompt = f"""
            You are a professional financial analyst. Based on the following user portfolio and recent news, provide a brief strategic report.

            User Portfolio Overview:
            - Total Assets: {snapshot.total_krw:,.0f} KRW
            - Total Profit Rate: {_pct(snapshot.profit_rate)}
            - Profit Rate after fees and tax: {_pct(snapshot.profit_rate_after_cost)}
            - Today's Change: {_pct(snapshot.daily_profit_rate)}
            - USD/KRW: {snapshot.exchange_rate:,.2f}
            - Holdings:
            {holdings_str}

            Active Risk Warnings on Held Stocks:
            {warnings_str}

            Recent Market News Keywords:
            {news_str}

            Please provide a response in the following format (Korean):
            1. **Market Outlook**: Brief assessment of the market situation based on the news.
            2. **Portfolio Strategy**: Specific advice on whether to hold, buy, or sell specific stocks in the portfolio. Reference today's change and any risk warnings.
            3. **Recommendation**: Suggest one sector or type of asset to watch closely (e.g., "Look into AI semiconductors" or "Bond ETFs").

            Keep the tone professional yet encouraging.
            """

            response = self.model.generate_content(prompt)
            return response.text

        except Exception as e:
            print(f"Error generating AI analysis: {e}")
            return "AI Analysis failed due to an error."
