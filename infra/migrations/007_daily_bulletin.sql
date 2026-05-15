-- ================================================================
-- 007 — Daily Bulletin storage
--
-- Stage 7b: persisted day-end summaries generated at the 18:30 IST
-- "post_close" schedule slot. Stores a JSON payload per day so the
-- UI can show today's bulletin AND let the user scroll back through
-- the archive without recomputing.
--
-- Schema:
--   bulletin_date  TEXT (YYYY-MM-DD, primary key — one bulletin/day)
--   content_json   TEXT (the generated payload — see daily_bulletin.py)
--   generated_at   TEXT (UTC iso8601 when the bulletin was written)
--   schema_version INTEGER (bump when the payload shape changes so
--                           old rows can be migrated/discarded)
-- ================================================================
CREATE TABLE IF NOT EXISTS daily_bulletin (
    bulletin_date  TEXT NOT NULL PRIMARY KEY,
    content_json   TEXT NOT NULL,
    generated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_daily_bulletin_generated
    ON daily_bulletin(generated_at DESC);
