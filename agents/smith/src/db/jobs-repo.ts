/**
 * CRUD + scheduling helpers for the `jobs` table.
 *
 * A "job" is a scheduled instruction. The engine ticks ~every 30s,
 * scans for due jobs (enabled = 1 AND next_fire_at <= now), runs
 * each one, then advances next_fire_at per trigger.
 *
 * Two trigger kinds today:
 *
 *   - "interval"     fires every N seconds while enabled
 *   - "once"         fires at a specific instant, then disables itself
 *
 * "cron" and "plugin_event" are reserved in the migration; they need
 * the cron-parser dep (cron) and a P10-style event subscription
 * (plugin_event). Both fail loudly on registration here until the
 * runners ship.
 */
import { randomBytes } from "node:crypto";

import { getDb } from "./sqlite.js";

export type TriggerKind = "interval" | "once" | "plugin_event" | "cron";
export type ActionKind = "llm_prompt" | "tool_call" | "shell";

export interface IntervalTrigger {
  kind: "interval";
  every_seconds: number;
  /** Random ± seconds applied per fire to avoid synchronized stampedes
   *  when many jobs share the same interval. Optional, default 0. */
  jitter_seconds?: number;
}
export interface OnceTrigger {
  kind: "once";
  fire_at: string; // ISO-8601
}
export interface PluginEventTrigger {
  kind: "plugin_event";
  /** Tool name prefix this trigger reacts to. "*" matches any. Smith
   *  convention: plugin tools are named `<slug>__<tool>` so "mantis"
   *  here matches mantis__list_bugs / mantis__add_comment / etc. */
  plugin_slug: string;
  /** Stage to react to. Today supported: "tool_end" (matches all tools
   *  from that plugin) and "tool_end:<tool>" (specific tool). */
  event: string;
  /** When true, only fire if the tool call ended in error. Useful for
   *  alerting on failures without firing on every success. */
  on_error?: boolean;
}
export interface CronTrigger {
  kind: "cron";
  expr: string;
}
export type Trigger =
  | IntervalTrigger | OnceTrigger | PluginEventTrigger | CronTrigger;

export interface LlmPromptAction {
  kind: "llm_prompt";
  prompt: string;
  /** Override the synthetic conversation id. Defaults to "job-<id>". */
  conversation_id?: string;
}
export interface ToolCallAction {
  kind: "tool_call";
  tool: string;
  args: unknown;
}
export interface ShellAction {
  kind: "shell";
  command: string;
}
export type Action = LlmPromptAction | ToolCallAction | ShellAction;

export interface JobRow {
  id: string;
  name: string;
  description: string | null;
  trigger_kind: TriggerKind;
  trigger_config: Trigger;
  action_kind: ActionKind;
  action_config: Action;
  enabled: boolean;
  next_fire_at: string | null;
  last_fired_at: string | null;
  last_status: "success" | "failed" | null;
  last_error: string | null;
  last_run_id: string | null;
  run_count: number;
  fail_count: number;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
}

interface JobRowDb {
  id: string;
  name: string;
  description: string | null;
  trigger_kind: TriggerKind;
  trigger_config: string;
  action_kind: ActionKind;
  action_config: string;
  enabled: number;
  next_fire_at: string | null;
  last_fired_at: string | null;
  last_status: "success" | "failed" | null;
  last_error: string | null;
  last_run_id: string | null;
  run_count: number;
  fail_count: number;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
}

function newJobId(): string {
  // Short enough to be quotable in tool args, long enough to not
  // collide with the few dozen jobs a user is realistically going to
  // have. Prefix makes them visually distinct from task ids and
  // convIds.
  return "j-" + randomBytes(4).toString("hex");
}

function rowToObject(row: JobRowDb): JobRow {
  return {
    id: row.id,
    name: row.name,
    description: row.description,
    trigger_kind: row.trigger_kind,
    trigger_config: JSON.parse(row.trigger_config) as Trigger,
    action_kind: row.action_kind,
    action_config: JSON.parse(row.action_config) as Action,
    enabled: row.enabled === 1,
    next_fire_at: row.next_fire_at,
    last_fired_at: row.last_fired_at,
    last_status: row.last_status,
    last_error: row.last_error,
    last_run_id: row.last_run_id,
    run_count: row.run_count,
    fail_count: row.fail_count,
    created_at: row.created_at,
    updated_at: row.updated_at,
    updated_by: row.updated_by,
  };
}

/** Compute next_fire_at given a trigger + a reference time.
 *  Returns null when the trigger has fired its last (one-shot done,
 *  or non-time-based like plugin_event). */
export function computeNextFireAt(trigger: Trigger, after: Date): string | null {
  switch (trigger.kind) {
    case "interval": {
      const base = trigger.every_seconds * 1000;
      const jitter = trigger.jitter_seconds
        ? Math.floor((Math.random() * 2 - 1) * trigger.jitter_seconds * 1000)
        : 0;
      return new Date(after.getTime() + base + jitter).toISOString();
    }
    case "once": {
      // First call (before first fire) returns the configured instant.
      // After fire (engine passes the fire time), returning null
      // disables the job. We detect "after first fire" by comparing
      // configured instant to `after`.
      const target = new Date(trigger.fire_at);
      if (after.getTime() >= target.getTime()) return null;
      return target.toISOString();
    }
    case "plugin_event":
    case "cron":
      // plugin_event: fires on external signal, no time schedule.
      // cron: not implemented yet (needs cron-parser dep). Treat as
      //       "never fires" until the parser is wired.
      return null;
  }
}

export interface CreateJobInput {
  name: string;
  description?: string | null;
  trigger: Trigger;
  action: Action;
  enabled?: boolean;
  updatedBy?: string;
}

export function createJob(input: CreateJobInput): JobRow {
  if (input.trigger.kind === "cron") {
    throw new Error("trigger.kind='cron' not implemented yet — add cron-parser dep first");
  }
  const id = newJobId();
  const now = new Date();
  const nowIso = now.toISOString();
  const nextFireAt = computeNextFireAt(input.trigger, now);

  getDb()
    .prepare(
      `INSERT INTO jobs (
         id, name, description,
         trigger_kind, trigger_config,
         action_kind, action_config,
         enabled, next_fire_at,
         created_at, updated_at, updated_by
       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    )
    .run(
      id,
      input.name,
      input.description ?? null,
      input.trigger.kind,
      JSON.stringify(input.trigger),
      input.action.kind,
      JSON.stringify(input.action),
      input.enabled === false ? 0 : 1,
      nextFireAt,
      nowIso,
      nowIso,
      input.updatedBy ?? null,
    );
  const row = getJobById(id);
  if (!row) throw new Error(`createJob: inserted job ${id} not found on readback`);
  return row;
}

export function getJobById(id: string): JobRow | null {
  const r = getDb().prepare(`SELECT * FROM jobs WHERE id = ?`).get(id) as
    | JobRowDb
    | undefined;
  return r ? rowToObject(r) : null;
}

export function listJobs(opts: {
  enabled?: boolean;
  triggerKind?: TriggerKind;
} = {}): JobRow[] {
  const wheres: string[] = [];
  const args: unknown[] = [];
  if (opts.enabled !== undefined) {
    wheres.push("enabled = ?");
    args.push(opts.enabled ? 1 : 0);
  }
  if (opts.triggerKind) {
    wheres.push("trigger_kind = ?");
    args.push(opts.triggerKind);
  }
  const where = wheres.length > 0 ? `WHERE ${wheres.join(" AND ")}` : "";
  const rows = getDb()
    .prepare(`SELECT * FROM jobs ${where} ORDER BY created_at DESC`)
    .all(...args) as JobRowDb[];
  return rows.map(rowToObject);
}

/** Find jobs due to fire at or before `now`. Used by the engine tick. */
export function findDueJobs(now: Date = new Date()): JobRow[] {
  const rows = getDb()
    .prepare(
      `SELECT * FROM jobs
        WHERE enabled = 1
          AND next_fire_at IS NOT NULL
          AND next_fire_at <= ?
        ORDER BY next_fire_at ASC`,
    )
    .all(now.toISOString()) as JobRowDb[];
  return rows.map(rowToObject);
}

export interface UpdateJobInput {
  name?: string;
  description?: string | null;
  trigger?: Trigger;
  action?: Action;
  enabled?: boolean;
  updatedBy?: string;
}

export function updateJob(id: string, patch: UpdateJobInput): JobRow {
  const sets: string[] = [];
  const args: unknown[] = [];
  if (patch.name !== undefined) { sets.push("name = ?"); args.push(patch.name); }
  if (patch.description !== undefined) {
    sets.push("description = ?"); args.push(patch.description);
  }
  if (patch.trigger !== undefined) {
    sets.push("trigger_kind = ?", "trigger_config = ?");
    args.push(patch.trigger.kind, JSON.stringify(patch.trigger));
    // Trigger change → recompute next_fire_at from now.
    sets.push("next_fire_at = ?");
    args.push(computeNextFireAt(patch.trigger, new Date()));
  }
  if (patch.action !== undefined) {
    sets.push("action_kind = ?", "action_config = ?");
    args.push(patch.action.kind, JSON.stringify(patch.action));
  }
  if (patch.enabled !== undefined) {
    sets.push("enabled = ?");
    args.push(patch.enabled ? 1 : 0);
    // Re-enabling? Recompute next fire from now using current trigger.
    if (patch.enabled) {
      const cur = getJobById(id);
      if (cur) {
        sets.push("next_fire_at = ?");
        args.push(computeNextFireAt(cur.trigger_config, new Date()));
      }
    }
  }
  if (patch.updatedBy !== undefined) {
    sets.push("updated_by = ?"); args.push(patch.updatedBy);
  }
  if (sets.length === 0) {
    const cur = getJobById(id);
    if (!cur) throw new Error(`updateJob: job ${id} not found`);
    return cur;
  }
  sets.push("updated_at = ?"); args.push(new Date().toISOString());
  args.push(id);

  const r = getDb()
    .prepare(`UPDATE jobs SET ${sets.join(", ")} WHERE id = ?`)
    .run(...args);
  if (r.changes === 0) throw new Error(`updateJob: job ${id} not found`);
  const updated = getJobById(id);
  if (!updated) throw new Error(`updateJob: missing on readback`);
  return updated;
}

/** Called by the engine after a run completes. */
export function recordRun(
  id: string,
  result: { status: "success" | "failed"; error?: string; runId?: string; nextFireAt: string | null },
): void {
  const sets = [
    "last_fired_at = ?",
    "last_status = ?",
    "last_error = ?",
    "last_run_id = ?",
    "next_fire_at = ?",
    "run_count = run_count + 1",
    `${result.status === "failed" ? "fail_count = fail_count + 1," : ""}`.replace(/,$/, ","),
    "updated_at = ?",
    "updated_by = ?",
  ].filter((s) => s.length > 0 && s !== ",");

  const nowIso = new Date().toISOString();
  // If "once" job fired (nextFireAt is null because computeNextFireAt
  // returned null for an already-past target), also disable so the
  // engine doesn't keep scanning a dead row.
  const shouldDisable = result.nextFireAt === null;
  if (shouldDisable) sets.push("enabled = ?");

  const args: unknown[] = [
    nowIso,
    result.status,
    result.error ?? null,
    result.runId ?? null,
    result.nextFireAt,
    nowIso,
    "engine",
  ];
  if (shouldDisable) args.push(0);
  args.push(id);

  getDb()
    .prepare(`UPDATE jobs SET ${sets.join(", ")} WHERE id = ?`)
    .run(...args);
}

export function deleteJob(id: string): boolean {
  const r = getDb().prepare(`DELETE FROM jobs WHERE id = ?`).run(id);
  return r.changes > 0;
}

/** Engine helper: bump next_fire_at to NOW so the next tick picks it up.
 *  Used by "run now" tool / HTTP. */
export function forceDueNow(id: string): JobRow {
  getDb()
    .prepare(`UPDATE jobs SET next_fire_at = ?, updated_at = ? WHERE id = ?`)
    .run(new Date().toISOString(), new Date().toISOString(), id);
  const j = getJobById(id);
  if (!j) throw new Error(`forceDueNow: job ${id} not found`);
  return j;
}
