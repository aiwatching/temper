/**
 * One-time migration: import `MCP_SERVERS` env var into the SQLite
 * plugins registry.
 *
 * Runs on Smith startup. Conditions for actually doing anything:
 *   - the `plugins` table is empty (so we only do this on a fresh
 *     install, never on subsequent boots — we'd rather have the
 *     operator manage via /plugins UI after the first import)
 *   - the MCP_SERVERS env var is set + parseable
 *
 * After successful import we log a notice telling the operator they
 * can remove MCP_SERVERS from .env to avoid double registration
 * (the legacy mcp-bridge.ts extension still reads the env var until
 * removed).
 *
 * Format of MCP_SERVERS (unchanged from mcp-bridge.ts):
 *
 *   name=URL,name=URL,...
 *
 *   stdio:///abs/path/to/bin            stdio transport
 *   http(s)://host/path                 streamable HTTP transport
 *
 * Auth wasn't supported in the env path; imported entries get auth
 * type='none'. Add an API key via /plugins UI's "Rotate secret".
 */
import { getConfig } from "../config.js";
import { listPlugins, upsertPlugin } from "./repository.js";
import type { MCPConfig } from "./types.js";

interface EnvSpec {
  slug: string;
  transport: "stdio" | "http" | "sse";
  endpoint: string;
}

function parseEnv(raw: string): EnvSpec[] {
  const out: EnvSpec[] = [];
  for (const piece of raw.split(",")) {
    const trimmed = piece.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    if (eq <= 0) continue;
    const slug = trimmed.slice(0, eq).trim();
    const url = trimmed.slice(eq + 1).trim();
    if (!slug || !url) continue;
    if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug)) {
      console.warn(`[plugins.migrate] skipping invalid slug '${slug}' — must match /^[a-z0-9][a-z0-9_-]*$/`);
      continue;
    }
    if (url.startsWith("stdio://")) {
      out.push({ slug, transport: "stdio", endpoint: url.replace(/^stdio:\/\//, "") });
    } else if (url.startsWith("http://") || url.startsWith("https://")) {
      out.push({ slug, transport: "http", endpoint: url });
    } else {
      console.warn(`[plugins.migrate] skipping '${slug}' — unsupported transport URL '${url}'`);
    }
  }
  return out;
}

/** Import MCP_SERVERS into the plugins table. No-op when the table
 *  already has rows OR the env var is unset/empty. Safe to call on
 *  every boot — re-runs are no-ops. */
export function migrateEnvMcpServers(): number {
  if (listPlugins().length > 0) return 0;          // table not empty — skip
  const cfg = getConfig();
  const raw = (cfg.mcpServers ?? "").trim();
  if (!raw) return 0;

  const specs = parseEnv(raw);
  if (specs.length === 0) return 0;

  for (const s of specs) {
    const config: MCPConfig = { transport: s.transport, endpoint: s.endpoint };
    upsertPlugin({
      slug: s.slug,
      kind: "mcp",
      display_name: s.slug,
      config,
      enabled: true,
    });
    console.log(
      `[plugins.migrate] imported '${s.slug}' (${s.transport}) from MCP_SERVERS`,
    );
  }
  console.log(
    `[plugins.migrate] imported ${specs.length} MCP server(s) from MCP_SERVERS. ` +
    `Now manageable via /plugins. Remove MCP_SERVERS from .env to avoid ` +
    `double registration (legacy mcp-bridge.ts still reads it).`,
  );
  return specs.length;
}
