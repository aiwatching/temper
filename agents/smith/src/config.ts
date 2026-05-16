/**
 * Smith's runtime configuration.
 *
 * History:
 *   v1 — loaded once from `.env` at startup, frozen, exported. The
 *        operator had to manage 10+ env vars and restart on every
 *        change.
 *   v2 (current) — DB-first via `db/settings.ts`, with `.env`
 *        kept only as the bootstrap source for SMITH_SECRET_KEY
 *        (auto-generated on first run; encrypts the DB itself, so
 *        it can't live IN the DB). Everything else is editable
 *        live from /settings; changes take effect on the next
 *        getConfig() call (sub-second cache).
 *
 * The shape of `SmithConfig` is unchanged across versions — every
 * call site (server.ts, session-manager.ts, plugins, scheduler)
 * keeps using `getConfig().X`. The change is purely in WHERE the
 * values come from.
 *
 * Bootstrap order matters: this module imports `db/sqlite.ts` so
 * `runMigrations()` MUST run before the first getConfig() call.
 * index.ts handles that.
 */
import "dotenv/config";

import { getSecretSetting, getSetting } from "./db/settings.js";

// pi-ai accepts arbitrary provider strings — built-ins are looked up by
// name against its catalog; anything else is treated as a custom provider
// (we register it explicitly via ModelRegistry.registerProvider).
type LlmProvider = string;

export interface SmithConfig {
  // IANA timezone name (e.g. "Asia/Shanghai", "America/Los_Angeles").
  // Used to render "now" for the system prompt, to interpret natural-
  // language times in user messages ("9am tomorrow"), and as the
  // default zone for cron-style triggers when those land. Auto-detected
  // on first boot from Intl.DateTimeFormat().resolvedOptions().timeZone;
  // user can override from /settings.
  timezone: string;
  // Temper
  temperBaseUrl: string;
  temperApiKey: string;
  // LLM
  llmProvider: LlmProvider;
  llmApiKey: string;
  llmModel: string;
  // When set, Smith registers an OpenAI-compatible custom provider with
  // pi's ModelRegistry pointed at this base URL — for internal company
  // LLM gateways (e.g. http://nac-ai.fortinet-us.com:7001/v1). Leave
  // empty to use pi-ai's built-in provider for cfg.llmProvider.
  llmBaseUrl: string;
  // The slug used when the operator created Smith's TEMPER API key.
  smithAgentSlug: string;
  // MCP server config — legacy env path. New plugins live in the
  // SQLite plugins table; this is kept for the one-time env→DB
  // migration in plugins/migrate_env.ts.
  mcpServers: string;
  // HTTP control plane. Service-level — hard-coded defaults; not
  // user-tunable. Override only via SMITH_HOST / SMITH_PORT env if
  // running in a non-standard container layout.
  smithHost: string;
  smithPort: number;
  // Bearer token gating /chat /approve /deny /pending /plugins
  // (JSON sides; HTML GETs stay open). Empty = no auth (dev mode,
  // pair with smithHost=127.0.0.1). The setup wizard generates and
  // stores one by default.
  smithSecret: string;
  // Periodic consolidate. 0 = disabled.
  consolidateScheduleHours: number;
  consolidateAutoApply: boolean;
}

// ----------------------- read paths -----------------------------------

/**
 * The names we use in the settings table. Same as the bullet list in
 * db/settings.ts (where they're documented).
 */
const KEYS = {
  timezone: "smith.timezone",
  temperBaseUrl: "temper.base_url",
  temperApiKey: "temper.api_key",
  llmProvider: "llm.provider",
  llmApiKey: "llm.api_key",
  llmModel: "llm.model",
  llmBaseUrl: "llm.base_url",
  smithAgentSlug: "smith.agent_slug",
  smithSecret: "smith.bearer_secret",
  consolidateScheduleHours: "consolidate.schedule_hours",
  consolidateAutoApply: "consolidate.auto_apply",
} as const;

/** OS-detected timezone. Used as the default when no setting is
 *  stored yet, and as the fallback if the stored value is invalid.
 *  Falls back to "UTC" on the unusual case where Intl can't resolve. */
export function detectSystemTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (tz && typeof tz === "string") return tz;
  } catch {
    /* ignore */
  }
  return "UTC";
}

/** Validate an IANA timezone string. Returns the input on success,
 *  the system default on failure — so a typo in /settings never
 *  brings Smith down, it just falls back. */
export function validateTimezone(tz: string): string {
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: tz });
    return tz;
  } catch {
    return detectSystemTimezone();
  }
}

function envStr(name: string, fallback = ""): string {
  return (process.env[name] ?? "").trim() || fallback;
}

function envNum(name: string, fallback: number): number {
  const v = Number(process.env[name] ?? "");
  return Number.isFinite(v) && v >= 0 ? v : fallback;
}

function envBool(name: string): boolean {
  return /^(1|true|yes)$/i.test((process.env[name] ?? "").trim());
}

/** Read a string setting from DB; fall back to env; fall back to default. */
function dbStr(key: string, envName: string, dflt = ""): string {
  const v = getSetting(key);
  if (typeof v === "string" && v.trim()) return v.trim();
  return envStr(envName, dflt);
}

function dbNum(key: string, envName: string, dflt: number): number {
  const v = getSetting(key);
  if (typeof v === "number" && Number.isFinite(v)) return v;
  return envNum(envName, dflt);
}

function dbBool(key: string, envName: string): boolean {
  const v = getSetting(key);
  if (typeof v === "boolean") return v;
  return envBool(envName);
}

function dbSecret(key: string, envName: string): string {
  // Prefer the DB-encrypted secret. Fall back to env for the
  // first-boot moment before the setup wizard has run.
  const v = getSecretSetting(key);
  if (v !== null) return v;
  return envStr(envName, "");
}

/**
 * Return the current config snapshot. NOT memoized — every call
 * re-reads SQLite (< 1 ms) so settings UI edits take effect on
 * the next call. Secrets go through Fernet decrypt; cheap enough
 * not to worry about.
 *
 * Bootstrap caveat: if `db/sqlite.ts` hasn't been initialized yet
 * (i.e. runMigrations() hasn't run), getDb() will create the file
 * and the queries return empty — fallback to env covers that
 * window. index.ts orders migrations BEFORE the first getConfig().
 */
export function getConfig(): SmithConfig {
  // Service-level: never DB-managed. Defaults work for 99% of
  // single-user deployments.
  const smithHost = envStr("SMITH_HOST", "127.0.0.1");
  const smithPort = envNum("SMITH_PORT", 18099);

  return {
    timezone: validateTimezone(dbStr(KEYS.timezone, "SMITH_TIMEZONE", detectSystemTimezone())),
    temperBaseUrl: dbStr(KEYS.temperBaseUrl, "TEMPER_BASE_URL", "http://127.0.0.1:18088"),
    temperApiKey: dbSecret(KEYS.temperApiKey, "TEMPER_API_KEY"),
    llmProvider: dbStr(KEYS.llmProvider, "LLM_PROVIDER", ""),
    llmApiKey: dbSecret(KEYS.llmApiKey, "LLM_API_KEY"),
    llmModel: dbStr(KEYS.llmModel, "LLM_MODEL", ""),
    llmBaseUrl: dbStr(KEYS.llmBaseUrl, "LLM_BASE_URL", ""),
    smithAgentSlug: dbStr(KEYS.smithAgentSlug, "SMITH_AGENT_SLUG", "smith"),
    smithSecret: dbSecret(KEYS.smithSecret, "SMITH_SECRET"),
    smithHost,
    smithPort,
    // mcpServers stays env-only — legacy import path, retired by
    // plugins/migrate_env.ts on first boot.
    mcpServers: envStr("MCP_SERVERS"),
    consolidateScheduleHours: Math.max(
      0, dbNum(KEYS.consolidateScheduleHours, "CONSOLIDATE_SCHEDULE_HOURS", 0),
    ),
    consolidateAutoApply: dbBool(KEYS.consolidateAutoApply, "CONSOLIDATE_AUTO_APPLY"),
  };
}

// ----------------------- pi env adapter --------------------------------

/**
 * Translate Smith's provider-agnostic LLM_* values into the env vars
 * pi-ai's AuthStorage expects when using a built-in provider. Called
 * once at startup AND whenever the LLM key/provider changes (so
 * settings UI edits flow through to pi).
 *
 * For CUSTOM providers (cfg.llmBaseUrl set), session-manager.ts
 * calls pi.registerProvider with the key looked up by literal env
 * name "LLM_API_KEY" — we set that env var here so the lookup
 * works whether the key came from .env or the DB.
 */
export function mapEnvForPi(cfg: SmithConfig = getConfig()): void {
  // Disable pi's install-telemetry attribution headers explicitly — we may
  // be running in a corporate network where any outbound metadata is
  // suspect. Doesn't affect LLM provider calls.
  process.env.PI_TELEMETRY = "0";

  // Custom provider: pi reads LLM_API_KEY by literal name.
  if (cfg.llmApiKey) {
    process.env.LLM_API_KEY = cfg.llmApiKey;
  }

  if (cfg.llmBaseUrl) {
    return;
  }
  switch (cfg.llmProvider) {
    case "anthropic": {
      const target = cfg.llmApiKey.startsWith("sk-ant-oat")
        ? "ANTHROPIC_OAUTH_TOKEN"
        : "ANTHROPIC_API_KEY";
      process.env[target] = cfg.llmApiKey;
      break;
    }
    case "openai":
      process.env.OPENAI_API_KEY = cfg.llmApiKey;
      break;
    case "deepseek":
      process.env.DEEPSEEK_API_KEY = cfg.llmApiKey;
      break;
    case "google":
      process.env.GEMINI_API_KEY = cfg.llmApiKey;
      break;
  }
}

// Re-export so /setup wizard can write the same key names callers
// here use without hard-coding strings everywhere.
export const SETTING_KEYS = KEYS;
