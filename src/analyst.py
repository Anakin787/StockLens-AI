import google.generativeai as genai
import json

class Analyst:
    def __init__(self, config):
        self.config = config
        self.api_key = config.get('google_ai', {}).get('api_key')
        self.model_name = config.get('google_ai', {}).get('model', 'gemini-1.5-flash')
        
        if self.api_key:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
        else:
            self.model = None

    def analyze_portfolio(self, portfolio_data, news_data):
        """
        Analyze the portfolio and news using Gemini to generate insights.
        """
        if not self.model:
            return "AI Analyst is not configured (Missing API Key)."

        try:
            # Prepare context for AI
            total_profit_rate = portfolio_data.get('profit_rate', 0)
            
            # Format portfolio for prompt
            holdings_str = ""
            for item in portfolio_data.get('portfolio', []):
                holdings_str += f"- {item['name']}: {item['qty']} shares, Profit: {item['profit_rate']}%, Current Price: {item['current_price']} {item['currency']}\n"
            
            # Format news for prompt
            news_str = ""
            for news in news_data.get('general', [])[:5]: # Top 5 news only
                news_str += f"- {news['title']}\n"

            prompt = f"""
            You are a professional financial analyst. Based on the following user portfolio and recent news, provide a brief strategic report.
            
            User Portfolio Overview:
            - Total Profit Rate: {total_profit_rate}%
            - Holdings:
            {holdings_str}
            
            Recent Market News Keywords:
            {news_str}
            
            Please provide a response in the following format (Korean):
            1. **Market Outlook**: Brief assessment of the market situation based on the news.
            2. **Portfolio Strategy**: Specific advice on whether to hold, buy, or sell specific stocks in the portfolio.
            3. **Recommendation**: Suggest one sector or type of asset to watch closely (e.g., "Look into AI semiconductors" or "Bond ETFs").
            
            Keep the tone professional yet encouraging.
            """
            
            response = self.model.generate_content(prompt)
            return response.text
            
        except Exception as e:
            print(f"Error generating AI analysis: {e}")
            return "AI Analysis failed due to an error."

if __name__ == "__main__":
    # Test stub
    pass
