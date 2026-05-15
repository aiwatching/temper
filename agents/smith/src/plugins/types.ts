/**
 * Plugin = a thing that connects to an external service and exposes
 * tools the LLM can call.
 *
 * Smith has three planned kinds at v0.5:
 *   - mcp     : MCP server (stdio / http / sse). The first one to land.
 *   - http    : REST API described by an OpenAPI spec (later).
 *   - shell   : pre-configured shell command set (later).
 *   - builtin : Smith's own internal tool packs (memory, vault, tasks)
 *               packed as a "plugin" for uniformity in the listing UI.
 *
 * The pi runtime sees only `registerTool` results — it doesn't know
 * "plugin" exists. Plugin is Smith's layer for managing the
 * configuration / lifecycle / health of those tools' backing
 * services. See docs/smith-architecture.md § Plugin subsystem.
 */
export type PluginKind = "mcp" | "http" | "shell" | "builtin";

/** A tool exposed by a plugin. Plugin-local name (no slug prefix yet);
 *  the manager registers it with pi as `<slug>__<name>`. */
export interface PluginToolSpec {
  name: string;
  description: string;
  // JSON Schema for the tool's parameters. MCP servers return this
  // directly; HTTP plugins synthesize it from their OpenAPI op.
  inputSchema?: unknown;
}

/** Wire-shape of a tool invocation result. Matches pi's tool return
 *  contract (content blocks + isError + arbitrary details). */
export interface PluginInvokeResult {
  // Array of { type: "text" | "image" | ..., ... } content blocks.
  // (MCP's CallToolResult.content is already in this shape.)
  content: unknown;
  isError?: boolean;
  details?: Record<string, unknown>;
}

export interface PluginHealth {
  ok: boolean;
  toolCount?: number;
  ms?: number;
  error?: string;
}

export interface Plugin {
  readonly slug: string;
  readonly kind: PluginKind;
  enabled: boolean;

  /** Connect / set up the backing client. Called once per plugin
   *  lifecycle on Smith startup (or on plugin re-enable). Returns the
   *  tools this plugin exposes; the manager uses the spec to register
   *  pi tools. */
  connect(): Promise<PluginToolSpec[]>;

  /** Invoke a tool. `toolName` is the plugin-local name (no slug
   *  prefix — the manager strips that before dispatching). */
  invoke(toolName: string, args: unknown): Promise<PluginInvokeResult>;

  /** Ping the backing service. Used by the manager's poll loop to
   *  refresh `last_seen_at` / `last_tool_count` / `last_error` on the
   *  plugins table. Should be cheap (list tools, ping endpoint, etc). */
  health(): Promise<PluginHealth>;

  /** Tear down the connection. Called on Smith SIGINT/SIGTERM and when
   *  the plugin is deleted or disabled. */
  dispose(): Promise<void>;
}

// ─── kind-specific config shapes ─────────────────────────────────────
//
// Each kind defines its own `config_json` shape (validated by the
// repository before instantiating the plugin). The first kind to land
// is MCP; HTTP / Shell shapes will go here when those land.

export interface MCPConfig {
  transport: "stdio" | "http" | "sse";
  /** stdio: /abs/path/to/binary    http/sse: full URL */
  endpoint: string;
  /** stdio only — args passed to the spawned binary */
  args?: string[];
  /** auth (http / sse only — stdio is local + trusted) */
  auth?: {
    type: "none" | "bearer" | "header";
    /** when type='header', the header name (e.g. 'X-API-Key'). */
    header?: string;
  };
}

// ─── DB row shape ────────────────────────────────────────────────────

export interface PluginRow {
  slug: string;
  kind: PluginKind;
  display_name: string;
  config_json: string;          // raw JSON; parse to MCPConfig / HTTPConfig etc
  secret_ref: string | null;
  enabled: 0 | 1;
  last_seen_at: string | null;
  last_tool_count: number | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}
