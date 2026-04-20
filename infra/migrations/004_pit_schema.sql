-- Migration 004: Point-in-time fundamentals + universe history
-- FAZ 2 core deliverable: survivorship-free fundamental lookups and
-- universe queries. Every filing carries filed_at so a query "as of
-- 2022-03-15" only sees fundamentals publicly known by that date.
--
-- fundamentals_pit: one row per (symbol, period_end, metric, source).
--   period_end  = fiscal period end (e.g. '2021-12-31' for Q4 2021).
--   filed_at    = date the filing became public (KAP disclosure date).
--   metric      = a slug like 'net_income', 'roe', 'revenue'. Keeping
--                 metrics as rows instead of columns so new metrics do
--                 not require another migration.
--   source      = 'borsapy', 'kap', 'manual', 'synthetic'. Lets Phase 3
--                 audit compare sources without joining across tables.
--   value       = numeric canonical form; NULL allowed for pure-text metrics.
--   value_text  = optional string variant for categorical fields.

CREATE TABLE IF NOT EXISTS fundamentals_pit (
    symbol      TEXT NOT NULL,
    period_end  TEXT NOT NULL,
    filed_at    TEXT NOT NULL,
    source      TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL,
    value_text  TEXT,
    PRIMARY KEY (symbol, period_end, metric, source)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_symbol_filed
    ON fundamentals_pit(symbol, filed_at DESC);

CREATE INDEX IF NOT EXISTS idx_fundamentals_pit_metric
    ON fundamentals_pit(metric);

-- universe_history: membership intervals for index universes (BIST30, BIST100, ...).
-- from_date inclusive, to_date exclusive (NULL = still a member).
-- reason = 'approximate' for seed entries without a firm source date;
-- Phase 3 will audit/sharpen these. Anything else ('addition', 'removal',
-- 'delisting', 'merger') is authoritative.

CREATE TABLE IF NOT EXISTS universe_history (
    universe_name TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    from_date     TEXT NOT NULL,
    to_date       TEXT,
    reason        TEXT NOT NULL DEFAULT 'approximate',
    PRIMARY KEY (universe_name, symbol, from_date)
);

CREATE INDEX IF NOT EXISTS idx_universe_history_active
    ON universe_history(universe_name, from_date, to_date);
