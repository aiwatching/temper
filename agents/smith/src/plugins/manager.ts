/**
 * PluginManager — singleton owning the live plugin pool + hot-reload.
 *
 * Responsibilities:
 *   - On Smith startup: read enabled plugins from SQLite, instantiate
 *     them (kind-dispatched), connect each, return tool specs so the
 *     bootstrap can `pi.registerTool` for every (slug, tool) pair.
 *   - Periodic poll (default 30s): diff the DB against the in-memory
 *     pool, hot-reload what we can:
 *       * row disabled → remove its tools from pi's active set,
 *                        dispose client
 *       * row re-enabled (and previously connected) → re-add its
 *                        tools to the active set
 *       * row's config or secret changed → reconnect the underlying
 *                        client, keep the pi.registerTool entry
 *                        unchanged (execute() closes over a mutable
 *                        client reference held by the Plugin instance)
 *       * row deleted → same as disabled + dispose
 *       * new row → log a "restart required" notice; pi has no
 *                   unregisterTool so we can't add new tool names
 *                   without re-running the extension load phase
 *   - On dispose (SIGINT/SIGTERM): close every plugin's client.
 *   - On lookup: route a tool invocation by slug back to the right
 *     plugin instance.
 *
 * Why a singleton: only one Smith process owns one set of MCP
 * connections; no reason for multiple manager instances.
 */
import { createHash } from "node:crypto";

import type { Plugin, PluginRow, PluginToolSpec } from "./types.js";
import {
  listPlugins,
  loadSecret,
  setPluginHealth,
} from "./repository.js";
import { MCPPlugin } from "./mcp.js";

interface ManagedPlugin {
  plugin: Plugin;
  tools: PluginToolSpec[];
  /** Hash of (config_json || secret_ref || secret_hash) so we can
   *  cheaply detect changes during poll without re-decrypting every
   *  secret every tick. Updated whenever we (re)connect this plugin. */
  signature: string;
  /** Was this plugin in the active tool set last we checked? Tracks
   *  pi.setActiveTools state so we don't churn the call every poll. */
  active: boolean;
}

/** What the manager emits for the bootstrap to register with pi. */
export interface RegistrableTool {
  slug: string;             // plugin slug
  toolName: string;         // plugin-local tool name (no prefix yet)
  spec: PluginToolSpec;
  plugin: Plugin;           // for invoke() dispatch (proxy below)
}

/** Stable signature of a plugin row's connection-relevant bits. */
function rowSignature(row: PluginRow): string {
  const secret = row.secret_ref ? (loadSecret(row.slug) ?? "") : "";
  return createHash("sha256")
    .update(row.config_json)
    .update("|")
    .update(row.secret_ref ?? "")
    .update("|")
    .update(secret)
    .digest("hex")
    .slice(0, 16);
}

/** Wraps a Plugin so the pi tool's execute closure can call the
 *  CURRENT plugin instance even after the manager swaps it during a
 *  hot reconnect. Without this indirection, pi.registerTool would
 *  capture the *original* Plugin and keep using the disposed client. */
class PluginProxy implements Plugin {
  constructor(
    public readonly slug: string,
    private current: Plugin,
  ) {}
  get kind() { return this.current.kind; }
  get enabled() { return this.current.enabled; }
  set enabled(v: boolean) { this.current.enabled = v; }
  swap(next: Plugin): void { this.current = next; }
  connect() { return this.current.connect(); }
  invoke(t: string, a: unknown) { return this.current.invoke(t, a); }
  health() { return this.current.health(); }
  dispose() { return this.current.dispose(); }
}

class PluginManager {
  /** Slug → managed entry. Lifetime spans the whole Smith process. */
  private plugins = new Map<string, ManagedPlugin>();
  /** Slug → proxy. The execute closures in pi.registerTool capture
   *  these; never reassigned after first creation in this map. */
  private proxies = new Map<string, PluginProxy>();
  /** The pi extension hands this in once on load so we can call
   *  `pi.setActiveTools` from the poll loop. */
  private pi: { setActiveTools: (names: string[]) => void } | null = null;
  /** Tool names we have *registered* with pi at startup (one per
   *  (slug, tool)). The "active" subset is whatever's enabled now. */
  private registeredToolNames = new Set<string>();
  private pollTimer: NodeJS.Timeout | null = null;

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
        const plugin = await this.instantiate(row);
        const tools = await plugin.connect();
        const proxy = new PluginProxy(row.slug, plugin);
        this.proxies.set(row.slug, proxy);
        this.plugins.set(row.slug, {
          plugin, tools, signature: rowSignature(row), active: true,
        });
        setPluginHealth(row.slug, { ok: true, toolCount: tools.length });
        for (const spec of tools) {
          const fullName = `${row.slug}__${spec.name}`;
          this.registeredToolNames.add(fullName);
          out.push({ slug: row.slug, toolName: spec.name, spec, plugin: proxy });
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

  /** Install the pi handle + start polling. Called once after loadAll
   *  by the plugin-system extension. Polling tick = 30s; bumpable via
   *  PLUGIN_POLL_SECONDS env. */
  startPolling(pi: { setActiveTools: (names: string[]) => void }): void {
    this.pi = pi;
    const sec = Math.max(5, Number(process.env.PLUGIN_POLL_SECONDS ?? "30") || 30);
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => {
      this.poll().catch((e) => {
        console.warn(`[plugins] poll crash: ${(e as Error).message}`);
      });
    }, sec * 1000);
    console.log(`[plugins] hot-reload poll every ${sec}s`);
  }

  /** Single diff tick. Idempotent. */
  async poll(): Promise<void> {
    const rows = listPlugins();
    const seen = new Set<string>();

    for (const row of rows) {
      seen.add(row.slug);
      const managed = this.plugins.get(row.slug);

      if (!managed) {
        // New row — can't hot-add (pi has no unregisterTool). Log once
        // per slug to nudge the operator without spamming every tick.
        if (!this.newSlugWarned.has(row.slug)) {
          console.log(
            `[plugins] new row '${row.slug}' — restart Smith to register its tools`,
          );
          this.newSlugWarned.add(row.slug);
        }
        continue;
      }

      const sig = rowSignature(row);
      const enabled = row.enabled === 1;

      if (sig !== managed.signature) {
        // Config or secret changed — rebuild the client.
        try {
          await this.reconnect(row);
          managed.signature = sig;
        } catch (e) {
          const msg = (e as Error).message;
          console.warn(`[plugins] ${row.slug} reconnect failed: ${msg}`);
          setPluginHealth(row.slug, { ok: false, error: msg });
        }
      }

      // enable/disable toggle — pi.setActiveTools delta.
      if (enabled !== managed.active) {
        managed.active = enabled;
        this.recomputeActiveTools();
        console.log(`[plugins] ${row.slug} ${enabled ? "enabled" : "disabled"}`);
      }
    }

    // Deleted rows — drop from pool + active list.
    for (const slug of [...this.plugins.keys()]) {
      if (seen.has(slug)) continue;
      const managed = this.plugins.get(slug)!;
      try { await managed.plugin.dispose(); } catch { /* best-effort */ }
      this.plugins.delete(slug);
      this.proxies.delete(slug);
      console.log(`[plugins] ${slug} removed (DB row deleted)`);
    }
    this.recomputeActiveTools();
  }

  /** Rebuild a plugin's client in place. Keeps the proxy + the pi
   *  tool registrations untouched (execute() still works). */
  private async reconnect(row: PluginRow): Promise<void> {
    const managed = this.plugins.get(row.slug)!;
    const proxy = this.proxies.get(row.slug)!;
    const oldPlugin = managed.plugin;

    const next = await this.instantiate(row);
    const tools = await next.connect();

    proxy.swap(next);
    managed.plugin = next;
    managed.tools = tools;
    setPluginHealth(row.slug, { ok: true, toolCount: tools.length });

    try { await oldPlugin.dispose(); } catch { /* best-effort */ }
    console.log(
      `[plugins] ${row.slug}: reconnected (config/secret changed), ${tools.length} tools`,
    );
  }

  /** Push the current enabled-tool set to pi.setActiveTools. Cheap +
   *  idempotent; pi handles "already active" correctly. */
  private recomputeActiveTools(): void {
    if (!this.pi) return;
    const active: string[] = [];
    for (const [slug, m] of this.plugins) {
      if (!m.active) continue;
      for (const t of m.tools) active.push(`${slug}__${t.name}`);
    }
    // Note: this only controls plugin tools. The non-plugin tools
    // (memory_*, vault_*, builtin, etc.) are NOT in
    // this.registeredToolNames so we musn't clobber them; pi merges
    // the call against its full registered list, dropping our names
    // that aren't in `active` and keeping everything else as-is.
    // ...EXCEPT pi.setActiveTools is "the full active set", not a
    // delta. So we need to include the names we DON'T own too. We
    // don't have that list from our side; the safe thing is to only
    // ever pass our own tool universe and let pi merge — but pi
    // doesn't do that.
    //
    // Pragmatic fix: read pi.getActiveTools() back, replace our
    // slice, push the union. The pi extension's plugin-system.ts
    // wires getActiveTools in alongside setActiveTools.
    if (this.getActiveTools) {
      const current = this.getActiveTools();
      const owned = this.registeredToolNames;
      const keep = current.filter((n) => !owned.has(n));
      this.pi.setActiveTools([...keep, ...active]);
    } else {
      // Fallback: just push our names. Risk losing other extensions'
      // tools — extension wires getActiveTools so this branch
      // shouldn't run in production.
      this.pi.setActiveTools(active);
    }
  }
  /** Set by the extension at startPolling time so we can compute the
   *  union without clobbering non-plugin tools. */
  private getActiveTools: (() => string[]) | null = null;
  setGetActiveTools(fn: () => string[]): void { this.getActiveTools = fn; }

  /** Track slugs we've already warned about so we log "restart
   *  required" once per slug, not every 30s. */
  private newSlugWarned = new Set<string>();

  private async instantiate(row: PluginRow): Promise<Plugin> {
    const config = JSON.parse(row.config_json);
    const secret = loadSecret(row.slug);
    switch (row.kind) {
      case "mcp":
        return new MCPPlugin(row.slug, true, config, secret);
      // case "http":    TODO P5+
      // case "shell":   TODO P5+
      // case "builtin": TODO (wrapping temper-memory etc.)
      default:
        throw new Error(`unsupported plugin kind '${row.kind}'`);
    }
  }

  /** Resolve a plugin by slug. Used by the pi tool's execute closure
   *  (which actually receives the proxy — same shape, follows
   *  reconnects). */
  get(slug: string): Plugin | undefined {
    return this.proxies.get(slug);
  }

  /** Tear down every plugin's client. Called from SIGINT/SIGTERM. */
  async disposeAll(): Promise<void> {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    for (const [slug, m] of this.plugins.entries()) {
      try {
        await m.plugin.dispose();
      } catch (e) {
        console.warn(`[plugins] dispose ${slug} failed: ${(e as Error).message}`);
      }
    }
    this.plugins.clear();
    this.proxies.clear();
  }
}

let _mgr: PluginManager | null = null;
export function getPluginManager(): PluginManager {
  if (!_mgr) _mgr = new PluginManager();
  return _mgr;
}
