/**
 * pi extension: exposes TEMPER's memory write/search as two tools to the
 * LLM tool loop. The shape mirrors what we taught agents in TEMPER's
 * /admin/integrate page — same field names, same semantics — so the model
 * doesn't need to learn anything new.
 *
 * Registered via `DefaultResourceLoader.extensionFactories` from index.ts.
 */
import { Type } from "typebox";

import { getConfig } from "../config.js";
import { Temper } from "../temper.js";

/**
 * `pi` here is the ExtensionAPI handle pi-coding-agent passes to factories.
 * We type it loosely (`any`) for now because pi's SDK types are still in
 * flux and we don't want to chase upstream renames every release. The
 * surface we touch is documented in pi's `extensions/types.ts`:
 *   - pi.registerTool({ name, label, description, parameters, execute })
 *
 * Keep the surface narrow so when the API stabilises we tighten the type
 * in one place.
 */
// biome-ignore lint: pi.ExtensionAPI's types are still moving — see comment.
type PiExtensionAPI = any;

export function temperMemoryExtension(pi: PiExtensionAPI): void {
  const temper = new Temper();

  // ---- memory_search ----
  pi.registerTool({
    name: "memory_search",
    label: "Search memory",
    description:
      "Semantic search over the user's long-term memory in TEMPER. Call " +
      "this at task start, when the user references prior context " +
      "(\"as I mentioned\", \"last time\"), or before answering anything " +
      "personal. Hits include fact, valid_at, invalid_at, score — surface " +
      "only the top 1–3 paraphrased.",
    parameters: Type.Object({
      query: Type.String({
        description: "Free-text search terms — keywords or a short question.",
      }),
      limit: Type.Optional(
        Type.Integer({
          minimum: 1,
          maximum: 25,
          default: 5,
          description: "Max hits to return. Default 5.",
        }),
      ),
      as_of: Type.Optional(
        Type.String({
          format: "date-time",
          description:
            "ISO-8601 instant. Returns facts that were active at that time " +
            "(handles 'what was true last week?' questions).",
        }),
      ),
      namespaces: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Restrict scope. Omit to use your default agent namespace + " +
            "cross-agent recall (recommended).",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { query: string; limit?: number; as_of?: string; namespaces?: string[] },
    ) {
      const hits = await temper.search({
        query: params.query,
        limit: params.limit,
        asOf: params.as_of,
        namespaces: params.namespaces,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ hits }, null, 2),
          },
        ],
        details: { hitCount: hits.length },
      };
    },
  });

  // ---- memory_write ----
  pi.registerTool({
    name: "memory_write",
    label: "Write memory",
    description:
      "Write one fact to TEMPER. Use sparingly — one discrete fact per " +
      "call, never dump whole transcripts. Call when the user shares a " +
      "preference, decision, identity fact, or anything durable that may " +
      "be recalled later. Never store credentials or unconsented PII. " +
      "Contradictions: just write the new state; Temper handles temporal " +
      "invalidation.",
    parameters: Type.Object({
      content: Type.String({
        minLength: 1,
        description:
          "One or two sentences capturing what to remember, paraphrased " +
          "rather than verbatim transcript.",
      }),
      source_description: Type.Optional(
        Type.String({
          default: "user said this in smith chat",
          description: "Where this fact came from (chat / email / doc).",
        }),
      ),
      reference_time: Type.Optional(
        Type.String({
          format: "date-time",
          description:
            "When the event actually happened. Defaults to now if omitted.",
        }),
      ),
      tags: Type.Optional(Type.Array(Type.String())),
      saga: Type.Optional(
        Type.String({
          description:
            "Optional saga name to chain related episodes (e.g. a single " +
            "conversation). Episodes sharing a saga get linked via " +
            "NEXT_EPISODE edges.",
        }),
      ),
      namespace: Type.Optional(
        Type.String({
          description:
            "Override default namespace. Use 'user:me' for cross-agent " +
            "recall, otherwise leave blank to keep memory in this agent's " +
            "private scope.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        content: string;
        source_description?: string;
        reference_time?: string;
        tags?: string[];
        saga?: string;
        namespace?: string;
      },
    ) {
      const result = await temper.write({
        content: params.content,
        sourceType: "message",
        sourceDescription: params.source_description ?? "user said this in smith chat",
        referenceTime: params.reference_time,
        tags: params.tags,
        saga: params.saga,
        namespace: params.namespace,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result),
          },
        ],
        details: {},
      };
    },
  });

  // ---- memory_consolidate (plan only — safe, read-only) ----
  pi.registerTool({
    name: "memory_consolidate",
    label: "Plan a memory consolidation",
    description:
      "Produce a dry-run plan of dedup + cleanup actions on the user's " +
      "memory. Read-only; nothing changes until you call " +
      "memory_consolidate_apply with the returned plan_id. ALWAYS show " +
      "the plan's action list to the user verbatim and get explicit " +
      "go-ahead before applying. Modes: " +
      "'all' (dedup-exact + cleanup-tags), 'dedup-exact' (merge facts " +
      "with identical text), 'dedup-semantic' (one LLM call to cluster " +
      "facts that say the same thing in different words; cap 200), " +
      "'cleanup-tags' (delete episodes tagged forget / deprecated / forget-me).",
    parameters: Type.Object({
      mode: Type.Optional(
        Type.Union(
          [
            Type.Literal("all"),
            Type.Literal("dedup-exact"),
            Type.Literal("dedup-semantic"),
            Type.Literal("cleanup-tags"),
          ],
          { default: "all" },
        ),
      ),
      namespace: Type.Optional(
        Type.String({
          description:
            "Override scope. Default: this agent's own namespace " +
            "(agent:me/<slug>). Use 'user:me' to consolidate the user's " +
            "flat namespace (shared across agents).",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { mode?: "all" | "dedup-exact" | "cleanup-tags"; namespace?: string },
    ) {
      const cfg = getConfig();
      const ns = (params.namespace ?? `agent:me/${cfg.smithAgentSlug}`).trim();
      const plan = await temper.consolidatePlan({ namespace: ns, mode: params.mode ?? "all" });
      return {
        content: [
          {
            type: "text",
            text:
              `Plan ${plan.plan_id} for ${plan.namespace} (mode=${plan.mode}):\n` +
              `  ${plan.counts.invalidate_fact} invalidate · ${plan.counts.delete_fact} delete-fact · ${plan.counts.delete_episode} delete-episode (${plan.counts.total} total)\n\n` +
              (plan.counts.total === 0
                ? "Nothing to do."
                : "ACTIONS:\n" +
                  JSON.stringify(plan.actions, null, 2) +
                  `\n\nWhen the user approves, call memory_consolidate_apply with plan_id="${plan.plan_id}". Plan expires at ${plan.expires_at}.`),
          },
        ],
        details: { plan_id: plan.plan_id, total: plan.counts.total, expires_at: plan.expires_at },
      };
    },
  });

  // ---- memory_consolidate_apply (dangerous — gated by A5) ----
  //
  // The "apply" suffix matches isDangerous()'s mutation-verb regex in
  // approval-store.ts, so a first call blocks → UI Approve button →
  // user clicks → next turn this runs through.
  pi.registerTool({
    name: "memory_consolidate_apply",
    label: "Apply a memory consolidation plan",
    description:
      "Execute a previously-generated consolidation plan. This is " +
      "DESTRUCTIVE: invalidates / deletes facts and episodes per the " +
      "plan. The namespace is locked (sleeping, all reads + writes " +
      "return 423) during execution. The approval gate blocks the " +
      "first call; tell the user what's about to happen and wait for " +
      "their Approve click.",
    parameters: Type.Object({
      plan_id: Type.String({
        description: "Returned by memory_consolidate. Plans expire after 5 minutes.",
      }),
    }),
    async execute(_toolCallId: string, params: { plan_id: string }) {
      const result = await temper.consolidateApply(params.plan_id);
      return {
        content: [
          {
            type: "text",
            text:
              `Applied: ${result.applied} action(s), ${result.failed} failed.\n` +
              (result.errors.length > 0
                ? `Errors: ${JSON.stringify(result.errors, null, 2)}`
                : ""),
          },
        ],
        details: {
          applied: result.applied,
          failed: result.failed,
          plan_id: result.plan_id,
        },
      };
    },
  });
}
