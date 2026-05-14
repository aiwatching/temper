/**
 * Per-conversation AgentSession pool.
 *
 * pi's AgentSession is single-flight: while .prompt() is running, you queue
 * follow-ups but can't run a second prompt concurrently. To let smith
 * handle multiple users / chat windows in parallel we keep one Session
 * per conversation_id and create on demand.
 *
 * MVP: in-memory only. Restart wipes mid-conversation history; semantic
 * memory stays put in TEMPER. Replace this with a disk-backed or
 * Temper-backed implementation when conversation continuity across
 * restarts matters.
 */
import {
  createAgentSession,
  DefaultResourceLoader,
  SessionManager as PiSessionManager,
  AuthStorage,
  ModelRegistry,
} from "@earendil-works/pi-coding-agent";

import { getConfig, type SmithConfig } from "./config.js";
import { temperMemoryExtension } from "./extensions/temper-memory.js";
import { mcpBridgeExtension } from "./extensions/mcp-bridge.js";

// biome-ignore lint: pi's AgentSession type isn't re-exported cleanly yet.
type AgentSession = Awaited<ReturnType<typeof createAgentSession>>["session"];

// TODO(systemPrompt): pi has no createAgentSession.systemPrompt option.
// The intended path is either a Skill (markdown bundle) or an extension
// that hooks the `before_provider_request` event and injects a system
// message. For MVP we rely on the tool descriptions to teach the model
// what memory_search/memory_write are for; revisit once tool calls work
// end-to-end so we can A/B against a real personality prompt.
//
// Keeping the draft here so we don't lose the wording:
//
//   You are Smith, a personal company-level assistant.
//   You have two tool surfaces:
//     1. memory_search / memory_write — long-term memory in TEMPER ...
//     2. Internal company tools bridged from MCP servers ...
//   Default to terse, action-oriented responses. Surface only the top
//   1–3 memory hits, paraphrased — never read raw JSON to the user.

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

    const resourceLoader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: process.cwd(),                  // we don't ship ~/.pi-style assets
      extensionFactories: [
        // Order matters: temper-memory must be available even if MCP
        // setup fails partway through.
        (pi) => temperMemoryExtension(pi),
        (pi) => { void mcpBridgeExtension(pi); }, // fire-and-forget; awaits inside
      ],
    });
    await resourceLoader.reload();

    const { session } = await createAgentSession({
      model,
      authStorage: this.authStorage,
      modelRegistry: this.modelRegistry,
      resourceLoader,
      sessionManager: PiSessionManager.inMemory(),
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
