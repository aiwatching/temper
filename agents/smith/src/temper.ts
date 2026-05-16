/**
 * Temper HTTP client. Smith is a Temper client — nothing in this file
 * imports memory-service internals. Mirrors the shape of temper.py from
 * the earlier Python scaffold but uses the global `fetch` (Node 18+).
 *
 * If Smith needs a memory primitive Temper doesn't expose yet, the rule
 * is to add the endpoint in Temper first, then call it from here.
 */
import { getConfig } from "./config.js";

export class TemperError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`Temper ${status}: ${detail}`);
    this.status = status;
    this.detail = detail;
  }
}

export interface WriteEpisodeArgs {
  content: string;
  sourceType?: "message" | "text" | "json";
  sourceDescription?: string;
  referenceTime?: string;        // ISO-8601
  tags?: string[];
  saga?: string;
  namespace?: string;            // omit to use the key's default scope
  asyncExtract?: boolean;
}

export interface SearchArgs {
  query: string;
  limit?: number;
  namespaces?: string[];
  nodeLabels?: string[];
  edgeTypes?: string[];
  asOf?: string;                 // ISO-8601
  // "rrf" (default, no LLM, rank-based scores), "mmr" (diversity),
  // "cross_encoder" (LLM-rescored, true relevance in [0,1] — comparable
  // across queries, suitable for threshold filtering).
  reranker?: "rrf" | "mmr" | "cross_encoder";
  // Relevance floor — passed through to Graphiti's
  // SearchConfig.reranker_min_score so server-side reranking drops
  // below-threshold hits before they leave the search code. Only
  // useful with reranker="cross_encoder" (true [0,1] scores). Range
  // [0,1]; reasonable values: 0.3 (loose), 0.5 (strict).
  minScore?: number;
  // Graph-topology biasing. center re-ranks hits by proximity to this
  // node UUID (Graphiti auto-swaps to node_distance reranker when rrf
  // is used + center is set). bfsOrigins walks the graph N hops from
  // these seed UUIDs and includes everything reached — true
  // "associated information" retrieval, not semantic-match. Pair with
  // bfsMaxDepth to control the radius.
  center?: string;
  bfsOrigins?: string[];
  bfsMaxDepth?: number;
}

export interface SearchHit {
  fact?: string;
  name?: string;
  score?: number;
  valid_at?: string | null;
  invalid_at?: string | null;
  source_episode_ids?: string[];
  // UUIDs — populated server-side. `id` is the edge UUID when kind="fact"
  // and the entity UUID when kind="entity". memory_correct uses these
  // to invalidate / resummarize without a follow-up lookup.
  id?: string;
  source_node_uuid?: string;
  target_node_uuid?: string;
  kind?: "fact" | "entity" | "community";
  namespace?: string;
  // pass-through of anything else the server returns; the consumer
  // (an LLM tool) sees the raw structure.
  [k: string]: unknown;
}

export interface EpisodeSummary {
  episode_id: string;
  namespace: string;
  created_by_agent?: string;
  source_type: string;
  tags?: string[];
  reference_time?: string | null;
  created_at: string;
}

export interface EpisodeDetail extends EpisodeSummary {
  content: string;
  entities?: Array<{ uuid: string; name: string; summary?: string }>;
  facts?: Array<{ uuid: string; fact: string }>;
}

export interface PlannedAction {
  type: "invalidate_fact" | "delete_fact" | "delete_episode";
  target_id: string;
  reason: string;
  kept_id: string | null;
  label: string;
}

export interface ConsolidatePlan {
  plan_id: string;
  namespace: string;
  mode: string;
  created_at: string;
  expires_at: string;
  counts: {
    invalidate_fact: number;
    delete_fact: number;
    delete_episode: number;
    total: number;
  };
  actions: PlannedAction[];
}

export interface ConsolidateApplyResult {
  plan_id: string;
  namespace: string;
  applied: number;
  failed: number;
  errors: Array<{ action_type: string; target_id: string; error: string }>;
  started_at: string;
  completed_at: string;
}

export interface ConsolidateStatus {
  namespace: string;
  state: {
    status: "sleeping";
    mode: string;
    started_at: string;
    ttl_seconds_remaining: number;
  } | null;
}

export interface FactDetail {
  id: string;
  namespace: string;
  fact: string;
  name: string | null;
  source_uuid: string;
  target_uuid: string;
  source_name: string | null;
  target_name: string | null;
  valid_at: string | null;
  invalid_at: string | null;
  created_at: string | null;
  episodes: string[];
}

export interface FactInvalidateResult {
  id: string;
  namespace: string;
  fact: string;
  valid_at: string | null;
  invalid_at: string | null;
}

export interface ResummarizeResult {
  id: string;
  namespace: string;
  name: string | null;
  summary_before: string;
  summary_after: string;
  source_episode_count: number;
  note?: string;
}

export interface BuildCommunitiesResult {
  namespace: string;
  communities_created: number;
  community_edges_created: number;
}

// ─── memory_blocks ──────────────────────────────────────────────────────
// Structured per-user key/value memory — the storage for first-person
// assertions (nickname, preferences, daily routine) that Graphiti's
// entity extraction is structurally bad at. See TEMPER's
// core/blocks.py for the design rationale.

export type BlockScope = "own" | "global" | "both";

export interface MemoryBlock {
  id: string;
  user_id: string;
  agent_slug: string;
  block_key: string;
  block_value: unknown;
  pinned: boolean;
  priority: number;
  description: string | null;
  created_at: string;
  updated_at: string;
  updated_by: string | null;
  scope: "own" | "global";
}

export interface UpsertBlockArgs {
  value: unknown;
  pinned?: boolean;
  priority?: number;
  description?: string;
  scope?: BlockScope;
  agentSlug?: string;
}

export interface PatchBlockArgs {
  value?: unknown;             // deep-merge target (JSONB)
  pinned?: boolean;
  priority?: number;
  description?: string;
  scope?: BlockScope;
  agentSlug?: string;
}

export interface ListBlocksArgs {
  scope?: BlockScope;          // default: "both"
  pinned?: boolean;
  prefix?: string;
}

export class Temper {
  private baseUrl: string;
  private apiKey: string;

  constructor() {
    const cfg = getConfig();
    this.baseUrl = cfg.temperBaseUrl.replace(/\/+$/, "");
    this.apiKey = cfg.temperApiKey;
  }

  private async req<T = unknown>(
    method: string,
    path: string,
    opts: { body?: unknown; params?: Record<string, string | number | boolean | undefined> } = {},
  ): Promise<T> {
    const url = new URL(this.baseUrl + path);
    for (const [k, v] of Object.entries(opts.params ?? {})) {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    }
    const r = await fetch(url, {
      method,
      headers: {
        "X-API-Key": this.apiKey,
        "Content-Type": "application/json",
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (r.status >= 400) {
      let detail = "";
      try {
        const j = (await r.json()) as { detail?: string | unknown };
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? r.statusText);
      } catch {
        detail = await r.text();
      }
      throw new TemperError(r.status, detail);
    }
    if (r.status === 204) return undefined as T;
    return (await r.json()) as T;
  }

  async write(args: WriteEpisodeArgs): Promise<unknown> {
    const body: Record<string, unknown> = {
      content: args.content,
      source_type: args.sourceType ?? "message",
    };
    if (args.sourceDescription !== undefined) body.source_description = args.sourceDescription;
    if (args.referenceTime !== undefined) body.reference_time = args.referenceTime;
    if (args.tags) body.tags = args.tags;
    if (args.saga) body.saga = args.saga;
    if (args.namespace) body.namespace = args.namespace;
    return this.req("POST", "/v1/episodes", {
      body,
      params: args.asyncExtract ? { async_extract: "true" } : undefined,
    });
  }

  async search(args: SearchArgs): Promise<SearchHit[]> {
    const params: Record<string, string | number | undefined> = {
      query: args.query,
      limit: args.limit ?? 10,
    };
    if (args.namespaces?.length) params.namespaces = args.namespaces.join(",");
    if (args.nodeLabels?.length) params.node_labels = args.nodeLabels.join(",");
    if (args.edgeTypes?.length) params.edge_types = args.edgeTypes.join(",");
    if (args.asOf) params.as_of = args.asOf;
    if (args.center) params.center = args.center;
    if (args.bfsOrigins?.length) params.bfs_origins = args.bfsOrigins.join(",");
    if (args.bfsMaxDepth !== undefined) params.bfs_max_depth = args.bfsMaxDepth;
    if (args.reranker) params.reranker = args.reranker;
    if (args.minScore !== undefined) params.min_score = args.minScore;
    // NOTE: TEMPER returns `facts`, not `hits`. The variable name in the
    // client API stays SearchHit because that's what callers expect to
    // see in tool output — the wire field is just where it lives.
    const body = await this.req<{ facts?: SearchHit[] }>("GET", "/v1/search", { params });
    return body.facts ?? [];
  }

  async listEpisodes(args: {
    namespace?: string;
    limit?: number;
    before?: string;
  } = {}): Promise<EpisodeSummary[]> {
    const params: Record<string, string | number | undefined> = {
      limit: args.limit ?? 20,
    };
    if (args.namespace) params.namespace = args.namespace;
    if (args.before) params.before = args.before;
    const body = await this.req<{ episodes?: EpisodeSummary[] }>(
      "GET",
      "/v1/episodes",
      { params },
    );
    return body.episodes ?? [];
  }

  async getEpisode(episodeId: string): Promise<EpisodeDetail> {
    return this.req<EpisodeDetail>("GET", `/v1/episodes/${episodeId}`);
  }

  async consolidatePlan(args: {
    namespace: string;
    mode?: "dedup-exact" | "dedup-semantic" | "cleanup-tags" | "all";
  }): Promise<ConsolidatePlan> {
    return this.req("POST", "/v1/consolidate/plan", {
      body: { namespace: args.namespace, mode: args.mode ?? "all" },
    });
  }

  async consolidateApply(planId: string): Promise<ConsolidateApplyResult> {
    return this.req("POST", "/v1/consolidate/apply", {
      body: { plan_id: planId },
    });
  }

  async consolidateStatus(namespace: string): Promise<ConsolidateStatus> {
    return this.req("GET", "/v1/consolidate/status", { params: { namespace } });
  }

  // ---- correction primitives ----

  /** Look up a fact (RELATES_TO edge) by UUID. Used by memory_correct
   *  to confirm the right fact was found before mutating it. */
  async getFact(factUuid: string): Promise<FactDetail> {
    return this.req<FactDetail>("GET", `/v1/facts/${factUuid}`);
  }

  /** PATCH a fact's `invalid_at`. Pass `null` to reactivate.
   *  Omitting `invalidAt` defaults to "now on the server".
   *  Used by memory_correct to retire a wrong fact in place. */
  async invalidateFact(
    factUuid: string,
    invalidAt: string | null = new Date().toISOString(),
  ): Promise<FactInvalidateResult> {
    return this.req<FactInvalidateResult>("PATCH", `/v1/facts/${factUuid}`, {
      body: { invalid_at: invalidAt },
    });
  }

  /** Rebuild an entity's summary from its source episodes (LLM call).
   *  Pair with invalidateFact so the next recall doesn't carry the
   *  stale wording forward in `node.summary`. */
  async resummarizeEntity(entityUuid: string): Promise<ResummarizeResult> {
    return this.req<ResummarizeResult>(
      "POST",
      `/v1/admin/entities/${entityUuid}/resummarize`,
    );
  }

  /** Cluster entities + rebuild community summaries for a namespace.
   *  Communities are LLM-summarized neighborhoods — one community ≈
   *  N entities in coverage, so memory_search picks them up as
   *  dense, low-token recall context. Heavy operation; trigger from
   *  a scheduled job after consolidate, not from the hot path. */
  async buildCommunities(namespace?: string): Promise<BuildCommunitiesResult> {
    const params: Record<string, string> = {};
    if (namespace) params.namespace = namespace;
    return this.req<BuildCommunitiesResult>("POST", "/v1/admin/communities/build", { params });
  }

  // ─── memory_blocks (KV memory, not Graphiti) ──────────────────────

  async listMemoryBlocks(args: ListBlocksArgs = {}): Promise<MemoryBlock[]> {
    const params: Record<string, string | boolean> = {};
    if (args.scope) params.scope = args.scope;
    if (args.pinned !== undefined) params.pinned = args.pinned;
    if (args.prefix) params.prefix = args.prefix;
    const body = await this.req<{ blocks?: MemoryBlock[] }>(
      "GET", "/v1/memory/blocks", { params },
    );
    return body.blocks ?? [];
  }

  async getMemoryBlock(
    key: string, scope: BlockScope = "own",
  ): Promise<MemoryBlock | null> {
    try {
      return await this.req<MemoryBlock>(
        "GET", `/v1/memory/blocks/${encodeURIComponent(key)}`,
        { params: { scope } },
      );
    } catch (e) {
      if (e instanceof TemperError && e.status === 404) return null;
      throw e;
    }
  }

  async upsertMemoryBlock(key: string, args: UpsertBlockArgs): Promise<MemoryBlock> {
    return this.req<MemoryBlock>(
      "PUT", `/v1/memory/blocks/${encodeURIComponent(key)}`,
      {
        body: {
          value: args.value,
          pinned: args.pinned,
          priority: args.priority,
          description: args.description,
          scope: args.scope,
          agent_slug: args.agentSlug,
        },
      },
    );
  }

  async patchMemoryBlock(key: string, args: PatchBlockArgs): Promise<MemoryBlock> {
    return this.req<MemoryBlock>(
      "PATCH", `/v1/memory/blocks/${encodeURIComponent(key)}`,
      {
        body: {
          value: args.value,
          pinned: args.pinned,
          priority: args.priority,
          description: args.description,
          scope: args.scope,
          agent_slug: args.agentSlug,
        },
      },
    );
  }

  async deleteMemoryBlock(
    key: string, scope: BlockScope = "own", agentSlug?: string,
  ): Promise<void> {
    const params: Record<string, string> = { scope };
    if (agentSlug) params.agent_slug = agentSlug;
    await this.req<void>(
      "DELETE", `/v1/memory/blocks/${encodeURIComponent(key)}`, { params },
    );
  }

  // ─── typed memory API (/v1/memory/tasks, /focus, /preferences, … ) ──
  //
  // Thin wrappers around TEMPER's typed endpoints. The whole point is
  // that TEMPER decides where each kind of memory lands (block vs
  // graphiti, scope, pinned, priority). This client must NEVER second-
  // guess that routing — if you find yourself reaching for
  // upsertMemoryBlock("state.active_tasks", ...) in agent code, that's
  // the bug we built this layer to prevent. Call addTask() instead.

  async addTask(args: {
    title: string;
    status?: "todo" | "doing" | "blocked";
    priority?: number;
    notes?: string;
  }): Promise<TypedTask> {
    return this.req<TypedTask>("POST", "/v1/memory/tasks", {
      body: {
        title: args.title,
        status: args.status ?? "todo",
        priority: args.priority ?? 50,
        notes: args.notes,
      },
    });
  }

  async listTasks(status?: "todo" | "doing" | "blocked"): Promise<TypedTask[]> {
    const body = await this.req<{ tasks?: TypedTask[] }>(
      "GET", "/v1/memory/tasks",
      { params: status ? { status } : undefined },
    );
    return body.tasks ?? [];
  }

  async updateTask(
    taskId: string,
    patch: {
      title?: string;
      status?: "todo" | "doing" | "blocked";
      priority?: number;
      notes?: string;
    },
  ): Promise<TypedTask> {
    return this.req<TypedTask>(
      "PATCH", `/v1/memory/tasks/${encodeURIComponent(taskId)}`,
      { body: patch },
    );
  }

  async completeTask(
    taskId: string,
    summary?: string,
  ): Promise<TaskCompleteResult> {
    return this.req<TaskCompleteResult>(
      "POST", `/v1/memory/tasks/${encodeURIComponent(taskId)}/complete`,
      { body: { summary } },
    );
  }

  async getFocus(): Promise<TypedFocus> {
    return this.req<TypedFocus>("GET", "/v1/memory/focus");
  }

  async setFocus(value: string, note?: string): Promise<TypedFocus> {
    return this.req<TypedFocus>("PUT", "/v1/memory/focus", {
      body: { value, note },
    });
  }

  async listPreferences(): Promise<TypedPreference[]> {
    const body = await this.req<{ preferences?: TypedPreference[] }>(
      "GET", "/v1/memory/preferences",
    );
    return body.preferences ?? [];
  }

  async setPreference(
    key: string, value: unknown, description?: string,
  ): Promise<TypedPreference> {
    return this.req<TypedPreference>(
      "PUT", `/v1/memory/preferences/${encodeURIComponent(key)}`,
      { body: { value, description } },
    );
  }

  async noteEvent(args: {
    content: string;
    namespace?: string;
    referenceTime?: string;
    tags?: string[];
    saga?: string;
  }): Promise<NoteEventResult> {
    return this.req<NoteEventResult>("POST", "/v1/memory/events", {
      body: {
        content: args.content,
        namespace: args.namespace,
        reference_time: args.referenceTime,
        tags: args.tags,
        saga: args.saga,
      },
    });
  }

  /** One-call read bundle for before_agent_start. Returns pinned blocks
   *  + structured shortcuts (active_tasks / focus / preferences) +
   *  graphiti recall against `query`. Replaces three separate calls
   *  (listMemoryBlocks + search agent + search user:me). */
  async getTurnContext(args: {
    query?: string;
    recallLimit?: number;
    namespaces?: string[];
  } = {}): Promise<TurnContext> {
    const params: Record<string, string | number> = {};
    if (args.query) params.query = args.query;
    if (args.recallLimit !== undefined) params.recall_limit = args.recallLimit;
    if (args.namespaces?.length) params.namespaces = args.namespaces.join(",");
    return this.req<TurnContext>("GET", "/v1/memory/turn_context", { params });
  }

  async health(): Promise<unknown> {
    return this.req("GET", "/v1/health");
  }

  async whoami(): Promise<{ id?: string; email?: string }> {
    return this.req("GET", "/v1/auth/me");
  }
}

// ─── typed memory wire shapes ──────────────────────────────────────────

export interface TypedTask {
  id: string;
  title: string;
  status: "todo" | "doing" | "blocked" | "done";
  priority: number;
  notes?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskCompleteResult {
  completed: TypedTask;
  episode_id: string;
}

export interface TypedFocus {
  value: string | null;
  updated_at: string | null;
  episode_id?: string | null;
}

export interface TypedPreference {
  key: string;
  value: unknown;
  description: string | null;
  updated_at: string;
}

export interface NoteEventResult {
  episode_id: string;
  namespace: string;
  created_at: string;
}

export interface PinnedBlockWire {
  key: string;
  value: unknown;
  priority: number;
  description: string | null;
  scope: "own" | "global";
}

export interface RecalledEpisodeWire {
  episode_id: string | null;
  namespace: string;
  fact: string;
  score: number;
  valid_at: string | null;
  invalid_at: string | null;
}

export interface TurnContext {
  active_tasks: TypedTask[];
  current_focus: string | null;
  preferences: Record<string, unknown>;
  pinned_blocks: PinnedBlockWire[];
  recalled_episodes: RecalledEpisodeWire[];
  namespaces_searched: string[];
}
