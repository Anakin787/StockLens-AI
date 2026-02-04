# Feature: AI Financial Analyst Integration using Google Gemini

## Summary
Added an AI-powered analysis module to provide strategic insights and portfolio feedback alongside the daily report. This transforms the tool from a passive reporter into an active financial assistant.

## Implementation Details

### 1. AI Engine Integration
- **Model**: Google Gemini 2.0 Flash Exp (via `google-generativeai`).
- **Reasoning**: Chosen for its high performance in text analysis and free tier availability for personal use.

### 2. New Module: `src/analyst.py`
- Accepts **Portfolio Data** (holdings, profit/loss) and **News Data** (keywords, headlines).
- Constructs a prompt to simulate a "Professional Financial Analyst".
- Returns a structured 3-part analysis:
    1.  **Market Outlook**: Assessment of current market conditions.
    2.  **Portfolio Strategy**: Specific advice on holding/selling based on performance and news.
    3.  **Recommendation**: Sector or asset class suggestions.

### 3. Report Upgrade (`src/notion.py`)
- The daily Notion report now includes a **"🧠 AI Analyst Insight"** section at the very top.
- Uses a `callout` block to highlight the AI's advice distinctively.

### 4. Configuration
- Added `google_ai` section to `config.yaml` to securely manage the API Key.

## Usage
Ensure the `google_ai` section in `config.yaml` is populated with a valid API Key from Google AI Studio.
