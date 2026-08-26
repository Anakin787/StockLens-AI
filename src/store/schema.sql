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

-- ---------------------------------------------------------------- Phase 2
--
-- Every strategy decision is recorded, accepted or not. A rejected signal is
-- as interesting as an executed one: "the strategy wanted to buy and the risk
-- gate said no, here is the rule" is the answer to most questions about why a
-- day went the way it did, and it is unrecoverable if not written down.

CREATE TABLE IF NOT EXISTS signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    order_type  TEXT NOT NULL,
    quantity    TEXT,
    amount      TEXT,
    limit_price TEXT,
    currency    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    payload     TEXT,               -- Signal.meta as JSON
    outcome     TEXT NOT NULL       -- accepted | rejected
);

CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals (ts DESC);

CREATE TABLE IF NOT EXISTS rejections (
    signal_id   INTEGER NOT NULL REFERENCES signals (id),
    rule        TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_rejections_signal ON rejections (signal_id);

CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,   -- our idempotency key
    order_id        TEXT,               -- assigned by Toss
    signal_id       INTEGER REFERENCES signals (id),
    ts              TEXT NOT NULL,
    strategy        TEXT,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL,
    quantity        TEXT,
    amount          TEXT,
    price           TEXT,
    currency        TEXT,
    -- Denormalised so the daily notional limit is one SUM rather than a
    -- re-derivation that would need the day's exchange rate back.
    notional_krw    TEXT,
    status          TEXT NOT NULL,
    mode            TEXT NOT NULL,      -- paper | live
    error_code      TEXT,
    updated_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders (ts DESC);

CREATE TABLE IF NOT EXISTS fills (
    order_id    TEXT NOT NULL,
    ts          TEXT NOT NULL,
    quantity    TEXT NOT NULL,
    price       TEXT NOT NULL,
    commission  TEXT,
    tax         TEXT,
    PRIMARY KEY (order_id, ts, quantity, price)
);

-- Daily OHLC cache for backtesting (step [6]). Money as TEXT, same rule as
-- everywhere else - a cached price is still a price.
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,     -- YYYY-MM-DD
    open        TEXT NOT NULL,
    high        TEXT NOT NULL,
    low         TEXT NOT NULL,
    close       TEXT NOT NULL,     -- split/dividend adjusted
    raw_close   TEXT,              -- as the session actually printed
    volume      TEXT,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_daily_bars_symbol_date ON daily_bars (symbol, date);

-- One row per symbol: what range the cache believes it holds, so a refresh
-- only asks the source for what is missing rather than the whole history.
CREATE TABLE IF NOT EXISTS bar_coverage (
    symbol      TEXT PRIMARY KEY,
    first_date  TEXT,
    last_date   TEXT,
    source      TEXT,
    fetched_at  TEXT
);
