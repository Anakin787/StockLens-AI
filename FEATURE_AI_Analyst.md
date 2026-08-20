# Feature: AI Financial Analyst Integration using Google Gemini

## Summary
Added an AI-powered analysis module to provide strategic insights and portfolio feedback alongside the daily report. This transforms the tool from a passive reporter into an active financial assistant.

## Implementation Details

### 1. AI Engine Integration
- **Model**: Google Gemini 3.7 Flash (via `google-genai`).
  - 2026-08-20: migrated off `google-generativeai`, which Google retired. Reasoning depth is pinned to `thinking_level: low` — Gemini 3.x thinks at `medium` by default, which a three-paragraph daily summary does not need.
- **Reasoning**: Chosen for its high performance in text analysis and free tier availability for personal use.

### 2. New Module: `src/analyst.py`
- Accepts **Portfolio Data** (holdings, profit/loss) and **News Data** (keywords, headlines).
- Constructs a prompt to simulate a "Professional Financial Analyst".
- Each holding is annotated with its instrument type from the Toss master
  record (`securityType`, `leverageFactor`), and the prompt forbids inferring
  the instrument from the ticker. Without this the model read IONX as IonQ
  common stock rather than the 2x leveraged ETF it is, and advised
  accordingly.
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
