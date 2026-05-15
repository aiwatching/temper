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
import { Temper, TemperError } from "../temper.js";

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

  // ---- memory_correct_apply (destructive — gated by A5) ----
  //
  // The end-to-end "this memory is wrong, fix it" path. Workflow the
  // model should run:
  //
  //   1. Call memory_search to find the wrong fact. Hits now carry
  //      `id` (fact UUID) + `source_node_uuid` (entity UUID for the
  //      "User" entity whose summary needs refreshing).
  //   2. Show the candidate fact text + UUIDs to the user. Get explicit
  //      confirmation that this is the fact to retire.
  //   3. Call memory_correct_apply with the UUIDs + the corrected
  //      content. The approval gate blocks the first attempt; user
  //      clicks Approve in the UI, the LLM retries.
  //
  // What this tool does in one shot:
  //   a. PATCH /v1/facts/<wrong_fact_uuid> with invalid_at=now
  //      → retires the wrong edge without nuking the source episode.
  //   b. POST /v1/episodes with the corrected content → new episode
  //      goes through Graphiti extraction the usual way.
  //   c. (optional but default) POST /v1/admin/entities/<uuid>/resummarize
  //      → re-runs the LLM summary so the stale text disappears from
  //      node.summary (the path that Graphiti's append-only update
  //      flow would otherwise never rewrite).
  //
  // We don't try to be clever: if (a) succeeds but (b) or (c) fails we
  // surface the partial outcome. The user can re-run safely — (a) is
  // idempotent (PATCHing invalid_at again with the same timestamp is a
  // no-op), (b) creates a new episode either way, (c) is just an LLM
  // summary rewrite.
  pi.registerTool({
    name: "memory_correct_apply",
    label: "Correct a wrong memory fact",
    description:
      "Retire a wrong fact + write the corrected version + (optional) " +
      "regenerate the entity summary. DESTRUCTIVE: invalidates a fact " +
      "edge in place. Use after the user confirms which hit from " +
      "memory_search is the one to fix. Workflow: search → show user → " +
      "they confirm → call this. The approval gate blocks the first call; " +
      "tell the user what's about to happen and wait for their Approve click.",
    parameters: Type.Object({
      wrong_fact_uuid: Type.String({
        description:
          "The `id` from a memory_search hit with kind='fact'. This is " +
          "the edge that gets `invalid_at = now` so it stops showing up " +
          "in recall.",
      }),
      corrected_content: Type.String({
        minLength: 1,
        description:
          "The correct version of the fact, written as a new episode. " +
          "Be explicit about subject + object so Graphiti's extraction " +
          "doesn't flip agency again (e.g. include the assistant's name " +
          "instead of '我'). One discrete fact, paraphrased.",
      }),
      entity_uuid: Type.Optional(
        Type.String({
          description:
            "Optional. The entity whose `.summary` should be regenerated " +
            "after the correction. Usually the `source_node_uuid` from " +
            "the same search hit. If omitted, the stale summary text " +
            "stays in place even though the underlying fact was retired.",
        }),
      ),
      namespace: Type.Optional(
        Type.String({
          description:
            "Namespace for the corrected episode. Default = wrong fact's " +
            "own namespace (rediscovered via getFact).",
        }),
      ),
      source_description: Type.Optional(
        Type.String({
          default: "user-confirmed correction via smith",
          description: "Source description for the corrected episode.",
        }),
      ),
      tags: Type.Optional(
        Type.Array(Type.String(), {
          default: ["correction"],
          description: "Tags on the corrected episode. Defaults to ['correction'].",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        wrong_fact_uuid: string;
        corrected_content: string;
        entity_uuid?: string;
        namespace?: string;
        source_description?: string;
        tags?: string[];
      },
    ) {
      const steps: string[] = [];
      const details: Record<string, unknown> = { wrong_fact_uuid: params.wrong_fact_uuid };

      // (a) PATCH the wrong fact. Look it up first so we can echo the
      // exact text back to the user (sanity check) AND learn its
      // namespace for step (b) when the caller didn't supply one.
      let ns = params.namespace;
      try {
        const fact = await temper.getFact(params.wrong_fact_uuid);
        if (!ns) ns = fact.namespace;
        details.wrong_fact_text = fact.fact;
        details.wrong_fact_namespace = fact.namespace;
      } catch (e) {
        const msg = e instanceof TemperError ? e.detail : (e as Error).message;
        return {
          content: [{ type: "text", text: `getFact failed: ${msg}` }],
          details: { ...details, error: "getFact failed", message: msg },
        };
      }

      try {
        const r = await temper.invalidateFact(params.wrong_fact_uuid);
        steps.push(`✓ invalidated fact ${r.id}: "${r.fact}" (invalid_at=${r.invalid_at})`);
        details.invalidated_at = r.invalid_at;
      } catch (e) {
        const msg = e instanceof TemperError ? e.detail : (e as Error).message;
        return {
          content: [{ type: "text", text: `invalidateFact failed: ${msg}` }],
          details: { ...details, error: "invalidate failed", message: msg },
        };
      }

      // (b) Write the corrected episode.
      try {
        const w = (await temper.write({
          content: params.corrected_content,
          sourceType: "message",
          sourceDescription: params.source_description ?? "user-confirmed correction via smith",
          tags: params.tags ?? ["correction"],
          namespace: ns,
        })) as { episode_id?: string };
        steps.push(`✓ wrote corrected episode ${w.episode_id ?? "(no id)"} in ${ns}`);
        details.corrected_episode_id = w.episode_id;
        details.corrected_namespace = ns;
      } catch (e) {
        const msg = e instanceof TemperError ? e.detail : (e as Error).message;
        steps.push(`✗ corrected write failed: ${msg}`);
        details.write_error = msg;
      }

      // (c) Resummarize the entity. Skip cleanly when no UUID given.
      if (params.entity_uuid) {
        try {
          const r = await temper.resummarizeEntity(params.entity_uuid);
          steps.push(
            `✓ resummarized entity ${r.name ?? r.id} ` +
            `(${r.source_episode_count} source episodes${r.note ? `; note: ${r.note}` : ""})`,
          );
          details.resummarized = {
            id: r.id,
            before: r.summary_before,
            after: r.summary_after,
            source_episode_count: r.source_episode_count,
          };
        } catch (e) {
          const msg = e instanceof TemperError ? e.detail : (e as Error).message;
          steps.push(`✗ resummarize failed: ${msg}`);
          details.resummarize_error = msg;
        }
      } else {
        steps.push(
          "◌ entity_uuid not supplied — stale text may persist in entity.summary; " +
          "pass `source_node_uuid` from the search hit next time.",
        );
      }

      return {
        content: [{ type: "text", text: steps.join("\n") }],
        details,
      };
    },
  });
}
