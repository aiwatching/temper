/**
 * pi extension: typed memory tools.
 *
 * These tools 1:1 wrap TEMPER's typed memory endpoints (/v1/memory/tasks,
 * /focus, /preferences, /events). The tool *name* is the routing
 * decision — the model picks intent by picking the tool. TEMPER decides
 * the storage (block vs graphiti, scope, pinned, priority).
 *
 * Why this exists alongside temper-memory.ts:
 *
 *   temper-memory.ts has memory_write (raw episode) + remember (raw
 *   block) — both "catch-all" by design, requiring the model to figure
 *   out (a) which primitive to use, (b) the right key conventions, (c)
 *   pinned/priority/scope, every single time. In practice the model
 *   gets one of those wrong on most writes (the heizai / tasks-in-
 *   graphiti regressions both came from this).
 *
 *   typed-memory.ts replaces that decision with a tool-name lookup:
 *     "user has a task" → task_add(title)
 *     "user changed focus" → set_focus(value)
 *     "user states a preference" → set_preference(key, value)
 *     "something happened in the world" → note_event(content)
 *
 *   The old tools stay registered as escape hatches but the prompt
 *   marks them deprecated for normal use.
 */
import { Type } from "typebox";

import { Temper, TemperError } from "../temper.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving.
type PiExtensionAPI = any;

function _errMsg(e: unknown): string {
  if (e instanceof TemperError) return e.detail;
  return (e as Error).message;
}

export function typedMemoryExtension(pi: PiExtensionAPI): void {
  const temper = new Temper();

  // ─── tasks ────────────────────────────────────────────────────────

  pi.registerTool({
    name: "task_add",
    label: "Add an active task",
    description:
      "Add ONE task to the user's active list. Use when the user says " +
      "'I need to do X' / 'remind me to Y' / 'remember I'm working on Z'. " +
      "The list is small and bounded (~20 items) — it goes into your " +
      "system prompt every turn so you always see what's active. " +
      "Each call adds one item; for multiple tasks, call multiple times.",
    parameters: Type.Object({
      title: Type.String({
        minLength: 1,
        description: "Short imperative title. One sentence, paraphrased.",
      }),
      status: Type.Optional(
        Type.Union(
          [Type.Literal("todo"), Type.Literal("doing"), Type.Literal("blocked")],
          { default: "todo" },
        ),
      ),
      priority: Type.Optional(
        Type.Integer({
          minimum: 0,
          maximum: 100,
          default: 50,
          description: "0 = backlog, 50 = normal, 100 = drop everything.",
        }),
      ),
      notes: Type.Optional(Type.String()),
    }),
    async execute(
      _toolCallId: string,
      params: {
        title: string;
        status?: "todo" | "doing" | "blocked";
        priority?: number;
        notes?: string;
      },
    ) {
      try {
        const t = await temper.addTask(params);
        return {
          content: [{
            type: "text",
            text:
              `Added task ${t.id}: "${t.title}" ` +
              `(status=${t.status}, priority=${t.priority}).`,
          }],
          details: { id: t.id, status: t.status, priority: t.priority },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `task_add failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });

  pi.registerTool({
    name: "task_update",
    label: "Update an active task",
    description:
      "Modify a task in the active list. Use for status changes ('I " +
      "started X' → status=doing; 'I'm blocked on Y' → status=blocked), " +
      "priority bumps, or to add notes. For completion use task_complete " +
      "instead (it logs to history).",
    parameters: Type.Object({
      task_id: Type.String({ minLength: 1 }),
      title: Type.Optional(Type.String()),
      status: Type.Optional(
        Type.Union(
          [Type.Literal("todo"), Type.Literal("doing"), Type.Literal("blocked")],
        ),
      ),
      priority: Type.Optional(Type.Integer({ minimum: 0, maximum: 100 })),
      notes: Type.Optional(Type.String()),
    }),
    async execute(
      _toolCallId: string,
      params: {
        task_id: string;
        title?: string;
        status?: "todo" | "doing" | "blocked";
        priority?: number;
        notes?: string;
      },
    ) {
      try {
        const { task_id, ...patch } = params;
        const t = await temper.updateTask(task_id, patch);
        return {
          content: [{
            type: "text",
            text: `Updated task ${t.id}: status=${t.status}, priority=${t.priority}.`,
          }],
          details: { id: t.id, status: t.status },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `task_update failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });

  pi.registerTool({
    name: "task_complete",
    label: "Mark a task complete (atomic: block + history)",
    description:
      "Mark a task as done. Two writes in one shot: removes the task " +
      "from the active list AND appends a graphiti episode " +
      "'completed <title> on <date>' so 'what did I do last week' " +
      "queries can find it later. Pass an optional summary to enrich " +
      "the history entry.",
    parameters: Type.Object({
      task_id: Type.String({ minLength: 1 }),
      summary: Type.Optional(
        Type.String({
          description:
            "One-liner kept in the graphiti episode. If omitted, the " +
            "episode just records the title + completion date.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { task_id: string; summary?: string },
    ) {
      try {
        const r = await temper.completeTask(params.task_id, params.summary);
        return {
          content: [{
            type: "text",
            text:
              `Completed task ${r.completed.id}: "${r.completed.title}" ` +
              `(logged as episode ${r.episode_id}).`,
          }],
          details: { id: r.completed.id, episode_id: r.episode_id },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `task_complete failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });

  pi.registerTool({
    name: "list_tasks",
    label: "List the active tasks",
    description:
      "Read the user's active task list. The same list is in your " +
      "system prompt every turn under 'Active tasks' — only call this " +
      "if you need a refreshed view mid-conversation (e.g. user just " +
      "added one and you want to confirm the id).",
    parameters: Type.Object({
      status: Type.Optional(
        Type.Union(
          [Type.Literal("todo"), Type.Literal("doing"), Type.Literal("blocked")],
          { description: "Filter by status." },
        ),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { status?: "todo" | "doing" | "blocked" },
    ) {
      try {
        const tasks = await temper.listTasks(params.status);
        return {
          content: [{ type: "text", text: JSON.stringify(tasks, null, 2) }],
          details: { count: tasks.length },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `list_tasks failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });

  // ─── focus ────────────────────────────────────────────────────────

  pi.registerTool({
    name: "set_focus",
    label: "Set current focus / working project",
    description:
      "Record what the user is currently focused on (a project, saga, " +
      "or topic). The new value lives in your system prompt every turn. " +
      "Use when the user says 'I'm switching to X' / 'forget that, " +
      "let's do Y' / 'today I'm working on Z'. A graphiti episode is " +
      "appended automatically so focus changes are queryable as history " +
      "('when did I start working on auth?').",
    parameters: Type.Object({
      value: Type.String({
        minLength: 1,
        description:
          "Short name for what's being focused on. Project slug, saga " +
          "name, or one-line description. Replaces the previous focus.",
      }),
      note: Type.Optional(
        Type.String({
          description: "Free-form context for the change episode.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { value: string; note?: string },
    ) {
      try {
        const r = await temper.setFocus(params.value, params.note);
        return {
          content: [{
            type: "text",
            text: `Focus set to "${r.value}".`,
          }],
          details: { value: r.value, episode_id: r.episode_id },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `set_focus failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });

  // ─── preferences ──────────────────────────────────────────────────

  pi.registerTool({
    name: "set_preference",
    label: "Set a user preference (cross-agent)",
    description:
      "Record a user preference that should apply across ALL of the " +
      "user's agents (Smith, Forge, future ones). Use for preferences " +
      "that aren't agent-specific: 'reply in Chinese', 'code without " +
      "comments', 'always greet me with X'. The block is pinned + " +
      "global-scope, so it surfaces in every agent's system prompt.",
    parameters: Type.Object({
      key: Type.String({
        minLength: 1,
        description:
          "Bare preference key — TEMPER adds the 'preferences.' prefix " +
          "automatically. Examples: 'language', 'communication_style', " +
          "'code_comments', 'how_to_call_user'. Do NOT include " +
          "'preferences.' in the key (server rejects pre-prefixed keys).",
      }),
      value: Type.Any({
        description: "Any JSON value. Strings are common but objects work too.",
      }),
      description: Type.Optional(
        Type.String({
          description:
            "One-liner shown to agents on read so the preference is " +
            "self-documenting. Default: 'User preference: <key>'.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { key: string; value: unknown; description?: string },
    ) {
      try {
        const p = await temper.setPreference(params.key, params.value, params.description);
        return {
          content: [{
            type: "text",
            text: `Preference set: ${p.key} = ${JSON.stringify(p.value)}.`,
          }],
          details: { key: p.key },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `set_preference failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });

  // ─── events ───────────────────────────────────────────────────────

  pi.registerTool({
    name: "note_event",
    label: "Note a third-party event / fact to long-term history",
    description:
      "Record a third-party event or fact to graphiti — the append-only " +
      "history of things that happened, people met, projects discussed, " +
      "decisions made. Use when the subject is NOT the user themselves: " +
      "  'Bob is on the auth team' → note_event " +
      "  'we decided to use JWT last sprint' → note_event " +
      "  'I want to be called Heizai' → set_preference (not note_event!) " +
      "  'I'm working on auth' → set_focus (not note_event!) " +
      "Future recall picks these up via auto-recall when the query is " +
      "semantically related.",
    parameters: Type.Object({
      content: Type.String({
        minLength: 1,
        description:
          "One or two sentences capturing the event/fact, paraphrased. " +
          "Be explicit about WHO did WHAT — graphiti's entity extractor " +
          "needs clear subject+object to build the graph correctly.",
      }),
      tags: Type.Optional(Type.Array(Type.String())),
      saga: Type.Optional(
        Type.String({
          description: "Optional saga name to chain related episodes.",
        }),
      ),
      reference_time: Type.Optional(
        Type.String({
          format: "date-time",
          description: "When the event happened. Defaults to now.",
        }),
      ),
      namespace: Type.Optional(
        Type.String({
          description:
            "Override namespace. Default = your agent's private scope. " +
            "Use 'user:me' to share across the user's other agents.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        content: string;
        tags?: string[];
        saga?: string;
        reference_time?: string;
        namespace?: string;
      },
    ) {
      try {
        const r = await temper.noteEvent({
          content: params.content,
          tags: params.tags,
          saga: params.saga,
          referenceTime: params.reference_time,
          namespace: params.namespace,
        });
        return {
          content: [{
            type: "text",
            text: `Noted in ${r.namespace}: episode ${r.episode_id}.`,
          }],
          details: { episode_id: r.episode_id, namespace: r.namespace },
        };
      } catch (e) {
        return {
          content: [{ type: "text", text: `note_event failed: ${_errMsg(e)}` }],
          details: { error: _errMsg(e) },
        };
      }
    },
  });
}
