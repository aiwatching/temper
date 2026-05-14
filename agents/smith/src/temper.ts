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
  center?: string;
}

export interface SearchHit {
  fact?: string;
  name?: string;
  score?: number;
  valid_at?: string | null;
  invalid_at?: string | null;
  source_episode_ids?: string[];
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

  async health(): Promise<unknown> {
    return this.req("GET", "/v1/health");
  }

  async whoami(): Promise<{ id?: string; email?: string }> {
    return this.req("GET", "/v1/auth/me");
  }
}
