import yfinance as yf
import yaml
import time
from datetime import datetime

class PortfolioManager:
    def __init__(self, config):
        self.config = config
        self.stocks = config.get('portfolio', {}).get('stocks', [])
        
    def fetch_portfolio_data(self):
        """
        Fetches real-time price data for the manually configured stock list.
        Calculates total evaluation and profit.
        """
        if not self.stocks:
            print("No stocks configured in config.yaml under 'portfolio'.")
            return self._get_empty_portfolio()
            
        print(f"Fetching data for {len(self.stocks)} stocks...")
        
        # 1. Prepare symbols and fetch all at once for speed
        symbols = [s['symbol'] for s in self.stocks]
        tickers = " ".join(symbols)
        
        try:
            # fetch data
            data = yf.Tickers(tickers)
            
            # Fetch Exchange Rate (USD to KRW)
            usd_krw = 1330.0 # fallback
            try:
                forex = yf.Ticker("KRW=X")
                hist = forex.history(period="1d")
                if not hist.empty:
                    usd_krw = hist['Close'].iloc[-1]
            except Exception as e:
                print(f"Warning: Failed to fetch exchange rate, using fallback {usd_krw}: {e}")

            portfolio_items = []
            total_eval_krw = 0
            total_buy_krw = 0
            
            for stock_conf in self.stocks:
                symbol = stock_conf['symbol']
                qty = stock_conf['qty']
                avg_price = stock_conf.get('avg_price', 0)
                
                # Get Ticker Object
                ticker = data.tickers[symbol]
                
                # Get Current Price
                # Try 'fast_info' first (newer yfinance), then 'history'
                current_price = 0
                try:
                    # For US stocks, fast_info usually works well
                    # For some, we might need history
                    hist = ticker.history(period="1d")
                    if not hist.empty:
                        current_price = hist['Close'].iloc[-1]
                    else:
                        # Market might be closed or pre-market
                        current_price = ticker.fast_info.last_price
                except:
                    current_price = 0
                
                if current_price == 0:
                    print(f"Warning: Could not fetch price for {symbol}")
                    continue

                # Determine currency (approximation)
                is_krw = ".KS" in symbol or ".KQ" in symbol
                currency = "KRW" if is_krw else "USD"
                
                # Calculate Values
                # 1. Current Value (in KRW) using real-time Exchange Rate
                current_exchange_rate = 1.0 if is_krw else usd_krw
                eval_value_krw = current_price * qty * current_exchange_rate
                
                # 2. Buy Value (in KRW) using Historical Exchange Rate
                # If avg_exchange_rate is not provided, we assume current rate (as if bought today)
                historical_exchange_rate = stock_conf.get('avg_exchange_rate', current_exchange_rate)
                if is_krw:
                    historical_exchange_rate = 1.0
                
                buy_value_krw = avg_price * qty * historical_exchange_rate
                
                # 3. Profits
                profit_krw = eval_value_krw - buy_value_krw
                
                # Yield is calculated based on Invested Capital (KRW)
                # This accurately reflects "Currency Gain/Loss" + "Stock Gain/Loss"
                profit_rate = (profit_krw / buy_value_krw * 100) if buy_value_krw > 0 else 0
                
                item = {
                    "name": symbol,
                    "qty": qty,
                    "currency": currency,
                    "current_price": round(current_price, 2),
                    "avg_price": avg_price,
                    "avg_exchange_rate": round(historical_exchange_rate, 2) if not is_krw else 1.0,
                    "eval_amount_krw": round(eval_value_krw),
                    "eval_profit_krw": round(profit_krw),
                    "profit_rate": round(profit_rate, 2)
                }
                portfolio_items.append(item)
                
                total_eval_krw += eval_value_krw
                total_buy_krw += buy_value_krw

            # Summary
            total_profit_krw = total_eval_krw - total_buy_krw
            total_yield = (total_profit_krw / total_buy_krw * 100) if total_buy_krw > 0 else 0
            
            return {
                "total_balance": round(total_eval_krw), # Total Assets (Stocks only)
                "total_evaluation": round(total_eval_krw),
                "total_profit": round(total_profit_krw),
                "profit_rate": round(total_yield, 2),
                "portfolio": portfolio_items,
                "exchange_rate": round(usd_krw, 2)
            }
            
        except Exception as e:
            print(f"Error fetching portfolio data: {e}")
            import traceback
            traceback.print_exc()
            return self._get_empty_portfolio()

    def _get_empty_portfolio(self):
        return {
            "total_balance": 0,
            "total_evaluation": 0,
            "total_profit": 0,
            "profit_rate": 0,
            "portfolio": []
        }

if __name__ == "__main__":
    # Test
    conf = {
        "portfolio": {
            "stocks": [
                {"symbol": "AAPL", "qty": 10, "avg_price": 150},
                {"symbol": "TSLA", "qty": 5, "avg_price": 200},
                {"symbol": "005930.KS", "qty": 20, "avg_price": 70000}
            ]
        }
    }
    pm = PortfolioManager(conf)
    print(pm.fetch_portfolio_data())
