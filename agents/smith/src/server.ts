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
import { getConfig } from "./config.js";
import { conversationIndex } from "./conversation-index.js";
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

export function buildApp(): Hono {
  const app = new Hono();
  const cfg = getConfig();

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
    const GATED_PATH = /^\/(?:chat|approve|deny|pending(?:\/.*)?|conversations(?:\/.*)?|plugins(?:\/.*)?)$/;
    app.use(async (c, next) => {
      if (!GATED_PATH.test(c.req.path)) return next();
      if (c.req.method === "GET" && (c.req.path === "/chat" || c.req.path === "/plugins")) {
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
  app.get("/plugins", (c) => c.html(PLUGINS_PAGE));

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

  return app;
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
  // List all plugins (no secrets in response).
  app.get("/plugins", (c) => {
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
