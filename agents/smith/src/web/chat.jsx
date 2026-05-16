// chat.jsx — single-pane conversation view. The default `/` route.
// No brief strip, no MCP rail — just a focused chat surface that wires
// every backend Smith exposes today (SSE chat, approval gate, conversation
// index, health). For the dashboard with brief cards, see /briefs.

const ChatApp = () => {
  const chat = useChatStream();
  const { list, refresh } = useConversations(chat.convId);
  const { meta, error: healthErr } = useHealth();
  const [forkSeed, setForkSeed] = React.useState(null); // { anchorIndex, preview }

  const logRef = React.useRef(null);
  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [chat.turns]);

  const onFork = (i, preview) => setForkSeed({ anchorIndex: i, preview });

  const isMain = chat.convId === MAIN_CONV_ID;

  return (
    <AppShell current="chat">
      <div className="smith-app" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* top bar */}
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 18px', borderBottom: '1px solid var(--line)', background: 'var(--panel)' }}>
        <Avatar kind="smith" size="lg" />
        <div>
          <div className="row" style={{ gap: 6 }}>
            <strong style={{ fontSize: 14 }}>Smith</strong>
            <span className="muted mono" style={{ fontSize: 10.5 }}>
              · {isMain ? <span style={{ color: 'var(--accent)' }}>⭐ main</span> : chat.convId}
            </span>
          </div>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>
          {meta
            ? <><StatusDot status={meta.status === 'ok' ? 'ok' : 'warn'} />{' '}{meta.temper_user || '?'} · {meta.llm_provider}/{meta.llm_model}{meta.status === 'ok' ? '' : ' · ' + meta.status}</>
            : healthErr ? '(/healthz unreachable)' : '…'}
        </span>
        <span style={{ flex: 1 }} />
        <ConvPicker
          activeId={chat.convId}
          conversations={list}
          onSwitch={chat.switchTo}
          onAfterChange={() => {
            // After clear/delete, the entry's still here (clear) or
            // gone (delete on a branch); reload index + history.
            refresh();
            chat.switchTo(chat.convId);
          }}
        />
      </header>

      {/* thread */}
      <main ref={logRef} className="scrl" style={{ flex: 1, padding: '18px 0' }}>
        <div style={{ maxWidth: 780, margin: '0 auto', padding: '0 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          {chat.turns.length === 0 && <EmptyState />}
          {chat.turns.map((t, i) => {
            if (t.kind === 'user') return <UserBubble key={i} text={t.text} />;
            return (
              <SmithCard key={i} turn={t} turnIndex={i}
                onApprove={() => chat.approve(t.pending)}
                onDeny={() => chat.deny(t.pending)}
                onFork={() => onFork(i, t.text || '')}
              />
            );
          })}
        </div>
      </main>

      {forkSeed && (
        <ForkModal
          sourceConv={chat.convId}
          anchorIndex={forkSeed.anchorIndex}
          anchorPreview={forkSeed.preview}
          onClose={() => setForkSeed(null)}
          onSwitchTo={(id) => chat.switchTo(id)}
          onRefresh={refresh}
        />
      )}

      {/* composer */}
      <footer style={{ padding: '12px 22px 16px', background: 'var(--panel)', borderTop: '1px solid var(--line)' }}>
        <div style={{ maxWidth: 780, margin: '0 auto' }}>
          <Composer onSend={chat.send} busy={chat.busy} placeholder="消息 Smith — Enter 发送" />
        </div>
      </footer>
      </div>
    </AppShell>
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

const SmithCard = ({ turn, turnIndex, onApprove, onDeny, onFork }) => {
  // turn: { kind:'smith', text, thinking, toolCalls:[], pending, pendingState, error, done }
  const hasThinking = !!turn.thinking;
  const inFlight = !turn.done && !turn.error;
  const [hovering, setHovering] = React.useState(false);
  return (
    <div
      onMouseEnter={() => setHovering(true)} onMouseLeave={() => setHovering(false)}
      style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px', boxShadow: 'var(--shadow-sm)', position: 'relative' }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <Avatar kind="smith" />
        <strong style={{ fontSize: 12.5 }}>Smith</strong>
        {inFlight && <span className="muted mono" style={{ fontSize: 10.5 }}>· streaming…</span>}
        <span style={{ flex: 1 }} />
        {turn.done && !turn.error && hovering && onFork && (
          <button className="btn xs subtle" onClick={onFork} title={`Fork branch from this reply (turn #${turnIndex})`}>
            <Icon name="fork" size={11} /> Fork
          </button>
        )}
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
