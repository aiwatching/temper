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
      // Explicit Accept so the server can content-negotiate. Some
      // routes (notably /plugins) double as HTML pages — without
      // this, fetch's default `*/*` would let the HTML page handler
      // win and we'd parse <!doctype …> as JSON.
      "Accept": "application/json",
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

// ─── nav rail + app shell ────────────────────────────────────────────────
//
// 56px dark rail on the left of every "logged-in" screen. Replicates
// docs/design/src/app-shell.jsx but each screen still ships as its own
// HTML page — clicking a rail icon is a real navigation, not a
// client-side route swap. Trade-off: every nav costs a fresh React
// bundle parse (Babel-standalone is the big one), but the vendor JS is
// cached by the browser after first load so it's fast in practice and
// each page can stay focused.
//
// Active state: by `location.pathname` startsWith the screen prefix —
// /chat matches /chat#conv=..., /tasks matches /tasks/<anything>.
//
// Tasks badge: pulls real count of (pending + waiting) tasks from
// /tasks on mount. Cheap (one fetch), refreshes every 30s so stale
// numbers don't sit forever in the rail.
//
// Keyboard nav: G then D/T/M/S/B switches screens. Skipped when an
// input/textarea is focused so users can type "g" without warping.
const NAV_ITEMS = [
  { id: 'chat',     icon: 'chat',  label: 'Chat',      href: '/chat',     key: 'd' },
  { id: 'briefs',   icon: 'side',  label: 'Briefs',    href: '/briefs',   key: 'b' },
  { id: 'tasks',    icon: 'check', label: '任务',       href: '/tasks',    key: 't', badge: 'tasks' },
  { id: 'plugins',  icon: 'flash', label: 'MCP',        href: '/plugins',  key: 'm' },
  { id: 'settings', icon: 'cog',   label: '设置',        href: '/settings', key: 's' },
];

function currentScreenFromPath() {
  const p = location.pathname;
  if (p.startsWith('/chat')) return 'chat';
  if (p.startsWith('/briefs')) return 'briefs';
  if (p.startsWith('/tasks')) return 'tasks';
  if (p.startsWith('/plugins')) return 'plugins';
  if (p.startsWith('/settings')) return 'settings';
  return null;
}

function useNavBadges() {
  const [badges, setBadges] = useState({});
  useEffect(() => {
    let stopped = false;
    const load = async () => {
      try {
        const data = await api('/tasks');
        if (stopped) return;
        const list = data.tasks || [];
        const tasks = list.filter(t => t.status === 'pending' || t.status === 'waiting').length;
        setBadges({ tasks: tasks > 0 ? tasks : null });
      } catch (_) {
        if (!stopped) setBadges({});
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => { stopped = true; clearInterval(t); };
  }, []);
  return badges;
}

const NavRail = ({ current }) => {
  const screen = current ?? currentScreenFromPath();
  const badges = useNavBadges();

  // Keyboard: g then <key>. Match the design's behaviour.
  useEffect(() => {
    let last = 0, lastKey = '';
    const h = (e) => {
      const tag = e.target?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.target?.isContentEditable) return;
      const now = Date.now();
      if (lastKey === 'g' && now - last < 800) {
        const hit = NAV_ITEMS.find(i => i.key === e.key.toLowerCase());
        if (hit) {
          e.preventDefault();
          if (location.pathname !== hit.href) location.href = hit.href;
        }
        lastKey = '';
        return;
      }
      lastKey = e.key.toLowerCase();
      last = now;
    };
    document.addEventListener('keydown', h);
    return () => document.removeEventListener('keydown', h);
  }, []);

  return (
    <nav style={{
      width: 56, flex: '0 0 56px',
      backgroundImage: 'linear-gradient(180deg, #1A1A14, #14140F)',
      color: '#C3BEB1',
      display: 'flex', flexDirection: 'column',
      padding: '10px 0',
      borderRight: '1px solid #0A0A07',
    }}>
      <div style={{ padding: '4px 0 10px', display: 'flex', justifyContent: 'center' }}>
        <a href="/chat" style={{
          width: 32, height: 32, borderRadius: 8,
          background: 'var(--accent)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: 'white', textDecoration: 'none',
        }} title="Smith">
          <Icon name="spark" size={16} />
        </a>
      </div>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, marginTop: 6 }}>
        {NAV_ITEMS.slice(0, -1).map(item => (
          <NavButton key={item.id} item={item} active={screen === item.id} badge={item.badge ? badges[item.badge] : null} />
        ))}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, paddingBottom: 4 }}>
        <NavButton item={NAV_ITEMS[NAV_ITEMS.length - 1]} active={screen === 'settings'} />
      </div>
    </nav>
  );
};

const NavButton = ({ item, active, badge }) => (
  <a
    href={item.href}
    title={`${item.label}  (g ${item.key})`}
    style={{
      width: 40, height: 40, borderRadius: 8,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: active ? 'rgba(255,255,255,0.10)' : 'transparent',
      color: active ? '#FAFAF7' : '#A29F95',
      transition: 'all 0.15s',
      position: 'relative',
      textDecoration: 'none',
    }}
  >
    <Icon name={item.icon} size={17} />
    {badge != null && (
      <span style={{
        position: 'absolute', top: 4, right: 4,
        minWidth: 14, height: 14, padding: '0 4px',
        borderRadius: 7, background: 'var(--danger)', color: 'white',
        fontSize: 9, fontWeight: 600, fontFamily: 'var(--mono)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        lineHeight: 1,
      }}>{badge}</span>
    )}
    {active && (
      <span style={{
        position: 'absolute', left: -10, top: 8, bottom: 8, width: 2,
        background: '#FAFAF7', borderRadius: 1,
      }} />
    )}
  </a>
);

/** Wrap a page in the nav rail. Pages call this in their root render. */
const AppShell = ({ current, children }) => (
  <div className="smith-app" style={{ display: 'flex', height: '100%', flexDirection: 'row', minHeight: 0 }}>
    <NavRail current={current} />
    <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {children}
    </div>
  </div>
);

window.NavRail = NavRail;
window.AppShell = AppShell;

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
const MAIN_CONV_ID = "main";
function newConvId() {
  return "ui-" + Math.random().toString(36).slice(2, 10);
}

function _initialConvId() {
  // 1. URL hash wins (deep-links from the /tasks page, branch redirects)
  const m = (location.hash || "").match(/(?:^#|&)conv=([^&]+)/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    sessionStorage.setItem("smith.convId", id);
    // Scrub the hash so a refresh doesn't lock us into a deleted conv.
    history.replaceState(null, "", location.pathname + location.search);
    return id;
  }
  // 2. Whatever we used last in this browser session
  const saved = sessionStorage.getItem("smith.convId");
  if (saved) return saved;
  // 3. Fresh visit → land on the persistent home conv. Not a "ui-..."
  //    minted on the fly anymore — the user explicitly clicks "+ new"
  //    if they want a separate branch.
  sessionStorage.setItem("smith.convId", MAIN_CONV_ID);
  return MAIN_CONV_ID;
}

function useChatStream() {
  const [convId, setConvId] = useState(_initialConvId);
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
    // "+ New" mints a fresh branch (not the main home). User wanting
    // a clean main conv goes through the picker's "Clear" instead.
    const id = newConvId();
    setConvId(id);
    sessionStorage.setItem("smith.convId", id);
    setTurns([]);
  }, []);

  /** Drop the in-memory transcript without changing convId — used by
   *  the picker's "Clear" affordance after the server's /clear succeeds. */
  const clearLocal = useCallback(() => setTurns([]), []);

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
            if (line.startsWith("event:")) {
              evt = line.slice(6).trim();
            } else if (line.startsWith("data:")) {
              // Per the SSE spec (WHATWG): strip ONE leading space
              // after `data:` (the optional cosmetic gap), and join
              // multiple data: lines in the same event with a literal
              // newline. The old `.trim()` here stripped ALL leading
              // and trailing whitespace AND dropped the newline-join,
              // which mangled markdown — list items concatenated
              // into one line, indented code blocks lost their
              // indent. Reload fixed it because JSONL replay went
              // through Markdown with the raw text intact.
              const piece = line.slice(5).replace(/^ /, "");
              data = data ? data + "\n" + piece : piece;
            }
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

  return { convId, switchTo, reset, clearLocal, turns, send, approve, deny, busy, loadingHistory };
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
// Main conv lives at the top with a ⭐ — it's the persistent home, can
// only be cleared (not deleted). Other convs (branches + ad-hoc new
// chats) follow, most-recently-used first. Per-row menu surfaces
// the right destructive action (clear vs delete) based on isMain.
const ConvPicker = ({ activeId, conversations, onSwitch, onAfterChange }) => {
  const [open, setOpen] = useState(false);
  const sorted = [...conversations].sort((a, b) => {
    if (a.id === MAIN_CONV_ID) return -1;
    if (b.id === MAIN_CONV_ID) return 1;
    return b.lastUsedAt.localeCompare(a.lastUsedAt);
  });
  const active = conversations.find((c) => c.id === activeId);
  const label = active?.title || activeId;

  return (
    <div style={{ position: 'relative' }}>
      <button className="btn sm subtle" onClick={() => setOpen(!open)} title="切换会话">
        {activeId === MAIN_CONV_ID && <span style={{ color: 'var(--accent)' }}>⭐</span>}
        <span style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        <Icon name="chevronDown" size={10} />
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: '100%', right: 0, marginTop: 4,
          background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8,
          boxShadow: 'var(--shadow-md)', minWidth: 320, maxWidth: 480,
          maxHeight: '70vh', overflow: 'auto', zIndex: 50,
        }}>
          <div style={{ padding: 8, borderBottom: '1px solid var(--line)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span className="muted" style={{ fontSize: 11, padding: '0 4px' }}>{sorted.length} 个会话</span>
            <button className="btn xs" onClick={async () => {
              setOpen(false);
              const id = "ui-" + Math.random().toString(36).slice(2, 10);
              onSwitch(id);
            }}><Icon name="plus" size={10} /> 新建</button>
          </div>
          {sorted.map((c) => (
            <ConvRow key={c.id} entry={c} active={c.id === activeId}
              onSwitch={() => { setOpen(false); onSwitch(c.id); }}
              onAfterChange={() => { onAfterChange && onAfterChange(); }}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const ConvRow = ({ entry, active, onSwitch, onAfterChange }) => {
  const [busy, setBusy] = useState(false);
  const isMain = entry.id === MAIN_CONV_ID;
  const clear = async () => {
    const archived = isMain
      ? confirm(`清空 main 会话?\n\n会归档摘要到 TEMPER,删 .data/smith-sessions/main.jsonl,保留会话槽位。\n\n确定?`)
      : confirm(`清空 '${entry.title}'?\n\n不会归档(已经是 branch)。删 jsonl + 重置消息数。`);
    if (!archived) return;
    setBusy(true);
    try {
      const qp = isMain ? "?archive=true" : "?archive=false";
      await api(`/conversations/${encodeURIComponent(entry.id)}/clear${qp}`, { method: "POST", body: "{}" });
      onAfterChange && onAfterChange();
    } catch (e) { alert("清空失败: " + e.message); }
    setBusy(false);
  };
  const del = async () => {
    if (isMain) return alert("main 不能删,只能清空");
    if (!confirm(`删除会话 '${entry.title}'?\n\n会清掉 jsonl + 索引条目。TEMPER 长期记忆保留。`)) return;
    const archive = confirm("先归档一句话摘要到 TEMPER 吗?\n\n确定 = 归档后删 · 取消 = 直接删");
    setBusy(true);
    try {
      await api(`/conversations/${encodeURIComponent(entry.id)}${archive ? "?archive=true" : ""}`, { method: "DELETE" });
      onAfterChange && onAfterChange();
    } catch (e) { alert("删除失败: " + e.message); }
    setBusy(false);
  };
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr auto', gap: 6, padding: '8px 12px',
      borderTop: '1px solid var(--line)',
      background: active ? 'var(--panel-2)' : 'transparent',
      opacity: busy ? 0.5 : 1,
    }}>
      <button onClick={onSwitch} style={{ textAlign: 'left', background: 'transparent', border: 0, cursor: 'pointer', minWidth: 0 }}>
        <div className="row" style={{ gap: 6 }}>
          {isMain && <span style={{ color: 'var(--accent)' }}>⭐</span>}
          {entry.forkedFrom && <span title="branched" style={{ color: 'var(--purple)' }}>↳</span>}
          <span style={{ fontSize: 12.5, fontWeight: active ? 600 : 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{entry.title}</span>
        </div>
        <div className="muted mono" style={{ fontSize: 10.5, marginTop: 2 }}>
          {entry.lastUsedAt?.replace("T", " ").slice(0, 16)} · {entry.messageCount}t
          {entry.forkedFrom && <> · from {entry.forkedFrom.conv}@{entry.forkedFrom.anchor_turn} ({entry.forkedFrom.range})</>}
        </div>
      </button>
      <div className="row" style={{ gap: 2 }}>
        <button className="btn xs subtle" disabled={busy} onClick={clear} title="清空(归档 + 删 jsonl + 保留槽位)">
          <Icon name="refresh" size={10} />
        </button>
        {!isMain && (
          <button className="btn xs subtle" disabled={busy} onClick={del} title="删除整个会话">
            <Icon name="trash" size={10} />
          </button>
        )}
      </div>
    </div>
  );
};

// ─── fork modal ────────────────────────────────────────────────────────────
// Opens from a smith-reply hover button. Lets the user pick range +
// optional branch name, then POSTs /conversations/fork and redirects
// (via onSwitchTo) to the new branch.
const ForkModal = ({ sourceConv, anchorIndex, anchorPreview, onClose, onSwitchTo, onRefresh }) => {
  const [range, setRange] = useState('B');
  const [n, setN] = useState(2);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      const body = { source: sourceConv, anchor_turn: anchorIndex, range };
      if (range === 'C') body.n = Math.max(1, Number(n) || 1);
      if (name.trim()) body.name = name.trim();
      const entry = await api('/conversations/fork', {
        method: 'POST', body: JSON.stringify(body),
      });
      onRefresh && onRefresh();
      onSwitchTo(entry.id);
      onClose();
    } catch (e) {
      alert('fork 失败: ' + e.message);
    }
    setBusy(false);
  };
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(20,20,15,0.45)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
    }} onClick={onClose}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 10,
        padding: 20, width: 520, maxWidth: '90vw', boxShadow: 'var(--shadow-lg)',
      }}>
        <h3 style={{ marginTop: 0, marginBottom: 6, fontSize: 15 }}>Fork branch from {sourceConv}</h3>
        <div className="muted" style={{ fontSize: 12, marginBottom: 14 }}>
          新会话独立于 {sourceConv} — 不会反向影响,也不会自动同步后续更新。
        </div>
        {anchorPreview && (
          <div style={{ padding: 10, background: 'var(--panel-2)', borderRadius: 6, fontSize: 11.5, marginBottom: 14, maxHeight: 100, overflow: 'auto' }}>
            <div className="muted mono" style={{ fontSize: 10, marginBottom: 4 }}>引用的 reply (#{anchorIndex})</div>
            <div style={{ whiteSpace: 'pre-wrap' }}>{anchorPreview.slice(0, 300)}{anchorPreview.length > 300 ? '…' : ''}</div>
          </div>
        )}

        <div className="sec-label" style={{ marginBottom: 8 }}>注入范围</div>
        <div className="col" style={{ gap: 6, marginBottom: 14 }}>
          {[
            { id: 'A', t: 'A — 仅引用的回复', s: '最少;只在引用本身自洽时用' },
            { id: 'B', t: 'B — 引用 + 触发它的提问 (默认)', s: '1 round;cheap + 几乎总够' },
            { id: 'C', t: 'C — 引用前后各 N 轮', s: '上下文最丰富' },
            { id: 'E', t: 'E — 引用 + LLM 总结 (reserved)', s: '暂未实现,会 fallback 到 B' },
          ].map(opt => (
            <label key={opt.id} className="row" style={{ gap: 8, padding: 8, borderRadius: 6, background: range === opt.id ? 'var(--accent-soft)' : 'transparent', cursor: 'pointer', alignItems: 'flex-start' }}>
              <input type="radio" name="range" checked={range === opt.id} onChange={() => setRange(opt.id)} />
              <div>
                <div style={{ fontSize: 13, fontWeight: 500 }}>{opt.t}</div>
                <div className="muted" style={{ fontSize: 11 }}>{opt.s}</div>
              </div>
            </label>
          ))}
        </div>

        {range === 'C' && (
          <div className="row" style={{ marginBottom: 14 }}>
            <span className="muted" style={{ fontSize: 12, minWidth: 80 }}>N (rounds)</span>
            <input type="number" min={1} max={10} value={n} onChange={(e) => setN(e.target.value)}
                   style={{ width: 80, fontSize: 12 }} />
          </div>
        )}

        <div className="row" style={{ marginBottom: 14 }}>
          <span className="muted" style={{ fontSize: 12, minWidth: 80 }}>名称 (可选)</span>
          <input value={name} onChange={(e) => setName(e.target.value)}
                 placeholder={`Branch from ${sourceConv} @ turn ${anchorIndex}`}
                 style={{ flex: 1, fontSize: 12 }} />
        </div>

        <div className="row" style={{ gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn sm subtle" onClick={onClose}>取消</button>
          <button className="btn sm primary" disabled={busy} onClick={submit}>
            {busy ? '创建中…' : 'Fork'}
          </button>
        </div>
      </div>
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
  ToolChip, ConfirmCard, Composer, ConvPicker, ForkModal,
  // state hooks
  useChatStream, useConversations, useHealth,
  // helpers
  api, authHeaders, promptForSecret, deleteConversation, renderMarkdown,
  // constants
  MAIN_CONV_ID,
});
