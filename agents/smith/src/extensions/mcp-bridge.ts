/**
 * pi extension: bridges external MCP servers into pi's tool surface.
 *
 * Why this exists: pi-coding-agent has no built-in MCP support (author's
 * deliberate stance — see https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/).
 * The user's company exposes internal systems via MCP, so we adapt at
 * the edge here: list each server's tools at startup, register one pi
 * tool per MCP tool with `pi.registerTool`, route calls through the MCP
 * client at execute time.
 *
 * Tool names are prefixed `<server-name>__<tool-name>` so two MCP
 * servers exposing a `search` tool don't collide.
 *
 * Env var format (MCP_SERVERS):
 *   `<name>=<URL>,<name>=<URL>,...`
 *
 *   stdio:///absolute/path/to/binary    spawn that binary, no args (MVP)
 *   http(s)://host/path                 HTTP-streamable MCP server
 *
 * Args / headers / authentication for MCP servers will land here as the
 * use case demands. Keep this thin for now.
 */
import { Type } from "typebox";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

import { getConfig } from "../config.js";

interface McpServerSpec {
  name: string;
  transportUrl: string;
}

export function parseMcpServers(raw: string): McpServerSpec[] {
  const out: McpServerSpec[] = [];
  for (const piece of raw.split(",")) {
    const trimmed = piece.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const name = trimmed.slice(0, eq).trim();
    const url = trimmed.slice(eq + 1).trim();
    if (!name || !url) continue;
    out.push({ name, transportUrl: url });
  }
  return out;
}

async function openMcpClient(spec: McpServerSpec): Promise<Client> {
  const client = new Client(
    { name: `smith-mcp-bridge:${spec.name}`, version: "0.0.1" },
    { capabilities: {} },
  );
  if (spec.transportUrl.startsWith("stdio:")) {
    // stdio:///abs/path/to/bin → spawn that binary directly. No args for
    // MVP — wrap in a shell script if you need args.
    const path = spec.transportUrl.replace(/^stdio:\/\//, "");
    if (!path) throw new Error(`Bad stdio MCP URL for ${spec.name}: ${spec.transportUrl}`);
    const transport = new StdioClientTransport({ command: path, args: [] });
    await client.connect(transport);
  } else if (spec.transportUrl.startsWith("http://") || spec.transportUrl.startsWith("https://")) {
    const transport = new StreamableHTTPClientTransport(new URL(spec.transportUrl));
    await client.connect(transport);
  } else {
    throw new Error(
      `Unsupported MCP transport for ${spec.name}: ${spec.transportUrl} ` +
      `(want stdio:///path or http(s)://host/path)`,
    );
  }
  return client;
}

// biome-ignore lint: pi.ExtensionAPI types are in flux — see temper-memory.ts.
type PiExtensionAPI = any;

/**
 * Connect to every configured MCP server, list tools, register each as a
 * pi tool. Returns the list of opened MCP clients so the caller can keep
 * them alive (don't let them be GC'd) and close them on shutdown.
 *
 * Best-effort per server: if one MCP server is down, we log and skip it
 * instead of refusing to start. Smith should still boot in a degraded
 * mode when an internal system is unreachable.
 */
export async function mcpBridgeExtension(pi: PiExtensionAPI): Promise<Client[]> {
  const specs = parseMcpServers(getConfig().mcpServers);
  if (specs.length === 0) return [];

  const clients: Client[] = [];
  for (const spec of specs) {
    try {
      const client = await openMcpClient(spec);
      clients.push(client);
      const { tools } = await client.listTools();
      for (const tool of tools) {
        const piName = `${spec.name}__${tool.name}`;
        pi.registerTool({
          name: piName,
          label: tool.name,
          description:
            (tool.description ?? `${spec.name}.${tool.name}`) +
            `\n\n(MCP server: ${spec.name})`,
          // MCP gives us JSON Schema. TypeBox accepts opaque JSON Schema
          // via Type.Unsafe — pi just forwards the schema to the LLM, so
          // this is safe at runtime even though we lose static typing
          // on params at compile time.
          parameters: Type.Unsafe<Record<string, unknown>>(tool.inputSchema ?? { type: "object" }),
          async execute(_toolCallId: string, params: unknown) {
            const result = await client.callTool({
              name: tool.name,
              arguments: (params ?? {}) as Record<string, unknown>,
            });
            // MCP CallToolResult.content is already in the
            // { type: "text" | "image" | ... } shape pi expects, so we
            // pass it through. isError surfaces tool-level errors so the
            // LLM can decide whether to retry / fall back.
            return {
              content: result.content,
              details: { mcpServer: spec.name, mcpTool: tool.name },
              isError: result.isError ?? false,
            };
          },
        });
      }
      console.log(`[mcp] ${spec.name}: connected, ${tools.length} tools registered`);
    } catch (e) {
      console.error(
        `[mcp] ${spec.name}: failed to connect/register (${(e as Error).message}). Continuing without it.`,
      );
    }
  }
  return clients;
}
