-- 001 — plugins + secrets tables.
--
-- plugins: registration for external services exposed as LLM tools.
--   `kind` says what adapter to instantiate (mcp / http / shell);
--   `config_json` is shape-per-kind. `secret_ref` points into the
--   secrets table so the encrypted blob isn't carried inline.
--
-- secrets: AES-256-GCM ciphertext for any sensitive value (API keys,
--   bearer tokens). Key in env SMITH_SECRET_KEY (auto-generated on
--   first use, see db/secrets.ts). Multiple things can point to the
--   same ref if they share a secret; today only plugins do.

CREATE TABLE plugins (
  slug             TEXT PRIMARY KEY,
  kind             TEXT NOT NULL CHECK(kind IN ('mcp','http','shell','builtin')),
  display_name     TEXT NOT NULL,
  config_json      TEXT NOT NULL,           -- JSON; shape depends on kind
  secret_ref       TEXT,                    -- → secrets.ref; NULL = no secret
  enabled          INTEGER NOT NULL DEFAULT 1,
  last_seen_at     TEXT,                    -- last successful health check (ISO)
  last_tool_count  INTEGER,
  last_error       TEXT,                    -- last failure detail (truncated)
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_plugins_enabled ON plugins(enabled);

CREATE TABLE secrets (
  ref              TEXT PRIMARY KEY,        -- e.g. 'plugin/mantis/a1b2c3d4'
  ciphertext       BLOB NOT NULL,           -- nonce(12) || tag(16) || ciphertext
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
