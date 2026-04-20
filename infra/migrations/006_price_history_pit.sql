-- Migration 006: PIT price history (daily OHLCV)
-- FAZ 3.0 needs historical prices for the labeler + validator. Keeping
-- it in a PIT-shaped table means delisted symbols are still queryable
-- for forward-return calculations.
--
-- source = 'borsapy' / 'synthetic' / etc. Primary key allows multi-source
-- rows to coexist; the label/validator layer picks by source_priority.
-- Index by (symbol, trade_date) covers the typical range query.

CREATE TABLE IF NOT EXISTS price_history_pit (
    symbol      TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    source      TEXT NOT NULL,
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL,
    volume      REAL,
    adjusted_close REAL,
    PRIMARY KEY (symbol, trade_date, source)
);

CREATE INDEX IF NOT EXISTS idx_price_history_pit_symbol_date
    ON price_history_pit(symbol, trade_date);

CREATE INDEX IF NOT EXISTS idx_price_history_pit_date
    ON price_history_pit(trade_date);
