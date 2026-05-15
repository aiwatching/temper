-- 002 — settings table.
--
-- Flat key/value store for Smith's runtime configuration. Values
-- that are sensitive (LLM API keys, TEMPER API keys, bearer tokens)
-- are NOT stored in `value` — they go through the existing
-- `secrets` table and `secret_ref` points at the row. The reader
-- helper decrypts on demand.
--
-- This is what `.env` used to hold (TEMPER_*, LLM_*, SMITH_AGENT_SLUG,
-- CONSOLIDATE_*, etc.). The `installed` key acts as a first-run
-- marker so the bootstrap can route un-configured browsers to /setup.

CREATE TABLE settings (
  key          TEXT PRIMARY KEY,
  value        TEXT,                  -- plaintext JSON for non-sensitive; NULL when secret_ref is set
  secret_ref   TEXT,                  -- → secrets.ref for sensitive values; NULL otherwise
  description  TEXT,                  -- for human + UI hover help
  updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by   TEXT                   -- 'setup-wizard', 'settings-ui', 'env-migration', etc.
);
