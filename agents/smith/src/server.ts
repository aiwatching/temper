/**
 * Smith's HTTP control plane. Tiny — anything that wants to drive Smith
 * (a CLI, a web UI, an IDE plugin, an iOS shortcut) speaks plain JSON
 * over HTTP at the same port.
 *
 *   GET  /                              minimal chat web UI (browser entry)
 *   GET  /healthz                       liveness — Temper reachable + LLM creds
 *   POST /chat   body:{message,conversationId?}   single turn
 *
 * Future: streaming SSE on /chat once we wire `session.subscribe()`
 * through to the response body. MVP returns a single JSON when the turn
 * completes.
 */
import { Hono } from "hono";

import { getConfig } from "./config.js";
import { Temper, TemperError } from "./temper.js";
import { getSessionPool } from "./session-manager.js";

/**
 * Minimal vanilla-JS chat UI. Inline HTML keeps the agent self-contained
 * (no build step, no static asset directory). Replace with a real
 * frontend later if/when the conversation surface grows beyond a single
 * text input + scroll-back.
 */
const CHAT_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Smith</title>
  <style>
    :root { color-scheme: light dark; }
    * { box-sizing: border-box; }
    body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           margin: 0; padding: 0; height: 100vh; display: flex; flex-direction: column; }
    header { padding: 10px 16px; border-bottom: 1px solid #d0d7de;
             display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    header h1 { font-size: 14px; margin: 0; font-weight: 600; }
    header .meta { font-size: 12px; color: #57606a; }
    header button { font-size: 12px; padding: 4px 10px; border: 1px solid #d0d7de;
                    background: transparent; border-radius: 4px; cursor: pointer; }
    main { flex: 1; overflow-y: auto; padding: 16px; max-width: 760px; margin: 0 auto;
           width: 100%; }
    .row { margin-bottom: 14px; }
    .role { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em;
            color: #57606a; margin-bottom: 3px; }
    .bubble { padding: 10px 12px; border-radius: 6px; border: 1px solid #d0d7de;
              white-space: pre-wrap; word-break: break-word; }
    .user .bubble { background: #ddf4ff; border-color: #b6e3ff; }
    .smith .bubble { background: #f6f8fa; }
    .err .bubble { background: #ffebe9; border-color: #ff8182; }
    footer { border-top: 1px solid #d0d7de; padding: 10px 16px; display: flex; gap: 8px;
             max-width: 760px; margin: 0 auto; width: 100%; }
    textarea { flex: 1; padding: 8px 10px; border: 1px solid #d0d7de; border-radius: 4px;
               font: inherit; resize: none; min-height: 36px; max-height: 200px; }
    button.send { padding: 8px 14px; border: 0; border-radius: 4px; background: #2da44e;
                  color: white; font-weight: 600; cursor: pointer; }
    button.send:disabled { background: #6e7781; cursor: not-allowed; }
    code { background: rgba(175,184,193,0.2); padding: 1px 5px; border-radius: 3px; }
  </style>
</head>
<body>
  <header>
    <h1>Smith</h1>
    <span class="meta" id="meta">…</span>
    <button id="reset">Reset conversation</button>
  </header>
  <main id="log"></main>
  <footer>
    <textarea id="msg" rows="1" placeholder="Message Smith — Cmd/Ctrl+Enter to send"></textarea>
    <button class="send" id="send">Send</button>
  </footer>
<script>
const log = document.getElementById("log");
const msg = document.getElementById("msg");
const send = document.getElementById("send");
const reset = document.getElementById("reset");
const meta = document.getElementById("meta");

let conversationId = sessionStorage.getItem("smith.convId")
  || "ui-" + Math.random().toString(36).slice(2, 10);
sessionStorage.setItem("smith.convId", conversationId);

function addRow(role, text, isError) {
  const row = document.createElement("div");
  row.className = "row " + role + (isError ? " err" : "");
  const label = document.createElement("div");
  label.className = "role";
  label.textContent = role === "user" ? "you" : "smith";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(label);
  row.appendChild(bubble);
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}

async function refreshMeta() {
  try {
    const r = await fetch("/healthz");
    const b = await r.json();
    meta.textContent = (b.temper_user || "?") + " · " + b.llm_provider + "/" + b.llm_model
      + (b.status === "ok" ? "" : " · " + b.status);
  } catch (e) { meta.textContent = "(/healthz unreachable)"; }
}

async function sendMessage() {
  const text = msg.value.trim();
  if (!text) return;
  msg.value = "";
  addRow("user", text);
  send.disabled = true;
  try {
    const r = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversationId, message: text }),
    });
    const j = await r.json();
    if (r.ok) {
      addRow("smith", j.reply || "(empty reply)");
    } else {
      addRow("smith", "Error " + r.status + ": " + (j.error || JSON.stringify(j)), true);
    }
  } catch (e) {
    addRow("smith", "Network error: " + e.message, true);
  } finally {
    send.disabled = false;
    msg.focus();
  }
}

send.addEventListener("click", sendMessage);
msg.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); sendMessage(); }
});
reset.addEventListener("click", () => {
  conversationId = "ui-" + Math.random().toString(36).slice(2, 10);
  sessionStorage.setItem("smith.convId", conversationId);
  log.innerHTML = "";
});

refreshMeta();
setInterval(refreshMeta, 30000);
msg.focus();
</script>
</body>
</html>`;

export function buildApp(): Hono {
  const app = new Hono();

  // ---- chat UI ----
  app.get("/", (c) => c.html(CHAT_HTML));

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

    // Collect the assistant's final reply via `agent_end` — fires when
    // the whole turn (including any tool roundtrips) is done and carries
    // the complete message transcript. `text_delta` events also stream
    // for short replies, but the model often emits no delta when a turn
    // is dominated by tool calls; reading from the final transcript
    // is robust to both cases.
    //
    // SSE upgrade will switch to streaming `text_delta` directly through
    // the response body; agent_end stays as the close signal.
    // Pull reply + any provider-level error out of the final agent_end.
    // pi's `prompt()` resolves cleanly even when Anthropic / OpenAI / etc
    // returned a 4xx — the failure shows up as `stopReason: "error"` on
    // the last assistant message with the detail in `errorMessage`.
    // Surface it explicitly so the caller doesn't get a silent empty reply.
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

  return app;
}
