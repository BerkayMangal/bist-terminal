-- Migration 002: last_accessed_at columns on watchlist and alerts (marker)
-- Retroactively promoted from Phase 1's _ensure_column calls in init_db().
-- FAZ 1.5.6: infrastructure only; sweep job arrives in Phase 6.
--
-- SQLite lacks ADD COLUMN IF NOT EXISTS, so the actual column addition is
-- done by infra/storage.py:init_db() via _ensure_column() on every startup.
-- This migration file exists as a tracking marker so the _schema_migrations
-- table records that the Phase 1 column additions were applied.

SELECT 1 WHERE 0;
