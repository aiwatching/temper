/**
 * pi extension: load every enabled Plugin from the SQLite registry,
 * connect them, register one pi tool per (plugin, tool) pair.
 *
 * Tool names are `<plugin_slug>__<tool_name>`, matching the
 * convention the old mcp-bridge.ts established (so existing
 * approval-store rules etc. continue to work — the tool naming
 * surface for the LLM hasn't changed shape).
 *
 * Per docs/smith-architecture.md:
 *   - this extension owns the registerTool surface for all plugins
 *   - dispose is handled in index.ts via getPluginManager().disposeAll()
 *
 * Coexistence note: while we're in P1+P2, the older env-driven
 * mcp-bridge.ts can still run alongside. The two surfaces will only
 * collide if the same MCP server is configured in both env AND DB —
 * in that case pi.registerTool throws on duplicate name, and the
 * later registration loses. Operators migrating from env to DB
 * should remove MCP_SERVERS once a server is in the DB.
 */
import { Type } from "typebox";

import { getPluginManager } from "../plugins/manager.js";

// biome-ignore lint: pi.ExtensionAPI's types are in flux — see other extensions.
type PiExtensionAPI = any;

export async function pluginSystemExtension(pi: PiExtensionAPI): Promise<void> {
  const mgr = getPluginManager();
  const tools = await mgr.loadAll();

  for (const { slug, toolName, spec, plugin } of tools) {
    pi.registerTool({
      name: `${slug}__${toolName}`,
      label: toolName,
      description:
        (spec.description ?? `${slug}.${toolName}`) +
        `\n\n(plugin: ${slug})`,
      // MCP returns JSON Schema; HTTP plugins will too. typebox accepts
      // arbitrary JSON Schema via Type.Unsafe — pi forwards it to the
      // LLM as-is, so we lose static typing here (acceptable: the
      // schema is the contract).
      parameters: Type.Unsafe<Record<string, unknown>>(
        (spec.inputSchema as Record<string, unknown>) ?? { type: "object" },
      ),
      async execute(_toolCallId: string, args: unknown) {
        const result = await plugin.invoke(toolName, args);
        return {
          content: result.content,
          details: result.details,
          isError: result.isError ?? false,
        };
      },
    });
  }
}
