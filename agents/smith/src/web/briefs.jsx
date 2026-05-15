// briefs.jsx — full workspace dashboard. Layout C from the design mockup.
//
// What's wired vs placeholder (2026-05-14):
//
//   ✓ wired: top bar status pill, conversation thread (SSE), tool chips,
//            approval card, conversation switcher, recent sessions list.
//
//   ◌ placeholder: brief cards (static BRIEFS[] — backend coming),
//                  MCP rail beyond name+status (no tool count / latency
//                  endpoint yet), loaded skills list, per-conversation
//                  TEMPER recall counters, FortiAuthenticator nudge.
//
// As we add the backend, we'll replace the BRIEFS[] / MOCK_MCP / MOCK_SKILLS
// constants below with real fetchers. The shape is intentionally close to
// what `.smith/briefs/<id>.md` will deserialize to.

// ─── static brief registry (placeholder until .smith/briefs/ loader lands) ──
const BRIEFS = [
  { id: 'triage',  icon: 'bug',   title: 'Mantis triage',     big: '—', sub: '后端未接入',          tint: 'warn',   cmd: '/triage',  group: '今日', source: 'mantis',     file: 'mantis-triage.md',  builtin: true },
  { id: 'cr',      icon: 'git',   title: '等我 review 的 MR', big: '—', sub: '后端未接入',          tint: 'danger', cmd: '/cr',      group: '今日', source: 'gitlab',     file: 'gitlab-cr.md',      builtin: true },
  { id: 'inbox',   icon: 'mail',  title: '收件箱',             big: '—', sub: '后端未接入',          tint: 'accent', cmd: '/inbox',   group: '今日', source: 'outlook',    file: 'outlook-inbox.md',  builtin: true },
  { id: 'standup', icon: 'flash', title: 'Standup 草稿',       big: '—', sub: '后端未接入',          tint: 'good',   cmd: '/standup', group: '今日', source: 'temper',     file: 'standup.md',        builtin: true },
  { id: 'specs',   icon: 'book',  title: 'PMDB spec',          big: '—', sub: '后端未接入',          tint: 'purple', cmd: '/spec',    group: '今日', source: 'pmdb',       file: 'pmdb-specs.md',     builtin: true },
  { id: 'pipes',   icon: 'flash', title: 'CI 红',              big: '—', sub: '后端未接入',          tint: 'danger', cmd: '/ci',      group: '关注', source: 'gitlab',     file: 'gitlab-ci.md',      builtin: true },
  { id: 'release', icon: 'clock', title: 'Release RC',         big: '—', sub: '后端未接入',          tint: 'accent', cmd: '/release', group: '关注', source: 'pmdb',       file: 'release-rc.md',     builtin: true },
  { id: 'oncall',  icon: 'phone', title: 'On-call',            big: '—', sub: '后端未接入',          tint: '',       cmd: '/oncall',  group: '关注', source: 'sharepoint', file: 'oncall.md',         builtin: true },
];

const SAMPLE_BRIEF_MD = `---
id: wad-saga
title: WAD crash saga
icon: eye
group: 关注
tint: purple
cmd: /saga              # 点击 = typed,复用 .smith/prompts/saga.md
source: temper          # 显示用 + 健康检查依赖
refresh: 10m
big:
  tool: temper__memory_search
  args: { query: 'saga:wad-ssl-crash', limit: 50 }
  extract: count_delta_since: 24h
  format: '↑'
sub:
  template: 'memory 中 {n} 条新更新'
---

# WAD crash saga

跟踪 wad_ssl.c:1142 这条 SSL deep-inspect 崩溃 saga。任何新的
\`memory_search\` 命中(tag: \`wad\` 或 \`saga:wad-ssl-crash\`)都触发更新。
`;

const BriefApp = () => {
  const chat = useChatStream();
  const { list: conversations, refresh: refreshConvs } = useConversations(chat.convId);
  const { meta, error: healthErr } = useHealth();
  const [briefGroup, setBriefGroup] = React.useState('全部');
  const [briefMode, setBriefMode] = React.useState('cards');
  const [activeBrief, setActiveBrief] = React.useState('triage');
  const [manageOpen, setManageOpen] = React.useState(false);

  const logRef = React.useRef(null);
  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [chat.turns]);

  const briefGroups = ['全部', ...Array.from(new Set(BRIEFS.map((b) => b.group)))];
  const briefs = briefGroup === '全部' ? BRIEFS : BRIEFS.filter((b) => b.group === briefGroup);

  const onPickBrief = (b) => {
    setActiveBrief(b.id);
    // For now, type the command into the chat. Once .smith/briefs has
    // real backing prompts, this can dispatch directly.
    chat.send(b.cmd);
  };

  const onDeleteConv = async () => {
    const ok = await deleteConversation(chat.convId);
    if (ok) { chat.reset(); refreshConvs(); }
  };

  return (
    <div className="smith-app" style={{ display: 'grid', gridTemplateRows: 'auto 1fr auto', gridTemplateColumns: '1fr 300px', position: 'relative' }}>
      {/* top bar */}
      <header style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 12, padding: '8px 18px', borderBottom: '1px solid var(--line)', background: 'var(--panel)' }}>
        <div className="row" style={{ gap: 9 }}>
          <Avatar kind="smith" size="lg" />
          <div>
            <div className="row" style={{ gap: 6 }}>
              <strong style={{ fontSize: 14 }}>Smith</strong>
              <span className="muted mono" style={{ fontSize: 10.5 }}>· workspace</span>
            </div>
          </div>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>
          {meta
            ? <>{meta.temper_user || '?'} · {meta.llm_provider}/{meta.llm_model}{meta.status === 'ok' ? '' : ' · ' + meta.status}</>
            : healthErr ? '(/healthz unreachable)' : '…'}
        </span>
        <span style={{ flex: 1 }} />
        <a href="/chat" className="btn sm subtle" title="Switch to focused chat"><Icon name="chat" size={12} /> Chat</a>
        <ConvPicker
          activeId={chat.convId}
          conversations={conversations}
          onSwitch={chat.switchTo}
          onReset={() => { chat.reset(); refreshConvs(); }}
          onDelete={onDeleteConv}
        />
      </header>

      {/* main column */}
      <main style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--line)', minWidth: 0 }}>
        {/* brief strip */}
        <div style={{ padding: '14px 18px 0', background: 'var(--panel-2)', borderBottom: '1px solid var(--line)' }}>
          <div className="spread" style={{ marginBottom: 10 }}>
            <div>
              <div className="row" style={{ gap: 8 }}>
                <span className="sec-label">今日简报</span>
                <span className="muted mono" style={{ fontSize: 10.5 }}>· {briefs.length} / {BRIEFS.length}</span>
                <span className="pill warn" style={{ padding: '0 6px', fontSize: 10 }}>UI-only · 后端未接</span>
              </div>
              <div className="muted" style={{ fontSize: 11.5, marginTop: 2 }}>未来:每张卡 = <code style={{ background: 'transparent', border: 0, padding: 0 }}>.smith/briefs/&lt;id&gt;.md</code> · 现在是静态占位</div>
            </div>
            <div className="row" style={{ gap: 4 }}>
              <div className="row" style={{ gap: 0, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 5, padding: 2 }}>
                {briefGroups.map((g) => (
                  <button key={g}
                    onClick={() => setBriefGroup(g)}
                    className="btn xs"
                    style={{
                      padding: '3px 8px',
                      background: briefGroup === g ? 'var(--ink)' : 'transparent',
                      color: briefGroup === g ? 'var(--paper)' : 'var(--ink-2)',
                      border: 0,
                    }}
                  >{g}</button>
                ))}
              </div>
              <div className="row" style={{ gap: 0, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 5, padding: 2 }}>
                <button onClick={() => setBriefMode('cards')} className="btn xs" style={{ padding: '3px 6px', background: briefMode === 'cards' ? 'var(--ink)' : 'transparent', color: briefMode === 'cards' ? 'var(--paper)' : 'var(--ink-3)', border: 0 }} title="卡片"><Icon name="side" size={11} /></button>
                <button onClick={() => setBriefMode('compact')} className="btn xs" style={{ padding: '3px 6px', background: briefMode === 'compact' ? 'var(--ink)' : 'transparent', color: briefMode === 'compact' ? 'var(--paper)' : 'var(--ink-3)', border: 0 }} title="紧凑列表"><Icon name="dots" size={11} /></button>
              </div>
              <button onClick={() => setManageOpen(true)} className="btn xs subtle"><Icon name="folder" size={11} /> 管理</button>
            </div>
          </div>

          {briefMode === 'cards' ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(168px, 1fr))', gap: 8, paddingBottom: 14 }}>
              {briefs.map((b) => (
                <button key={b.id} onClick={() => onPickBrief(b)} style={{
                  padding: '11px 12px',
                  borderRadius: 8,
                  background: 'var(--panel)',
                  border: '1px solid ' + (activeBrief === b.id ? 'var(--ink)' : 'var(--line)'),
                  textAlign: 'left',
                  boxShadow: activeBrief === b.id ? 'var(--shadow-md)' : 'var(--shadow-sm)',
                  minWidth: 0,
                }}>
                  <div className="row" style={{ marginBottom: 4, gap: 6 }}>
                    <span className={`pill ${b.tint}`} style={{ padding: '2px 6px' }}><Icon name={b.icon} size={11} /></span>
                    <span style={{ fontSize: 11.5, fontWeight: 500, color: 'var(--ink-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>{b.title}</span>
                  </div>
                  <div style={{ fontSize: 24, fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1, marginTop: 6 }}>{b.big}</div>
                  <div className="muted" style={{ fontSize: 11, marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.sub}</div>
                  <div className="row" style={{ marginTop: 7, gap: 6 }}>
                    <code style={{ fontSize: 10.5, background: 'transparent', border: 0, padding: 0, color: 'var(--ink-3)' }}>{b.cmd}</code>
                    <span style={{ flex: 1 }} />
                    <span className="muted mono" style={{ fontSize: 9.5 }}>{b.source}</span>
                  </div>
                  <div style={{ marginTop: 6, paddingTop: 6, borderTop: '1px dashed var(--line)', display: 'flex', alignItems: 'center', gap: 5 }}>
                    <Icon name="file" size={9.5} style={{ color: 'var(--ink-4)' }} />
                    <code style={{ fontSize: 10, background: 'transparent', border: 0, padding: 0, color: 'var(--ink-4)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>.smith/briefs/{b.file}</code>
                  </div>
                </button>
              ))}
              <button style={{
                padding: '11px 12px',
                borderRadius: 8,
                background: 'transparent',
                border: '1px dashed var(--line-strong)',
                color: 'var(--ink-3)',
                display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                gap: 4, minHeight: 110,
              }} onClick={() => setManageOpen(true)}>
                <Icon name="plus" size={14} />
                <span style={{ fontSize: 11.5 }}>新建简报</span>
                <span style={{ fontSize: 10.5, color: 'var(--ink-4)' }}>= 一份 markdown</span>
              </button>
            </div>
          ) : (
            <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, marginBottom: 14, overflow: 'hidden' }}>
              {briefs.map((b, i) => (
                <button key={b.id} onClick={() => onPickBrief(b)} style={{
                  display: 'grid', gridTemplateColumns: 'auto auto 1fr auto auto auto', gap: 12, alignItems: 'center',
                  width: '100%', textAlign: 'left',
                  padding: '8px 12px',
                  borderTop: i ? '1px solid var(--line)' : 'none',
                  background: activeBrief === b.id ? 'var(--panel-2)' : 'transparent',
                }}>
                  <span className={`pill ${b.tint}`} style={{ padding: '2px 6px' }}><Icon name={b.icon} size={11} /></span>
                  <span style={{ fontSize: 12.5, fontWeight: 500, minWidth: 140 }}>{b.title}</span>
                  <span className="muted" style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.sub}</span>
                  <span style={{ fontFamily: 'var(--mono)', fontWeight: 600, fontSize: 13, minWidth: 36, textAlign: 'right' }}>{b.big}</span>
                  <code style={{ fontSize: 10.5, background: 'transparent', border: 0, padding: 0, color: 'var(--ink-3)', minWidth: 70 }}>{b.cmd}</code>
                  <span className="muted mono" style={{ fontSize: 10, minWidth: 60, textAlign: 'right' }}>{b.source}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* thread */}
        <div ref={logRef} className="scrl" style={{ flex: 1, padding: '18px 0 22px' }}>
          <div style={{ maxWidth: 820, margin: '0 auto', padding: '0 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
            {chat.turns.length === 0 && <EmptyThread />}
            {chat.turns.map((t, i) =>
              t.kind === 'user'
                ? <DUserBubble key={i} text={t.text} />
                : <DSmithCard
                    key={i}
                    turn={t}
                    onApprove={() => chat.approve(t.pending)}
                    onDeny={() => chat.deny(t.pending)}
                  />
            )}
          </div>
        </div>

        {/* composer */}
        <div style={{ padding: '12px 22px 16px', background: 'var(--panel)', borderTop: '1px solid var(--line)' }}>
          <div style={{ maxWidth: 820, margin: '0 auto' }}>
            <Composer
              onSend={chat.send}
              busy={chat.busy}
              placeholder="向 Smith 提问 — Enter 发送 · Shift+Enter 换行"
            />
          </div>
        </div>
      </main>

      {/* right rail */}
      <aside className="scrl" style={{ background: 'var(--panel-2)', padding: '14px 14px 18px' }}>
        <div className="sec-label" style={{ marginBottom: 8 }}>服务 · 来自 /healthz</div>
        <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, marginBottom: 14, padding: '10px 12px', fontSize: 11.5 }}>
          {meta ? (
            <>
              <div className="spread" style={{ marginBottom: 4 }}><span className="muted">Status</span><span className="row" style={{ gap: 5 }}><StatusDot status={meta.status === 'ok' ? 'ok' : 'warn'} /><span className="mono">{meta.status}</span></span></div>
              <div className="spread" style={{ marginBottom: 4 }}><span className="muted">TEMPER user</span><span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160 }}>{meta.temper_user || '?'}</span></div>
              <div className="spread" style={{ marginBottom: 4 }}><span className="muted">LLM</span><span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 160 }}>{meta.llm_provider}/{meta.llm_model}</span></div>
              <div className="spread"><span className="muted">Sessions</span><span className="mono">{meta.active_sessions ?? '—'}</span></div>
              {meta.temper_error && <div className="muted" style={{ marginTop: 6, color: 'var(--danger)', fontSize: 11 }}>{meta.temper_error}</div>}
            </>
          ) : <span className="muted">…</span>}
        </div>

        <div className="sec-label" style={{ marginBottom: 8 }}>MCP · 占位</div>
        <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, marginBottom: 14, padding: '10px 12px', fontSize: 11.5 }}>
          <div className="muted">/healthz 还没有 per-MCP 状态字段。等加上之后这里会列出每个 server 的 tool 数 + 延迟 + 健康状态。</div>
        </div>

        <div className="sec-label" style={{ marginBottom: 8 }}>Skills · 占位</div>
        <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, marginBottom: 14, padding: '10px 12px', fontSize: 11.5 }}>
          <div className="muted">已加载的 skill 列表(从 <code style={{ background: 'transparent', border: 0, padding: 0 }}>.smith/skills/</code>)接口未上,先空着。</div>
        </div>

        <div className="sec-label" style={{ marginBottom: 8 }}>最近会话</div>
        {conversations.slice(0, 6).map((s) => (
          <button key={s.id} onClick={() => chat.switchTo(s.id)} style={{
            display: 'block', textAlign: 'left', padding: '6px 8px',
            borderRadius: 5, width: '100%', marginBottom: 2,
            background: s.id === chat.convId ? 'var(--panel)' : 'transparent',
            border: s.id === chat.convId ? '1px solid var(--line)' : '1px solid transparent',
          }}>
            <div style={{ fontSize: 11.5, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.title}</div>
            <div className="muted" style={{ fontSize: 10.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.lastUsedAt.replace('T',' ').slice(0,16)} · {s.messageCount}t</div>
          </button>
        ))}
        {conversations.length === 0 && <div className="muted" style={{ fontSize: 11 }}>(还没有保存的会话)</div>}
      </aside>

      {/* footer */}
      <footer style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: 14, padding: '6px 18px', borderTop: '1px solid var(--line)', background: 'var(--panel)', fontSize: 11, color: 'var(--ink-3)', fontFamily: 'var(--mono)', overflow: 'hidden' }}>
        <span className="row" style={{ gap: 5 }}>
          <StatusDot status={meta?.status === 'ok' ? 'ok' : meta ? 'warn' : 'bad'} /> agent {meta?.status ?? '—'}
        </span>
        <span>·</span>
        <span>conv:{chat.convId}</span>
        <span style={{ flex: 1 }} />
        <span>smith · pi-coding-agent</span>
      </footer>

      {/* manage briefs sheet */}
      {manageOpen && (
        <ManageSheet onClose={() => setManageOpen(false)} />
      )}
    </div>
  );
};

// ─── dashboard-flavored bubbles ───────────────────────────────────────────
const EmptyThread = () => (
  <div style={{ padding: '20px 12px', textAlign: 'center', color: 'var(--ink-3)', fontSize: 12.5 }}>
    点上面的简报卡片触发对应命令,或直接在下方输入框开始对话。
  </div>
);

const DUserBubble = ({ text }) => (
  <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
    <div className="msg-user">{text}</div>
  </div>
);

const DSmithCard = ({ turn, onApprove, onDeny }) => {
  const inFlight = !turn.done && !turn.error;
  return (
    <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 10, padding: '14px 16px', boxShadow: 'var(--shadow-sm)' }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <Avatar kind="smith" />
        <strong style={{ fontSize: 12.5 }}>Smith</strong>
        {inFlight && <span className="muted mono" style={{ fontSize: 10.5 }}>· streaming…</span>}
      </div>
      {turn.thinking && (
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
      {inFlight && !turn.text && !turn.toolCalls.length && <span className="cursor" />}
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

// ─── manage briefs sheet ─────────────────────────────────────────────────
const ManageSheet = ({ onClose }) => (
  <div onClick={onClose}
    style={{
      position: 'absolute', inset: 0, zIndex: 50,
      background: 'rgba(20, 20, 15, 0.32)',
      display: 'flex', alignItems: 'stretch', justifyContent: 'flex-end',
      backdropFilter: 'blur(2px)',
    }}>
    <div onClick={(e) => e.stopPropagation()}
      style={{
        width: 680, background: 'var(--panel)',
        boxShadow: '-30px 0 80px rgba(20,20,15,0.18)',
        display: 'flex', flexDirection: 'column',
      }}>
      <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 10 }}>
        <Icon name="folder" size={15} style={{ color: 'var(--ink-3)' }} />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>简报 = Skill 系列的一员</div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: 1 }}>
            未来:存放在 <code style={{ background: 'transparent', border: 0, padding: 0 }}>.smith/briefs/</code> · 跟 skills、prompts 用同一套 ResourceLoader · 分发包<code style={{ background: 'transparent', border: 0, padding: 0 }}>@fortinet/smith-briefs</code>
          </div>
        </div>
        <button className="btn icon subtle" onClick={onClose}><Icon name="x" size={14} /></button>
      </div>

      <div className="scrl" style={{ flex: 1, padding: '16px 18px 18px' }}>
        <div className="spread" style={{ marginBottom: 8 }}>
          <span className="sec-label">占位 · {BRIEFS.length}</span>
        </div>

        <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden', marginBottom: 18 }}>
          {BRIEFS.map((b, i) => (
            <div key={b.id} style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto auto auto', gap: 12, alignItems: 'center', padding: '9px 12px', borderTop: i ? '1px solid var(--line)' : 'none' }}>
              <span className={`pill ${b.tint}`} style={{ padding: '2px 6px' }}><Icon name={b.icon} size={11} /></span>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontWeight: 500, fontSize: 12.5 }}>{b.title}</div>
                <code style={{ fontSize: 10.5, background: 'transparent', border: 0, padding: 0, color: 'var(--ink-3)' }}>.smith/briefs/{b.file}</code>
              </div>
              <span className="pill" style={{ fontSize: 10 }}>{b.group}</span>
              <span className="muted mono" style={{ fontSize: 10.5 }}>{b.source}</span>
              <span className="pill xs" style={{ fontSize: 9.5 }}>builtin</span>
            </div>
          ))}
        </div>

        <div className="sec-label" style={{ marginBottom: 8 }}>简报 markdown · 字段速览(规划中)</div>
        <pre style={{
          margin: 0, padding: 14,
          background: '#1F1E1A', color: '#E6E3DC',
          borderRadius: 8, fontFamily: 'var(--mono)', fontSize: 11.5, lineHeight: 1.55,
          overflow: 'auto', maxHeight: 320,
        }}>{SAMPLE_BRIEF_MD}</pre>
      </div>
    </div>
  </div>
);

window.BriefApp = BriefApp;
