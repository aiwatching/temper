/**
 * Per-conversation AgentSession pool.
 *
 * pi's AgentSession is single-flight: while .prompt() is running, you queue
 * follow-ups but can't run a second prompt concurrently. To let smith
 * handle multiple users / chat windows in parallel we keep one Session
 * per conversation_id and create on demand.
 *
 * Sessions are now disk-persisted as JSONL — one file per conversation_id
 * under `<cwd>/.data/smith-sessions/<id>.jsonl`. Restart resumes prior
 * conversation history. Semantic memory still flows through TEMPER (the
 * two layers complement each other: JSONL = "what was just said in this
 * thread", TEMPER = "what I know about this user across all threads").
 */
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join, resolve as resolvePath } from "node:path";

import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager as PiSessionManager,
  AuthStorage,
  ModelRegistry,
} from "@earendil-works/pi-coding-agent";

import { getConfig, type SmithConfig } from "./config.js";
import { approvalGateExtension } from "./extensions/approval-gate.js";
import { compactionPolicyExtension } from "./extensions/compaction-policy.js";
import { scheduledJobsExtension } from "./extensions/scheduled-jobs.js";
import { temperMemoryExtension } from "./extensions/temper-memory.js";
import { typedMemoryExtension } from "./extensions/typed-memory.js";
import { mcpBridgeExtension } from "./extensions/mcp-bridge.js";
import { pluginSystemExtension } from "./extensions/plugin-system.js";
import { smithPersonalityExtension } from "./extensions/smith-personality.js";

// biome-ignore lint: pi's AgentSession type isn't re-exported cleanly yet.
type AgentSession = Awaited<ReturnType<typeof createAgentSession>>["session"];

// System prompt lives in src/extensions/smith-personality.ts and is
// injected via pi's `before_agent_start` event — see comments there
// for why this is non-optional for the memory discipline to fire.

/**
 * Sanitise a conversation_id into something safe to use as a filename.
 * Whitelist alnum + dash + underscore; anything else collapses to '_'.
 * 64-char cap. Falls back to "default" for an empty input.
 */
function safeConvId(raw: string): string {
  const cleaned = raw.replace(/[^A-Za-z0-9_-]/g, "_").slice(0, 64);
  return cleaned || "default";
}

function sessionFilePath(conversationId: string): string {
  const root = resolvePath(process.cwd(), ".data", "smith-sessions");
  mkdirSync(root, { recursive: true });
  return join(root, `${safeConvId(conversationId)}.jsonl`);
}

/**
 * Get a pi SessionManager pointed at the conversation's JSONL file.
 * - File exists → resumes (loads prior entries, points the manager at it).
 * - File missing → touch empty + open; pi's setSessionFile() detects the
 *   empty file, calls newSession() internally, and starts writing a
 *   fresh session header at our chosen path.
 *
 * Returns the path too so the caller can log / surface it.
 */
function loadSessionManager(conversationId: string): {
  sm: PiSessionManager;
  path: string;
  resumed: boolean;
} {
  const path = sessionFilePath(conversationId);
  const resumed = existsSync(path);
  if (!resumed) writeFileSync(path, "");      // touch so open() accepts it
  return { sm: PiSessionManager.open(path), path, resumed };
}

class SmithSessionPool {
  private sessions = new Map<string, AgentSession>();
  private authStorage = AuthStorage.create();
  private modelRegistry = ModelRegistry.create(this.authStorage);
  private customProviderRegistered = false;

  /**
   * One-shot: when LLM_BASE_URL is set we register the configured
   * model against the configured provider on pi's ModelRegistry. This
   * works for two cases:
   *
   *   1. A brand-new provider name (e.g. "forti") — registry stores it.
   *   2. A built-in provider name (e.g. "deepseek") — registry override
   *      replaces the catalog model list with ours. Useful when a
   *      corporate gateway emulates an OpenAI-style API for its own
   *      model id (here: "forti-k2") that's not in pi-ai's catalog.
   *
   * Idempotent: called once per process. `compat.supportsDeveloperRole`
   * + `supportsReasoningEffort` are both off — most internal gateways
   * don't speak those modern OpenAI extensions yet, and turning them
   * off keeps the wire-format conservative.
   */
  private ensureCustomProvider(cfg: SmithConfig): void {
    if (this.customProviderRegistered || !cfg.llmBaseUrl) return;
    this.modelRegistry.registerProvider(cfg.llmProvider, {
      baseUrl: cfg.llmBaseUrl,
      // String value is the ENV VAR NAME pi reads at request time.
      // LLM_API_KEY is already in process.env via dotenv.
      apiKey: "LLM_API_KEY",
      api: "openai-completions",
      authHeader: true,
      models: [
        {
          id: cfg.llmModel,
          name: cfg.llmModel,
          reasoning: false,
          input: ["text"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 128_000,
          maxTokens: 4096,
          compat: {
            supportsDeveloperRole: false,
            supportsReasoningEffort: false,
          },
        },
      ],
    });
    this.customProviderRegistered = true;
  }

  async getOrCreate(conversationId: string): Promise<AgentSession> {
    const existing = this.sessions.get(conversationId);
    if (existing) return existing;

    const cfg = getConfig();
    this.ensureCustomProvider(cfg);

    // Pull the resolved Model from the registry — works for built-in
    // providers (catalog) and for custom ones we just registered.
    const model = this.modelRegistry.find(cfg.llmProvider, cfg.llmModel);
    if (!model) {
      throw new Error(
        `Model not found: provider=${cfg.llmProvider} model=${cfg.llmModel}. ` +
        (cfg.llmBaseUrl
          ? "Custom-provider registration was attempted; check baseUrl and LLM_PROVIDER spelling."
          : "Either pick a model in pi-ai's catalog " +
            "(node_modules/@earendil-works/pi-ai/dist/models.generated.d.ts) " +
            "or set LLM_BASE_URL to register a custom OpenAI-compatible gateway."),
      );
    }

    // Skills + prompt templates live under <cwd>/.smith/. Drop a .md
    // there with frontmatter and pi auto-loads it. See
    // .smith/skills/example.md for the shape. The directory ships
    // with the repo; teams can layer their own bundle on top
    // (planned: `@fortinet/smith-skills` npm package — roadmap B6).
    const smithRoot = resolvePath(process.cwd(), ".smith");
    const skillsPath = resolvePath(smithRoot, "skills");
    const promptsPath = resolvePath(smithRoot, "prompts");
    mkdirSync(skillsPath, { recursive: true });
    mkdirSync(promptsPath, { recursive: true });

    const resourceLoader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: process.cwd(),                  // we don't ship ~/.pi-style assets
      additionalSkillPaths: [skillsPath],
      additionalPromptTemplatePaths: [promptsPath],
      extensionFactories: [
        // Order matters: temper-memory must be available even if MCP /
        // plugin setup fails partway through. Personality goes first
        // so the system prompt is in place before any tool sees a turn.
        // approvalGate + compactionPolicy register last — they're
        // pure event listeners, no tools.
        //
        // plugin-system and mcp-bridge coexist during the P1-P4
        // transition. plugin-system reads the SQLite registry (empty
        // until the user adds plugins via the upcoming UI);
        // mcp-bridge still honors MCP_SERVERS env for backward compat.
        // Once a plugin is added via the registry, drop it from
        // MCP_SERVERS — pi.registerTool throws on duplicate names.
        (pi) => smithPersonalityExtension(pi),
        (pi) => typedMemoryExtension(pi),       // task_*/set_focus/set_preference/note_event
        (pi) => scheduledJobsExtension(pi),     // schedule_job / list_scheduled_jobs / ...
        (pi) => temperMemoryExtension(pi),      // memory_search + legacy escape hatches
        (pi) => { void pluginSystemExtension(pi); }, // fire-and-forget
        (pi) => { void mcpBridgeExtension(pi); },    // legacy env path
        (pi) => approvalGateExtension(pi, conversationId),
        (pi) => compactionPolicyExtension(pi, conversationId),
      ],
    });
    await resourceLoader.reload();

    const { sm: piSession, path: sessPath, resumed } = loadSessionManager(conversationId);
    console.log(
      `[smith] session ${resumed ? "resumed" : "created"}: convId=${conversationId} → ${sessPath}`,
    );

    const { session } = await createAgentSession({
      model,
      authStorage: this.authStorage,
      modelRegistry: this.modelRegistry,
      resourceLoader,
      sessionManager: piSession,
      // Disable pi's built-in coding tools (read / bash / edit / write /
      // grep / find / ls). They're useful in the coding-agent CLI but
      // irrelevant for an enterprise personal assistant and would just
      // pollute the model's tool surface. Extension-registered tools
      // (memory_*, MCP-bridged) stay enabled.
      noTools: "builtin",
    });
    this.sessions.set(conversationId, session);
    return session;
  }

  async dispose(conversationId: string): Promise<void> {
    const s = this.sessions.get(conversationId);
    if (!s) return;
    this.sessions.delete(conversationId);
    s.dispose();
  }

  async disposeAll(): Promise<void> {
    for (const [, s] of this.sessions) s.dispose();
    this.sessions.clear();
  }

  count(): number {
    return this.sessions.size;
  }
}

let _pool: SmithSessionPool | null = null;
export function getSessionPool(): SmithSessionPool {
  if (_pool === null) _pool = new SmithSessionPool();
  return _pool;
}
