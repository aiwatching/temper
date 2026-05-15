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
  //
  // Exposes most of TEMPER's search precision knobs to the model. Smith's
  // own before_agent_start hook already runs a high-precision auto-recall
  // (cross_encoder + min_score=0.3) and pastes hits into the prompt — so
  // the model only calls THIS tool when it needs something the auto-recall
  // missed. Common patterns the model should pick:
  //
  //   - "tell me more about X" right after recall surfaced X →
  //       memory_search(query="X", reranker="cross_encoder", min_score=0.5)
  //       for precision; or center=<X's uuid> to bias by graph distance.
  //   - "give me everything connected to entity X" →
  //       memory_search(query=..., bfs_origins=[<uuid>], bfs_max_depth=2)
  //       structural recall, not semantic guessing.
  //   - "as of <past date>" → as_of=<ISO>.
  //   - schema-typed queries ("who LIVES_IN Lyon?") →
  //       edge_types=["LIVES_IN"] / node_labels=["Person","Place"].
  pi.registerTool({
    name: "memory_search",
    label: "Search memory",
    description:
      "Semantic search over the user's long-term memory in TEMPER. Call " +
      "this when the auto-recall block at the top of your system prompt " +
      "didn't cover the question — the auto-recall already runs a " +
      "high-precision cross_encoder pass for every turn. Use this tool " +
      "when you need a more specific query, a tighter score threshold, " +
      "graph-topology biasing (center / bfs_origins), or type-filtered " +
      "results. Hits include fact, score, valid_at, invalid_at, id, " +
      "source_node_uuid, target_node_uuid — surface only the top 1–3 " +
      "paraphrased.",
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
            "(handles 'what was true last week?' questions). Defaults to " +
            "'now' on the server when you pass reranker=cross_encoder, " +
            "so retired/invalidated facts get filtered automatically.",
        }),
      ),
      namespaces: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Restrict scope. Omit to use your default agent namespace + " +
            "cross-agent recall. Pass ['user:me'] for the user's flat " +
            "cross-agent namespace; ['agent:me/<slug>'] for a specific " +
            "agent's private memory.",
        }),
      ),
      reranker: Type.Optional(
        Type.Union(
          [Type.Literal("rrf"), Type.Literal("mmr"), Type.Literal("cross_encoder")],
          {
            default: "rrf",
            description:
              "Ranking strategy. 'rrf' (default) is fast and free — good " +
              "for browse-style queries. 'cross_encoder' costs one LLM " +
              "call but gives true relevance in [0,1] — required if you " +
              "want to pair with min_score. 'mmr' optimizes for diversity " +
              "(less redundant hits).",
          },
        ),
      ),
      min_score: Type.Optional(
        Type.Number({
          minimum: 0,
          maximum: 1,
          description:
            "Relevance floor [0,1] — only meaningful with " +
            "reranker='cross_encoder'. Suggested values: 0.3 loose, " +
            "0.5 strict. Server-side filter; below-threshold hits are " +
            "dropped during reranking and never returned.",
        }),
      ),
      center: Type.Optional(
        Type.String({
          description:
            "Node UUID to bias ranking around. Facts/entities closer to " +
            "this node in the graph score higher. Pair with reranker " +
            "omitted (auto-swaps to node_distance) when you want " +
            "graph-topology recall (e.g. 'related to the User entity').",
        }),
      ),
      bfs_origins: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Seed entity UUIDs for a BFS walk. Returns every fact/entity " +
            "reachable within bfs_max_depth hops — true 'associated " +
            "information' retrieval, not semantic match. Use when the " +
            "user asks about a specific named entity and you want its " +
            "neighborhood, not a fuzzy keyword search.",
        }),
      ),
      bfs_max_depth: Type.Optional(
        Type.Integer({
          minimum: 1,
          maximum: 5,
          default: 2,
          description: "BFS hop limit. Default 2; rarely useful past 3.",
        }),
      ),
      edge_types: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Restrict to relations with these names (e.g. ['LIVES_IN', " +
            "'TEACHES']). Cheap server-side filter — use when the query " +
            "type is known.",
        }),
      ),
      node_labels: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Restrict entity hits to these labels (e.g. ['Person', " +
            "'Place']). Cheap server-side filter.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        query: string;
        limit?: number;
        as_of?: string;
        namespaces?: string[];
        reranker?: "rrf" | "mmr" | "cross_encoder";
        min_score?: number;
        center?: string;
        bfs_origins?: string[];
        bfs_max_depth?: number;
        edge_types?: string[];
        node_labels?: string[];
      },
    ) {
      const hits = await temper.search({
        query: params.query,
        limit: params.limit,
        asOf: params.as_of,
        namespaces: params.namespaces,
        reranker: params.reranker,
        minScore: params.min_score,
        center: params.center,
        bfsOrigins: params.bfs_origins,
        bfsMaxDepth: params.bfs_max_depth,
        edgeTypes: params.edge_types,
        nodeLabels: params.node_labels,
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

  // ─── memory_blocks (KV memory, NOT Graphiti) ────────────────────────
  //
  // The class of memory Graphiti is bad at: first-person assertions about
  // self / preferences / current state. Each block is a JSONB value under
  // a string key, optionally pinned (auto-included in system prompt).
  //
  // When to use what:
  //   "Call me X" / "I prefer Y" / "I'm working on Z" → remember(...)
  //   "Sarah teaches Portuguese" / "Bruno is Anna's student"  → memory_write(...)
  //   "What was true at <past time>"                          → memory_search(as_of=...)

  // ---- remember ----
  pi.registerTool({
    name: "remember",
    label: "Remember a user preference / state (KV memory)",
    description:
      "Save a structured key/value memory block. Use for first-person " +
      "assertions: nicknames, preferences, current focus, daily routine, " +
      "external bookmarks. NOT for third-party facts (use memory_write " +
      "for those). Set pinned=true if the user wants you to always know " +
      "this every turn (e.g. 'call me X', 'always greet me with Y'). " +
      "The block replaces any prior value at the same key — no merge.",
    parameters: Type.Object({
      key: Type.String({
        minLength: 1,
        description:
          "Dot-prefixed key. Convention: 'preferences.X' for likes/dislikes, " +
          "'persona.X' for identity facts, 'state.X' for current working " +
          "context, 'routine.X' for recurring patterns, 'bookmark.X' for " +
          "external links. Anything else is fine — caller picks.",
      }),
      value: Type.Any({
        description: "Block value. Any JSON shape. Replaces existing value.",
      }),
      pinned: Type.Optional(
        Type.Boolean({
          default: false,
          description:
            "true = auto-injected into the system prompt every turn. " +
            "Use sparingly — pinned content costs prompt tokens on " +
            "every call. Default false.",
        }),
      ),
      description: Type.Optional(
        Type.String({
          description:
            "One-liner shown to you on read so the block is self-documenting.",
        }),
      ),
      scope: Type.Optional(
        Type.Union(
          [Type.Literal("own"), Type.Literal("global")],
          {
            default: "own",
            description:
              "'own' (default) = visible only to this agent (Smith). " +
              "'global' = every agent under this user sees it. Use " +
              "'global' for cross-agent identity facts like the user's name.",
          },
        ),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        key: string;
        value: unknown;
        pinned?: boolean;
        description?: string;
        scope?: "own" | "global";
      },
    ) {
      const block = await temper.upsertMemoryBlock(params.key, {
        value: params.value,
        pinned: params.pinned,
        description: params.description,
        scope: params.scope ?? "own",
      });
      return {
        content: [
          {
            type: "text",
            text:
              `Remembered '${block.block_key}' = ${JSON.stringify(block.block_value)} ` +
              `(scope=${block.scope}, pinned=${block.pinned})`,
          },
        ],
        details: { key: block.block_key, scope: block.scope, pinned: block.pinned },
      };
    },
  });

  // ---- update_memory (deep JSONB merge) ----
  pi.registerTool({
    name: "update_memory",
    label: "Patch a memory block (deep merge)",
    description:
      "Deep-merge a partial JSON value into an existing memory block. " +
      "Use when adding/changing one field on a block whose value is an " +
      "object (e.g. preferences.drinks already exists as " +
      "{prefers: 'americano'}; add {avoids: ['latte']} via update_memory). " +
      "Lists and scalars are REPLACED, not appended.",
    parameters: Type.Object({
      key: Type.String({ minLength: 1 }),
      patch: Type.Any({
        description: "Partial JSON to deep-merge into the existing value.",
      }),
      scope: Type.Optional(
        Type.Union(
          [Type.Literal("own"), Type.Literal("global")],
          { default: "own" },
        ),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { key: string; patch: unknown; scope?: "own" | "global" },
    ) {
      try {
        const block = await temper.patchMemoryBlock(params.key, {
          value: params.patch,
          scope: params.scope ?? "own",
        });
        return {
          content: [{
            type: "text",
            text: `Updated '${block.block_key}' = ${JSON.stringify(block.block_value)}`,
          }],
          details: { key: block.block_key },
        };
      } catch (e) {
        const msg = e instanceof TemperError ? e.detail : (e as Error).message;
        return {
          content: [{ type: "text", text: `update_memory failed: ${msg}` }],
          details: { error: msg },
        };
      }
    },
  });

  // ---- forget ----
  pi.registerTool({
    name: "forget",
    label: "Forget (delete) a memory block",
    description:
      "Delete a memory block by key. Use when the user explicitly says " +
      "'forget X' or asks to remove a preference. Not for invalidating " +
      "facts (use memory_correct_apply for Graphiti facts).",
    parameters: Type.Object({
      key: Type.String({ minLength: 1 }),
      scope: Type.Optional(
        Type.Union(
          [Type.Literal("own"), Type.Literal("global")],
          { default: "own" },
        ),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { key: string; scope?: "own" | "global" },
    ) {
      try {
        await temper.deleteMemoryBlock(params.key, params.scope ?? "own");
        return {
          content: [{ type: "text", text: `Forgot '${params.key}'.` }],
          details: { key: params.key },
        };
      } catch (e) {
        const msg = e instanceof TemperError ? e.detail : (e as Error).message;
        return {
          content: [{ type: "text", text: `forget failed: ${msg}` }],
          details: { error: msg },
        };
      }
    },
  });

  // ---- get_memory ----
  pi.registerTool({
    name: "get_memory",
    label: "Fetch a memory block by key",
    description:
      "Look up a single memory block. Pinned blocks are already in your " +
      "system prompt every turn — only call this for non-pinned blocks " +
      "you actually need to read on demand (e.g. a bookmark URL). " +
      "Returns null if not found.",
    parameters: Type.Object({
      key: Type.String({ minLength: 1 }),
      scope: Type.Optional(
        Type.Union(
          [Type.Literal("own"), Type.Literal("global")],
          {
            default: "own",
            description:
              "'own' falls back to 'global' if not found in own scope.",
          },
        ),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { key: string; scope?: "own" | "global" },
    ) {
      const block = await temper.getMemoryBlock(params.key, params.scope ?? "own");
      if (block === null) {
        return {
          content: [{ type: "text", text: `(no block named '${params.key}')` }],
          details: { found: false },
        };
      }
      return {
        content: [{
          type: "text",
          text: JSON.stringify(block, null, 2),
        }],
        details: { found: true, key: block.block_key, scope: block.scope },
      };
    },
  });
}
