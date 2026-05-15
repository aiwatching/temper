/**
 * PluginManager — singleton owning the live plugin pool.
 *
 * Responsibilities:
 *   - On Smith startup: read enabled plugins from SQLite, instantiate
 *     them (kind-dispatched), connect each, return tool specs so the
 *     bootstrap can `pi.registerTool` for every (slug, tool) pair.
 *   - On dispose (SIGINT/SIGTERM): close every plugin's client.
 *   - On lookup: route a tool invocation by slug back to the right
 *     plugin instance.
 *
 * What this DOESN'T do (yet):
 *   - Hot reload (P4). New rows in the plugins table won't appear
 *     until Smith restarts. Editing config / secret of an existing
 *     enabled plugin is also restart-only for now; we'll add a
 *     diff-based reconnector when needed.
 *   - Periodic health pings (P4). Health is set once at connect time
 *     in `loadAll`; later we'll setInterval-poll.
 *
 * Why a singleton: only one Smith process owns one set of MCP
 * connections; no reason for multiple manager instances.
 */
import type { Plugin, PluginToolSpec } from "./types.js";
import {
  listPlugins,
  loadSecret,
  setPluginHealth,
} from "./repository.js";
import { MCPPlugin } from "./mcp.js";

interface ManagedPlugin {
  plugin: Plugin;
  tools: PluginToolSpec[];
}

/** What the manager emits for the bootstrap to register with pi. */
export interface RegistrableTool {
  slug: string;             // plugin slug
  toolName: string;         // plugin-local tool name (no prefix yet)
  spec: PluginToolSpec;
  plugin: Plugin;           // for invoke() dispatch
}

class PluginManager {
  private plugins = new Map<string, ManagedPlugin>();

  /** Connect every enabled plugin from the DB. Returns the full list
   *  of (plugin, tool) pairs the bootstrap should expose via pi.
   *
   *  Connect failures are logged + recorded as `last_error` but DON'T
   *  abort the boot — Smith still starts in a degraded mode with
   *  whatever plugins did come up.  */
  async loadAll(): Promise<RegistrableTool[]> {
    const out: RegistrableTool[] = [];
    for (const row of listPlugins({ enabledOnly: true })) {
      try {
        const plugin = await this.instantiate(row.slug, row.kind, row.config_json);
        const tools = await plugin.connect();
        this.plugins.set(row.slug, { plugin, tools });
        setPluginHealth(row.slug, { ok: true, toolCount: tools.length });
        for (const spec of tools) {
          out.push({ slug: row.slug, toolName: spec.name, spec, plugin });
        }
        console.log(
          `[plugins] ${row.slug} (${row.kind}): connected, ${tools.length} tools`,
        );
      } catch (e) {
        const msg = (e as Error).message;
        console.error(
          `[plugins] ${row.slug}: connect failed (${msg}). Continuing without it.`,
        );
        setPluginHealth(row.slug, { ok: false, error: msg });
      }
    }
    return out;
  }

  private async instantiate(
    slug: string,
    kind: string,
    configJson: string,
  ): Promise<Plugin> {
    const config = JSON.parse(configJson);
    const secret = loadSecret(slug);

    switch (kind) {
      case "mcp":
        return new MCPPlugin(slug, true, config, secret);
      // case "http":  TODO P5+
      // case "shell": TODO P5+
      // case "builtin": TODO (wrapping temper-memory etc.)
      default:
        throw new Error(`unsupported plugin kind '${kind}'`);
    }
  }

  /** Resolve a plugin by slug. Used by the pi tool's execute closure. */
  get(slug: string): Plugin | undefined {
    return this.plugins.get(slug)?.plugin;
  }

  /** Tear down every plugin's client. Called from SIGINT/SIGTERM. */
  async disposeAll(): Promise<void> {
    for (const [slug, m] of this.plugins.entries()) {
      try {
        await m.plugin.dispose();
      } catch (e) {
        console.warn(`[plugins] dispose ${slug} failed: ${(e as Error).message}`);
      }
    }
    this.plugins.clear();
  }
}

let _mgr: PluginManager | null = null;
export function getPluginManager(): PluginManager {
  if (!_mgr) _mgr = new PluginManager();
  return _mgr;
}
