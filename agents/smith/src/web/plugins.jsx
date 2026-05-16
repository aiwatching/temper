// plugins.jsx — Plugin (MCP / HTTP / shell) registry management.
//
// Reads/writes Smith's local SQLite plugins table via /plugins/* API.
// Secrets are write-only on the wire — UI never reads the plaintext
// back; "Rotate secret" replaces, "Delete" wipes.
//
// Adding a plugin doesn't hot-add its tools to the running pi session
// (pi has no unregisterTool). Restart Smith to pick them up. The page
// surfaces this in a yellow banner whenever an enabled plugin's
// `last_seen_at` is null (i.e. never connected since current process
// started).

const PluginsApp = () => {
  const { meta, error: healthErr } = useHealth();
  const [plugins, setPlugins] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const [message, setMessage] = React.useState(null);
  const [editor, setEditor] = React.useState(null);  // null | { isNew, plugin? }
  const [secretFor, setSecretFor] = React.useState(null); // slug | null

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api('/plugins');
      setPlugins(r.plugins || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);
  React.useEffect(() => { load(); }, [load]);

  const flash = (msg) => {
    setMessage(msg);
    setTimeout(() => setMessage(null), 2500);
  };

  const onCreate = () => setEditor({ isNew: true });
  const onEdit = (p) => setEditor({ isNew: false, plugin: p });
  const onClose = () => setEditor(null);

  const onSaved = async () => {
    onClose();
    await load();
    // Hot-reload semantics (P4):
    //   - edit existing plugin (config / secret) → in ≤30s the
    //     PluginManager poll picks it up + reconnects, no restart
    //   - enable/disable existing plugin → same
    //   - NEW plugin → pi can't add tool names live; needs restart
    flash('Saved. Edits to existing plugins take effect within 30s; new plugins need a Smith restart to register their tools.');
  };

  const onTest = async (p) => {
    try {
      const r = await api('/plugins/test', {
        method: 'POST',
        body: JSON.stringify({
          slug: p.slug,
          kind: p.kind,
          config: p.config,
          use_secret_from: p.has_secret ? p.slug : undefined,
        }),
      });
      if (r.ok) {
        flash(`✓ ${p.slug} ok — ${r.tool_count} tools in ${r.ms}ms`);
      } else {
        setError(`${p.slug} test failed: ${r.error}`);
      }
    } catch (e) {
      setError(e.message);
    }
  };

  const onTogglePin = async (p) => {
    // Toggle enabled (the "pin" of the plugin row — keep / drop it
    // from the active set on next restart).
    try {
      await api(`/plugins/${encodeURIComponent(p.slug)}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: !p.enabled }),
      });
      await load();
    } catch (e) { setError(e.message); }
  };

  const onDelete = async (p) => {
    if (!confirm(`Delete plugin '${p.slug}'? Its secret will be wiped.`)) return;
    try {
      await api(`/plugins/${encodeURIComponent(p.slug)}`, { method: 'DELETE' });
      flash(`Deleted ${p.slug}. Connection drops within 30s; LLM tool registrations stay until next restart (calls return errors).`);
      await load();
    } catch (e) { setError(e.message); }
  };

  return (
    <AppShell current="plugins">
      <div className="smith-app" style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 18px', borderBottom: '1px solid var(--line)', background: 'var(--panel)' }}>
        <Avatar kind="smith" size="lg" />
        <div className="row" style={{ gap: 6 }}>
          <strong style={{ fontSize: 14 }}>Smith</strong>
          <span className="muted mono" style={{ fontSize: 10.5 }}>· plugins</span>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>
          {meta
            ? <><StatusDot status={meta.status === 'ok' ? 'ok' : 'warn'} />{' '}{meta.temper_user || '?'}</>
            : healthErr ? '(/healthz unreachable)' : '…'}
        </span>
        <span style={{ flex: 1 }} />
        <a href="/chat" className="btn sm subtle" title="Back to chat"><Icon name="chat" size={12} /> Chat</a>
        <a href="/briefs" className="btn sm subtle"><Icon name="side" size={12} /> Briefs</a>
      </header>

      <main className="scrl" style={{ flex: 1, padding: '22px' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          <div className="spread" style={{ marginBottom: 18 }}>
            <div>
              <h2 style={{ margin: 0 }}>Plugins</h2>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                External services exposed as LLM tools.
                Tool name format: <code style={{ background:'var(--panel-2)', padding:'1px 5px', borderRadius:3 }}>&lt;slug&gt;__&lt;tool&gt;</code>.
                Adding / removing whole plugins requires a Smith restart (pi has no unregisterTool).
              </div>
            </div>
            <button className="btn primary" onClick={onCreate}>
              <Icon name="plus" size={12} /> Add plugin
            </button>
          </div>

          {error && <div style={{ marginBottom: 12, padding: '8px 12px', background:'var(--danger-soft)', border:'1px solid var(--danger)', color:'var(--danger)', borderRadius:6, fontSize:12 }}>{error}</div>}
          {message && <div style={{ marginBottom: 12, padding: '8px 12px', background:'var(--good-soft)', border:'1px solid var(--good)', color:'var(--good)', borderRadius:6, fontSize:12 }}>{message}</div>}

          {loading && plugins.length === 0
            ? <div className="muted" style={{ padding: 30, textAlign:'center' }}>Loading…</div>
            : plugins.length === 0
              ? <EmptyState onCreate={onCreate} />
              : <PluginsTable plugins={plugins} onEdit={onEdit} onTest={onTest} onToggle={onTogglePin} onDelete={onDelete} onRotateSecret={setSecretFor} />}
        </div>
      </main>

      {editor && (
        <PluginEditor
          existing={editor.plugin}
          isNew={editor.isNew}
          onClose={onClose}
          onSaved={onSaved}
        />
      )}

      {secretFor && (
        <RotateSecretDialog
          slug={secretFor}
          onClose={() => setSecretFor(null)}
          onRotated={() => { setSecretFor(null); flash('Secret rotated. Takes effect on next plugin poll (≤30s) — no restart needed.'); load(); }}
        />
      )}
      </div>
    </AppShell>
  );
};

const EmptyState = ({ onCreate }) => (
  <div style={{ padding: '50px 20px', textAlign:'center', color:'var(--ink-3)', background:'var(--panel)', border:'1px dashed var(--line-strong)', borderRadius:10 }}>
    <Icon name="cog" size={32} />
    <div style={{ marginTop: 12, fontSize: 14, color:'var(--ink-2)' }}>No plugins yet</div>
    <div style={{ marginTop: 6, fontSize: 12 }}>Connect your first MCP server to give Smith tools beyond the built-ins.</div>
    <button className="btn primary" style={{ marginTop: 16 }} onClick={onCreate}>
      <Icon name="plus" size={12} /> Add plugin
    </button>
  </div>
);

const PluginsTable = ({ plugins, onEdit, onTest, onToggle, onDelete, onRotateSecret }) => (
  <table style={{ width:'100%', borderCollapse:'collapse', background:'var(--panel)', border:'1px solid var(--line)', borderRadius:8, overflow:'hidden' }}>
    <thead>
      <tr style={{ background:'var(--panel-2)', textAlign:'left' }}>
        <th style={th}>Slug</th>
        <th style={th}>Kind / endpoint</th>
        <th style={th}>Auth</th>
        <th style={th}>Tools</th>
        <th style={th}>Last seen</th>
        <th style={th}>State</th>
        <th style={{ ...th, textAlign:'right' }}>Actions</th>
      </tr>
    </thead>
    <tbody>
      {plugins.map(p => <PluginRow key={p.slug} p={p} onEdit={onEdit} onTest={onTest} onToggle={onToggle} onDelete={onDelete} onRotateSecret={onRotateSecret} />)}
    </tbody>
  </table>
);

const th = { fontSize: 11, color:'var(--ink-3)', fontWeight: 500, textTransform:'uppercase', letterSpacing:'0.05em', padding:'8px 12px', borderBottom:'1px solid var(--line)' };
const td = { padding:'10px 12px', borderBottom:'1px solid var(--line)', fontSize:12.5, verticalAlign:'top' };

const PluginRow = ({ p, onEdit, onTest, onToggle, onDelete, onRotateSecret }) => {
  const health = !p.enabled ? 'off'
    : p.last_error ? 'bad'
    : p.last_seen_at ? 'ok'
    : 'warn';
  const cfg = p.config || {};
  return (
    <tr>
      <td style={td}>
        <div className="row" style={{ gap: 6 }}>
          <StatusDot status={health} />
          <code style={{ background:'transparent', border:0, padding:0, fontWeight: 600 }}>{p.slug}</code>
        </div>
        <div className="muted" style={{ fontSize: 11, marginTop: 3 }}>{p.display_name}</div>
      </td>
      <td style={td}>
        <div className="mono" style={{ fontSize: 11 }}>
          <span className="pill" style={{ padding:'1px 6px', marginRight: 6 }}>{p.kind}/{cfg.transport ?? '?'}</span>
        </div>
        <div className="mono" style={{ fontSize: 11, color:'var(--ink-3)', marginTop: 3, wordBreak:'break-all' }}>
          {cfg.endpoint || '—'}
        </div>
      </td>
      <td style={td}>
        <div style={{ fontSize: 11 }}>
          {cfg.auth?.type ?? 'none'}
          {p.has_secret && <span className="pill" style={{ marginLeft: 4, padding:'1px 5px' }}>secret set</span>}
        </div>
      </td>
      <td style={{ ...td, fontFamily:'var(--mono)' }}>{p.last_tool_count ?? '—'}</td>
      <td style={{ ...td, fontSize: 11, color:'var(--ink-3)' }}>
        {p.last_seen_at ? p.last_seen_at.replace('T', ' ').slice(0, 16) : 'never'}
        {p.last_error && (
          <div style={{ color:'var(--danger)', marginTop: 3, maxWidth: 220 }} title={p.last_error}>
            {p.last_error.slice(0, 60)}{p.last_error.length > 60 ? '…' : ''}
          </div>
        )}
      </td>
      <td style={td}>
        <span className={`pill ${p.enabled ? 'good' : ''}`} style={{ padding:'1px 6px' }}>{p.enabled ? 'enabled' : 'disabled'}</span>
      </td>
      <td style={{ ...td, textAlign:'right', whiteSpace:'nowrap' }}>
        <button className="btn xs subtle" onClick={() => onTest(p)} title="Test connection">Test</button>
        <button className="btn xs subtle" onClick={() => onEdit(p)} title="Edit config">Edit</button>
        <button className="btn xs subtle" onClick={() => onRotateSecret(p.slug)} title="Rotate secret">Secret</button>
        <button className="btn xs subtle" onClick={() => onToggle(p)} title={p.enabled ? 'Disable' : 'Enable'}>
          {p.enabled ? 'Disable' : 'Enable'}
        </button>
        <button className="btn xs subtle" style={{ color:'var(--danger)' }} onClick={() => onDelete(p)} title="Delete">Delete</button>
      </td>
    </tr>
  );
};

// --- editor (create + edit) ----------------------------------------------

const PluginEditor = ({ existing, isNew, onClose, onSaved }) => {
  const [slug, setSlug] = React.useState(existing?.slug ?? '');
  const [kind] = React.useState(existing?.kind ?? 'mcp');  // only mcp for now
  const [displayName, setDisplayName] = React.useState(existing?.display_name ?? '');
  const cfg = existing?.config ?? {};
  const [transport, setTransport] = React.useState(cfg.transport ?? 'stdio');
  const [endpoint, setEndpoint] = React.useState(cfg.endpoint ?? '');
  const [args, setArgs] = React.useState((cfg.args ?? []).join(' '));
  const [authType, setAuthType] = React.useState(cfg.auth?.type ?? 'none');
  const [authHeader, setAuthHeader] = React.useState(cfg.auth?.header ?? 'X-API-Key');
  const [secret, setSecret] = React.useState('');  // empty = leave alone on edit
  const [error, setError] = React.useState(null);
  const [testResult, setTestResult] = React.useState(null);
  const [saving, setSaving] = React.useState(false);

  const slugLooksValid = /^[a-z0-9][a-z0-9_-]*$/.test(slug);

  const buildBody = () => ({
    slug,
    kind,
    display_name: displayName || slug,
    config: {
      transport,
      endpoint,
      ...(transport === 'stdio' && args.trim() ? { args: args.trim().split(/\s+/) } : {}),
      ...(transport !== 'stdio' && authType !== 'none'
        ? { auth: { type: authType, ...(authType === 'header' ? { header: authHeader } : {}) } }
        : {}),
    },
    // undefined = keep existing on edit; empty string skipped (no rotation)
    ...(isNew || secret ? { secret: secret || undefined } : {}),
  });

  const doTest = async () => {
    setError(null);
    setTestResult({ loading: true });
    try {
      const body = buildBody();
      // For existing rows where the secret field is left blank, fall
      // back to the stored secret on the server side.
      if (!isNew && !secret && existing?.has_secret) {
        body.use_secret_from = existing.slug;
      }
      const r = await api('/plugins/test', { method: 'POST', body: JSON.stringify(body) });
      setTestResult(r);
    } catch (e) {
      setError(e.message);
      setTestResult(null);
    }
  };

  const doSave = async () => {
    setError(null);
    setSaving(true);
    try {
      if (isNew) {
        await api('/plugins', { method: 'POST', body: JSON.stringify(buildBody()) });
      } else {
        await api(`/plugins/${encodeURIComponent(existing.slug)}`, {
          method: 'PUT', body: JSON.stringify(buildBody()),
        });
      }
      onSaved();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div onClick={onClose} style={modalMask}>
      <div onClick={(e) => e.stopPropagation()} style={modalPanel}>
        <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--line)', display:'flex', alignItems:'center', gap:10 }}>
          <Icon name="cog" size={15} style={{ color:'var(--ink-3)' }} />
          <strong style={{ fontSize: 14, flex: 1 }}>
            {isNew ? 'Add plugin' : `Edit ${existing.slug}`}
          </strong>
          <button className="btn icon subtle" onClick={onClose}><Icon name="x" size={14} /></button>
        </div>

        <div className="scrl" style={{ padding: 18, flex: 1 }}>
          {error && <div style={{ marginBottom: 12, padding: '6px 10px', background:'var(--danger-soft)', color:'var(--danger)', border:'1px solid var(--danger)', borderRadius:5, fontSize: 12 }}>{error}</div>}

          <Field label="Slug" hint="Lowercase identifier. Tool names will be prefixed with this. Can't be changed after create.">
            <input value={slug} disabled={!isNew} onChange={e => setSlug(e.target.value)}
                   placeholder="mantis" style={input} />
            {isNew && slug && !slugLooksValid && (
              <div style={{ color:'var(--danger)', fontSize: 11, marginTop: 4 }}>
                slug must be lowercase letters/digits/underscore/dash, starting alnum
              </div>
            )}
          </Field>

          <Field label="Kind">
            <div className="row" style={{ gap: 10 }}>
              <label className="row" style={{ gap: 5, fontSize: 12 }}>
                <input type="radio" checked={kind === 'mcp'} readOnly /> MCP
              </label>
              <label className="row" style={{ gap: 5, fontSize: 12, color:'var(--ink-4)' }}>
                <input type="radio" disabled /> HTTP <span className="muted" style={{ fontSize: 10 }}>(later)</span>
              </label>
              <label className="row" style={{ gap: 5, fontSize: 12, color:'var(--ink-4)' }}>
                <input type="radio" disabled /> Shell <span className="muted" style={{ fontSize: 10 }}>(later)</span>
              </label>
            </div>
          </Field>

          <Field label="Display name">
            <input value={displayName} onChange={e => setDisplayName(e.target.value)}
                   placeholder={slug || "Mantis (FortiNAC)"} style={input} />
          </Field>

          <Field label="Transport">
            <select value={transport} onChange={e => setTransport(e.target.value)} style={input}>
              <option value="stdio">stdio (local binary)</option>
              <option value="http">http (StreamableHTTP)</option>
              <option value="sse">sse (Server-Sent Events)</option>
            </select>
          </Field>

          <Field label={transport === 'stdio' ? 'Binary path' : 'URL'}
                 hint={transport === 'stdio' ? '/abs/path/to/mcp-server (must be executable)' : 'https://internal.example.com/mcp'}>
            <input value={endpoint} onChange={e => setEndpoint(e.target.value)} style={input}
                   placeholder={transport === 'stdio' ? '/usr/local/bin/mantis-mcp' : 'https://mantis.example.com/mcp'} />
          </Field>

          {transport === 'stdio' && (
            <Field label="Args" hint="Space-separated arguments passed to the binary">
              <input value={args} onChange={e => setArgs(e.target.value)} style={input}
                     placeholder="--config /etc/mantis.toml" />
            </Field>
          )}

          {transport !== 'stdio' && (
            <Field label="Auth">
              <select value={authType} onChange={e => setAuthType(e.target.value)} style={input}>
                <option value="none">none</option>
                <option value="bearer">Bearer token (Authorization header)</option>
                <option value="header">Custom header (e.g. X-API-Key)</option>
              </select>
              {authType === 'header' && (
                <input value={authHeader} onChange={e => setAuthHeader(e.target.value)}
                       placeholder="X-API-Key" style={{ ...input, marginTop: 8 }} />
              )}
              {authType !== 'none' && (
                <input
                  type="password" value={secret} onChange={e => setSecret(e.target.value)}
                  placeholder={isNew ? "secret value" : (existing?.has_secret ? "(leave empty to keep existing)" : "secret value")}
                  style={{ ...input, marginTop: 8 }} />
              )}
            </Field>
          )}

          {testResult && (
            <div style={{ marginTop: 12, padding: '10px 12px', background:'var(--panel-2)', border:'1px solid var(--line)', borderRadius:6, fontSize: 12 }}>
              {testResult.loading
                ? <span className="muted">Testing…</span>
                : testResult.ok
                  ? <>
                      <div style={{ color:'var(--good)' }}>✓ Connected in {testResult.ms}ms · {testResult.tool_count} tools</div>
                      <details style={{ marginTop: 6 }}>
                        <summary className="muted" style={{ cursor:'pointer', fontSize: 11 }}>tool list</summary>
                        <ul style={{ margin:'4px 0 0 16px', padding: 0, fontSize: 11 }}>
                          {testResult.tools.map(t => <li key={t.name}><code>{t.name}</code> — <span className="muted">{t.description?.slice(0, 60) ?? ''}</span></li>)}
                        </ul>
                      </details>
                    </>
                  : <div style={{ color:'var(--danger)' }}>✗ {testResult.error}</div>}
            </div>
          )}
        </div>

        <div style={{ padding: '12px 18px', borderTop:'1px solid var(--line)', display:'flex', justifyContent:'flex-end', gap: 8 }}>
          <button className="btn subtle" onClick={onClose}>Cancel</button>
          <button className="btn" onClick={doTest} disabled={!endpoint || (isNew && !slugLooksValid)}>Test</button>
          <button className="btn primary" onClick={doSave} disabled={saving || !endpoint || (isNew && !slugLooksValid) || !displayName && !slug}>
            {saving ? 'Saving…' : (isNew ? 'Create' : 'Save')}
          </button>
        </div>
      </div>
    </div>
  );
};

const Field = ({ label, hint, children }) => (
  <div style={{ marginBottom: 14 }}>
    <label style={{ display:'block', fontSize: 12, color:'var(--ink-2)', marginBottom: 4, fontWeight: 500 }}>{label}</label>
    {children}
    {hint && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{hint}</div>}
  </div>
);

const input = {
  width:'100%', padding:'6px 10px', border:'1px solid var(--line-strong)',
  borderRadius:5, fontSize: 13, background:'var(--panel)',
  fontFamily: 'inherit',
};

// --- secret rotation -----------------------------------------------------

const RotateSecretDialog = ({ slug, onClose, onRotated }) => {
  const [secret, setSecret] = React.useState('');
  const [error, setError] = React.useState(null);
  const [busy, setBusy] = React.useState(false);

  const doRotate = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/plugins/${encodeURIComponent(slug)}/secret`, {
        method: 'PUT', body: JSON.stringify({ secret: secret || null }),
      });
      onRotated();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div onClick={onClose} style={modalMask}>
      <div onClick={(e) => e.stopPropagation()} style={{ ...modalPanel, width: 460 }}>
        <div style={{ padding:'14px 18px', borderBottom:'1px solid var(--line)', display:'flex', alignItems:'center', gap:10 }}>
          <Icon name="lock" size={15} />
          <strong style={{ fontSize: 14, flex: 1 }}>Rotate secret · {slug}</strong>
          <button className="btn icon subtle" onClick={onClose}><Icon name="x" size={14} /></button>
        </div>
        <div style={{ padding: 18 }}>
          <div className="muted" style={{ fontSize: 12, marginBottom: 10 }}>
            New secret replaces the encrypted value in <code>.data/smith.db</code>.
            Leave empty + Submit to clear the secret entirely (turns auth off).
          </div>
          <input
            type="password" value={secret} onChange={e => setSecret(e.target.value)}
            placeholder="new secret value (empty = clear)"
            style={input}
            autoFocus />
          {error && <div style={{ marginTop: 10, color:'var(--danger)', fontSize: 12 }}>{error}</div>}
        </div>
        <div style={{ padding: '12px 18px', borderTop:'1px solid var(--line)', display:'flex', justifyContent:'flex-end', gap: 8 }}>
          <button className="btn subtle" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={busy} onClick={doRotate}>
            {busy ? 'Saving…' : 'Rotate'}
          </button>
        </div>
      </div>
    </div>
  );
};

const modalMask = {
  position:'absolute', inset: 0, zIndex: 50,
  background:'rgba(20,20,15,0.32)',
  display:'flex', alignItems:'flex-start', justifyContent:'center',
  paddingTop: '10vh', backdropFilter: 'blur(2px)',
};
const modalPanel = {
  width: 640, maxHeight:'80vh',
  background:'var(--panel)', borderRadius: 10,
  boxShadow:'0 30px 80px rgba(20,20,15,0.28)',
  display:'flex', flexDirection:'column',
};

window.PluginsApp = PluginsApp;
