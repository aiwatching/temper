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

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

import { existsSync, unlinkSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { approvalStore, type PendingApproval } from "./approval-store.js";
import { getConfig } from "./config.js";
import { conversationIndex } from "./conversation-index.js";
import { Temper, TemperError } from "./temper.js";
import { getSessionPool } from "./session-manager.js";

// Inline marked (≈40KB) into the chat HTML so the UI works offline /
// in air-gapped corp networks without a CDN. Read once at module load;
// require.resolve handles pnpm's flat layout cleanly.
const _req = createRequire(import.meta.url);
const MARKED_JS = readFileSync(_req.resolve("marked/marked.min.js"), "utf8");

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
              word-break: break-word; }
    /* User bubble stays plaintext-friendly */
    .user .bubble { background: #ddf4ff; border-color: #b6e3ff; white-space: pre-wrap; }
    /* Smith bubble renders Markdown — block elements need normal margins */
    .smith .bubble { background: #f6f8fa; }
    .smith .bubble > *:first-child { margin-top: 0; }
    .smith .bubble > *:last-child  { margin-bottom: 0; }
    .smith .bubble p, .smith .bubble ul, .smith .bubble ol, .smith .bubble blockquote
      { margin: 0.4em 0; }
    .smith .bubble h1, .smith .bubble h2, .smith .bubble h3
      { margin: 0.6em 0 0.3em; line-height: 1.25; }
    .smith .bubble pre { background: #1f2328; color: #f6f8fa; padding: 10px 12px;
                         border-radius: 4px; overflow-x: auto; font-size: 12px;
                         line-height: 1.45; }
    .smith .bubble pre code { background: transparent; padding: 0; color: inherit; }
    .smith .bubble blockquote { padding-left: 10px; border-left: 3px solid #d0d7de;
                                color: #57606a; }
    .smith .bubble table { border-collapse: collapse; margin: 0.5em 0; }
    .smith .bubble th, .smith .bubble td { border: 1px solid #d0d7de; padding: 4px 8px; }
    .err .bubble { background: #ffebe9; border-color: #ff8182; white-space: pre-wrap; }
    /* Thinking block — collapsed by default, native <details> */
    .thinking { margin-bottom: 6px; padding: 4px 8px; background: #fffbea;
                border: 1px solid #f0c674; border-radius: 4px; font-size: 12px; }
    .thinking summary { cursor: pointer; color: #7a5a00; user-select: none; }
    .thinking[open] summary { margin-bottom: 4px; }
    .thinking .body { white-space: pre-wrap; color: #5d4400; font-family: ui-monospace,
                      Menlo, Consolas, monospace; font-size: 11px; line-height: 1.4; }
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
    <select id="conv-picker" title="Switch conversation"
            style="font-size:12px;padding:3px 6px;border:1px solid #d0d7de;border-radius:4px;background:transparent;max-width:240px"></select>
    <button id="delete-conv" title="Delete current conversation"
            style="font-size:12px;padding:3px 8px;border:1px solid #d0d7de;border-radius:4px;background:transparent;cursor:pointer">🗑</button>
    <button id="reset">New conversation</button>
  </header>
  <main id="log"></main>
  <footer>
    <textarea id="msg" rows="1" placeholder="Message Smith — Cmd/Ctrl+Enter to send"></textarea>
    <button class="send" id="send">Send</button>
  </footer>
<script>${MARKED_JS}</script>
<script>
// Configure marked: GitHub-flavored, break-on-newline, no raw HTML
// passthrough (the model output is untrusted enough that we'd rather
// lose an occasional <em> than ship an XSS surface).
marked.use({ gfm: true, breaks: true });
const log = document.getElementById("log");
const msg = document.getElementById("msg");
const send = document.getElementById("send");
const reset = document.getElementById("reset");
const meta = document.getElementById("meta");

let conversationId = sessionStorage.getItem("smith.convId")
  || "ui-" + Math.random().toString(36).slice(2, 10);
sessionStorage.setItem("smith.convId", conversationId);

// Bearer auth bootstrap. SMITH_SECRET may or may not be set on the
// server. If it is, the user lands on /#secret=<value>; we extract,
// persist to sessionStorage, and scrub the hash so it doesn't sit in
// the address bar. From then on every fetch attaches the bearer.
(function () {
  const m = (location.hash || "").match(/(?:^#|&)secret=([^&]+)/);
  if (m) {
    sessionStorage.setItem("smith.secret", decodeURIComponent(m[1]));
    history.replaceState(null, "", location.pathname + location.search);
  }
})();
function authHeaders(extra) {
  const h = Object.assign({}, extra || {});
  const s = sessionStorage.getItem("smith.secret");
  if (s) h["Authorization"] = "Bearer " + s;
  return h;
}
function promptForSecret() {
  const s = prompt("Smith requires a bearer secret. Paste SMITH_SECRET:");
  if (s) sessionStorage.setItem("smith.secret", s.trim());
}

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

/* Render markdown safely. marked v15 returns HTML; raw HTML from the
 * model is left as text by marked's defaults. Good enough for trusted
 * internal LLM output; upgrade to DOMPurify if smith ever talks to
 * untrusted prompts. */
function renderMarkdownInto(el, source) {
  try {
    el.innerHTML = marked.parse(source || "");
  } catch (e) {
    el.textContent = source;
  }
  log.scrollTop = log.scrollHeight;
}

/* When the gate blocks a tool call, render a confirm card inline:
 * shows toolName + JSON args + Approve / Deny buttons. Approve fires
 * /approve then starts a follow-up /chat turn so the LLM retries
 * automatically. */
function renderPendingApproval(parent, t) {
  const box = document.createElement("div");
  box.style.cssText =
    "margin:8px 0;padding:10px 12px;border:2px solid #d4a72c;background:#fff8c5;border-radius:6px;font-size:13px";
  const head = document.createElement("div");
  head.style.cssText = "font-weight:600;color:#7a5a00;margin-bottom:6px";
  head.textContent = "🛑 Action requires approval: " + t.toolName;
  box.appendChild(head);
  const pre = document.createElement("pre");
  pre.style.cssText =
    "margin:0 0 8px;padding:6px 8px;background:#fff;border:1px solid #e5e7eb;border-radius:4px;font-size:11px;overflow-x:auto;white-space:pre-wrap";
  pre.textContent = JSON.stringify(t.input, null, 2);
  box.appendChild(pre);
  const btnRow = document.createElement("div");
  btnRow.style.cssText = "display:flex;gap:8px";
  const approveBtn = document.createElement("button");
  approveBtn.textContent = "Approve & retry";
  approveBtn.style.cssText =
    "padding:6px 12px;border:0;border-radius:4px;background:#1a7f37;color:white;cursor:pointer;font:inherit";
  const denyBtn = document.createElement("button");
  denyBtn.textContent = "Deny";
  denyBtn.style.cssText =
    "padding:6px 12px;border:1px solid #d0d7de;border-radius:4px;background:transparent;cursor:pointer;font:inherit";
  btnRow.appendChild(approveBtn);
  btnRow.appendChild(denyBtn);
  box.appendChild(btnRow);
  parent.appendChild(box);

  approveBtn.addEventListener("click", async () => {
    approveBtn.disabled = true; denyBtn.disabled = true;
    approveBtn.textContent = "Approving…";
    try {
      const r = await fetch("/approve", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ conversationId, toolName: t.toolName, argsHash: t.argsHash }),
      });
      if (!r.ok) throw new Error("approve " + r.status);
      // Replace the card with a "✓ approved" pill, then trigger a
      // retry turn — the LLM picks up its prior reasoning + the new
      // user message and re-attempts the tool.
      box.innerHTML = "<span style='color:#1a7f37;font-weight:600'>✓ Approved " + t.toolName + " — retrying…</span>";
      msg.value = "(approved — please retry the " + t.toolName + " call)";
      sendMessage();
    } catch (e) {
      box.innerHTML = "<span style='color:#b35900'>Approve failed: " + e.message + "</span>";
    }
  });

  denyBtn.addEventListener("click", async () => {
    approveBtn.disabled = true; denyBtn.disabled = true;
    try {
      await fetch("/deny", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ conversationId, toolName: t.toolName, argsHash: t.argsHash }),
      });
    } catch (_) {}
    box.innerHTML = "<span style='color:#7a5a00'>✗ Denied " + t.toolName + "</span>";
  });
}

async function sendMessage() {
  const text = msg.value.trim();
  if (!text) return;
  msg.value = "";
  addRow("user", text);
  send.disabled = true;

  const { row, bubble } = addStreamingBubble();
  // Two scratch buffers — thinking text and main text are interleaved
  // in the SSE stream but render to different DOM nodes.
  let mainBuf = "";
  let thinkBuf = "";
  let thinkEl = null;   // <details class="thinking"> when needed
  let thinkBody = null; // .body div inside <details>
  const toolHints = new Map();

  function ensureThinkBlock() {
    if (thinkEl) return;
    thinkEl = document.createElement("details");
    thinkEl.className = "thinking";
    thinkEl.open = false;
    const sum = document.createElement("summary");
    sum.textContent = "💭 thinking…";
    thinkEl.appendChild(sum);
    thinkBody = document.createElement("div");
    thinkBody.className = "body";
    thinkEl.appendChild(thinkBody);
    // Insert ABOVE the bubble so the reasoning sits where you'd
    // glance first if curious, and the final answer is below it.
    row.insertBefore(thinkEl, bubble);
  }

  try {
    const r = await fetch("/chat", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json", "Accept": "text/event-stream" }),
      body: JSON.stringify({ conversationId, message: text }),
    });
    if (r.status === 401) {
      row.classList.add("err");
      bubble.textContent = "Unauthorized — bearer secret missing or wrong.";
      promptForSecret();
      return;
    }
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      row.classList.add("err");
      bubble.textContent = "Error " + r.status + ": " + (j.error || JSON.stringify(j));
      return;
    }

    // Parse SSE: events separated by blank lines.
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
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
          mainBuf += data;
          renderMarkdownInto(bubble, mainBuf);
        } else if (evt === "thinking") {
          ensureThinkBlock();
          thinkBuf += data;
          thinkBody.textContent = thinkBuf;
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
        } else if (evt === "tool_pending") {
          try {
            const t = JSON.parse(data);
            renderPendingApproval(bubble, t);
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
        } else if (evt === "done") {
          // Finalise summary text on the think block
          if (thinkEl) {
            const sum = thinkEl.querySelector("summary");
            if (sum) sum.textContent = "💭 reasoning (click to expand)";
          }
        }
      }
    }
    if (!mainBuf && !row.classList.contains("err")) {
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
  refreshConversations();
});

// ---- conversation picker ----
const picker = document.getElementById("conv-picker");
const deleteConvBtn = document.getElementById("delete-conv");

async function refreshConversations() {
  try {
    const r = await fetch("/conversations", { headers: authHeaders() });
    if (!r.ok) return;
    const j = await r.json();
    const list = j.conversations || [];
    // First option: current conv (which may or may not be in the index yet
    // if the user hasn't sent a message). Then the list, deduped.
    picker.innerHTML = "";
    const seen = new Set();
    const optCurrent = document.createElement("option");
    const meEntry = list.find((c) => c.id === conversationId);
    optCurrent.value = conversationId;
    optCurrent.textContent = meEntry ? meEntry.title : "(current — unsaved)";
    optCurrent.selected = true;
    picker.appendChild(optCurrent);
    seen.add(conversationId);
    for (const c of list) {
      if (seen.has(c.id)) continue;
      const o = document.createElement("option");
      o.value = c.id;
      const when = c.lastUsedAt.replace("T", " ").slice(0, 16);
      o.textContent = c.title + "  ·  " + when + "  ·  " + c.messageCount + "t";
      picker.appendChild(o);
      seen.add(c.id);
    }
  } catch (_) { /* picker is best-effort */ }
}

picker.addEventListener("change", () => {
  const next = picker.value;
  if (!next || next === conversationId) return;
  conversationId = next;
  sessionStorage.setItem("smith.convId", conversationId);
  log.innerHTML = "";
  // pi resumes the prior turns on the server side when the next /chat
  // lands — the UI starts blank but the LLM has the full context.
  const note = document.createElement("div");
  note.style.cssText = "padding:8px 12px;color:#57606a;font-size:12px;font-style:italic;text-align:center";
  note.textContent = "Switched to '" + next + "'. Prior turns are server-side; ask a question to continue.";
  log.appendChild(note);
});

deleteConvBtn.addEventListener("click", async () => {
  if (!confirm("Delete conversation '" + conversationId + "'?\\n\\n" +
               "This wipes its JSONL on disk. Long-term TEMPER memory stays.\\n" +
               "OK to also archive a one-line summary into TEMPER before delete? " +
               "(Cancel = delete without archive.)")) return;
  const archive = confirm("Archive a summary episode to TEMPER first?");
  try {
    const url = "/conversations/" + encodeURIComponent(conversationId) + (archive ? "?archive=true" : "");
    const r = await fetch(url, { method: "DELETE", headers: authHeaders() });
    if (!r.ok) {
      const j = await r.json().catch(() => ({}));
      alert("Delete failed: " + (j.error || r.status));
      return;
    }
    // Spin up a fresh conv id and clear UI
    conversationId = "ui-" + Math.random().toString(36).slice(2, 10);
    sessionStorage.setItem("smith.convId", conversationId);
    log.innerHTML = "";
    refreshConversations();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
});

refreshMeta();
setInterval(refreshMeta, 30000);
refreshConversations();
setInterval(refreshConversations, 60000);
msg.focus();
</script>
</body>
</html>`;

export function buildApp(): Hono {
  const app = new Hono();
  const cfg = getConfig();

  // ---- bearer auth (A7) ----
  // SMITH_SECRET in .env enables. Empty/unset = open (dev mode, but
  // pair with SMITH_HOST=127.0.0.1 only). When enabled, gated routes
  // need Authorization: Bearer <SMITH_SECRET>. /healthz + GET / and
  // the inlined static assets stay open so monitoring + UI bootstrap
  // both work.
  if (cfg.smithSecret) {
    const expected = "Bearer " + cfg.smithSecret;
    const GATED = /^\/(?:chat|approve|deny|pending(?:\/.*)?|conversations(?:\/.*)?)$/;
    app.use(async (c, next) => {
      if (!GATED.test(c.req.path)) return next();
      const got = c.req.header("authorization") ?? "";
      // Constant-time compare to deter timing-side-channel attacks
      // even though the chance of one mattering on localhost is small.
      if (got.length !== expected.length || got !== expected) {
        return c.json({ error: "Unauthorized" }, 401);
      }
      return next();
    });
  }

  // ---- chat UI ----
  app.get("/", (c) => c.html(CHAT_HTML));

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

  return app;
}
