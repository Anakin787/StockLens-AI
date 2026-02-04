# Issue Report: Challenges with Kiwoom OpenAPI for Global Stocks

## Summary
The goal was to automate the retrieval of US stock portfolio data using Kiwoom Securities' Open API. However, due to significant technical limitations and system fragmentation, we pivoted to a semi-automated hybrid approach (Manual Quantity + Real-time Market Data).

## Technical Limitations Identified

### 1. Fragmentation of Systems
- **OpenAPI+ (Domestic)** vs **OpenAPI W (Global)**: Kiwoom operates two completely separate API modules.
- The standard `OpenAPI+`, which is widely used and documented, **does not support US stock inquiries**.
- To access global stocks, a separate installation of `OpenAPI W` is required.

### 2. Environment Constraints
- Both APIs strictly require a **32-bit Python environment**, which conflicts with modern 64-bit development workflows.
- This forces the maintenance of a separate virtual environment and specific legacy library versions (e.g., older `pandas`, `numpy`).

### 3. Limited Functionality for Global Stocks
- Even 'Global' APIs from Kiwoom focus heavily on **Futures & Options**, offering limited support for standard stock portfolio management.
- Authentication and session management for global accounts are more complex and unstable compared to domestic ones.

## Resolution
- **Deprecated**: Direct connection to Kiwoom API (`src/kiwoom.py` and 32-bit setup).
- **Adoped Steps**:
    1. **Data Source**: Switched to `yfinance` API for reliable, 64-bit compatible real-time US stock data.
    2. **Portfolio Management**: Implemented `PortfolioManager` to calculate valuations based on manually inputted holdings and exchange rates.
    3. **Currency Handling**: Added logic to account for historical exchange rates (at purchase) vs. current real-time exchange rates to calculate accurate KRW returns.
