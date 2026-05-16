// settings.jsx — post-install edit page. Same families of settings
// the /setup wizard collects, but each section saves independently
// (so you can rotate the LLM key without re-confirming TEMPER, etc.).
//
// Secrets:
//   - GET /settings returns has_secret booleans only, never plaintext
//   - PUT writes new value (encrypts server-side via setSecretSetting)
//   - Empty input = "leave existing in place"; explicit "Clear" = null

const SettingsApp = () => {
  const { meta, error: healthErr } = useHealth();
  const [settings, setSettings] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [message, setMessage] = React.useState(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const j = await api('/settings');
      setSettings(j.settings || []);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const flash = (msg) => {
    setMessage(msg);
    setTimeout(() => setMessage(null), 2500);
  };

  // Build a lookup from key → setting row for the forms below.
  const byKey = React.useMemo(() => {
    const m = {};
    for (const s of settings) m[s.key] = s;
    return m;
  }, [settings]);

  return (
    <AppShell current="settings">
      <div className="smith-app" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 18px', borderBottom: '1px solid var(--line)', background: 'var(--panel)' }}>
        <Avatar kind="smith" size="lg" />
        <div className="row" style={{ gap: 6 }}>
          <strong style={{ fontSize: 14 }}>Smith</strong>
          <span className="muted mono" style={{ fontSize: 10.5 }}>· settings</span>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>
          {meta
            ? <><StatusDot status={meta.status === 'ok' ? 'ok' : 'warn'} />{' '}{meta.temper_user || '?'}</>
            : healthErr ? '(/healthz unreachable)' : '…'}
        </span>
        <span style={{ flex: 1 }} />
        <a href="/chat" className="btn sm subtle"><Icon name="chat" size={12} /> Chat</a>
        <a href="/plugins" className="btn sm subtle"><Icon name="cog" size={12} /> Plugins</a>
      </header>

      <main className="scrl" style={{ flex: 1, padding: '22px' }}>
        <div style={{ maxWidth: 720, margin: '0 auto' }}>
          <h2 style={{ marginTop: 0 }}>Settings</h2>
          <p className="muted" style={{ fontSize: 12.5 }}>
            Changes take effect on the next getConfig() call (sub-second). Restart Smith only for
            connectivity-fail scenarios (LLM provider changed, etc).
          </p>

          {error && <Banner kind="danger">{error}</Banner>}
          {message && <Banner kind="good">{message}</Banner>}

          {loading && settings.length === 0
            ? <div className="muted" style={{ padding: 30, textAlign: 'center' }}>Loading…</div>
            : (
              <>
                <Section title="Smith identity" desc="Affects which TEMPER namespace Smith writes into.">
                  <StringField row={byKey['smith.agent_slug']} setKey="smith.agent_slug" placeholder="smith"
                               onSaved={(s) => { flash(`Saved smith.agent_slug = ${s}`); load(); }} onError={setError} />
                </Section>

                <Section title="Timezone" desc="IANA name (e.g. Asia/Shanghai, America/Los_Angeles). Auto-detected from your OS on first boot. Used to render current time + interpret relative times in your messages (tomorrow / every morning / in 2h).">
                  <TimezoneField row={byKey['smith.timezone']} setKey="smith.timezone"
                                 onSaved={(s) => { flash(`Saved smith.timezone = ${s}`); load(); }} onError={setError} />
                </Section>

                <Section title="TEMPER memory service">
                  <StringField row={byKey['temper.base_url']} setKey="temper.base_url" placeholder="http://127.0.0.1:18088"
                               onSaved={(s) => { flash(`Saved temper.base_url`); load(); }} onError={setError} />
                  <SecretField row={byKey['temper.api_key']} setKey="temper.api_key" placeholder="mk_..."
                               onSaved={() => { flash('TEMPER key rotated'); load(); }} onError={setError} />
                </Section>

                <Section title="LLM" desc="Edits apply to NEW sessions. Active conversations keep the old key until they end.">
                  <StringField row={byKey['llm.provider']} setKey="llm.provider" placeholder="deepseek"
                               onSaved={() => { flash('Saved llm.provider'); load(); }} onError={setError} />
                  <StringField row={byKey['llm.model']} setKey="llm.model" placeholder="forti-k2"
                               onSaved={() => { flash('Saved llm.model'); load(); }} onError={setError} />
                  <StringField row={byKey['llm.base_url']} setKey="llm.base_url" placeholder="(empty = provider default)"
                               onSaved={() => { flash('Saved llm.base_url'); load(); }} onError={setError} />
                  <SecretField row={byKey['llm.api_key']} setKey="llm.api_key" placeholder="sk-... / mk_..."
                               onSaved={() => { flash('LLM key rotated'); load(); }} onError={setError} />
                </Section>

                <Section title="HTTP bearer" desc="Gates /chat /approve /plugins /settings — rotate periodically. Clearing makes Smith open (only safe with localhost binding).">
                  <SecretField row={byKey['smith.bearer_secret']} setKey="smith.bearer_secret" placeholder="(new bearer token)"
                               onSaved={() => { flash('Bearer rotated. Re-open Smith UI with /#secret=<new-value>.'); load(); }}
                               onError={setError} allowClear />
                </Section>

                <Section title="Consolidate schedule">
                  <NumberField row={byKey['consolidate.schedule_hours']} setKey="consolidate.schedule_hours" placeholder="0 (off)"
                               onSaved={() => { flash('Saved consolidate.schedule_hours'); load(); }} onError={setError} />
                  <BoolField row={byKey['consolidate.auto_apply']} setKey="consolidate.auto_apply"
                             label="Auto-apply plans (instead of just logging them)"
                             onSaved={() => { flash('Saved consolidate.auto_apply'); load(); }} onError={setError} />
                </Section>

                <Section title="Recall log verbosity">
                  <EnumField row={byKey['recall.log_level']} setKey="recall.log_level"
                             options={['quiet', 'verbose', 'full', 'dump']}
                             onSaved={() => { flash('Saved recall.log_level'); load(); }} onError={setError} />
                </Section>

                <Section title="All settings (raw)" desc="The full settings table — useful for debugging.">
                  <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr style={{ textAlign: 'left', color: 'var(--ink-3)' }}>
                        <th style={{ padding: '4px 8px' }}>Key</th>
                        <th style={{ padding: '4px 8px' }}>Value</th>
                        <th style={{ padding: '4px 8px' }}>Updated</th>
                      </tr>
                    </thead>
                    <tbody>
                      {settings.map(s => (
                        <tr key={s.key}>
                          <td style={{ padding: '4px 8px', fontFamily: 'var(--mono)' }}>{s.key}</td>
                          <td style={{ padding: '4px 8px' }}>{s.has_secret ? <em>(secret)</em> : <code style={{ background: 'var(--panel-2)', padding: '1px 5px', borderRadius: 3 }}>{JSON.stringify(s.value)}</code>}</td>
                          <td style={{ padding: '4px 8px', fontSize: 11, color: 'var(--ink-3)' }}>{s.updated_at?.replace('T', ' ').slice(0, 16)} <span>· {s.updated_by ?? '—'}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Section>
              </>
            )}
        </div>
      </main>
      </div>
    </AppShell>
  );
};

const Section = ({ title, desc, children }) => (
  <div style={{ marginBottom: 24, padding: 16, background: 'var(--panel)', border: '1px solid var(--line)', borderRadius: 8 }}>
    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>{title}</div>
    {desc && <div className="muted" style={{ fontSize: 12, marginBottom: 12 }}>{desc}</div>}
    {children}
  </div>
);

const Banner = ({ kind, children }) => (
  <div style={{
    marginBottom: 12, padding: '8px 12px', borderRadius: 6, fontSize: 12,
    background: kind === 'danger' ? 'var(--danger-soft)' : 'var(--good-soft)',
    color: kind === 'danger' ? 'var(--danger)' : 'var(--good)',
    border: `1px solid ${kind === 'danger' ? 'var(--danger)' : 'var(--good)'}`,
  }}>{children}</div>
);

// --- field types ---

const Row = ({ children }) => (
  <div className="row" style={{ marginBottom: 10, gap: 8 }}>{children}</div>
);

const KeyLabel = ({ k }) => (
  <code style={{ minWidth: 200, fontSize: 11, color: 'var(--ink-3)', background: 'transparent', border: 0, padding: 0 }}>{k}</code>
);

const TimezoneField = ({ row, setKey, onSaved, onError }) => {
  const detected = (typeof Intl !== 'undefined') ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC';
  const initial = typeof row?.value === 'string' && row.value ? row.value : detected;
  const [v, setV] = React.useState(initial);
  React.useEffect(() => {
    if (typeof row?.value === 'string') setV(row.value);
  }, [row?.value]);
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    const t = setInterval(() => setTick(x => x + 1), 1000);
    return () => clearInterval(t);
  }, []);
  let preview;
  try {
    preview = new Intl.DateTimeFormat('en-CA', {
      timeZone: v, year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      timeZoneName: 'shortOffset',
    }).format(new Date());
  } catch {
    preview = '(invalid IANA zone — Smith will fall back to ' + detected + ')';
  }
  void tick;
  const save = async () => {
    try {
      await api(`/settings/${encodeURIComponent(setKey)}`, {
        method: 'PUT', body: JSON.stringify({ value: v.trim() }),
      });
      onSaved(v.trim());
    } catch (e) { onError(e.message); }
  };
  // Common zones for convenience; user can also type a free IANA name.
  const COMMON = [
    'Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Tokyo', 'Asia/Singapore', 'Asia/Kolkata',
    'Europe/London', 'Europe/Berlin', 'Europe/Paris',
    'America/Los_Angeles', 'America/Denver', 'America/Chicago', 'America/New_York',
    'UTC',
  ];
  return (
    <div>
      <Row>
        <KeyLabel k={setKey} />
        <input value={v} onChange={e => setV(e.target.value)} placeholder={detected}
               list="tz-suggestions" style={input} />
        <datalist id="tz-suggestions">
          {COMMON.map(z => <option key={z} value={z} />)}
        </datalist>
        <button className="btn sm" onClick={save}>Save</button>
        {v !== detected && (
          <button className="btn sm subtle" title="Use OS-detected zone" onClick={() => setV(detected)}>
            Use system ({detected})
          </button>
        )}
      </Row>
      <div className="muted" style={{ marginLeft: 200, marginTop: -4, marginBottom: 8, fontSize: 11, fontFamily: 'var(--mono)' }}>
        现在: {preview}
      </div>
    </div>
  );
};

const StringField = ({ row, setKey, placeholder, onSaved, onError }) => {
  const [v, setV] = React.useState(typeof row?.value === 'string' ? row.value : '');
  React.useEffect(() => { if (typeof row?.value === 'string') setV(row.value); }, [row?.value]);
  const save = async () => {
    try {
      await api(`/settings/${encodeURIComponent(setKey)}`, {
        method: 'PUT', body: JSON.stringify({ value: v }),
      });
      onSaved(v);
    } catch (e) { onError(e.message); }
  };
  return (
    <Row>
      <KeyLabel k={setKey} />
      <input value={v} onChange={e => setV(e.target.value)} placeholder={placeholder} style={input} />
      <button className="btn sm" onClick={save}>Save</button>
    </Row>
  );
};

const NumberField = ({ row, setKey, placeholder, onSaved, onError }) => {
  const [v, setV] = React.useState(typeof row?.value === 'number' ? row.value : 0);
  React.useEffect(() => { if (typeof row?.value === 'number') setV(row.value); }, [row?.value]);
  const save = async () => {
    try {
      await api(`/settings/${encodeURIComponent(setKey)}`, {
        method: 'PUT', body: JSON.stringify({ value: Number(v) }),
      });
      onSaved(v);
    } catch (e) { onError(e.message); }
  };
  return (
    <Row>
      <KeyLabel k={setKey} />
      <input type="number" min="0" value={v} onChange={e => setV(Number(e.target.value))} placeholder={placeholder} style={{ ...input, width: 120 }} />
      <button className="btn sm" onClick={save}>Save</button>
    </Row>
  );
};

const BoolField = ({ row, setKey, label, onSaved, onError }) => {
  const [v, setV] = React.useState(row?.value === true);
  React.useEffect(() => { setV(row?.value === true); }, [row?.value]);
  const save = async (newV) => {
    setV(newV);
    try {
      await api(`/settings/${encodeURIComponent(setKey)}`, {
        method: 'PUT', body: JSON.stringify({ value: newV }),
      });
      onSaved(newV);
    } catch (e) { onError(e.message); setV(!newV); }
  };
  return (
    <Row>
      <KeyLabel k={setKey} />
      <label className="row" style={{ gap: 6 }}>
        <input type="checkbox" checked={v} onChange={e => save(e.target.checked)} />
        <span style={{ fontSize: 12 }}>{label}</span>
      </label>
    </Row>
  );
};

const EnumField = ({ row, setKey, options, onSaved, onError }) => {
  const [v, setV] = React.useState(typeof row?.value === 'string' ? row.value : options[0]);
  React.useEffect(() => { if (typeof row?.value === 'string') setV(row.value); }, [row?.value]);
  const save = async (newV) => {
    setV(newV);
    try {
      await api(`/settings/${encodeURIComponent(setKey)}`, {
        method: 'PUT', body: JSON.stringify({ value: newV }),
      });
      onSaved(newV);
    } catch (e) { onError(e.message); }
  };
  return (
    <Row>
      <KeyLabel k={setKey} />
      <select value={v} onChange={e => save(e.target.value)} style={input}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </Row>
  );
};

const SecretField = ({ row, setKey, placeholder, onSaved, onError, allowClear }) => {
  const [v, setV] = React.useState('');
  const hasValue = row?.has_secret;
  const save = async () => {
    if (!v) return;
    try {
      await api(`/settings/${encodeURIComponent(setKey)}/secret`, {
        method: 'PUT', body: JSON.stringify({ secret: v }),
      });
      setV('');
      onSaved();
    } catch (e) { onError(e.message); }
  };
  const clear = async () => {
    if (!confirm(`Clear ${setKey}? Routes that rely on this become open/unauth'd.`)) return;
    try {
      await api(`/settings/${encodeURIComponent(setKey)}/secret`, {
        method: 'PUT', body: JSON.stringify({ secret: null }),
      });
      onSaved();
    } catch (e) { onError(e.message); }
  };
  return (
    <Row>
      <KeyLabel k={setKey} />
      <span style={{ minWidth: 100, fontSize: 11, color: hasValue ? 'var(--good)' : 'var(--ink-3)' }}>
        {hasValue ? '✓ set' : '— not set'}
      </span>
      <input type="password" value={v} onChange={e => setV(e.target.value)}
             placeholder={hasValue ? '(rotate: enter new value)' : placeholder} style={input} />
      <button className="btn sm" onClick={save} disabled={!v}>{hasValue ? 'Rotate' : 'Set'}</button>
      {allowClear && hasValue && <button className="btn sm subtle" style={{ color: 'var(--danger)' }} onClick={clear}>Clear</button>}
    </Row>
  );
};

const input = {
  flex: 1, padding: '6px 10px', border: '1px solid var(--line-strong)',
  borderRadius: 5, fontSize: 13, background: 'var(--panel)',
  fontFamily: 'inherit',
};

window.SettingsApp = SettingsApp;
