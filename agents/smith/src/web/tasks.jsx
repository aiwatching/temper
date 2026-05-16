// tasks.jsx — unified Tasks screen.
//
// 1:1 from docs/design/src/screen-tasks.jsx, swapping the design mock
// for real /tasks endpoint data. Aggregator (server-side) merges
// pending/active/scheduled/done into one list. Action buttons delegate
// to existing endpoints (/approve, /deny, /jobs/:id/run, /jobs/:id,
// /conversations/:id).
//
// Status tabs + grouped list. Click a row → right-side detail panel.
// Search filters by title / sub / conv. Refresh button is manual;
// 30s background poll auto-refreshes since "pending" / "scheduled"
// state changes outside this page (engine fires jobs in the
// background; new approvals come in from /chat).

const TASK_STATUSES = [
  { id: 'pending',  label: '待批准',  tint: 'warn',   icon: 'lock',  desc: '工具调用被 beforeToolCall 拦下,需你点同意/拒绝' },
  { id: 'active',   label: '进行中',  tint: 'accent', icon: 'chat',  desc: '最近 1 小时有活动的会话' },
  { id: 'waiting',  label: '等待中',  tint: '',       icon: 'clock', desc: '等外部:推送、CI、人回复(暂未启用)' },
  { id: 'scheduled',label: '计划',    tint: 'purple', icon: 'flash', desc: '周期 / 一次性触发,未来某时刻执行' },
  { id: 'done',     label: '已完成',  tint: 'good',   icon: 'check', desc: '1 小时前最后活动的会话' },
];

const TasksApp = () => {
  const [tasks, setTasks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    try {
      setError(null);
      const data = await api('/tasks');
      setTasks(data.tasks || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // 30s background refresh — engine fires jobs out of our sight,
    // new approvals come in from /chat. Mostly cheap (one fetch).
    const t = setInterval(load, 30_000);
    const c = setInterval(() => setNow(Date.now()), 60_000); // age recompute
    return () => { clearInterval(t); clearInterval(c); };
  }, [load]);

  const counts = useMemo(() => {
    const out = { all: tasks.length };
    for (const s of TASK_STATUSES) out[s.id] = 0;
    for (const t of tasks) out[t.status] = (out[t.status] || 0) + 1;
    return out;
  }, [tasks]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return tasks
      .filter(t => filter === 'all' || t.status === filter)
      .filter(t => !needle ||
        t.title.toLowerCase().includes(needle) ||
        (t.sub || '').toLowerCase().includes(needle) ||
        (t.conv || '').toLowerCase().includes(needle));
  }, [tasks, filter, search]);

  const grouped = useMemo(() =>
    TASK_STATUSES
      .map(s => ({ s, tasks: visible.filter(t => t.status === s.id) }))
      .filter(g => g.tasks.length),
    [visible]);

  const selectedTask = selected ? tasks.find(t => t.id === selected) : null;
  void now; // referenced just to re-render on the minute tick

  return (
    <AppShell current="tasks">
      <div className="col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* header */}
      <div style={{ padding: '14px 22px 0', borderBottom: '1px solid var(--line)', background: 'var(--panel)', flex: '0 0 auto' }}>
        <div className="spread" style={{ marginBottom: 12 }}>
          <div>
            <div className="row" style={{ gap: 9 }}>
              <h1 style={{ fontSize: 18, fontWeight: 600, margin: 0, letterSpacing: '-0.01em' }}>任务</h1>
              <span className="pill ghost"><Icon name="clock" size={10} /> 实时</span>
              {error && <span className="pill danger" title={error}><Icon name="x" size={10} /> 加载失败</span>}
            </div>
            <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>
              聚合 approvalStore / conversation_index / jobs · 30 秒自动刷新
            </div>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <div className="composer" style={{ padding: '5px 10px', display: 'flex', alignItems: 'center', gap: 7, boxShadow: 'none', minWidth: 220 }}>
              <Icon name="search" size={12} style={{ color: 'var(--ink-3)' }} />
              <input
                placeholder="搜索任务、内容、conv id…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{ flex: 1, fontSize: 12, border: 0, outline: 'none', background: 'transparent', color: 'inherit' }}
              />
              {search && <button onClick={() => setSearch('')} className="btn icon subtle" style={{ width: 16, height: 16, padding: 0 }}><Icon name="x" size={10} /></button>}
            </div>
            <button className="btn icon subtle" title="手动刷新" onClick={load}><Icon name="refresh" size={12} /></button>
            <a href="/chat" className="btn sm subtle"><Icon name="chat" size={12} /> 聊天</a>
          </div>
        </div>

        {/* status tabs */}
        <div className="row" style={{ gap: 2, paddingBottom: 0, marginBottom: -1 }}>
          <TaskTab label="全部" count={counts.all} active={filter === 'all'} onClick={() => setFilter('all')} />
          {TASK_STATUSES.map(s => (
            <TaskTab key={s.id} label={s.label} count={counts[s.id] || 0} active={filter === s.id} onClick={() => setFilter(s.id)} />
          ))}
        </div>
      </div>

      {/* body */}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: selected ? '1fr 380px' : '1fr', minHeight: 0 }}>
        <div className="scrl" style={{ padding: '18px 22px' }}>
          {loading && (
            <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--ink-3)', fontSize: 13 }}>
              加载中…
            </div>
          )}

          {!loading && grouped.length === 0 && (
            <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--ink-3)' }}>
              <Icon name="check" size={28} />
              <div style={{ marginTop: 10, fontSize: 13 }}>没有符合条件的任务</div>
            </div>
          )}

          {grouped.map(({ s, tasks: rows }) => (
            <div key={s.id} style={{ marginBottom: 22 }}>
              <div className="row" style={{ marginBottom: 8, gap: 8 }}>
                <span className={'pill ' + s.tint}><Icon name={s.icon} size={10} /> {s.label}</span>
                <span className="muted mono" style={{ fontSize: 11 }}>{rows.length}</span>
                <span className="muted" style={{ fontSize: 11.5 }}>· {s.desc}</span>
              </div>
              <div style={{ background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8, overflow: 'hidden' }}>
                {rows.map((t, i) => (
                  <TaskRow
                    key={t.id} task={t} statusInfo={s} divider={i > 0}
                    selected={selected === t.id}
                    onClick={() => setSelected(selected === t.id ? null : t.id)}
                    onAction={load}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>

        {selectedTask && (
          <TaskDetail task={selectedTask} onClose={() => setSelected(null)} onAction={load} />
        )}
      </div>
      </div>
    </AppShell>
  );
};

const TaskTab = ({ label, count, active, onClick }) => (
  <button
    onClick={onClick}
    style={{
      padding: '7px 11px 9px',
      fontSize: 12,
      color: active ? 'var(--ink)' : 'var(--ink-3)',
      fontWeight: active ? 600 : 500,
      borderBottom: '2px solid ' + (active ? 'var(--ink)' : 'transparent'),
      display: 'flex', alignItems: 'center', gap: 6,
      background: 'transparent', border: 0, cursor: 'pointer',
    }}
  >
    {label}
    <span style={{
      fontFamily: 'var(--mono)', fontSize: 10.5,
      padding: '1px 5px', borderRadius: 3,
      background: active ? 'var(--panel-2)' : 'transparent',
      color: active ? 'var(--ink-2)' : 'var(--ink-4)',
    }}>{count}</span>
  </button>
);

const TaskRow = ({ task, statusInfo, divider, selected, onClick, onAction }) => {
  const prioDot = task.priority === 'critical' ? 'bad' : task.priority === 'high' ? 'warn' : 'ok';
  return (
    <div
      onClick={onClick}
      style={{
        display: 'grid',
        gridTemplateColumns: 'auto auto 1fr auto auto auto',
        gap: 14,
        alignItems: 'center',
        width: '100%',
        textAlign: 'left',
        padding: '11px 14px',
        borderTop: divider ? '1px solid var(--line)' : 'none',
        background: selected ? 'var(--panel-2)' : 'transparent',
        cursor: 'pointer',
      }}
    >
      <span className={'dot ' + prioDot} title={'priority: ' + task.priority} />
      <span className={'pill ' + statusInfo.tint} style={{ minWidth: 64, justifyContent: 'center', fontSize: 10 }}>
        {task.danger ? <Icon name="lock" size={10} /> : <Icon name={statusInfo.icon} size={10} />}
        {statusInfo.label}
      </span>
      <div style={{ minWidth: 0 }}>
        <div className="row" style={{ gap: 6 }}>
          <span style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{task.title}</span>
          {task.recurring && <span className="pill purple" style={{ fontSize: 9.5 }}>{task.recurring}</span>}
          {task.external && <span className="pill" style={{ fontSize: 9.5 }}><Icon name="ext" size={9} /> {task.external}</span>}
        </div>
        <div className="muted" style={{ fontSize: 11.5, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{task.sub}</div>
      </div>
      <div className="muted mono" style={{ fontSize: 10.5, textAlign: 'right', minWidth: 90 }}>
        {task.turns > 0 && <>{task.turns}t {task.tools > 0 ? ' · ' + task.tools + 'T' : ''}</>}
        {task.pending > 0 && <> · <span style={{ color: 'var(--warn)', fontWeight: 600 }}>{task.pending}p</span></>}
      </div>
      <div className="muted mono" style={{ fontSize: 11, textAlign: 'right', minWidth: 60 }}>{task.age}</div>
      <div className="row" style={{ gap: 4 }}>
        <TaskRowActions task={task} onAction={onAction} />
      </div>
    </div>
  );
};

const TaskRowActions = ({ task, onAction }) => {
  const stop = (e) => e.stopPropagation();
  const handle = async (e, fn) => {
    stop(e);
    try {
      await fn();
      onAction && onAction();
    } catch (err) {
      alert(err.message);
    }
  };
  if (task.status === 'pending') {
    return (
      <Fragment>
        <a href={'/chat#conv=' + encodeURIComponent(task.conv || '')} className="btn xs subtle" onClick={stop}>查看</a>
        <button className="btn xs primary" onClick={(e) => handle(e, async () => {
          // The actual approval payload needs toolName + argsHash. We
          // got both via /pending/:conv. Re-fetch to get fresh.
          const { pending: p } = await api('/pending/' + encodeURIComponent(task.conv));
          if (!p) throw new Error('pending entry vanished');
          await api('/approve', {
            method: 'POST',
            body: JSON.stringify({
              conversationId: task.conv,
              toolName: p.toolName,
              argsHash: p.argsHash,
            }),
          });
        })}>批准</button>
      </Fragment>
    );
  }
  if (task.status === 'active' || task.status === 'done') {
    return (
      <a href={'/chat#conv=' + encodeURIComponent(task.conv || '')} className="btn xs subtle" onClick={stop}>
        <Icon name="ext" size={10} /> 打开
      </a>
    );
  }
  if (task.status === 'scheduled') {
    return (
      <button className="btn xs subtle" onClick={(e) => handle(e, () =>
        api('/jobs/' + encodeURIComponent(task.jobId) + '/run', { method: 'POST', body: '{}' })
      )}>立即执行</button>
    );
  }
  if (task.status === 'waiting') {
    return (
      <Fragment>
        <a href={'/chat#conv=' + encodeURIComponent(task.conv || '')} className="btn xs subtle" onClick={stop}>检查</a>
        <button className="btn xs subtle" title="标记为不再等待"
          onClick={(e) => handle(e, () =>
            api('/conversations/' + encodeURIComponent(task.conv) + '/waiting', { method: 'DELETE' })
          )}>解除</button>
      </Fragment>
    );
  }
  return null;
};

const TaskDetail = ({ task, onClose, onAction }) => {
  const prioDot = task.priority === 'critical' ? 'bad' : task.priority === 'high' ? 'warn' : 'ok';
  return (
    <div style={{ borderLeft: '1px solid var(--line)', background: 'var(--panel)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--line)' }}>
        <div className="row" style={{ gap: 8, marginBottom: 6 }}>
          <span className={'dot ' + prioDot} />
          <span className="muted mono" style={{ fontSize: 10.5 }}>{task.id} · {task.status}</span>
          <span style={{ flex: 1 }} />
          <button className="btn icon subtle" onClick={onClose}><Icon name="x" size={13} /></button>
        </div>
        <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.3, marginBottom: 4 }}>{task.title}</div>
        <div className="muted" style={{ fontSize: 12 }}>{task.sub}</div>
      </div>
      <div className="scrl" style={{ flex: 1, padding: '14px 18px' }}>
        <div style={{ marginBottom: 16 }}>
          <div className="sec-label" style={{ marginBottom: 6 }}>属性</div>
          <div style={{ display: 'grid', gridTemplateColumns: '80px 1fr', gap: '6px 12px', fontSize: 12 }}>
            <span className="muted">优先级</span><span style={{ textTransform: 'capitalize' }}>{task.priority}</span>
            <span className="muted">状态</span><span style={{ textTransform: 'capitalize' }}>{task.status}</span>
            {task.conv && <Fragment><span className="muted">会话</span><code>{task.conv}</code></Fragment>}
            {task.jobId && <Fragment><span className="muted">Job</span><code>{task.jobId}</code></Fragment>}
            <span className="muted">最后活动</span><span>{task.ts}</span>
            {task.recurring && <Fragment><span className="muted">触发</span><span>{task.recurring}</span></Fragment>}
            {task.external && <Fragment><span className="muted">等待</span><span>{task.external}</span></Fragment>}
            {task.resolution && <Fragment><span className="muted">结果</span><span>{task.resolution}</span></Fragment>}
          </div>
        </div>

        {task.danger && task.status === 'pending' && (
          <PendingDangerCard task={task} onAction={onAction} />
        )}

        <div style={{ marginBottom: 16 }}>
          <div className="sec-label" style={{ marginBottom: 6 }}>统计</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <Stat label="对话轮次" value={task.turns} />
            <Stat label="工具调用" value={task.tools} />
            <Stat label="待批准" value={task.pending} tone={task.pending > 0 ? 'warn' : ''} />
            <Stat label="存活时长" value={task.age} />
          </div>
        </div>

        <div>
          <div className="sec-label" style={{ marginBottom: 6 }}>操作</div>
          <div className="col" style={{ gap: 4 }}>
            <TaskDetailActions task={task} onAction={onAction} />
          </div>
        </div>
      </div>
    </div>
  );
};

const PendingDangerCard = ({ task, onAction }) => {
  const [pending, setPending] = useState(null);
  useEffect(() => {
    if (!task.conv) return;
    api('/pending/' + encodeURIComponent(task.conv))
      .then((r) => setPending(r?.pending ?? null))
      .catch(() => setPending(null));
  }, [task.conv]);
  const approve = async () => {
    if (!pending) return;
    try {
      await api('/approve', {
        method: 'POST',
        body: JSON.stringify({
          conversationId: task.conv,
          toolName: pending.toolName,
          argsHash: pending.argsHash,
        }),
      });
      onAction && onAction();
    } catch (e) { alert(e.message); }
  };
  const deny = async () => {
    if (!pending) return;
    try {
      await api('/deny', {
        method: 'POST',
        body: JSON.stringify({
          conversationId: task.conv,
          toolName: pending.toolName,
          argsHash: pending.argsHash,
        }),
      });
      onAction && onAction();
    } catch (e) { alert(e.message); }
  };
  return (
    <div style={{ marginBottom: 16, padding: 12, background: 'var(--warn-soft)', border: '1px solid rgba(178,100,23,0.25)', borderRadius: 6 }}>
      <div className="row" style={{ gap: 6, fontSize: 12, color: 'var(--warn)', fontWeight: 600, marginBottom: 5 }}>
        <Icon name="lock" size={12} /> 待批准的工具调用
      </div>
      {pending ? (
        <Fragment>
          <div style={{ fontSize: 11.5, color: 'var(--ink-2)', lineHeight: 1.5 }}>
            <code>{pending.toolName}</code> args hash{' '}
            <code className="mono">{pending.argsHash}</code>
          </div>
          <pre style={{
            margin: '8px 0 0', padding: 8, background: 'var(--panel)',
            border: '1px solid var(--line)', borderRadius: 5, fontSize: 11,
            maxHeight: 160, overflow: 'auto', whiteSpace: 'pre-wrap',
            fontFamily: 'var(--mono)',
          }}>{JSON.stringify(pending.input, null, 2)}</pre>
          <div className="row" style={{ gap: 6, marginTop: 10 }}>
            <button className="btn xs danger" onClick={deny}>拒绝</button>
            <button className="btn xs primary" style={{ marginLeft: 'auto' }} onClick={approve}>批准</button>
          </div>
        </Fragment>
      ) : (
        <div className="muted" style={{ fontSize: 11.5 }}>加载 pending 详情…</div>
      )}
    </div>
  );
};

const TaskDetailActions = ({ task, onAction }) => {
  const act = async (fn, ok) => {
    try {
      await fn();
      onAction && onAction();
    } catch (e) { alert(e.message); }
  };
  const rows = [];
  if (task.conv) {
    rows.push(<ActionRow key="open" icon="ext" label="打开会话"
      onClick={() => { location.href = '/chat#conv=' + encodeURIComponent(task.conv); }} />);
  }
  if (task.status === 'scheduled' && task.jobId) {
    rows.push(<ActionRow key="run" icon="flash" label="立即执行"
      onClick={() => act(() => api('/jobs/' + encodeURIComponent(task.jobId) + '/run', { method: 'POST', body: '{}' }))} />);
    rows.push(<ActionRow key="dueNow" icon="refresh" label="排到下个 tick"
      onClick={() => act(() => api('/jobs/' + encodeURIComponent(task.jobId) + '/due-now', { method: 'POST', body: '{}' }))} />);
    rows.push(<ActionRow key="delete" icon="trash" label="删除 job" tone="danger"
      onClick={() => {
        if (!confirm('删除 job ' + task.jobId + '?')) return;
        act(() => api('/jobs/' + encodeURIComponent(task.jobId), { method: 'DELETE' }));
      }} />);
  }
  if (task.status === 'waiting' && task.conv) {
    rows.push(<ActionRow key="clear-wait" icon="check" label="解除等待标记"
      onClick={() => act(() => api('/conversations/' + encodeURIComponent(task.conv) + '/waiting', { method: 'DELETE' }))} />);
  }
  if (task.conv && (task.status === 'active' || task.status === 'done')) {
    rows.push(<ActionRow key="archive" icon="memory" label="归档到 memory + 删除会话" tone="danger"
      onClick={() => {
        if (!confirm('归档并删除 ' + task.conv + '?')) return;
        act(() => api('/conversations/' + encodeURIComponent(task.conv) + '?archive=true', { method: 'DELETE' }));
      }} />);
  }
  if (rows.length === 0) {
    rows.push(<div key="empty" className="muted" style={{ fontSize: 12 }}>无可用操作</div>);
  }
  return rows;
};

const Stat = ({ label, value, tone }) => (
  <div style={{ padding: '8px 10px', background: 'var(--panel-2)', borderRadius: 5, border: '1px solid var(--line)' }}>
    <div className="muted" style={{ fontSize: 10.5, marginBottom: 2 }}>{label}</div>
    <div className="mono" style={{ fontSize: 14, fontWeight: 600, color: tone === 'warn' ? 'var(--warn)' : 'var(--ink)' }}>
      {value === undefined || value === null || value === '' ? '—' : value}
    </div>
  </div>
);

const ActionRow = ({ icon, label, tone, onClick }) => (
  <button
    className="btn subtle sm"
    style={{ width: '100%', justifyContent: 'flex-start', color: tone === 'danger' ? 'var(--danger)' : 'inherit', gap: 8 }}
    onClick={onClick}
  >
    <Icon name={icon} size={12} />{label}
  </button>
);

window.TasksApp = TasksApp;
