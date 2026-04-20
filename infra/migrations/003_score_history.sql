-- Migration 003: score_history table (closes KR-001)
-- See KNOWN_REGRESSIONS.md KR-001 for the full story.
--
-- Phase 1 reviewer note: scoring_version column added so Phase 4 calibrated
-- scoring can coexist with v13 on the same table. PK is the triple.

CREATE TABLE IF NOT EXISTS score_history (
    symbol           TEXT NOT NULL,
    snap_date        TEXT NOT NULL,
    score            REAL,
    momentum         REAL,
    risk             REAL,
    fa_score         REAL,
    ivme             REAL,
    decision         TEXT,
    scoring_version  TEXT NOT NULL DEFAULT 'v13_handpicked',
    PRIMARY KEY (symbol, snap_date, scoring_version)
);

CREATE INDEX IF NOT EXISTS idx_score_history_symbol_date
    ON score_history(symbol, snap_date DESC);
