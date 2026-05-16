/**
 * Smith's HTTP control plane. Tiny — anything that wants to drive Smith
 * (a CLI, a web UI, an IDE plugin, an iOS shortcut) speaks plain JSON
 * over HTTP at the same port.
 *
 *   GET  /                              redirect → /chat
 *   GET  /chat                          focused single-pane chat UI
 *   GET  /briefs                        dashboard workspace (brief strip + thread + right rail)
 *   GET  /healthz                       liveness — Temper reachable + LLM creds
 *   POST /chat   body:{message,conversationId?}   single turn
 *
 * /chat content negotiation:
 *   - Accept: text/event-stream       SSE streaming (one event per token)
 *   - anything else (default)         single JSON {reply, stopReason}
 *
 * UI assets (src/web/*.css, *.jsx) and React/ReactDOM/Babel UMD bundles
 * are inlined into each served page so the browser needs zero external
 * network on first load (air-gap friendly).
 */
import { Hono } from "hono";
import { streamSSE } from "hono/streaming";

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { existsSync, unlinkSync } from "node:fs";

import { approvalStore, type PendingApproval } from "./approval-store.js";
import { getConfig, SETTING_KEYS } from "./config.js";
import { conversationIndex } from "./conversation-index.js";
import {
  isInstalled,
  listSettings,
  markInstalled,
  setSecretSetting,
  setSetting,
} from "./db/settings.js";
import { Temper, TemperError } from "./temper.js";
import { getSessionPool } from "./session-manager.js";
import type { MCPConfig, PluginKind, PluginRow } from "./plugins/types.js";
import {
  deletePlugin,
  getPlugin,
  listPlugins,
  loadSecret,
  upsertPlugin,
} from "./plugins/repository.js";
import { MCPPlugin } from "./plugins/mcp.js";
import {
  createJob,
  deleteJob,
  forceDueNow,
  getJobById,
  listJobs,
  updateJob,
  type Trigger,
  type Action,
} from "./db/jobs-repo.js";
import { runJobNow } from "./jobs-engine.js";
import {
  aggregateTaskById,
  aggregateTasks,
  type TaskStatus,
} from "./lib/tasks-aggregator.js";

// ---- inline vendor + UI source ----
//
// Every script we ship to the browser is read off disk once at module
// load and pasted into the page HTML. Keeps the browser at zero CDN
// dependencies (corp networks often firewall jsdelivr / unpkg), and
// the bytes are cached by the HTTP layer after the first GET.
//
// Sizes (UMD production / minified):
//   marked          ≈40KB    Markdown renderer (npm dep — already in package.json)
//   react           ≈12KB    UMD production — vendored under src/web/vendor/
//   react-dom       ≈135KB   UMD production — vendored under src/web/vendor/
//   @babel/standalone ≈3MB   Browser-side JSX → JS — vendored under src/web/vendor/
//
// React + Babel-standalone are vendored rather than depended-on via npm
// because they're immutable browser UMD blobs that never need ABI
// resolution; an npm dep would just bloat node_modules and the lockfile
// without gaining anything. Bump the pinned files in
// `src/web/vendor/README.md` when upgrading.
//
// The 3MB Babel dominates first-page weight. Gzip cuts it to ~600KB.
// Acceptable for an internal tool you launch once per session; revisit
// if Smith ever needs to load on a slow link.
const _req = createRequire(import.meta.url);
const MARKED_JS = readFileSync(_req.resolve("marked/marked.min.js"), "utf8");

// JSX / CSS / vendor JS all sit under src/web/. tsc only compiles .ts
// (see tsconfig.json `include`) so these files travel as raw text from
// src → dist via a separate copy step OR by reading at run-time from
// the source tree. We resolve them relative to the compiled module's
// directory and fall back to walking up to src/ — works in both
// `tsx watch` (dist absent) and `node dist/...` (dist present).
const _here = dirname(fileURLToPath(import.meta.url));
function readWeb(file: string): string {
  // Try src/web first (dev / tsx watch), then ../src/web (from dist).
  const candidates = [
    resolvePath(_here, "web", file),
    resolvePath(_here, "..", "src", "web", file),
  ];
  for (const p of candidates) {
    if (existsSync(p)) return readFileSync(p, "utf8");
  }
  throw new Error(`web asset not found: ${file} (looked in ${candidates.join(", ")})`);
}
const STYLES_CSS = readWeb("styles.css");
const SHARED_JSX = readWeb("shared.jsx");
const CHAT_JSX = readWeb("chat.jsx");
const BRIEFS_JSX = readWeb("briefs.jsx");
const PLUGINS_JSX = readWeb("plugins.jsx");
const SETUP_JSX = readWeb("setup.jsx");
const SETTINGS_JSX = readWeb("settings.jsx");
const TASKS_JSX = readWeb("tasks.jsx");
const REACT_JS = readWeb("vendor/react.production.min.js");
const REACT_DOM_JS = readWeb("vendor/react-dom.production.min.js");
const BABEL_JS = readWeb("vendor/babel.min.js");

/**
 * Render a complete HTML page that boots a React app. `bodyApp` is the
 * name of the global the JSX module exposes on window (e.g. "ChatApp" /
 * "BriefApp"); `appJsx` is the module's raw JSX that Babel transforms in
 * the browser. We escape `</script>` sequences so embedded strings in the
 * source don't prematurely close the script tag.
 */
function renderPage(title: string, bodyApp: string, appJsx: string): string {
  const escapeScript = (s: string) => s.replace(/<\/script>/gi, "<\\/script>");
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>${title}</title>
  <style>${STYLES_CSS}</style>
</head>
<body>
  <div id="root"></div>
  <script>${escapeScript(MARKED_JS)}</script>
  <script>marked.use({ gfm: true, breaks: true });</script>
  <script>${escapeScript(REACT_JS)}</script>
  <script>${escapeScript(REACT_DOM_JS)}</script>
  <script>${escapeScript(BABEL_JS)}</script>
  <script type="text/babel" data-presets="react">${escapeScript(SHARED_JSX)}</script>
  <script type="text/babel" data-presets="react">${escapeScript(appJsx)}</script>
  <script type="text/babel" data-presets="react">
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(${bodyApp}));
  </script>
</body>
</html>`;
}

const CHAT_PAGE = renderPage("Smith · Chat", "ChatApp", CHAT_JSX);
const BRIEFS_PAGE = renderPage("Smith · Briefs", "BriefApp", BRIEFS_JSX);
const PLUGINS_PAGE = renderPage("Smith · Plugins", "PluginsApp", PLUGINS_JSX);
const SETUP_PAGE = renderPage("Smith · Setup", "SetupApp", SETUP_JSX);
const SETTINGS_PAGE = renderPage("Smith · Settings", "SettingsApp", SETTINGS_JSX);
const TASKS_PAGE = renderPage("Smith · Tasks", "TasksApp", TASKS_JSX);

export function buildApp(): Hono {
  const app = new Hono();
  const cfg = getConfig();

  // ---- first-run gate ----
  //
  // Until the wizard marks settings.installed = true, we redirect
  // every HTML navigation to /setup. JSON callers (curl, memctl) get
  // 503 with a descriptive body. /setup itself + its API endpoints
  // + /healthz stay open so the wizard works.
  //
  // This runs BEFORE the bearer-secret gate — otherwise a fresh
  // install with no bearer set would also be blocked from /setup.
  app.use(async (c, next) => {
    if (isInstalled()) return next();
    const path = c.req.path;
    if (
      path === "/setup" ||
      path.startsWith("/setup/") ||
      path === "/healthz" ||
      path === "/favicon.ico"
    ) {
      return next();
    }
    const accept = c.req.header("accept") ?? "";
    if (accept.includes("text/html")) {
      return c.redirect("/setup");
    }
    return c.json({ error: "smith is not yet configured — open /setup in a browser" }, 503);
  });

  // ---- bearer auth (A7) ----
  // SMITH_SECRET in .env enables. Empty/unset = open (dev mode, but
  // pair with SMITH_HOST=127.0.0.1 only). When enabled, gated routes
  // need Authorization: Bearer <SMITH_SECRET>. /healthz and HTML page
  // GETs stay open so monitoring + UI bootstrap still work — the page
  // grabs the secret from #secret=<v> on first load and attaches it to
  // every subsequent fetch.
  if (cfg.smithSecret) {
    const expected = "Bearer " + cfg.smithSecret;
    // Gate the JSON / SSE API surface. POST /chat is gated; GET /chat
    // (the HTML page) is NOT — it's the bootstrap that picks up the
    // secret from the URL hash before any /approve, /deny, etc. fires.
    // /setup is NEVER bearer-gated (the wizard happens before any
    // bearer exists). /settings IS gated (post-install editor).
    const GATED_PATH = /^\/(?:chat|approve|deny|pending(?:\/.*)?|conversations(?:\/.*)?|plugins(?:\/.*)?|settings(?:\/.*)?|jobs(?:\/.*)?|tasks(?:\/.*)?)$/;
    app.use(async (c, next) => {
      if (!GATED_PATH.test(c.req.path)) return next();
      if (c.req.method === "GET" && (c.req.path === "/chat" || c.req.path === "/plugins" || c.req.path === "/settings" || c.req.path === "/tasks")) {
        return next();  // GET HTML pages = bootstrap, not API
      }
      const got = c.req.header("authorization") ?? "";
      // Constant-time compare to deter timing-side-channel attacks
      // even though the chance of one mattering on localhost is small.
      if (got.length !== expected.length || got !== expected) {
        return c.json({ error: "Unauthorized" }, 401);
      }
      return next();
    });
  }

  // ---- web UI ----
  // / redirects to the focused chat surface. /briefs is the dashboard
  // workspace (brief strip + thread + right rail). Both pages share
  // the same backend.
  app.get("/", (c) => c.redirect("/chat"));
  app.get("/chat", (c) => c.html(CHAT_PAGE));
  app.get("/briefs", (c) => c.html(BRIEFS_PAGE));
  // /plugins is a dual route: HTML page for browser navigation,
  // JSON list for fetch() calls. The /plugins JSON handler in
  // registerPluginRoutes() does the Accept-header check (browser
  // navigates with text/html → page; fetch() defaults / explicit
  // application/json → list).
  app.get("/setup", (c) => c.html(SETUP_PAGE));
  app.get("/setup/status", (c) => c.json({ installed: isInstalled() }));
  // /settings dual-routes like /plugins — Accept-negotiated. Browser
  // hits HTML; fetch from the page hits JSON list (the GET /settings
  // JSON handler is in registerSetupRoutes()).
  app.get("/settings", (c) => {
    const accept = c.req.header("accept") ?? "";
    if (accept.includes("text/html")) return c.html(SETTINGS_PAGE);
    return c.json({ settings: listSettings() });
  });

  // ---- approval gate ----
  //
  // /approve and /deny are paired with the `tool_pending` SSE event.
  // The UI POSTs {conversationId, toolName, argsHash} and the store
  // either lets the next retry through (/approve) or just clears the
  // pending state (/deny). On approve, the UI follows up with a /chat
  // turn that nudges the LLM to retry.
  app.post("/approve", async (c) => {
    type Body = { conversationId?: string; toolName?: string; argsHash?: string };
    let body: Body;
    try {
      body = (await c.req.json()) as Body;
    } catch {
      return c.json({ error: "Body must be JSON" }, 400);
    }
    const conversationId = body.conversationId?.trim();
    const toolName = body.toolName?.trim();
    const hash = body.argsHash?.trim();
    if (!conversationId || !toolName || !hash) {
      return c.json({ error: "conversationId, toolName, argsHash all required" }, 400);
    }
    approvalStore.approve(conversationId, toolName, hash);
    return c.json({ ok: true });
  });

  app.post("/deny", async (c) => {
    type Body = { conversationId?: string; toolName?: string; argsHash?: string };
    let body: Body;
    try {
      body = (await c.req.json()) as Body;
    } catch {
      return c.json({ error: "Body must be JSON" }, 400);
    }
    const conversationId = body.conversationId?.trim();
    const toolName = body.toolName?.trim();
    const hash = body.argsHash?.trim();
    if (!conversationId || !toolName || !hash) {
      return c.json({ error: "conversationId, toolName, argsHash all required" }, 400);
    }
    approvalStore.deny(conversationId, toolName, hash);
    return c.json({ ok: true });
  });

  // Optional: lets the UI re-fetch the current pending state on
  // page reload so a missed SSE event doesn't strand the user.
  app.get("/pending/:conversationId", (c) => {
    const p = approvalStore.getPending(c.req.param("conversationId"));
    return c.json({ pending: p ?? null });
  });

  // ---- conversation index (A6) ----
  //
  // GET /conversations            — list newest-first (powers the UI picker)
  // DELETE /conversations/:id     — wipe the JSONL + drop the index entry.
  //                                  Also dispose the in-memory AgentSession
  //                                  if it's currently in the pool, so a
  //                                  follow-up /chat on the same id starts
  //                                  fresh instead of resuming nothing.
  //                                  Optional ?archive=true writes a
  //                                  conversation-summary episode to TEMPER
  //                                  first, so the recall-side still has
  //                                  something to find later.
  app.get("/conversations", (c) => {
    return c.json({ conversations: conversationIndex.list() });
  });

  // GET /conversations/:id/messages
  // Replays a conversation's JSONL into the {role, text} shape the UI
  // needs to repopulate its scrollback when the user picks an older
  // conversation. We only emit user + assistant message turns and
  // flatten content into a single text string — tool calls, thinking
  // deltas, and SSE-only event types are skipped (the UI can't
  // reconstruct them in a way that's useful to look at later).
  app.get("/conversations/:id/messages", (c) => {
    const id = c.req.param("id");
    const jsonl = resolvePath(process.cwd(), ".data", "smith-sessions", `${id}.jsonl`);
    if (!existsSync(jsonl)) {
      return c.json({ id, messages: [] });
    }
    let raw: string;
    try {
      raw = readFileSync(jsonl, "utf8");
    } catch (e) {
      return c.json({ error: `read failed: ${(e as Error).message}` }, 500);
    }
    type Msg = { role: "user" | "assistant"; text: string; ts?: string };
    const messages: Msg[] = [];
    for (const line of raw.split("\n")) {
      if (!line.trim()) continue;
      let row: { type?: string; timestamp?: string; message?: { role?: string; content?: Array<{ type?: string; text?: string }> } };
      try { row = JSON.parse(line); } catch { continue; }
      if (row.type !== "message" || !row.message) continue;
      const role = row.message.role;
      if (role !== "user" && role !== "assistant") continue;
      // Concatenate text blocks; ignore tool_use / tool_result blocks
      // because the UI's tool chips reflect live execution and
      // historical chips wouldn't be actionable.
      const text = (row.message.content ?? [])
        .filter((b) => b?.type === "text" && typeof b.text === "string")
        .map((b) => b.text as string)
        .join("");
      if (!text) continue;
      messages.push({ role, text, ts: row.timestamp });
    }
    return c.json({ id, messages });
  });

  // Mark / clear the waiting flag from the /tasks UI. The model has its
  // own set_waiting / clear_waiting tools (see extensions/conv-waiting.ts);
  // these endpoints let the human override from the Kanban detail panel
  // (the "检查" button → "clear" if currently waiting, etc).
  app.post("/conversations/:id/waiting", async (c) => {
    let body: { external?: string; note?: string } = {};
    try { body = (await c.req.json()) as typeof body; } catch { /* allow empty */ }
    const ext = body.external?.trim();
    if (!ext) return c.json({ error: "external is required" }, 400);
    const e = conversationIndex.markWaiting(c.req.param("id"), ext, body.note);
    return e ? c.json(e) : c.json({ error: "conversation not found in index" }, 404);
  });

  app.delete("/conversations/:id/waiting", (c) => {
    const e = conversationIndex.clearWaiting(c.req.param("id"));
    return e ? c.json(e) : c.json({ error: "conversation not found in index" }, 404);
  });

  app.delete("/conversations/:id", async (c) => {
    const id = c.req.param("id");
    const archive = c.req.query("archive") === "true";
    const entry = conversationIndex.get(id);

    let archived: { episode_id?: string } | null = null;
    if (archive && entry) {
      try {
        const t = new Temper();
        const content =
          `Conversation '${entry.title}' (id=${entry.id}, ` +
          `${entry.messageCount} turns, last active ${entry.lastUsedAt}).\n\n` +
          `First user message: ${entry.firstMessage.slice(0, 400)}`;
        archived = (await t.write({
          content,
          sourceType: "text",
          sourceDescription: "smith conversation archive",
          tags: ["conversation-summary"],
        })) as { episode_id?: string };
      } catch (e) {
        // Best-effort: archive failure doesn't block deletion. The
        // operator may have wanted retention more than archive, so
        // we surface the error in the response instead of 500-ing.
        return c.json(
          { error: `archive failed: ${(e as Error).message}`, deleted: false },
          502,
        );
      }
    }

    // Drop the in-memory session so a future chat against this id
    // doesn't try to resume from a file we're about to delete.
    await getSessionPool().dispose(id);

    // Wipe the JSONL.
    const jsonl = resolvePath(
      process.cwd(),
      ".data",
      "smith-sessions",
      `${id}.jsonl`,
    );
    if (existsSync(jsonl)) {
      try { unlinkSync(jsonl); } catch (_) { /* ignore */ }
    }
    conversationIndex.delete(id);
    return c.json({ deleted: true, archived });
  });

  // ---- health ----
  app.get("/healthz", async (c) => {
    const cfg = getConfig();
    const body: Record<string, unknown> = {
      status: "ok",
      temper_base_url: cfg.temperBaseUrl,
      llm_provider: cfg.llmProvider,
      llm_model: cfg.llmModel,
      active_sessions: getSessionPool().count(),
    };
    try {
      const t = new Temper();
      await t.health();
      const me = await t.whoami();
      body.temper_user = me.email;
    } catch (e) {
      body.status = "degraded";
      body.temper_error = e instanceof TemperError ? e.detail : String(e);
    }
    return c.json(body);
  });

  // ---- chat ----
  app.post("/chat", async (c) => {
    type ChatBody = { message?: string; conversationId?: string };
    let body: ChatBody;
    try {
      body = (await c.req.json()) as ChatBody;
    } catch {
      return c.json({ error: "Body must be JSON" }, 400);
    }
    const message = (body.message ?? "").trim();
    if (!message) return c.json({ error: "message is required" }, 400);

    const conversationId = body.conversationId?.trim() || "default";
    const pool = getSessionPool();
    const session = await pool.getOrCreate(conversationId);

    const wantsSSE = (c.req.header("accept") ?? "").includes("text/event-stream");

    if (wantsSSE) {
      // Stream `text_delta` tokens as they arrive from pi, then a
      // terminal "done" event with stopReason. Errors become an "error"
      // event with the upstream detail so the UI can render them
      // distinctly from regular content.
      return streamSSE(c, async (stream) => {
        let stopReason: string | undefined;
        let errorMessage: string | undefined;
        let aborted = false;
        // biome-ignore lint: pi's event union is wide — typed locally.
        const unsubscribe = session.subscribe((e: any) => {
          if (aborted) return;
          // Stream text deltas as they happen
          if (
            e?.type === "message_update" &&
            e.assistantMessageEvent?.type === "text_delta"
          ) {
            const delta = String(e.assistantMessageEvent.delta ?? "");
            if (delta) stream.writeSSE({ event: "delta", data: delta }).catch(() => {});
            return;
          }
          // Reasoning models emit thinking_delta during their thought
          // pass before producing visible text. Smith forwards as a
          // separate event so the UI can show it in a collapsed block.
          if (
            e?.type === "message_update" &&
            e.assistantMessageEvent?.type === "thinking_delta"
          ) {
            const delta = String(e.assistantMessageEvent.delta ?? "");
            if (delta) stream.writeSSE({ event: "thinking", data: delta }).catch(() => {});
            return;
          }
          // Surface tool calls so the UI can show a "calling memory_search…" hint
          if (e?.type === "tool_execution_start") {
            stream
              .writeSSE({
                event: "tool_start",
                data: JSON.stringify({
                  toolName: e.toolName,
                  toolCallId: e.toolCallId,
                }),
              })
              .catch(() => {});
            return;
          }
          if (e?.type === "tool_execution_end") {
            stream
              .writeSSE({
                event: "tool_end",
                data: JSON.stringify({
                  toolName: e.toolName,
                  toolCallId: e.toolCallId,
                  isError: !!e.isError,
                }),
              })
              .catch(() => {});
            return;
          }
          // Final turn payload — pull stopReason + any provider error
          if (e?.type === "agent_end") {
            const msgs: any[] = e.messages ?? [];
            for (let i = msgs.length - 1; i >= 0; i--) {
              const m = msgs[i];
              if (m?.role !== "assistant") continue;
              stopReason = m.stopReason;
              errorMessage = m.errorMessage;
              break;
            }
          }
        });
        // Forward `tool_pending` from the approvalStore — fires when
        // the gate blocks a dangerous tool. The UI shows Approve/Deny
        // buttons keyed off the (toolCallId, argsHash) we ship here.
        const onPending = (p: PendingApproval) => {
          if (aborted) return;
          if (p.conversationId !== conversationId) return;
          stream
            .writeSSE({
              event: "tool_pending",
              data: JSON.stringify({
                toolCallId: p.toolCallId,
                toolName: p.toolName,
                input: p.input,
                argsHash: p.argsHash,
              }),
            })
            .catch(() => {});
        };
        approvalStore.events.on("pending", onPending);

        // If the client disconnects, stop pushing events. pi's
        // prompt() will keep running in the background; the next
        // /chat call against this session will queue / wait.
        stream.onAbort(() => {
          aborted = true;
          approvalStore.events.off("pending", onPending);
        });
        try {
          await session.prompt(message);
          conversationIndex.recordTurn(conversationId, message);
        } catch (e) {
          const msg = (e as Error).message ?? "";
          // pi rejects a concurrent prompt unless we pass
          // streamingBehavior. Happens when a previous turn's stream
          // was abandoned (browser closed mid-response, tab switch,
          // two tabs racing) — pi keeps the old turn alive server-side
          // and refuses the new one. "followUp" queues us behind it.
          if (msg.includes("Agent is already processing")) {
            console.warn(
              `[smith] convId=${conversationId} was busy — queuing this prompt as followUp`,
            );
            try {
              await session.prompt(message, { streamingBehavior: "followUp" });
              conversationIndex.recordTurn(conversationId, message);
            } catch (e2) {
              errorMessage = `queued prompt failed: ${(e2 as Error).message}`;
              stopReason = "error";
            }
          } else {
            errorMessage = msg;
            stopReason = "error";
          }
        } finally {
          unsubscribe();
          approvalStore.events.off("pending", onPending);
        }
        if (stopReason === "error") {
          await stream.writeSSE({
            event: "error",
            data: JSON.stringify({ error: errorMessage ?? "LLM error", stopReason }),
          });
        } else {
          await stream.writeSSE({
            event: "done",
            data: JSON.stringify({ stopReason, conversationId }),
          });
        }
      });
    }

    // ---- Non-SSE path (default) — backward-compatible single JSON ----
    let reply = "";
    let stopReason: string | undefined;
    let errorMessage: string | undefined;
    // biome-ignore lint: pi's event union is wide — typed locally.
    const unsubscribe = session.subscribe((e: any) => {
      if (e?.type !== "agent_end") return;
      const msgs: any[] = e.messages ?? [];
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m?.role !== "assistant") continue;
        stopReason = m.stopReason;
        errorMessage = m.errorMessage;
        for (const block of m.content ?? []) {
          if (block?.type === "text" && typeof block.text === "string") {
            reply += block.text;
          }
        }
        break;
      }
    });
    try {
      await session.prompt(message);
      conversationIndex.recordTurn(conversationId, message);
    } finally {
      unsubscribe();
    }
    if (stopReason === "error") {
      return c.json(
        { conversationId, error: errorMessage ?? "LLM error (no detail)", stopReason },
        502,
      );
    }
    return c.json({ conversationId, reply, stopReason });
  });

  // ---- plugins ----
  //
  // CRUD over the SQLite plugins registry. Secrets are write-only on
  // the wire: PUT body can carry plaintext, GET responses never return
  // it (only `has_secret` boolean). The /test endpoint does an
  // out-of-process probe (instantiate + connect + listTools + dispose)
  // without persisting — used by the UI's "Test" button before saving.
  //
  // Adding/removing whole plugins doesn't hot-reload pi tools (no
  // unregisterTool); the UI tells the user to restart Smith. Toggling
  // `enabled` / rotating secrets is hot-reload territory but lives in
  // P4 — for now those changes also need a restart to fully take
  // effect (the existing in-memory MCP client keeps the old config).
  registerPluginRoutes(app);
  registerSetupRoutes(app);
  registerJobsRoutes(app);
  registerTasksRoutes(app);

  return app;
}

// --- setup / settings routes ---
//
// /setup/test/temper — probe TEMPER URL+key. Does not persist.
// /setup/test/llm    — probe LLM provider+key with one minimal chat
//                       completion (OpenAI-compat shape). Real call,
//                       costs ~1 token.
// /setup/save        — write all settings + secrets, mark installed,
//                       return the bearer (if auto-generated).
// /settings          — JSON list of all current settings (gated by
//                       bearer once installed). Edit endpoints are
//                       part of P5d.

import { randomBytes } from "node:crypto";

interface SetupPayload {
  agent_slug?: string;
  temper_base_url?: string;
  temper_api_key?: string;
  llm_provider?: string;
  llm_model?: string;
  llm_base_url?: string;
  llm_api_key?: string;
  bearer_secret?: string;
  consolidate_schedule_hours?: number;
  recall_log_level?: string;
}

function registerSetupRoutes(app: Hono): void {

  // Probe TEMPER /v1/auth/me with the user-provided URL + key.
  app.post("/setup/test/temper", async (c) => {
    let b: { base_url?: string; api_key?: string };
    try { b = await c.req.json(); }
    catch { return c.json({ ok: false, error: "body must be JSON" }, 400); }
    const baseUrl = (b.base_url ?? "").trim().replace(/\/+$/, "");
    const apiKey = (b.api_key ?? "").trim();
    if (!baseUrl || !apiKey) {
      return c.json({ ok: false, error: "base_url + api_key required" });
    }
    const start = Date.now();
    try {
      const r = await fetch(`${baseUrl}/v1/auth/me`, {
        headers: { "X-API-Key": apiKey },
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        return c.json({ ok: false, error: `HTTP ${r.status}: ${detail.slice(0, 200)}` });
      }
      const me = (await r.json()) as { id?: string; email?: string };
      return c.json({
        ok: true, ms: Date.now() - start,
        email: me.email, user_id: me.id,
      });
    } catch (e) {
      return c.json({ ok: false, error: (e as Error).message });
    }
  });

  // Probe LLM with a 1-token chat completion. OpenAI-compatible only
  // for now (covers openai, deepseek, most internal gateways). For
  // Anthropic native API we'd need to detect + call /v1/messages —
  // future improvement; users can always skip the test.
  app.post("/setup/test/llm", async (c) => {
    let b: {
      provider?: string; model?: string;
      base_url?: string | null; api_key?: string;
    };
    try { b = await c.req.json(); }
    catch { return c.json({ ok: false, error: "body must be JSON" }, 400); }
    const provider = (b.provider ?? "").trim();
    const model = (b.model ?? "").trim();
    const apiKey = (b.api_key ?? "").trim();
    if (!model || !apiKey) {
      return c.json({ ok: false, error: "model + api_key required" });
    }

    const baseUrl = (b.base_url || providerDefaultBaseUrl(provider))?.replace(/\/+$/, "");
    if (!baseUrl) {
      return c.json({ ok: false, error: `no base_url and no default for provider '${provider}'` });
    }

    const start = Date.now();
    try {
      const r = await fetch(`${baseUrl}/chat/completions`, {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${apiKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          model,
          messages: [{ role: "user", content: "ping" }],
          max_tokens: 5,
          temperature: 0,
        }),
      });
      if (!r.ok) {
        const detail = await r.text().catch(() => "");
        return c.json({
          ok: false, ms: Date.now() - start,
          error: `HTTP ${r.status}: ${detail.slice(0, 300)}`,
        });
      }
      const j = await r.json() as {
        choices?: Array<{ message?: { content?: string } }>;
        usage?: { total_tokens?: number };
      };
      const reply = j.choices?.[0]?.message?.content ?? "";
      return c.json({
        ok: true, ms: Date.now() - start,
        reply, tokens_used: j.usage?.total_tokens,
      });
    } catch (e) {
      return c.json({ ok: false, error: (e as Error).message });
    }
  });

  // Persist wizard payload. Generates a bearer if the user left it
  // blank. Marks installed=true; returns the bearer so the wizard
  // can hash-fragment-redirect into the chat UI with auth already.
  app.post("/setup/save", async (c) => {
    if (isInstalled()) {
      return c.json({ error: "already installed; edit via /settings instead" }, 409);
    }
    let p: SetupPayload;
    try { p = await c.req.json(); }
    catch { return c.json({ error: "body must be JSON" }, 400); }

    // Auto-generate bearer when empty (24 url-safe chars).
    let bearer = (p.bearer_secret ?? "").trim();
    if (!bearer) {
      bearer = randomBytes(18).toString("base64url");
    }

    let n = 0;
    function writeStr(key: string, value: string | undefined, desc?: string) {
      if (value !== undefined) {
        setSetting(key, value, { description: desc, updatedBy: "setup-wizard" });
        n++;
      }
    }
    function writeNum(key: string, value: number | undefined, desc?: string) {
      if (value !== undefined) {
        setSetting(key, value, { description: desc, updatedBy: "setup-wizard" });
        n++;
      }
    }
    function writeSecret(key: string, value: string | undefined, desc?: string) {
      if (value !== undefined && value.length > 0) {
        setSecretSetting(key, value, { description: desc, updatedBy: "setup-wizard" });
        n++;
      }
    }

    writeStr(SETTING_KEYS.smithAgentSlug, p.agent_slug, "Namespace suffix in TEMPER (agent:me/<slug>)");
    writeStr(SETTING_KEYS.temperBaseUrl, p.temper_base_url, "TEMPER instance base URL");
    writeSecret(SETTING_KEYS.temperApiKey, p.temper_api_key, "TEMPER API key");
    writeStr(SETTING_KEYS.llmProvider, p.llm_provider, "pi-ai provider id");
    writeStr(SETTING_KEYS.llmModel, p.llm_model, "LLM model id");
    writeStr(SETTING_KEYS.llmBaseUrl, p.llm_base_url ?? "", "Custom LLM base URL (empty = use provider default)");
    writeSecret(SETTING_KEYS.llmApiKey, p.llm_api_key, "LLM API key");
    writeSecret(SETTING_KEYS.smithSecret, bearer, "Bearer secret gating /chat /plugins etc.");
    writeNum(SETTING_KEYS.consolidateScheduleHours, p.consolidate_schedule_hours, "Hours between auto-consolidate runs (0 = off)");
    writeStr("recall.log_level", p.recall_log_level, "SMITH_RECALL_LOG verbosity");

    markInstalled("setup-wizard");
    // also set the env var so the rest of THIS process sees the
    // new recall log level without restart (it's read via process.env)
    if (p.recall_log_level) process.env.SMITH_RECALL_LOG = p.recall_log_level;

    return c.json({
      ok: true,
      settings_written: n,
      // Return the bearer ONCE — the wizard hashes it into /chat URL,
      // and we never expose it via GET /settings.
      bearer_secret: bearer,
    });
  });

  // /settings JSON list is on the dual-route GET /settings above
  // (Accept-negotiated against the HTML page). Edit endpoints below.

  // PUT /settings/:key — set a non-secret value. Accepts any JSON
  // value in the body's `value` field.
  app.put("/settings/:key", async (c) => {
    const key = c.req.param("key");
    if (!key || key === "installed") {
      return c.json({ error: `cannot edit '${key}' via this endpoint` }, 400);
    }
    let body: { value?: unknown };
    try { body = await c.req.json(); }
    catch { return c.json({ error: "body must be JSON" }, 400); }
    if (!Object.hasOwn(body, "value")) {
      return c.json({ error: "body.value is required" }, 400);
    }
    setSetting(key, body.value, { updatedBy: "settings-ui" });
    return c.json({ ok: true, key });
  });

  // PUT /settings/:key/secret — rotate / clear a sensitive value.
  // body.secret can be string (set) or null (clear).
  app.put("/settings/:key/secret", async (c) => {
    const key = c.req.param("key");
    let body: { secret?: string | null };
    try { body = await c.req.json(); }
    catch { return c.json({ error: "body must be JSON" }, 400); }
    if (body.secret !== null && typeof body.secret !== "string") {
      return c.json({ error: "secret must be a string or null" }, 400);
    }
    setSecretSetting(key, body.secret, { updatedBy: "settings-ui" });
    return c.json({ ok: true, key, has_secret: body.secret !== null });
  });
}

/** Built-in provider defaults — same set pi-ai handles natively. For
 *  any other `provider` value, the user MUST supply base_url. */
function providerDefaultBaseUrl(provider: string): string | null {
  switch (provider) {
    case "openai": return "https://api.openai.com/v1";
    case "deepseek": return "https://api.deepseek.com/v1";
    case "google": return "https://generativelanguage.googleapis.com/v1beta/openai";
    case "anthropic": return null; // not openai-compat; skip test
    default: return null;
  }
}

// --- plugin routes (extracted to keep buildApp readable) ---

function rowToWire(row: PluginRow): Record<string, unknown> {
  return {
    slug: row.slug,
    kind: row.kind,
    display_name: row.display_name,
    config: JSON.parse(row.config_json),
    has_secret: row.secret_ref !== null,
    enabled: row.enabled === 1,
    last_seen_at: row.last_seen_at,
    last_tool_count: row.last_tool_count,
    last_error: row.last_error,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

function validateUpsertBody(b: unknown): {
  slug: string; kind: PluginKind; display_name: string;
  config: unknown; secret?: string | null; enabled?: boolean;
} {
  if (typeof b !== "object" || b === null) {
    throw new Error("body must be an object");
  }
  const o = b as Record<string, unknown>;
  const slug = typeof o.slug === "string" ? o.slug.trim() : "";
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug)) {
    throw new Error("slug must be lowercase alphanumeric + - _");
  }
  const kind = o.kind;
  if (kind !== "mcp" && kind !== "http" && kind !== "shell" && kind !== "builtin") {
    throw new Error("kind must be mcp | http | shell | builtin");
  }
  if (kind !== "mcp") {
    // We promise an mcp implementation in P2; http/shell are scaffolded
    // for future kinds but the manager doesn't instantiate them yet.
    throw new Error(`kind '${kind}' not implemented yet (P2 ships mcp only)`);
  }
  const display_name = typeof o.display_name === "string" && o.display_name.trim()
    ? o.display_name.trim() : slug;
  const config = o.config;
  if (typeof config !== "object" || config === null) {
    throw new Error("config is required");
  }
  // MCP-specific shape sanity check.
  const cfg = config as Record<string, unknown>;
  if (cfg.transport !== "stdio" && cfg.transport !== "http" && cfg.transport !== "sse") {
    throw new Error("config.transport must be stdio | http | sse");
  }
  if (typeof cfg.endpoint !== "string" || !cfg.endpoint.trim()) {
    throw new Error("config.endpoint is required");
  }
  let secret: string | null | undefined = undefined;
  if (o.secret === null) secret = null;
  else if (typeof o.secret === "string") secret = o.secret;
  const enabled = typeof o.enabled === "boolean" ? o.enabled : undefined;
  return { slug, kind, display_name, config, secret, enabled };
}

function registerPluginRoutes(app: Hono): void {
  // GET /plugins is dual-purpose: browser navigation gets the HTML
  // page (PLUGINS_PAGE) so plugins.jsx can boot; everything else
  // (fetch() from that same page, curl, memctl) gets the JSON list.
  // We split on Accept header because both surfaces want the same
  // URL — moving JSON under /api/plugins would work too but creates
  // unnecessary churn for the few callers we have.
  app.get("/plugins", (c) => {
    const accept = c.req.header("accept") ?? "";
    if (accept.includes("text/html")) {
      return c.html(PLUGINS_PAGE);
    }
    const rows = listPlugins();
    return c.json({ plugins: rows.map(rowToWire) });
  });

  // One plugin (no secret).
  app.get("/plugins/:slug", (c) => {
    const row = getPlugin(c.req.param("slug"));
    if (!row) return c.json({ error: "not found" }, 404);
    return c.json(rowToWire(row));
  });

  // Create.
  app.post("/plugins", async (c) => {
    let parsed;
    try {
      parsed = validateUpsertBody(await c.req.json());
    } catch (e) {
      return c.json({ error: (e as Error).message }, 400);
    }
    if (getPlugin(parsed.slug)) {
      return c.json({ error: `plugin '${parsed.slug}' already exists; use PUT to update` }, 409);
    }
    const row = upsertPlugin(parsed);
    return c.json(rowToWire(row), 201);
  });

  // Update (non-secret fields + optional secret rotation).
  app.put("/plugins/:slug", async (c) => {
    const slug = c.req.param("slug");
    const existing = getPlugin(slug);
    if (!existing) return c.json({ error: "not found" }, 404);
    let body: Record<string, unknown>;
    try {
      body = await c.req.json();
    } catch {
      return c.json({ error: "body must be JSON" }, 400);
    }
    // Force the slug from the URL — body slug (if present) must match.
    if (body.slug !== undefined && body.slug !== slug) {
      return c.json({ error: "slug in body does not match URL" }, 400);
    }
    body.slug = slug;
    // Fill kind / display_name from existing if omitted.
    if (body.kind === undefined) body.kind = existing.kind;
    if (body.display_name === undefined) body.display_name = existing.display_name;
    if (body.config === undefined) body.config = JSON.parse(existing.config_json);
    // secret undefined = keep existing; null = clear; string = rotate.
    let parsed;
    try { parsed = validateUpsertBody(body); }
    catch (e) { return c.json({ error: (e as Error).message }, 400); }
    const row = upsertPlugin(parsed);
    return c.json(rowToWire(row));
  });

  // Rotate just the secret (separate endpoint so UI can have a dedicated
  // "Rotate secret" flow that doesn't risk overwriting other config).
  app.put("/plugins/:slug/secret", async (c) => {
    const slug = c.req.param("slug");
    const existing = getPlugin(slug);
    if (!existing) return c.json({ error: "not found" }, 404);
    let body: { secret?: string | null };
    try { body = await c.req.json(); }
    catch { return c.json({ error: "body must be JSON" }, 400); }
    if (body.secret !== null && typeof body.secret !== "string") {
      return c.json({ error: "secret must be a string or null (to clear)" }, 400);
    }
    upsertPlugin({
      slug, kind: existing.kind, display_name: existing.display_name,
      config: JSON.parse(existing.config_json),
      secret: body.secret,
      enabled: existing.enabled === 1,
    });
    return c.json({ ok: true, has_secret: body.secret !== null });
  });

  // Delete.
  app.delete("/plugins/:slug", (c) => {
    const ok = deletePlugin(c.req.param("slug"));
    return ok ? c.json({ deleted: true }) : c.json({ error: "not found" }, 404);
  });

  // Test connection — instantiate a transient plugin, connect, list
  // tools, dispose. Doesn't persist anything. Used by the UI's "Test"
  // button BEFORE create (when no row exists yet) and FROM a row
  // (with `?slug=<existing>` to use that row's secret without
  // requiring the user to re-enter it).
  app.post("/plugins/test", async (c) => {
    let body: Record<string, unknown>;
    try {
      body = await c.req.json();
    } catch {
      return c.json({ error: "body must be JSON" }, 400);
    }
    // If `use_secret_from` is set, look up an existing plugin's secret
    // (so the UI doesn't need to ask the user to re-enter it for
    // already-configured plugins).
    let secret: string | null = null;
    if (typeof body.use_secret_from === "string") {
      secret = loadSecret(body.use_secret_from);
    } else if (typeof body.secret === "string") {
      secret = body.secret;
    }
    const kind = body.kind;
    if (kind !== "mcp") {
      return c.json({ error: `test only supports kind='mcp' (got '${kind}')` }, 400);
    }
    const cfg = body.config as MCPConfig | undefined;
    if (!cfg || typeof cfg !== "object") {
      return c.json({ error: "config is required" }, 400);
    }
    const probe = new MCPPlugin(
      (typeof body.slug === "string" && body.slug) || "test-probe",
      true,
      cfg,
      secret,
    );
    const started = Date.now();
    try {
      const tools = await probe.connect();
      const ms = Date.now() - started;
      await probe.dispose();
      return c.json({
        ok: true, ms, tool_count: tools.length,
        tools: tools.map((t) => ({ name: t.name, description: t.description })),
      });
    } catch (e) {
      try { await probe.dispose(); } catch { /* ignore */ }
      return c.json({
        ok: false, ms: Date.now() - started, error: (e as Error).message,
      });
    }
  });
}

// --- /jobs routes ---
//
// CRUD for scheduled job definitions. The engine ticks separately
// (see jobs-engine.ts) and reads the same table.
//
//   GET    /jobs                  list all (default enabled only)
//   GET    /jobs/:id              one
//   POST   /jobs                  create  body: { name, description?, trigger, action }
//   PATCH  /jobs/:id              partial update
//   DELETE /jobs/:id              hard delete
//   POST   /jobs/:id/run          fire immediately (out-of-band)
//   POST   /jobs/:id/due-now      schedule for the next tick
//
// All gated by the auth gate above (path-prefix `/jobs` covered by
// the same gate as `/plugins` / `/settings`).
function registerJobsRoutes(app: Hono): void {
  app.get("/jobs", (c) => {
    const enabledRaw = c.req.query("enabled");
    const enabled =
      enabledRaw === "true" ? true : enabledRaw === "false" ? false : undefined;
    return c.json({ jobs: listJobs({ enabled }) });
  });

  app.get("/jobs/:id", (c) => {
    const j = getJobById(c.req.param("id"));
    return j ? c.json(j) : c.json({ error: "not found" }, 404);
  });

  app.post("/jobs", async (c) => {
    let body: Record<string, unknown>;
    try { body = (await c.req.json()) as Record<string, unknown>; }
    catch { return c.json({ error: "JSON body required" }, 400); }

    const name = typeof body.name === "string" ? body.name.trim() : "";
    if (!name) return c.json({ error: "name is required" }, 400);
    const trigger = body.trigger as Trigger | undefined;
    if (!trigger || typeof trigger !== "object" || !("kind" in trigger)) {
      return c.json({ error: "trigger is required" }, 400);
    }
    const action = body.action as Action | undefined;
    if (!action || typeof action !== "object" || !("kind" in action)) {
      return c.json({ error: "action is required" }, 400);
    }
    try {
      const j = createJob({
        name,
        description: typeof body.description === "string" ? body.description : undefined,
        trigger,
        action,
        enabled: body.enabled !== false,
        updatedBy: "http:/jobs",
      });
      return c.json(j, 201);
    } catch (e) {
      return c.json({ error: (e as Error).message }, 400);
    }
  });

  app.patch("/jobs/:id", async (c) => {
    let body: Record<string, unknown>;
    try { body = (await c.req.json()) as Record<string, unknown>; }
    catch { return c.json({ error: "JSON body required" }, 400); }

    try {
      const j = updateJob(c.req.param("id"), {
        name: typeof body.name === "string" ? body.name : undefined,
        description:
          body.description === null
            ? null
            : typeof body.description === "string"
              ? body.description
              : undefined,
        trigger: body.trigger as Trigger | undefined,
        action: body.action as Action | undefined,
        enabled: typeof body.enabled === "boolean" ? body.enabled : undefined,
        updatedBy: "http:/jobs",
      });
      return c.json(j);
    } catch (e) {
      const msg = (e as Error).message;
      const status = msg.includes("not found") ? 404 : 400;
      return c.json({ error: msg }, status);
    }
  });

  app.delete("/jobs/:id", (c) => {
    const ok = deleteJob(c.req.param("id"));
    return ok ? c.body(null, 204) : c.json({ error: "not found" }, 404);
  });

  app.post("/jobs/:id/run", async (c) => {
    const j = getJobById(c.req.param("id"));
    if (!j) return c.json({ error: "not found" }, 404);
    try {
      await runJobNow(j);
      const fresh = getJobById(j.id);
      return c.json(fresh);
    } catch (e) {
      return c.json({ error: (e as Error).message }, 500);
    }
  });

  app.post("/jobs/:id/due-now", (c) => {
    try {
      const j = forceDueNow(c.req.param("id"));
      return c.json(j);
    } catch (e) {
      return c.json({ error: (e as Error).message }, 404);
    }
  });
}

// --- /tasks routes ---
//
// Unified view per docs/design/src/screen-tasks.jsx. Aggregates four
// sources (approvalStore, conversation_index, jobs, future waiting
// flag) into a single design-shaped list.
//
//   GET /tasks                 HTML page (when Accept: text/html)
//   GET /tasks                 JSON list (Accept: application/json)
//                              query: ?status=...&search=...
//   GET /tasks/:id             single task with detail
//
// Action endpoints aren't here — UI delegates to the existing
// /approve, /deny, /jobs/:id/* endpoints. Keeps this thin.
function registerTasksRoutes(app: Hono): void {
  app.get("/tasks", (c) => {
    const accept = c.req.header("accept") ?? "";
    if (accept.includes("text/html") && !accept.includes("application/json")) {
      return c.html(TASKS_PAGE);
    }
    const statusRaw = c.req.query("status");
    const status = statusRaw && statusRaw !== "all"
      ? (statusRaw as TaskStatus)
      : undefined;
    const tasks = aggregateTasks({
      status,
      search: c.req.query("search") ?? undefined,
    });
    return c.json({ tasks });
  });

  app.get("/tasks/:id", (c) => {
    const t = aggregateTaskById(c.req.param("id"));
    return t ? c.json(t) : c.json({ error: "not found" }, 404);
  });
}
