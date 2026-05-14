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
import { getModel } from "@earendil-works/pi-ai";

import { getConfig } from "./config.js";
import { temperMemoryExtension } from "./extensions/temper-memory.js";
import { mcpBridgeExtension } from "./extensions/mcp-bridge.js";

// biome-ignore lint: pi's AgentSession type isn't re-exported cleanly yet.
type AgentSession = Awaited<ReturnType<typeof createAgentSession>>["session"];

const SYSTEM_PROMPT = `You are Smith, a personal company-level assistant.

You have two tool surfaces:
  1. memory_search / memory_write — long-term memory in TEMPER (private to
     this user, scoped to your agent identity). Use memory_search at task
     start and whenever the user references prior context; use memory_write
     to record durable facts (preferences, decisions, identity info).
     One discrete fact per write. Never store credentials or unconsented PII.
  2. Internal company tools, bridged from MCP servers — tool names are
     prefixed with the server name (e.g. \`jira__search\`, \`files__read\`).

Default to terse, action-oriented responses. Surface only the top 1–3
memory hits, paraphrased — never read raw JSON to the user.

Today's date in the user's timezone is whatever the latest message
timestamp implies — don't assume.`.trim();

class SmithSessionPool {
  private sessions = new Map<string, AgentSession>();
  private authStorage = AuthStorage.create();
  private modelRegistry = ModelRegistry.create(this.authStorage);

  async getOrCreate(conversationId: string): Promise<AgentSession> {
    const existing = this.sessions.get(conversationId);
    if (existing) return existing;

    const cfg = getConfig();
    const model = getModel(cfg.llmProvider, cfg.llmModel);
    if (!model) {
      throw new Error(
        `Unknown LLM model: provider=${cfg.llmProvider} model=${cfg.llmModel}. ` +
        "Check the pi-ai catalog or add it via authStorage.",
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
      systemPrompt: SYSTEM_PROMPT,
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
