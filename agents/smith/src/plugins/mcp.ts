/**
 * MCPPlugin — implements the Plugin interface for an MCP server.
 *
 * Ported from `extensions/mcp-bridge.ts`, with two changes:
 *   1) config comes from the SQLite plugins table (not the
 *      MCP_SERVERS env var). Smith now learns about MCP servers
 *      from the registry the user maintains via /plugins UI.
 *   2) auth is now first-class (bearer / custom header), with the
 *      secret resolved by the manager and passed in at construction.
 *      The old env-driven path had no auth — it worked only for
 *      stdio binaries on localhost.
 *
 * Transports supported here: stdio, http (StreamableHTTP), sse.
 * The MCP SDK's STDIO transport spawns the binary directly; HTTP /
 * SSE talk to a server over the network.
 */
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import type {
  MCPConfig,
  Plugin,
  PluginHealth,
  PluginInvokeResult,
  PluginToolSpec,
} from "./types.js";

export class MCPPlugin implements Plugin {
  public readonly kind = "mcp" as const;
  private client: Client | null = null;
  private cachedTools: PluginToolSpec[] = [];

  constructor(
    public readonly slug: string,
    public enabled: boolean,
    private readonly config: MCPConfig,
    /** Plaintext secret resolved by the manager via loadSecret().
     *  Null for plugins without auth (e.g. local stdio). */
    private readonly secret: string | null,
  ) {}

  async connect(): Promise<PluginToolSpec[]> {
    const client = new Client(
      { name: `smith-plugin:${this.slug}`, version: "0.0.1" },
      { capabilities: {} },
    );
    await client.connect(this.makeTransport());
    this.client = client;

    const { tools } = await client.listTools();
    this.cachedTools = tools.map((t) => ({
      name: t.name,
      description: t.description ?? `${this.slug}.${t.name}`,
      inputSchema: t.inputSchema ?? { type: "object" },
    }));
    return this.cachedTools;
  }

  private makeTransport():
    | StdioClientTransport
    | StreamableHTTPClientTransport {
    const { transport, endpoint, args } = this.config;
    if (transport === "stdio") {
      return new StdioClientTransport({ command: endpoint, args: args ?? [] });
    }
    if (transport === "http" || transport === "sse") {
      const headers: Record<string, string> = {};
      if (this.secret && this.config.auth) {
        const { type, header } = this.config.auth;
        if (type === "bearer") {
          headers["Authorization"] = `Bearer ${this.secret}`;
        } else if (type === "header" && header) {
          headers[header] = this.secret;
        }
      }
      return new StreamableHTTPClientTransport(new URL(endpoint), {
        requestInit: { headers },
      });
    }
    throw new Error(`MCPPlugin ${this.slug}: unsupported transport '${transport}'`);
  }

  async invoke(toolName: string, args: unknown): Promise<PluginInvokeResult> {
    if (!this.client) {
      throw new Error(`MCPPlugin ${this.slug}: not connected (invoke before connect?)`);
    }
    const result = await this.client.callTool({
      name: toolName,
      arguments: (args ?? {}) as Record<string, unknown>,
    });
    return {
      content: result.content,
      isError: Boolean(result.isError),
      details: { plugin: this.slug, tool: toolName, kind: "mcp" },
    };
  }

  async health(): Promise<PluginHealth> {
    if (!this.client) return { ok: false, error: "not connected" };
    const start = Date.now();
    try {
      const { tools } = await this.client.listTools();
      return { ok: true, toolCount: tools.length, ms: Date.now() - start };
    } catch (e) {
      return { ok: false, error: (e as Error).message };
    }
  }

  async dispose(): Promise<void> {
    if (this.client) {
      try {
        await this.client.close();
      } catch {
        /* best-effort */
      }
      this.client = null;
    }
  }
}
