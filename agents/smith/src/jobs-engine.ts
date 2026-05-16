/**
 * Jobs engine — ticks ~every 30s, finds due jobs, runs each.
 *
 * For each due job:
 *   1. resolve a synthetic conversation id (job-<id> by default)
 *   2. get-or-create an AgentSession for that convId
 *   3. fire action_config (today: llm_prompt only; tool_call + shell
 *      reserved)
 *   4. await prompt() — the whole pi tool loop runs in the background;
 *      no UI is subscribed
 *   5. compute next_fire_at + record run outcome
 *
 * Failure isolation: any one job throwing should never knock another
 * off the tick. Outer try/catch logs + records + continues.
 *
 * Concurrency: we serialize fires within a single tick. Two jobs due
 * at the same instant run back-to-back, not in parallel. Reason:
 * pi sessions are per-convId stateful, and even cross-convId fires
 * share the model client / LLM quota; serializing is the cheapest
 * way to avoid burst-rate trouble. If this becomes a bottleneck add
 * a per-tick concurrency cap.
 */
import { appendFileSync, mkdirSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import {
  computeNextFireAt,
  findDueJobs,
  recordRun,
  type JobRow,
} from "./db/jobs-repo.js";
import { getSessionPool } from "./session-manager.js";

const TICK_MS = 30 * 1000;
const AUDIT_FILE = resolvePath(process.cwd(), ".data", "audit.log");

function auditAppend(row: Record<string, unknown>): void {
  try {
    mkdirSync(resolvePath(AUDIT_FILE, ".."), { recursive: true });
    appendFileSync(
      AUDIT_FILE,
      JSON.stringify({ ...row, when: new Date().toISOString() }) + "\n",
    );
  } catch {
    /* never block the schedule on audit-write failure */
  }
}

let _timer: NodeJS.Timeout | null = null;
let _running = false;

function syntheticConvId(job: JobRow): string {
  // llm_prompt may override; otherwise we stamp a per-job convId so the
  // jsonl + memory-recall scope stays consistent across fires of the
  // same scheduled job. Lets the user see "/data/smith-sessions/job-X.jsonl"
  // as the persistent transcript of every X fire.
  if (job.action_kind === "llm_prompt") {
    const a = job.action_config as { conversation_id?: string };
    if (a.conversation_id && a.conversation_id.trim()) {
      return a.conversation_id.trim();
    }
  }
  return `job-${job.id}`;
}

async function runJob(job: JobRow): Promise<void> {
  const startedAt = new Date();
  const convId = syntheticConvId(job);
  let status: "success" | "failed" = "success";
  let errorDetail: string | null = null;

  try {
    if (job.action_kind !== "llm_prompt") {
      throw new Error(
        `action_kind='${job.action_kind}' not implemented in engine yet ` +
        `(only 'llm_prompt' runs today)`,
      );
    }
    const action = job.action_config as { prompt: string };
    if (!action.prompt || !action.prompt.trim()) {
      throw new Error("llm_prompt.prompt is empty");
    }
    const session = await getSessionPool().getOrCreate(convId);
    // No SSE here — we're a background tick. session.prompt resolves
    // once the LLM finishes the turn. Anything the model decides to
    // do (memory writes, tool calls) runs through its normal hooks.
    await session.prompt(action.prompt);
  } catch (e) {
    status = "failed";
    errorDetail = (e as Error).message.slice(0, 500);
  }

  // Compute next fire AFTER this run completes. We pass startedAt so
  // interval triggers count from the START of this tick — keeps fires
  // evenly spaced even when a single run takes long.
  const next = computeNextFireAt(job.trigger_config, startedAt);
  recordRun(job.id, {
    status,
    error: errorDetail ?? undefined,
    runId: convId,
    nextFireAt: next,
  });

  auditAppend({
    kind: "job_run",
    job_id: job.id,
    job_name: job.name,
    conv_id: convId,
    status,
    error: errorDetail,
    next_fire_at: next,
    duration_ms: Date.now() - startedAt.getTime(),
  });

  console.log(
    `[smith.jobs] ${job.id} (${job.name}) — ${status}` +
    (errorDetail ? ` — ${errorDetail}` : "") +
    (next ? ` — next ${next}` : " — disabled"),
  );
}

async function tick(): Promise<void> {
  if (_running) return; // skip if last tick still going
  _running = true;
  try {
    const due = findDueJobs(new Date());
    if (due.length === 0) return;
    for (const job of due) {
      try {
        await runJob(job);
      } catch (e) {
        // runJob already records its own failure; this guards against
        // catastrophic bugs in runJob itself.
        console.warn(`[smith.jobs] runJob ${job.id} crashed: ${(e as Error).message}`);
      }
    }
  } finally {
    _running = false;
  }
}

export function startJobsEngine(): void {
  if (_timer !== null) return;
  console.log(`[smith.jobs] engine enabled — tick every ${TICK_MS / 1000}s`);
  _timer = setInterval(() => {
    tick().catch((e) => {
      console.warn(`[smith.jobs] tick crash: ${(e as Error).message}`);
    });
  }, TICK_MS);
}

export function stopJobsEngine(): void {
  if (_timer === null) return;
  clearInterval(_timer);
  _timer = null;
}

/** Force-run a job NOW out-of-band. Used by /jobs/:id/run + the
 *  scheduled_job_run_now tool. Bypasses the tick loop so the caller
 *  gets immediate feedback. */
export async function runJobNow(job: JobRow): Promise<void> {
  await runJob(job);
}
