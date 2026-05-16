-- 003 — scheduled jobs.
--
-- A "job" = recurring instruction. The engine ticks ~every 30s, finds
-- jobs whose `next_fire_at <= now`, spawns a synthetic AgentSession
-- per job, sends `action_prompt` as the user message, captures the
-- result, then computes the next fire time per the trigger.
--
-- Why an independent table (vs putting jobs in memory_blocks):
--   - lots of execution metadata (next_fire_at, last_status, error
--     trace) that has zero business sitting in pinned prompt context
--   - cheap indexed scan for "what's due now"
--   - clear separation: blocks = state the model sees, jobs = state
--     the scheduler uses
--
-- Trigger kinds (trigger_config is JSON shaped per kind):
--   "interval":      { every_seconds: 3600, jitter_seconds?: 60 }
--   "once":          { fire_at: "2026-05-20T09:00:00Z" }
--   "plugin_event":  { plugin_slug: "mantis", event: "ticket_assigned" }
--   "cron":          { expr: "0 9 * * 1-5" }   -- reserved; needs cron-parser dep
--
-- Action kinds (action_config is JSON shaped per kind):
--   "llm_prompt":    { prompt: "...", conversation_id?: "cron-<id>" }
--   "tool_call":     { tool: "...", args: {...} }                  -- reserved
--   "shell":         { command: "..." }                            -- reserved (sandboxed only)

CREATE TABLE jobs (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,            -- human label, e.g. "morning standup"
  description     TEXT,                     -- one-liner shown in UI/tools
  trigger_kind    TEXT NOT NULL CHECK(trigger_kind IN ('interval','once','plugin_event','cron')),
  trigger_config  TEXT NOT NULL,            -- JSON
  action_kind     TEXT NOT NULL CHECK(action_kind IN ('llm_prompt','tool_call','shell')),
  action_config   TEXT NOT NULL,            -- JSON

  enabled         INTEGER NOT NULL DEFAULT 1,
  next_fire_at    TEXT,                     -- ISO; NULL if "fired and done" (once) or paused
  last_fired_at   TEXT,
  last_status     TEXT,                     -- 'success' | 'failed' | NULL (never fired)
  last_error      TEXT,                     -- truncated error detail
  last_run_id     TEXT,                     -- conv id / run id for traceback
  run_count       INTEGER NOT NULL DEFAULT 0,
  fail_count      INTEGER NOT NULL DEFAULT 0,

  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_by      TEXT                      -- 'tool:job_add', 'http:/jobs', 'engine'
);

-- Engine tick scan: "give me everything enabled and due now".
-- Composite covers the predicate exactly; SQLite skips disabled rows
-- and rows scheduled for the future without a table scan.
CREATE INDEX ix_jobs_enabled_next ON jobs(enabled, next_fire_at);
