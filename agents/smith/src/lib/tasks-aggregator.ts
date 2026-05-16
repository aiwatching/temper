/**
 * Tasks aggregator — unifies four sources into one design-shaped list.
 *
 * Sources:
 *   pending    approvalStore.pendingByConv           (UI's "approve me!")
 *   active     conversationIndex (lastUsedAt < 1h)   (recent conv)
 *   done       conversationIndex (lastUsedAt > 1h)   (older conv)
 *   scheduled  jobs table (enabled=true)             (future fires)
 *
 * Why no "waiting" yet: requires marking a conversation as blocked on
 * an external system. No data source today — would need either a tool
 * smith can call (`set_waiting`) or a heuristic on conv content. Both
 * are easy to add later; the field stays optional in UnifiedTask so
 * the UI doesn't need to change when we wire it up.
 *
 * Why aggregate server-side: each source has its own auth / lock /
 * latency cost. Doing it once in-process keeps the /tasks page a
 * single fetch and lets the rendering layer stay dumb.
 */
import { approvalStore } from "../approval-store.js";
import { conversationIndex, type IndexEntry } from "../conversation-index.js";
import { listJobs, type JobRow } from "../db/jobs-repo.js";

export type TaskStatus = "pending" | "active" | "waiting" | "scheduled" | "done";
export type TaskPriority = "critical" | "high" | "normal" | "low";

export interface UnifiedTask {
  /** Globally unique id. Composed from source so multi-status pages stay collision-free. */
  id: string;
  status: TaskStatus;
  priority: TaskPriority;
  title: string;
  sub: string;

  /** Conv id when this task is conv-backed (pending / active / waiting / done). */
  conv?: string;
  /** Job id when this task is schedule-backed. */
  jobId?: string;

  /** Counts surfaced by the UI's stat columns. */
  turns?: number;
  tools?: number;
  pending?: number;

  /** Timestamp + human age — design's `ts` + `age` columns. */
  ts: string;
  age: string;

  /** Optional flags — sparingly populated, drive small UI affordances. */
  recurring?: string;     // "interval 3600s" / "once 2026-05-20T..."
  external?: string;      // waiting-on
  danger?: boolean;       // pending dangerous tool
  resolution?: string;    // closed outcome
  sourceBrief?: string;   // back-link
}

// Cutoff between "active" and "done" — a conv with no activity for an
// hour falls out of the active column. Cheap heuristic that matches
// the design's intent (today vs archive). Bump via env if it bugs you.
const ACTIVE_WINDOW_MS = 60 * 60 * 1000;

function humanAge(fromIso: string, nowMs = Date.now()): string {
  const from = new Date(fromIso).getTime();
  if (Number.isNaN(from)) return "—";
  const diff = nowMs - from;
  const abs = Math.abs(diff);
  const fmt = (n: number, unit: string) => `${n}${unit}`;
  if (abs < 60_000) return diff >= 0 ? fmt(Math.round(abs / 1000), "s") : `in ${fmt(Math.round(abs / 1000), "s")}`;
  if (abs < 3_600_000) return diff >= 0 ? fmt(Math.round(abs / 60_000), "m") : `in ${fmt(Math.round(abs / 60_000), "m")}`;
  if (abs < 86_400_000) return diff >= 0 ? fmt(Math.round(abs / 3_600_000), "h") : `in ${fmt(Math.round(abs / 3_600_000), "h")}`;
  return diff >= 0 ? fmt(Math.round(abs / 86_400_000), "d") : `in ${fmt(Math.round(abs / 86_400_000), "d")}`;
}

function pendingTasks(now: number): UnifiedTask[] {
  // approvalStore is in-memory only — no list method, just the
  // per-convId getter. We rebuild the list by scanning convs that
  // have a pending entry. There's no public iterator on the store;
  // we reach into the private map via a safe-ish typecheck.
  const store = approvalStore as unknown as {
    pendingByConv: Map<string, {
      conversationId: string;
      toolCallId: string;
      toolName: string;
      input: unknown;
      argsHash: string;
    }>;
  };
  const out: UnifiedTask[] = [];
  for (const [convId, p] of store.pendingByConv.entries()) {
    const conv = conversationIndex.get(convId);
    // Smith convention: any *_apply tool is the danger pattern; the
    // common case is the user said "yes do it" → LLM tries
    // mantis__add_comment / close_bug.
    const title = `approval needed: ${p.toolName}`;
    const sub = conv?.title ?? `(conv ${convId})`;
    const ts = conv?.lastUsedAt ?? new Date().toISOString();
    out.push({
      id: `pending-${convId}-${p.argsHash}`,
      status: "pending",
      priority: "high",
      title,
      sub,
      conv: convId,
      pending: 1,
      ts,
      age: humanAge(ts, now),
      danger: true,
    });
  }
  return out;
}

function convTasks(now: number): UnifiedTask[] {
  // pending-flagged convs surface under "pending" only — exclude them
  // here so the same conv doesn't render twice in the same view.
  const pendingConvIds = new Set<string>();
  const store = approvalStore as unknown as {
    pendingByConv: Map<string, unknown>;
  };
  for (const id of store.pendingByConv.keys()) pendingConvIds.add(id);

  const out: UnifiedTask[] = [];
  for (const e of conversationIndex.list()) {
    if (pendingConvIds.has(e.id)) continue;
    out.push(convToTask(e, now));
  }
  return out;
}

function convToTask(e: IndexEntry, now: number): UnifiedTask {
  // Status precedence: waiting > active/done. An explicit "blocked
  // on X" beats the time-window heuristic — a conv could be old but
  // still actively waiting for CI to come back.
  let status: TaskStatus;
  let external: string | undefined;
  if (e.waiting) {
    status = "waiting";
    external = e.waiting.external;
  } else {
    const lastUsedMs = new Date(e.lastUsedAt).getTime();
    const age = now - lastUsedMs;
    status = !Number.isNaN(age) && age < ACTIVE_WINDOW_MS ? "active" : "done";
  }
  // For waiting: use `since` as the timestamp so age shows how long
  // we've been blocked, not just how long since last message.
  const ts = e.waiting?.since ?? e.lastUsedAt;
  return {
    id: `conv-${e.id}`,
    status,
    priority: "normal",
    title: e.title,
    sub: e.waiting?.note
      ? `等 ${external} — ${e.waiting.note}`
      : `${e.messageCount} 轮 · ${e.firstMessage.slice(0, 80)}${e.firstMessage.length > 80 ? "…" : ""}`,
    conv: e.id,
    turns: e.messageCount,
    ts,
    age: humanAge(ts, now),
    external,
  };
}

function jobToTask(j: JobRow, now: number): UnifiedTask {
  const ts = j.next_fire_at ?? j.last_fired_at ?? j.created_at;
  let recurring: string = j.trigger_kind;
  if (j.trigger_kind === "interval") {
    const cfg = j.trigger_config as { every_seconds: number };
    recurring = `interval ${cfg.every_seconds}s`;
  } else if (j.trigger_kind === "once") {
    recurring = "once";
  }
  return {
    id: `job-${j.id}`,
    jobId: j.id,
    status: "scheduled",
    priority: "normal",
    title: j.name,
    sub: j.description
      ? j.description
      : (j.action_kind === "llm_prompt"
          ? ((j.action_config as { prompt: string }).prompt.slice(0, 80) +
              ((j.action_config as { prompt: string }).prompt.length > 80 ? "…" : ""))
          : `${j.action_kind} action`),
    ts,
    age: humanAge(ts, now),
    recurring,
    resolution: j.last_status === "failed" ? `last run failed: ${j.last_error ?? "(no detail)"}` : undefined,
  };
}

export interface AggregateOptions {
  status?: TaskStatus | "all";
  search?: string;
}

export function aggregateTasks(opts: AggregateOptions = {}): UnifiedTask[] {
  const now = Date.now();
  let all: UnifiedTask[] = [
    ...pendingTasks(now),
    ...convTasks(now),
    ...listJobs({ enabled: true }).map((j) => jobToTask(j, now)),
  ];

  if (opts.status && opts.status !== "all") {
    all = all.filter((t) => t.status === opts.status);
  }
  if (opts.search && opts.search.trim()) {
    const needle = opts.search.trim().toLowerCase();
    all = all.filter((t) =>
      t.title.toLowerCase().includes(needle) ||
      t.sub.toLowerCase().includes(needle) ||
      (t.conv?.toLowerCase().includes(needle) ?? false),
    );
  }

  // Sort: per-status order is pending → active → waiting → scheduled → done;
  // within each, most-recent first. The UI may re-group; this is a
  // sensible default.
  const order: Record<TaskStatus, number> =
    { pending: 0, active: 1, waiting: 2, scheduled: 3, done: 4 };
  all.sort((a, b) => {
    const so = order[a.status] - order[b.status];
    if (so !== 0) return so;
    return b.ts.localeCompare(a.ts);
  });
  return all;
}

/** Detail view for the right-panel — same shape with a couple of stats
 *  that are too expensive to compute for the list (today: just echo). */
export interface UnifiedTaskDetail extends UnifiedTask {
  /** Future: parse the jsonl for tool count; today we surface what we know. */
  full?: Record<string, unknown>;
}

export function aggregateTaskById(id: string): UnifiedTaskDetail | null {
  const [kind, ...rest] = id.split("-");
  const tail = rest.join("-");
  if (kind === "conv") {
    const e = conversationIndex.get(tail);
    if (!e) return null;
    return convToTask(e, Date.now());
  }
  if (kind === "job") {
    const j = listJobs().find((x) => x.id === tail);
    if (!j) return null;
    return jobToTask(j, Date.now());
  }
  if (kind === "pending") {
    const all = pendingTasks(Date.now());
    return all.find((t) => t.id === id) ?? null;
  }
  return null;
}
