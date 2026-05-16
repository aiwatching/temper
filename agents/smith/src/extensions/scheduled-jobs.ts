/**
 * pi extension: scheduled job tools.
 *
 * Naming: "scheduled_job" instead of "task" to avoid collision with
 * the typed memory active_tasks list (task_add / task_complete go
 * there). A scheduled job is a recurring/future-triggered instruction
 * the engine runs unattended; an "active task" is a TODO item the
 * user sees in pinned context.
 *
 * Both flow into the unified /tasks UI under different status columns:
 *   - state.active_tasks  → "active" column (TODO list)
 *   - jobs table          → "scheduled" column
 */
import { Type } from "typebox";

import {
  createJob,
  deleteJob,
  forceDueNow,
  getJobById,
  listJobs,
  updateJob,
  type Trigger,
} from "../db/jobs-repo.js";
import { runJobNow } from "../jobs-engine.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving.
type PiExtensionAPI = any;

function _trigger(params: {
  trigger_kind: "interval" | "once" | "plugin_event";
  every_seconds?: number;
  fire_at?: string;
  jitter_seconds?: number;
  plugin_slug?: string;
  event?: string;
  on_error?: boolean;
}): Trigger {
  if (params.trigger_kind === "interval") {
    if (!params.every_seconds || params.every_seconds < 60) {
      throw new Error("interval triggers require every_seconds >= 60");
    }
    return {
      kind: "interval",
      every_seconds: params.every_seconds,
      jitter_seconds: params.jitter_seconds,
    };
  }
  if (params.trigger_kind === "once") {
    if (!params.fire_at) {
      throw new Error("once triggers require fire_at (ISO-8601)");
    }
    return { kind: "once", fire_at: params.fire_at };
  }
  // plugin_event
  const slug = (params.plugin_slug ?? "").trim();
  const evt = (params.event ?? "tool_end").trim();
  if (!slug) {
    throw new Error("plugin_event triggers require plugin_slug (use '*' for any)");
  }
  return {
    kind: "plugin_event",
    plugin_slug: slug,
    event: evt,
    ...(params.on_error !== undefined ? { on_error: params.on_error } : {}),
  } as Trigger;
}

export function scheduledJobsExtension(pi: PiExtensionAPI): void {
  pi.registerTool({
    name: "schedule_job",
    label: "Schedule a recurring or one-shot LLM prompt",
    description:
      "Register a job that fires on a schedule. When due, the engine " +
      "starts a synthetic conversation (id 'job-<id>') and sends the " +
      "configured prompt — Smith runs it through the full tool loop, " +
      "same as if the user typed it. Use for 'every morning send the " +
      "standup', 'every hour check Mantis', 'remind me at 5pm Friday'. " +
      "Two trigger kinds: 'interval' (every N seconds) and 'once' " +
      "(specific ISO-8601 instant; auto-disables after firing).",
    parameters: Type.Object({
      name: Type.String({
        minLength: 1,
        description: "Human label. Shown in UI + tool listings.",
      }),
      description: Type.Optional(Type.String()),
      trigger_kind: Type.Union(
        [
          Type.Literal("interval"),
          Type.Literal("once"),
          Type.Literal("plugin_event"),
        ],
        {
          description:
            "interval = recurring time-based · " +
            "once = fire at a specific instant then disable · " +
            "plugin_event = react to a tool_end event from a plugin",
        },
      ),
      every_seconds: Type.Optional(
        Type.Integer({
          minimum: 60,
          description:
            "Interval triggers only. Daily=86400, hourly=3600, " +
            "every 15min=900. Floor 60s to avoid abusive tightness.",
        }),
      ),
      jitter_seconds: Type.Optional(
        Type.Integer({
          minimum: 0,
          description:
            "Interval triggers only. Random ± seconds added per fire " +
            "to avoid stampedes when many jobs share an interval.",
        }),
      ),
      fire_at: Type.Optional(
        Type.String({
          format: "date-time",
          description:
            "Once triggers only. ISO-8601 instant to fire at. After " +
            "fire the job auto-disables.",
        }),
      ),
      plugin_slug: Type.Optional(
        Type.String({
          description:
            "plugin_event triggers only. Tool name prefix to match " +
            "(Smith plugins are named '<slug>__<tool>'). Use '*' for " +
            "any plugin. Examples: 'mantis' / 'gitlab' / '*'.",
        }),
      ),
      event: Type.Optional(
        Type.String({
          default: "tool_end",
          description:
            "plugin_event triggers only. 'tool_end' fires after any " +
            "tool from the plugin completes; 'tool_end:<tool_name>' " +
            "fires after a specific tool (e.g. 'tool_end:list_bugs').",
        }),
      ),
      on_error: Type.Optional(
        Type.Boolean({
          description:
            "plugin_event triggers only. true = only fire when the " +
            "tool call ended in error (alerting). Default false.",
        }),
      ),
      prompt: Type.String({
        minLength: 1,
        description:
          "What the synthetic conversation receives as the user " +
          "message when the job fires. Be specific — this becomes " +
          "the model's only instruction; no human will clarify.",
      }),
      conversation_id: Type.Optional(
        Type.String({
          description:
            "Override the synthetic conv id (default 'job-<id>'). " +
            "Set this to route fires into an existing conversation's " +
            "JSONL — useful if you want the model to see prior turns.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        name: string;
        description?: string;
        trigger_kind: "interval" | "once" | "plugin_event";
        every_seconds?: number;
        jitter_seconds?: number;
        fire_at?: string;
        plugin_slug?: string;
        event?: string;
        on_error?: boolean;
        prompt: string;
        conversation_id?: string;
      },
    ) {
      try {
        const trigger = _trigger(params);
        const job = createJob({
          name: params.name,
          description: params.description,
          trigger,
          action: {
            kind: "llm_prompt",
            prompt: params.prompt,
            conversation_id: params.conversation_id,
          },
          updatedBy: "tool:schedule_job",
        });
        let triggerDesc: string;
        if (trigger.kind === "interval") {
          triggerDesc = `every ${trigger.every_seconds}s`;
        } else if (trigger.kind === "once") {
          triggerDesc = `at ${trigger.fire_at}`;
        } else if (trigger.kind === "plugin_event") {
          triggerDesc =
            `on ${trigger.plugin_slug} ${trigger.event}` +
            (trigger.on_error ? " (errors only)" : "");
        } else {
          // cron — _trigger() doesn't return this today; defensive
          triggerDesc = JSON.stringify(trigger);
        }
        return {
          content: [{
            type: "text",
            text:
              `Scheduled ${job.id} '${job.name}'. ` +
              `Trigger: ${trigger.kind} ${triggerDesc}. ` +
              `Next fire: ${job.next_fire_at ?? "(event-driven, no schedule)"}.`,
          }],
          details: { id: job.id, next_fire_at: job.next_fire_at },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `schedule_job failed: ${(e as Error).message}` }],
          details: { error: (e as Error).message },
        };
      }
    },
  });

  pi.registerTool({
    name: "list_scheduled_jobs",
    label: "List scheduled jobs",
    description:
      "List jobs registered with the scheduler. Returns id, name, " +
      "trigger, next fire time, last status. Use to answer 'what " +
      "have you scheduled' or to find a job id before " +
      "cancel_scheduled_job / run_scheduled_job_now.",
    parameters: Type.Object({
      enabled_only: Type.Optional(
        Type.Boolean({
          default: true,
          description:
            "Hide disabled (completed once-jobs, paused). Default true.",
        }),
      ),
    }),
    async execute(_toolCallId: string, params: { enabled_only?: boolean }) {
      const enabled = params.enabled_only === false ? undefined : true;
      const jobs = listJobs({ enabled });
      const summary = jobs.map((j) => ({
        id: j.id,
        name: j.name,
        enabled: j.enabled,
        trigger:
          j.trigger_kind === "interval"
            ? `every ${(j.trigger_config as { every_seconds: number }).every_seconds}s`
            : j.trigger_kind === "once"
              ? `once at ${(j.trigger_config as { fire_at: string }).fire_at}`
              : j.trigger_kind,
        next_fire_at: j.next_fire_at,
        last_fired_at: j.last_fired_at,
        last_status: j.last_status,
        run_count: j.run_count,
        fail_count: j.fail_count,
      }));
      return {
        content: [{ type: "text", text: JSON.stringify(summary, null, 2) }],
        details: { count: summary.length },
      };
    },
  });

  pi.registerTool({
    name: "cancel_scheduled_job",
    label: "Cancel (delete) a scheduled job",
    description:
      "Permanently delete a scheduled job. Use when the user says " +
      "'stop the daily report' / 'cancel the 5pm reminder'. For a " +
      "temporary pause prefer the pause variant (TODO: not built yet; " +
      "delete + re-schedule is the workaround).",
    parameters: Type.Object({
      job_id: Type.String({ minLength: 1 }),
    }),
    async execute(_toolCallId: string, params: { job_id: string }) {
      const ok = deleteJob(params.job_id);
      return {
        content: [{
          type: "text",
          text: ok ? `Deleted job ${params.job_id}.` : `Job ${params.job_id} not found.`,
        }],
        details: { deleted: ok },
      };
    },
  });

  pi.registerTool({
    name: "run_scheduled_job_now",
    label: "Fire a scheduled job immediately",
    description:
      "Run a scheduled job right now, out-of-band from its trigger " +
      "schedule. Doesn't change next_fire_at (the regular schedule " +
      "keeps ticking). Use for 'test the standup before tomorrow' / " +
      "'fire the digest now'.",
    parameters: Type.Object({
      job_id: Type.String({ minLength: 1 }),
    }),
    async execute(_toolCallId: string, params: { job_id: string }) {
      const job = getJobById(params.job_id);
      if (!job) {
        return {
          content: [{ type: "text", text: `Job ${params.job_id} not found.` }],
          details: { found: false },
        };
      }
      try {
        await runJobNow(job);
        const fresh = getJobById(params.job_id);
        return {
          content: [{
            type: "text",
            text:
              `Ran ${job.id} (${job.name}). Last status: ` +
              `${fresh?.last_status ?? "(unknown)"}.`,
          }],
          details: { id: job.id, last_status: fresh?.last_status },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `run failed: ${(e as Error).message}` }],
          details: { error: (e as Error).message },
        };
      }
    },
  });

  pi.registerTool({
    name: "pause_scheduled_job",
    label: "Pause / resume a scheduled job",
    description:
      "Toggle a job's enabled state. Paused jobs aren't picked up by " +
      "the engine tick but stay in the table for later resume. Use " +
      "when the user says 'mute the hourly Mantis check today' — " +
      "later resume restores the schedule.",
    parameters: Type.Object({
      job_id: Type.String({ minLength: 1 }),
      enabled: Type.Boolean({ description: "true = resume, false = pause" }),
    }),
    async execute(
      _toolCallId: string,
      params: { job_id: string; enabled: boolean },
    ) {
      try {
        const j = updateJob(params.job_id, {
          enabled: params.enabled,
          updatedBy: "tool:pause_scheduled_job",
        });
        return {
          content: [{
            type: "text",
            text: `Job ${j.id} ${j.enabled ? "resumed" : "paused"}.`,
          }],
          details: { id: j.id, enabled: j.enabled },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `pause failed: ${(e as Error).message}` }],
          details: { error: (e as Error).message },
        };
      }
    },
  });
}
