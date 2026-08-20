"""Gemini-backed portfolio commentary.

Uses the ``google-genai`` SDK. The predecessor ``google-generativeai`` was
retired by Google and printed a FutureWarning on every run.
"""

from decimal import Decimal

from google import genai
from google.genai import types

#: Gemini 3.x reasons before answering, and the default depth is more than a
#: three-paragraph summary needs. "low" keeps the daily batch job cheap and
#: quick; raise it in config.yaml if the commentary reads too shallow.
DEFAULT_THINKING_LEVEL = "low"


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
            self.thinking_level = analyst_cfg.thinking_level
        else:  # raw mapping, kept for the module's standalone use
            google_ai = config.get("google_ai", {})
            self.api_key = google_ai.get("api_key")
            self.model_name = google_ai.get("model", "gemini-3.7-flash")
            self.thinking_level = google_ai.get(
                "thinking_level", DEFAULT_THINKING_LEVEL
            )

        self.client = genai.Client(api_key=self.api_key) if self.api_key else None

    def _config(self):
        """Build the request config.

        Automatic function calling is off because this asks for prose and
        declares no tools. Left on - the SDK default - it logs a "direct use
        of AFC is not recommended" warning into every scheduled run's log,
        about a feature that is not in use.

        Thinking is omitted entirely rather than set to "minimal", which
        Gemini 3.x rejects outright.
        """
        level = (self.thinking_level or "").strip().lower()
        thinking = None
        if level and level != "off":
            thinking = types.ThinkingConfig(thinking_level=level)
        return types.GenerateContentConfig(
            thinking_config=thinking,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
        )

    def analyze_portfolio(self, snapshot, news_data):
        """Analyze the portfolio and news using Gemini to generate insights."""
        if not self.client:
            return "AI Analyst is not configured (Missing API Key)."

        try:
            holdings_str = ""
            for position in snapshot.positions:
                label = position.name or position.symbol
                ticker = position.symbol or "n/a"
                instrument = position.instrument or "security"
                holdings_str += (
                    f"- {label} [{ticker}] - {instrument}, "
                    f"{position.market_country or 'n/a'}: "
                    f"{position.quantity} shares, "
                    f"Profit: {_pct(position.profit_rate)}, "
                    f"Current Price: {position.last_price} {position.currency}\n"
                )

            news_str = ""
            for news in news_data.get("general", [])[:5]:
                news_str += f"- {news['title']}\n"

            warnings_str = "\n".join(f"- {w}" for w in snapshot.warnings) or "None"

            prompt = f"""
            You are a professional financial analyst. Based on the following user portfolio and recent news, provide a brief strategic report.

            Each holding below is annotated with what it actually is. Treat that
            annotation as authoritative and do NOT infer the instrument from the
            ticker or the name. In particular, a leveraged or inverse ETF resets
            daily and loses value to volatility decay in a choppy or sideways
            market - never analyse one as if it were shares in the underlying
            company.

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

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=self._config(),
            )
            # .text is None when the model returns no text part - a safety
            # block, for instance. Returning None would put "None" in the
            # report, so say what happened instead.
            return response.text or "AI Analysis returned no text."

        except Exception as e:
            print(f"Error generating AI analysis: {e}")
            return "AI Analysis failed due to an error."
