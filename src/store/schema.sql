-- StockLens-AI local store.
--
-- Monetary values are TEXT, not REAL: SQLite's REAL is a float and would
-- reintroduce exactly the rounding drift the Decimal pipeline removes. Read
-- them back through Decimal.
--
-- Phase 2 adds signals / orders / fills alongside these.

CREATE TABLE IF NOT EXISTS snapshots (
    ts                      TEXT PRIMARY KEY,   -- ISO8601, local time
    total_krw               TEXT NOT NULL,
    purchase_krw            TEXT NOT NULL,
    profit_krw              TEXT NOT NULL,
    profit_rate             TEXT NOT NULL,
    profit_after_cost_krw   TEXT,
    profit_rate_after_cost  TEXT,
    daily_profit_krw        TEXT,
    daily_profit_rate       TEXT,
    exchange_rate           TEXT NOT NULL,
    has_unconverted_fx      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS position_snapshots (
    ts              TEXT NOT NULL,
    symbol          TEXT,
    name            TEXT NOT NULL,
    market_country  TEXT,
    currency        TEXT NOT NULL,
    quantity        TEXT NOT NULL,
    last_price      TEXT NOT NULL,
    avg_price       TEXT NOT NULL,
    market_value    TEXT NOT NULL,
    profit_loss     TEXT,
    profit_rate     TEXT,
    daily_profit_loss TEXT,
    source          TEXT NOT NULL,
    PRIMARY KEY (ts, symbol, name)
);

CREATE INDEX IF NOT EXISTS idx_position_snapshots_ts
    ON position_snapshots (ts);

-- User-supplied display names, edited inline in the dashboard.
--
-- Toss returns name == symbol for some tickers (IONX, TSLL, ...), which makes
-- the holdings table hard to read. Overrides live here rather than in
-- config.yaml so that editing them does not rewrite a hand-commented file.
CREATE TABLE IF NOT EXISTS symbol_overrides (
    symbol      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Notion report history, so the dashboard's Reports tab has real data in
-- Phase 1 rather than waiting on the trading engine.
CREATE TABLE IF NOT EXISTS reports (
    page_id     TEXT PRIMARY KEY,
    ts          TEXT NOT NULL,
    title       TEXT,
    url         TEXT,
    ai_comment  TEXT
);

CREATE INDEX IF NOT EXISTS idx_reports_ts ON reports (ts DESC);
