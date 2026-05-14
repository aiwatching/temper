/**
 * Smith's HTTP control plane. Tiny — anything that wants to drive Smith
 * (a CLI, a web UI, an IDE plugin, an iOS shortcut) speaks plain JSON
 * over HTTP at the same port.
 *
 *   GET  /                              minimal chat web UI (browser entry)
 *   GET  /healthz                       liveness — Temper reachable + LLM creds
 *   POST /chat   body:{message,conversationId?}   single turn
 *
 * /chat content negotiation:
 *   - Accept: text/event-stream       SSE streaming (one event per token)
 *   - anything else (default)         single JSON {reply, stopReason}
 */
import { Hono } from "hono";
import { streamSSE } from "hono/streaming";

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

function addStreamingBubble() {
  // Like addRow("smith", "") but returns the bubble element so the
  // stream loop can keep appending text into it.
  const row = document.createElement("div");
  row.className = "row smith";
  const label = document.createElement("div");
  label.className = "role";
  label.textContent = "smith";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = "";
  row.appendChild(label);
  row.appendChild(bubble);
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return { row, bubble };
}

async function sendMessage() {
  const text = msg.value.trim();
  if (!text) return;
  msg.value = "";
  addRow("user", text);
  send.disabled = true;

  const { row, bubble } = addStreamingBubble();
  const toolHints = new Map();   // toolCallId → DOM node we can swap on tool_end

  try {
    const r = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify({ conversationId, message: text }),
    });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      row.classList.add("err");
      bubble.textContent = "Error " + r.status + ": " + (j.error || JSON.stringify(j));
      return;
    }

    // Parse SSE: events are "event: name\\ndata: payload\\n\\n".
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      // Split into completed events (separated by blank line)
      let idx;
      while ((idx = buf.indexOf("\\n\\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let evt = "message", data = "";
        for (const line of raw.split("\\n")) {
          if (line.startsWith("event:")) evt = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (evt === "delta") {
          bubble.textContent += data;
          log.scrollTop = log.scrollHeight;
        } else if (evt === "tool_start") {
          try {
            const t = JSON.parse(data);
            const span = document.createElement("span");
            span.style.cssText = "display:inline-block;margin:4px 0;padding:2px 8px;background:#fff8c5;border:1px solid #d4a72c;border-radius:3px;font-size:11px";
            span.textContent = "↻ " + t.toolName + "…";
            bubble.appendChild(span);
            toolHints.set(t.toolCallId, span);
          } catch (_) {}
        } else if (evt === "tool_end") {
          try {
            const t = JSON.parse(data);
            const span = toolHints.get(t.toolCallId);
            if (span) {
              span.textContent = (t.isError ? "✗ " : "✓ ") + t.toolName;
              span.style.background = t.isError ? "#ffebe9" : "#dafbe1";
              span.style.borderColor = t.isError ? "#ff8182" : "#1a7f37";
            }
          } catch (_) {}
        } else if (evt === "error") {
          try {
            const e = JSON.parse(data);
            row.classList.add("err");
            const errMsg = document.createElement("div");
            errMsg.style.marginTop = "4px";
            errMsg.textContent = "LLM error: " + e.error;
            bubble.appendChild(errMsg);
          } catch (_) {}
        }
        // "done" event closes — no UI action needed; loop exits via reader done.
      }
    }
    if (!bubble.textContent && !row.classList.contains("err")) {
      bubble.textContent = "(empty reply)";
    }
  } catch (e) {
    row.classList.add("err");
    bubble.textContent = "Network error: " + e.message;
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
        // If the client disconnects, stop pushing events. pi's
        // prompt() will keep running in the background; the next
        // /chat call against this session will queue / wait.
        stream.onAbort(() => { aborted = true; });
        try {
          await session.prompt(message);
        } finally {
          unsubscribe();
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
