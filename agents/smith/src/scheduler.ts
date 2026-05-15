/**
 * Periodic consolidate scheduler.
 *
 * Lives in-process. setInterval-based — not cron. For MVP we just need
 * "every N hours run plan + (optionally) auto-apply against this
 * agent's own namespace and log the outcome."
 *
 * Why in-process vs an external cron / Temper-side scheduler:
 *   - One Smith instance owns one agent identity; the schedule travels
 *     with it.
 *   - External cron means another moving piece in deploy.
 *   - Temper-side scheduling would need cross-namespace bookkeeping and
 *     a way for each agent to opt-in — heavier than the value today.
 *
 * Failure modes are silent (log + continue). A flaky schedule should
 * never knock Smith off its perch.
 */
import { appendFileSync, mkdirSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { getConfig } from "./config.js";
import { Temper, TemperError } from "./temper.js";

const AUDIT_FILE = resolvePath(process.cwd(), ".data", "audit.log");

function appendAudit(row: Record<string, unknown>): void {
  try {
    mkdirSync(resolvePath(AUDIT_FILE, ".."), { recursive: true });
    appendFileSync(
      AUDIT_FILE,
      JSON.stringify({ ...row, when: new Date().toISOString() }) + "\n",
    );
  } catch {
    // Audit-write failure shouldn't break the schedule.
  }
}

let _timer: NodeJS.Timeout | null = null;

async function runOnce(): Promise<void> {
  const cfg = getConfig();
  const ns = `agent:me/${cfg.smithAgentSlug}`;
  const t = new Temper();
  try {
    const plan = await t.consolidatePlan({ namespace: ns, mode: "all" });
    appendAudit({
      kind: "schedule_plan",
      namespace: ns,
      plan_id: plan.plan_id,
      total: plan.counts.total,
      by_kind: plan.counts,
    });
    console.log(
      `[smith.schedule] plan ${plan.plan_id} on ${ns}: ${plan.counts.total} actions ` +
      `(invalidate=${plan.counts.invalidate_fact} delete-fact=${plan.counts.delete_fact} delete-ep=${plan.counts.delete_episode})`,
    );
    if (plan.counts.total === 0) {
      return;
    }
    if (!cfg.consolidateAutoApply) {
      console.log(
        `[smith.schedule] CONSOLIDATE_AUTO_APPLY=false — plan ${plan.plan_id} logged but NOT applied. Click Apply in /admin/consolidate to commit.`,
      );
      return;
    }
    const result = await t.consolidateApply(plan.plan_id);
    appendAudit({
      kind: "schedule_apply",
      namespace: ns,
      plan_id: plan.plan_id,
      applied: result.applied,
      failed: result.failed,
      errors: result.errors,
    });
    console.log(
      `[smith.schedule] applied ${result.applied}/${plan.counts.total} (${result.failed} failed)`,
    );

    // After consolidate cleans the graph, rebuild communities so
    // memory_search's community-kind hits stay fresh. One LLM-clustered
    // community summarizes N related entities — denser context per
    // recall hit than raw entity summaries, and Graphiti only writes
    // them when this endpoint is called. Best-effort: a community
    // build failure shouldn't roll back the consolidate that just
    // succeeded.
    try {
      const comm = await t.buildCommunities(ns);
      appendAudit({
        kind: "schedule_communities",
        namespace: ns,
        communities_created: comm.communities_created,
        community_edges_created: comm.community_edges_created,
      });
      console.log(
        `[smith.schedule] communities rebuilt on ${ns}: ` +
        `${comm.communities_created} communities, ${comm.community_edges_created} edges`,
      );
    } catch (e) {
      const detail = e instanceof TemperError ? e.detail : (e as Error).message;
      console.warn(`[smith.schedule] build_communities failed: ${detail}`);
      appendAudit({ kind: "schedule_communities_failed", namespace: ns, error: detail });
    }
  } catch (e) {
    const detail = e instanceof TemperError ? e.detail : (e as Error).message;
    console.warn(`[smith.schedule] tick failed: ${detail}`);
    appendAudit({ kind: "schedule_failed", namespace: ns, error: detail });
  }
}

export function startSchedulerIfConfigured(): void {
  const cfg = getConfig();
  if (cfg.consolidateScheduleHours <= 0) return;
  const intervalMs = cfg.consolidateScheduleHours * 60 * 60 * 1000;
  console.log(
    `[smith.schedule] enabled — every ${cfg.consolidateScheduleHours}h, ` +
    `auto_apply=${cfg.consolidateAutoApply}`,
  );
  // Don't fire on startup — wait one interval so a quick restart doesn't
  // batter the gateway. Operator can hit the admin UI button if they
  // want a tick right now.
  _timer = setInterval(() => {
    runOnce().catch((e) => {
      console.warn(`[smith.schedule] tick crash: ${(e as Error).message}`);
    });
  }, intervalMs);
}

export function stopScheduler(): void {
  if (_timer) {
    clearInterval(_timer);
    _timer = null;
  }
}
