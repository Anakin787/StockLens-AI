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


def ai_settings(config):
    """``(api_key, model, thinking_level)`` from an AppConfig or a raw mapping.

    Shared with :mod:`src.universe_review`, which talks to the same model with
    the same key - one place to read that configuration means the two cannot
    drift into disagreeing about which model is configured.
    """
    analyst_cfg = getattr(config, "analyst", None)
    if analyst_cfg is not None:
        return (
            analyst_cfg.api_key,
            analyst_cfg.model,
            analyst_cfg.thinking_level,
        )
    google_ai = config.get("google_ai", {}) if hasattr(config, "get") else {}
    return (
        google_ai.get("api_key"),
        google_ai.get("model", "gemini-3.7-flash"),
        google_ai.get("thinking_level", DEFAULT_THINKING_LEVEL),
    )


def gemini_client(api_key):
    """A client, or None when no key is configured.

    None rather than a raised error: an unset AI key means the report runs
    without commentary, which has always been a supported state.
    """
    return genai.Client(api_key=api_key) if api_key else None


def _pct(rate):
    if rate is None:
        return "-"
    return f"{Decimal(rate) * 100:+.2f}%"


def _savings_str(plans):
    """Recurring safe-asset contributions, formatted for the prompt.

    Duck-typed on ``.name``/``.monthly_krw``/``.kind`` rather than importing
    ``SavingsPlan`` - a plain dict with the same attributes would not satisfy
    an isinstance check, and this only ever reads, never constructs one.
    """
    lines = []
    for plan in plans or []:
        name = getattr(plan, "name", None)
        monthly = getattr(plan, "monthly_krw", None)
        if not name or monthly is None:
            continue
        kind = getattr(plan, "kind", None) or "적금"
        lines.append(f"- {name} ({kind}): {Decimal(str(monthly)):,.0f} KRW/month")
    return "\n".join(lines) or "None"


def _keyword_news_str(news_data, limit=3):
    """Per-keyword headlines - macro keywords and, since [holding-news],
    held-stock names alike. A flat list keyed only by keyword: the analyst
    does not need to know which keywords came from config.yaml and which
    came from the portfolio to comment on them."""
    lines = []
    for keyword, items in (news_data or {}).get("keywords", {}).items():
        if not items:
            continue
        lines.append(f"- {keyword}:")
        for item in items[:limit]:
            lines.append(f"  - {item['title']}")
    return "\n".join(lines) or "None"


class Analyst:
    def __init__(self, config):
        analyst_cfg = getattr(config, "analyst", None)
        if analyst_cfg is not None:
            self.api_key = analyst_cfg.api_key
            self.model_name = analyst_cfg.model
            self.thinking_level = analyst_cfg.thinking_level
            self.savings_plans = list(getattr(config, "savings_plans", None) or [])
        else:  # raw mapping, kept for the module's standalone use
            google_ai = config.get("google_ai", {})
            self.api_key = google_ai.get("api_key")
            self.model_name = google_ai.get("model", "gemini-3.7-flash")
            self.thinking_level = google_ai.get(
                "thinking_level", DEFAULT_THINKING_LEVEL
            )
            from src.config import SavingsPlan

            self.savings_plans = [
                SavingsPlan(
                    name=raw.get("name"),
                    monthly_krw=Decimal(str(raw.get("monthly_krw", 0))),
                    kind=raw.get("kind") or "적금",
                )
                for raw in (config.get("portfolio", {}) or {}).get("savings") or []
                if isinstance(raw, dict) and raw.get("name")
            ]

        self.client = gemini_client(self.api_key)

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

            keyword_news_str = _keyword_news_str(news_data)
            savings_str = _savings_str(self.savings_plans)

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

            Keyword & Holding-Specific News (each holding's own name is
            searched, alongside any macro keywords from config):
            {keyword_news_str}

            Recurring Safe-Asset Contributions Outside This Brokerage Account:
            {savings_str}

            Please provide a response in the following format (Korean):
            1. **Market Outlook**: Brief assessment of the market situation based on the news.
            2. **Portfolio Strategy**: Specific advice on whether to hold, buy, or sell specific stocks in the portfolio. Reference today's change, any risk warnings, and anything notable in that stock's own news above - do not repeat a headline verbatim, summarise what it implies for the position.
            3. **Recommendation**: Suggest one sector or type of asset to watch closely (e.g., "Look into AI semiconductors" or "Bond ETFs"). If safe-asset contributions are listed above, factor them into the overall risk picture - they are already part of the user's total allocation, so do not recommend adding more cash-equivalents purely to diversify; instead comment on whether the brokerage account's own aggressiveness makes sense given that existing safety net.

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
