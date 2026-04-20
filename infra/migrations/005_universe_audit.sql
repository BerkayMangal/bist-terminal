-- Migration 005: universe_history audit columns (Phase 3 FAZ 3.1)
-- Add source_url column so 'verified' rows can be traced to KAP / Borsa
-- Istanbul disclosures. The reason column stays TEXT for SQLite-simplicity,
-- but the allowed values are now a documented enum:
--   'approximate' -- no source; Phase 2 best-effort or Phase 3 placeholder
--   'addition'    -- symbol added to the universe on from_date (source_url required)
--   'removal'     -- symbol removed on to_date (source_url required)
--   'verified'    -- from_date and to_date confirmed against a source (source_url required)
-- An app-level check in infra/pit.py:load_universe_history_csv enforces:
--   reason != 'approximate' => source_url must be non-null.
-- Migration 005 is additive -- existing rows keep NULL source_url and
-- stay 'approximate' until Phase 3 audit upgrades them.
--
-- SQLite lacks ADD COLUMN IF NOT EXISTS, so the actual ALTER is wrapped
-- via infra/migrations._ensure_column() in a Python hook at startup. This
-- .sql file is a tracking marker, like migration 002.

SELECT 1 WHERE 0;
