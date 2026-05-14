/**
 * Env-driven configuration. Loaded once at startup via dotenv, frozen, exported.
 *
 * Smith reads the user's `.env` file (or process.env) — it does NOT inherit
 * TEMPER's settings module since Smith is a different process. The variable
 * names match TEMPER's where they overlap (LLM_PROVIDER, LLM_API_KEY,
 * LLM_MODEL), and Smith maps them to the env vars pi-coding-agent expects
 * internally (ANTHROPIC_API_KEY / ANTHROPIC_OAUTH_TOKEN / OPENAI_API_KEY ...).
 */
import "dotenv/config";

type LlmProvider = "anthropic" | "openai" | "deepseek" | "google" | "ollama";

export interface SmithConfig {
  // Temper
  temperBaseUrl: string;
  temperApiKey: string;
  // LLM
  llmProvider: LlmProvider;
  llmApiKey: string;
  llmModel: string;
  // MCP
  mcpServers: string;  // raw env, parsed lazily by smith/extensions/mcp-bridge
  // HTTP control plane
  smithHost: string;
  smithPort: number;
}

function require_(name: string, value: string | undefined, fallback?: string): string {
  if (value && value.trim() !== "") return value.trim();
  if (fallback !== undefined) return fallback;
  throw new Error(`Missing required env var: ${name}`);
}

function loadConfig(): SmithConfig {
  const provider = (process.env.LLM_PROVIDER ?? "anthropic").trim() as LlmProvider;
  return Object.freeze({
    temperBaseUrl: require_("TEMPER_BASE_URL", process.env.TEMPER_BASE_URL, "http://127.0.0.1:18088"),
    temperApiKey: require_("TEMPER_API_KEY", process.env.TEMPER_API_KEY),
    llmProvider: provider,
    llmApiKey: require_("LLM_API_KEY", process.env.LLM_API_KEY),
    llmModel: require_("LLM_MODEL", process.env.LLM_MODEL, "claude-haiku-4-5"),
    mcpServers: (process.env.MCP_SERVERS ?? "").trim(),
    smithHost: require_("SMITH_HOST", process.env.SMITH_HOST, "127.0.0.1"),
    smithPort: Number(require_("SMITH_PORT", process.env.SMITH_PORT, "18099")),
  });
}

let _config: SmithConfig | null = null;
export function getConfig(): SmithConfig {
  if (_config === null) _config = loadConfig();
  return _config;
}

/**
 * Translate Smith's provider-agnostic LLM_* vars into the env vars pi-ai's
 * AuthStorage expects. Called once at startup before AuthStorage is created.
 *
 * Anthropic OAuth tokens (prefix `sk-ant-oat`) land in ANTHROPIC_OAUTH_TOKEN
 * which takes precedence over ANTHROPIC_API_KEY per pi's env-api-keys.ts.
 */
export function mapEnvForPi(cfg: SmithConfig): void {
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
    case "ollama":
      // ollama runs locally without a key; pi-ai treats it via base URL config.
      // No env var to set.
      break;
  }
  // Disable pi's install-telemetry attribution headers explicitly — we may
  // be running in a corporate network where any outbound metadata is
  // suspect. Doesn't affect LLM provider calls.
  process.env.PI_TELEMETRY = "0";
}
