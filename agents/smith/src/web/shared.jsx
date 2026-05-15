// shared.jsx — Smith UI atoms + real-data hooks. Loaded by /chat and /briefs
// before the per-page layout. Babel-standalone transforms in browser; nothing
// imports modules — everything hangs off `window` so the next script tag can
// pick it up.
//
// Anything that talks to the Smith HTTP plane goes through `api()`, which
// handles bearer auth + 401 prompt + JSON parse uniformly. Anything that
// talks to /chat (SSE) goes through `useChatStream`.

const { useState, useEffect, useRef, useMemo, useCallback, Fragment } = React;

// ─── auth + fetch ─────────────────────────────────────────────────────────
// SMITH_SECRET (when set) lands on the page via /#secret=<v>. Persist to
// sessionStorage, scrub the URL hash so it doesn't sit in the address bar.
(function bootstrapSecret() {
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

async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: authHeaders({
      ...(opts.body ? { "Content-Type": "application/json" } : {}),
      ...(opts.headers || {}),
    }),
  });
  if (r.status === 401) {
    promptForSecret();
    throw new Error("Unauthorized");
  }
  if (!r.ok) {
    let body = {};
    try { body = await r.json(); } catch (_) {}
    throw new Error(body.error || ("HTTP " + r.status));
  }
  return r.json();
}

// ─── icons ────────────────────────────────────────────────────────────────
const Icon = ({ name, size = 14, ...rest }) => {
  const s = size;
  const props = { width: s, height: s, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', ...rest };
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    search: <g><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></g>,
    send: <path d="M5 12h14M13 6l6 6-6 6" />,
    chevron: <path d="m9 6 6 6-6 6" />,
    chevronDown: <path d="m6 9 6 6 6-6" />,
    check: <path d="M5 12l5 5L20 7" />,
    x: <path d="M6 6l12 12M6 18 18 6" />,
    spark: <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />,
    memory: <g><rect x="4" y="6" width="16" height="12" rx="2" /><path d="M9 6V4M15 6V4M9 20v-2M15 20v-2M2 10h2M2 14h2M20 10h2M20 14h2M9 10h6M9 14h6" /></g>,
    cog: <g><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></g>,
    book: <path d="M4 4v16a2 2 0 0 1 2-2h14V2H6a2 2 0 0 0-2 2zM4 20h16" />,
    bug: <g><rect x="8" y="6" width="8" height="14" rx="4" /><path d="M8 10H4M8 14H3M8 18H4M16 10h4M16 14h5M16 18h4M9 6V4l-2-2M15 6V4l2-2" /></g>,
    git: <g><circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="14" r="2.2"/><path d="M6 8.2v7.6M8.2 6h5.6a2 2 0 0 1 2 2v3.8"/></g>,
    mail: <g><rect x="3" y="5" width="18" height="14" rx="2" /><path d="m3 7 9 7 9-7" /></g>,
    file: <g><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5" /></g>,
    clock: <g><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></g>,
    flash: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
    user: <g><circle cx="12" cy="8" r="4" /><path d="M3 21c0-4.4 4-7 9-7s9 2.6 9 7" /></g>,
    dots: <g><circle cx="6" cy="12" r="1.2" fill="currentColor"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/><circle cx="18" cy="12" r="1.2" fill="currentColor"/></g>,
    side: <g><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/></g>,
    pin: <path d="M12 2v8M5 10h14l-3 4-4 9-4-9z" />,
    ext: <path d="M14 4h6v6M20 4l-9 9M10 6H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-4" />,
    fork: <g><circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 7v3a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3V7M12 13v4"/></g>,
    chat: <path d="M21 12a8 8 0 1 1-3-6.2L21 4l-1 4.2A7.9 7.9 0 0 1 21 12z" />,
    folder: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
    download: <path d="M12 3v12m0 0-4-4m4 4 4-4M5 20h14" />,
    eye: <g><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></g>,
    lock: <g><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></g>,
    phone: <g><rect x="7" y="2" width="10" height="20" rx="2" /><circle cx="12" cy="18" r="0.8" fill="currentColor"/></g>,
    trash: <g><path d="M4 7h16M9 7V4h6v3M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13" /></g>,
    refresh: <path d="M3 12a9 9 0 0 1 15.5-6.3L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.5 6.3L3 16M3 21v-5h5" />,
  };
  return <svg {...props}>{paths[name]}</svg>;
};

// ─── atoms ────────────────────────────────────────────────────────────────
const StatusDot = ({ status }) => (
  <span className={`dot ${status === 'ok' ? 'ok' : status === 'warn' ? 'warn' : status === 'bad' || status === 'degraded' ? 'bad' : ''}`} />
);

const Avatar = ({ kind, label, size }) => {
  const className = `avatar ${kind === 'smith' ? 'smith' : ''} ${size === 'lg' ? 'lg' : size === 'sm' ? 'sm' : ''}`.trim();
  if (kind === 'smith') return <span className={className}><Icon name="spark" size={size === 'lg' ? 15 : 12} /></span>;
  return <span className={className}>{(label || '?').slice(0, 1).toUpperCase()}</span>;
};

// ─── markdown ─────────────────────────────────────────────────────────────
// `marked` is loaded by the page before shared.jsx. We render assistant
// text through it but keep raw HTML escaping on (marked.parse already
// does that by default for inline content; we don't enable `html: true`).
function renderMarkdown(source) {
  if (!source) return { __html: '' };
  try { return { __html: window.marked.parse(source) }; }
  catch (_) { return { __html: source.replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])) }; }
}
const Markdown = ({ source }) => (
  <div className="msg-smith" dangerouslySetInnerHTML={renderMarkdown(source)} />
);

// ─── tool-call cards ──────────────────────────────────────────────────────
const ToolChip = ({ call }) => {
  // In-progress / completed tool indicator that lives inside the smith
  // bubble, replacing the "✓ tool_name" pill we used in the old UI.
  const cls =
    call.status === 'ok' ? 'tool-chip ok' :
    call.status === 'error' ? 'tool-chip bad' :
    'tool-chip';
  const glyph =
    call.status === 'ok' ? '✓' :
    call.status === 'error' ? '✗' : '↻';
  return (
    <span className={cls} title={call.duration ? `${call.duration}ms` : ''}>
      {glyph} {call.toolName}
    </span>
  );
};

const ConfirmCard = ({ pending, onApprove, onDeny, state }) => {
  // pending: { toolName, input, argsHash, conversationId }
  // state:   'pending' | 'approved' | 'denied'
  return (
    <div className="tool-call dangerous" style={{ marginTop: 10 }}>
      <div className="tc-head">
        <Icon name="lock" size={12} />
        <strong>需批准 · {pending.toolName}</strong>
        <span style={{ flex: 1 }} />
        <span className="pill warn">beforeToolCall</span>
      </div>
      <div style={{ padding: '10px 12px' }}>
        <div className="muted" style={{ fontSize: 10.5, marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.04em' }}>args</div>
        <pre className="mono" style={{ margin: 0, padding: 8, background: 'var(--panel-2)', borderRadius: 4, fontSize: 11.5, color: 'var(--ink-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.45 }}>{JSON.stringify(pending.input, null, 2)}</pre>
        {state === 'pending' && (
          <div className="row" style={{ marginTop: 10, justifyContent: 'flex-end' }}>
            <button className="btn sm subtle" onClick={onDeny}>拒绝</button>
            <button className="btn sm primary" onClick={onApprove}>批准并重试</button>
          </div>
        )}
        {state === 'approved' && (
          <div className="row" style={{ marginTop: 8, color: 'var(--good)', fontSize: 12 }}>
            <Icon name="check" size={13} /><span>已批准 · 正在重试…</span>
          </div>
        )}
        {state === 'denied' && (
          <div className="row" style={{ marginTop: 8, color: 'var(--ink-3)', fontSize: 12 }}>
            <Icon name="x" size={13} /><span>已拒绝</span>
          </div>
        )}
      </div>
    </div>
  );
};

// ─── chat state hook ──────────────────────────────────────────────────────
// useChatStream wraps the SSE protocol Smith's /chat speaks. The returned
// `turns` is an append-only list of { kind, ... } items the layouts render
// however they like (bubbles, dashboard cards, etc). `send` posts a turn;
// `approve` / `deny` resolve a pending confirm.
//
// turn kinds:
//   { kind:'user',     text }
//   { kind:'smith',    text, thinking, toolCalls:[...], pending:{}|null,
//                      pendingState:'pending'|'approved'|'denied',
//                      error?, done }
function newConvId() {
  return "ui-" + Math.random().toString(36).slice(2, 10);
}

function useChatStream() {
  const [convId, setConvId] = useState(() => {
    const saved = sessionStorage.getItem("smith.convId");
    if (saved) return saved;
    const id = newConvId();
    sessionStorage.setItem("smith.convId", id);
    return id;
  });
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const convIdRef = useRef(convId);
  convIdRef.current = convId;

  const updateLastSmith = useCallback((patch) => {
    setTurns((prev) => {
      const next = prev.slice();
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].kind === 'smith') {
          next[i] = typeof patch === 'function' ? patch(next[i]) : { ...next[i], ...patch };
          break;
        }
      }
      return next;
    });
  }, []);

  // Replay a conversation's prior messages from its JSONL into `turns`
  // so the scrollback isn't empty when the user switches conversations.
  // Historical turns reconstruct as text-only — no thinking/tool replay,
  // since those event streams aren't kept verbatim on disk. We mark
  // them done so the streaming cursor doesn't render.
  const loadHistory = useCallback(async (id) => {
    setLoadingHistory(true);
    try {
      const j = await api(`/conversations/${encodeURIComponent(id)}/messages`);
      // Only mount the result if the user hasn't switched again since.
      if (convIdRef.current !== id) return;
      const next = (j.messages || []).map((m) =>
        m.role === 'user'
          ? { kind: 'user', text: m.text }
          : { kind: 'smith', text: m.text, thinking: '', toolCalls: [], pending: null, done: true },
      );
      setTurns(next);
    } catch (e) {
      if (convIdRef.current === id) {
        setTurns([{ kind: 'smith', text: '', thinking: '', toolCalls: [], pending: null, done: true, error: 'history load failed: ' + e.message }]);
      }
    } finally {
      setLoadingHistory(false);
    }
  }, []);

  // Load history for whatever convId we landed with at page open. If
  // the session was never persisted (a fresh "ui-…" id minted just
  // now), the endpoint returns an empty list — cheap.
  useEffect(() => {
    loadHistory(convId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const switchTo = useCallback((id) => {
    if (!id || id === convId) return;
    setConvId(id);
    sessionStorage.setItem("smith.convId", id);
    setTurns([]);
    loadHistory(id);
  }, [convId, loadHistory]);

  const reset = useCallback(() => {
    const id = newConvId();
    setConvId(id);
    sessionStorage.setItem("smith.convId", id);
    setTurns([]);
  }, []);

  const send = useCallback(async (text) => {
    if (!text || !text.trim()) return;
    text = text.trim();
    setTurns((prev) => [
      ...prev,
      { kind: 'user', text },
      { kind: 'smith', text: '', thinking: '', toolCalls: [], pending: null, done: false },
    ]);
    setBusy(true);

    try {
      const r = await fetch("/chat", {
        method: "POST",
        headers: authHeaders({ "Content-Type": "application/json", "Accept": "text/event-stream" }),
        body: JSON.stringify({ conversationId: convId, message: text }),
      });
      if (r.status === 401) { promptForSecret(); throw new Error("Unauthorized"); }
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error("Error " + r.status + ": " + (j.error || JSON.stringify(j)));
      }

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let evt = "message", data = "";
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) evt = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (evt === "delta") {
            updateLastSmith((s) => ({ ...s, text: s.text + data }));
          } else if (evt === "thinking") {
            updateLastSmith((s) => ({ ...s, thinking: s.thinking + data }));
          } else if (evt === "tool_start") {
            try {
              const t = JSON.parse(data);
              updateLastSmith((s) => ({
                ...s,
                toolCalls: [...s.toolCalls, { toolCallId: t.toolCallId, toolName: t.toolName, status: 'running' }],
              }));
            } catch (_) {}
          } else if (evt === "tool_end") {
            try {
              const t = JSON.parse(data);
              updateLastSmith((s) => ({
                ...s,
                toolCalls: s.toolCalls.map((c) =>
                  c.toolCallId === t.toolCallId
                    ? { ...c, status: t.isError ? 'error' : 'ok' }
                    : c,
                ),
              }));
            } catch (_) {}
          } else if (evt === "tool_pending") {
            try {
              const t = JSON.parse(data);
              updateLastSmith({ pending: { ...t, conversationId: convId }, pendingState: 'pending' });
            } catch (_) {}
          } else if (evt === "error") {
            try {
              const e = JSON.parse(data);
              updateLastSmith({ error: e.error || 'LLM error' });
            } catch (_) {}
          } else if (evt === "done") {
            updateLastSmith({ done: true });
          }
        }
      }
    } catch (e) {
      updateLastSmith({ error: e.message, done: true });
    } finally {
      setBusy(false);
    }
  }, [convId, updateLastSmith]);

  const approve = useCallback(async (pending) => {
    try {
      await api("/approve", {
        method: "POST",
        body: JSON.stringify({
          conversationId: pending.conversationId,
          toolName: pending.toolName,
          argsHash: pending.argsHash,
        }),
      });
      updateLastSmith({ pendingState: 'approved' });
      // Nudge the LLM to retry — same pattern as the legacy UI.
      send("(approved — please retry the " + pending.toolName + " call)");
    } catch (e) {
      updateLastSmith({ error: "Approve failed: " + e.message });
    }
  }, [send, updateLastSmith]);

  const deny = useCallback(async (pending) => {
    try {
      await api("/deny", {
        method: "POST",
        body: JSON.stringify({
          conversationId: pending.conversationId,
          toolName: pending.toolName,
          argsHash: pending.argsHash,
        }),
      });
    } catch (_) { /* deny is best-effort */ }
    updateLastSmith({ pendingState: 'denied' });
  }, [updateLastSmith]);

  return { convId, switchTo, reset, turns, send, approve, deny, busy, loadingHistory };
}

// ─── conversations + health pollers ───────────────────────────────────────
function useConversations(activeId) {
  const [list, setList] = useState([]);
  const refresh = useCallback(async () => {
    try {
      const j = await api("/conversations");
      setList(j.conversations || []);
    } catch (_) { /* picker is best-effort */ }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 60_000);
    return () => clearInterval(t);
  }, [refresh, activeId]);
  return { list, refresh };
}

function useHealth() {
  const [meta, setMeta] = useState(null);
  const [error, setError] = useState(null);
  const refresh = useCallback(async () => {
    try {
      const r = await fetch("/healthz");
      const b = await r.json();
      setMeta(b);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => clearInterval(t);
  }, [refresh]);
  return { meta, error, refresh };
}

// ─── conversation picker ──────────────────────────────────────────────────
const ConvPicker = ({ activeId, conversations, onSwitch, onReset, onDelete }) => {
  const meEntry = conversations.find((c) => c.id === activeId);
  return (
    <div className="row" style={{ gap: 6 }}>
      <select
        className="conv-picker"
        value={activeId}
        onChange={(e) => onSwitch(e.target.value)}
        title="切换会话"
      >
        <option value={activeId}>{meEntry ? meEntry.title : "(当前 · 未保存)"}</option>
        {conversations.filter((c) => c.id !== activeId).map((c) => (
          <option key={c.id} value={c.id}>
            {c.title} · {c.lastUsedAt.replace("T", " ").slice(0, 16)} · {c.messageCount}t
          </option>
        ))}
      </select>
      <button className="btn xs subtle" title="删除当前会话" onClick={onDelete}><Icon name="trash" size={12} /></button>
      <button className="btn xs subtle" title="新会话" onClick={onReset}><Icon name="plus" size={12} /></button>
    </div>
  );
};

// ─── composer ─────────────────────────────────────────────────────────────
// Enter inserts a newline. ⌘/Ctrl+Enter (or clicking Send) submits.
// We deliberately avoid the chat-app pattern of "Enter = send" so a
// stray Enter mid-paragraph doesn't fire off a half-written prompt.
const Composer = ({ onSend, busy, placeholder, accessory }) => {
  const [val, setVal] = useState('');
  const ref = useRef(null);
  const send = () => {
    if (!val.trim() || busy) return;
    onSend(val);
    setVal('');
  };
  return (
    <div className="composer">
      <textarea
        ref={ref}
        value={val}
        onChange={(e) => setVal(e.target.value)}
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            send();
          }
          // Plain Enter intentionally falls through → newline.
        }}
        rows={2}
        placeholder={placeholder || '消息 Smith — ⌘/Ctrl+Enter 发送 · Enter 换行'}
      />
      <div className="row" style={{ marginTop: 8, gap: 6 }}>
        {accessory}
        <span style={{ flex: 1 }} />
        <span className="muted" style={{ fontSize: 11 }}>
          <kbd style={{ fontFamily: 'var(--mono)' }}>⌘⏎</kbd> 发送 ·{' '}
          <kbd style={{ fontFamily: 'var(--mono)' }}>⏎</kbd> 换行
        </span>
        <button className="btn primary sm" disabled={busy || !val.trim()} onClick={send}>
          <Icon name="send" size={12} />发送
        </button>
      </div>
    </div>
  );
};

// ─── delete-conversation flow ─────────────────────────────────────────────
async function deleteConversation(id) {
  if (!confirm(`删除会话 '${id}'?\n\n会清掉 .data/smith-sessions/${id}.jsonl;TEMPER 长期记忆保留。\n\n确定后会再问是否归档摘要到 TEMPER。`)) return false;
  const archive = confirm("先把一句话摘要归档到 TEMPER 吗?\n\n确定 = 归档后再删 · 取消 = 直接删");
  try {
    const url = "/conversations/" + encodeURIComponent(id) + (archive ? "?archive=true" : "");
    await api(url, { method: "DELETE" });
    return true;
  } catch (e) {
    alert("删除失败: " + e.message);
    return false;
  }
}

// ─── expose to the page-layout script that loads after us ──────────────────
Object.assign(window, {
  // primitives
  Icon, StatusDot, Avatar, Markdown,
  // chat building blocks
  ToolChip, ConfirmCard, Composer, ConvPicker,
  // state hooks
  useChatStream, useConversations, useHealth,
  // helpers
  api, authHeaders, promptForSecret, deleteConversation, renderMarkdown,
});
