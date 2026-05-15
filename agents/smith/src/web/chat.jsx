// chat.jsx — single-pane conversation view. The default `/` route.
// No brief strip, no MCP rail — just a focused chat surface that wires
// every backend Smith exposes today (SSE chat, approval gate, conversation
// index, health). For the dashboard with brief cards, see /briefs.

const ChatApp = () => {
  const chat = useChatStream();
  const { list, refresh } = useConversations(chat.convId);
  const { meta, error: healthErr } = useHealth();

  const logRef = React.useRef(null);
  React.useEffect(() => {
    // Auto-scroll on new turn / streamed token.
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [chat.turns]);

  const onDeleteConv = async () => {
    const ok = await deleteConversation(chat.convId);
    if (ok) { chat.reset(); refresh(); }
  };

  return (
    <div className="smith-app">
      {/* top bar */}
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 18px', borderBottom: '1px solid var(--line)', background: 'var(--panel)' }}>
        <Avatar kind="smith" size="lg" />
        <div>
          <div className="row" style={{ gap: 6 }}>
            <strong style={{ fontSize: 14 }}>Smith</strong>
            <span className="muted mono" style={{ fontSize: 10.5 }}>· chat</span>
          </div>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>
          {meta
            ? <><StatusDot status={meta.status === 'ok' ? 'ok' : 'warn'} />{' '}{meta.temper_user || '?'} · {meta.llm_provider}/{meta.llm_model}{meta.status === 'ok' ? '' : ' · ' + meta.status}</>
            : healthErr ? '(/healthz unreachable)' : '…'}
        </span>
        <span style={{ flex: 1 }} />
        <a href="/briefs" className="btn sm subtle" title="Open the dashboard view"><Icon name="side" size={12} /> Briefs</a>
        <a href="/plugins" className="btn sm subtle" title="Manage MCP plugins"><Icon name="cog" size={12} /> Plugins</a>
        <a href="/settings" className="btn sm subtle" title="Smith settings"><Icon name="cog" size={12} /> Settings</a>
        <ConvPicker
          activeId={chat.convId}
          conversations={list}
          onSwitch={chat.switchTo}
          onReset={() => { chat.reset(); refresh(); }}
          onDelete={onDeleteConv}
        />
      </header>

      {/* thread */}
      <main ref={logRef} className="scrl" style={{ flex: 1, padding: '18px 0' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', padding: '0 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {chat.turns.length === 0 && <EmptyState />}
          {chat.turns.map((t, i) => {
            if (t.kind === 'user') return <UserBubble key={i} text={t.text} />;
            return (
              <SmithCard key={i} turn={t}
                onApprove={() => chat.approve(t.pending)}
                onDeny={() => chat.deny(t.pending)}
              />
            );
          })}
        </div>
      </main>

      {/* composer */}
      <footer style={{ padding: '12px 22px 16px', background: 'var(--panel)', borderTop: '1px solid var(--line)' }}>
        <div style={{ maxWidth: 780, margin: '0 auto' }}>
          <Composer onSend={chat.send} busy={chat.busy} placeholder="消息 Smith — Enter 发送" />
        </div>
      </footer>
    </div>
  );
};

const EmptyState = () => (
  <div style={{ padding: '40px 12px', textAlign: 'center', color: 'var(--ink-3)' }}>
    <Avatar kind="smith" size="lg" />
    <div style={{ marginTop: 12, fontSize: 13 }}>新会话。Smith 会记住跨会话的事实(写入 TEMPER),destructive 工具调用前会先问你。</div>
  </div>
);

const UserBubble = ({ text }) => (
  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
    <div className="msg-user">{text}</div>
  </div>
);

const SmithCard = ({ turn, onApprove, onDeny }) => {
  // turn: { kind:'smith', text, thinking, toolCalls:[], pending, pendingState, error, done }
  const hasThinking = !!turn.thinking;
  const inFlight = !turn.done && !turn.error;
  return (
    <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px', boxShadow: 'var(--shadow-sm)' }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <Avatar kind="smith" />
        <strong style={{ fontSize: 12.5 }}>Smith</strong>
        {inFlight && <span className="muted mono" style={{ fontSize: 10.5 }}>· streaming…</span>}
      </div>
      {hasThinking && (
        <details className="thinking" style={{ marginBottom: 10 }} open={!turn.done}>
          <summary>thinking</summary>
          <div className="think-body">{turn.thinking}</div>
        </details>
      )}
      {turn.toolCalls.length > 0 && (
        <div style={{ marginBottom: turn.text ? 8 : 0 }}>
          {turn.toolCalls.map((c) => <ToolChip key={c.toolCallId} call={c} />)}
        </div>
      )}
      {turn.text && <Markdown source={turn.text} />}
      {inFlight && !turn.text && !turn.toolCalls.length && (
        <span className="cursor" />
      )}
      {turn.pending && (
        <ConfirmCard
          pending={turn.pending}
          state={turn.pendingState || 'pending'}
          onApprove={onApprove}
          onDeny={onDeny}
        />
      )}
      {turn.error && (
        <div style={{ marginTop: 10, padding: '8px 10px', background: 'var(--danger-soft)', color: 'var(--danger)', border: '1px solid var(--danger)', borderRadius: 6, fontSize: 12 }}>
          <Icon name="x" size={12} /> {turn.error}
        </div>
      )}
    </div>
  );
};

window.ChatApp = ChatApp;
