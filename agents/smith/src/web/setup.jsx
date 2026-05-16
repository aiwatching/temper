// setup.jsx — first-run wizard. Walked through once per Smith install.
//
// Smith ships with no config (other than the auto-generated
// SMITH_SECRET_KEY that encrypts the DB). This wizard collects:
//   1. Smith identity (agent_slug, display name)
//   2. TEMPER connection (URL + API key + live probe)
//   3. LLM provider + model + key + live probe
//   4. Optional bearer secret (auto-generated; shown once)
//   5. Optional consolidate schedule / recall verbosity
//
// Submit writes settings (encrypted for secrets) + marks installed,
// then redirects to /chat with the bearer token in the URL hash so
// the next page-load picks it up into sessionStorage.

const SetupApp = () => {
  const [step, setStep] = React.useState(1);
  const [data, setData] = React.useState({
    agent_slug: 'smith',
    timezone: '',  // Step2Identity seeds via Intl detect on first render
    temper_base_url: 'http://127.0.0.1:18088',
    temper_api_key: '',
    llm_provider: 'deepseek',
    llm_model: '',
    llm_base_url: '',
    llm_api_key: '',
    bearer_secret: '',          // generated server-side on submit if empty
    consolidate_schedule_hours: 0,
    recall_log_level: 'quiet',
  });
  const update = (k, v) => setData(d => ({ ...d, [k]: v }));
  const [error, setError] = React.useState(null);

  const steps = [
    { n: 1, label: 'Welcome' },
    { n: 2, label: 'Identity' },
    { n: 3, label: 'TEMPER' },
    { n: 4, label: 'LLM' },
    { n: 5, label: 'Security & extras' },
    { n: 6, label: 'Finish' },
  ];

  return (
    <div className="smith-app" style={{ background: 'var(--paper)' }}>
      <header style={{ padding: '14px 24px', borderBottom: '1px solid var(--line)', background: 'var(--panel)', display: 'flex', alignItems: 'center', gap: 12 }}>
        <Avatar kind="smith" size="lg" />
        <div>
          <strong style={{ fontSize: 16 }}>Smith setup</strong>
          <div className="muted" style={{ fontSize: 12 }}>One-time configuration. Step {step} of {steps.length}.</div>
        </div>
      </header>

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <nav style={{ width: 200, background: 'var(--panel-2)', borderRight: '1px solid var(--line)', padding: '16px 0' }}>
          {steps.map(s => (
            <div key={s.n} className="row" style={{
              padding: '8px 18px', gap: 8, cursor: 'default',
              background: s.n === step ? 'var(--panel)' : 'transparent',
              color: s.n < step ? 'var(--good)' : s.n === step ? 'var(--ink)' : 'var(--ink-3)',
              fontSize: 13, fontWeight: s.n === step ? 600 : 400,
            }}>
              <span className="mono" style={{ minWidth: 14 }}>{s.n < step ? '✓' : s.n}</span>
              {s.label}
            </div>
          ))}
        </nav>

        <main className="scrl" style={{ flex: 1, padding: '28px 32px' }}>
          <div style={{ maxWidth: 560 }}>
            {error && <div style={{ marginBottom: 14, padding: '8px 12px', background:'var(--danger-soft)', border:'1px solid var(--danger)', color:'var(--danger)', borderRadius:6, fontSize: 12 }}>{error}</div>}

            {step === 1 && <Step1Welcome onNext={() => setStep(2)} />}
            {step === 2 && <Step2Identity data={data} update={update} onNext={() => setStep(3)} onBack={() => setStep(1)} />}
            {step === 3 && <Step3Temper data={data} update={update} onNext={() => setStep(4)} onBack={() => setStep(2)} onError={setError} />}
            {step === 4 && <Step4Llm data={data} update={update} onNext={() => setStep(5)} onBack={() => setStep(3)} onError={setError} />}
            {step === 5 && <Step5Security data={data} update={update} onNext={() => setStep(6)} onBack={() => setStep(4)} />}
            {step === 6 && <Step6Finish data={data} onBack={() => setStep(5)} onError={setError} />}
          </div>
        </main>
      </div>
    </div>
  );
};

// ----------------------- Step 1: welcome -----------------------------

const Step1Welcome = ({ onNext }) => (
  <>
    <h2 style={{ marginTop: 0 }}>Welcome to Smith</h2>
    <p style={{ color: 'var(--ink-2)', lineHeight: 1.55 }}>
      Smith is your personal AI agent. It connects to a TEMPER memory service for long-term recall, an LLM for chat, and external systems via MCP plugins.
    </p>
    <p style={{ color: 'var(--ink-2)', lineHeight: 1.55 }}>
      This wizard collects the configuration once and writes it to <code style={{ background:'var(--panel-2)', padding:'1px 5px', borderRadius:3 }}>.data/smith.db</code> (secrets encrypted at rest). You can edit any of it later from the Settings page.
    </p>
    <p className="muted" style={{ fontSize: 12.5, marginTop: 18 }}>
      You'll need: a TEMPER base URL + API key (from <code>/admin/integrate</code> on your TEMPER instance), and an LLM provider with API key.
    </p>
    <Actions next="Get started" onNext={onNext} />
  </>
);

// ----------------------- Step 2: identity ----------------------------

const Step2Identity = ({ data, update, onNext, onBack }) => {
  const slugOk = /^[a-z0-9][a-z0-9_-]*$/.test(data.agent_slug);
  const detectedTz = (typeof Intl !== 'undefined') ? Intl.DateTimeFormat().resolvedOptions().timeZone : 'UTC';
  // Seed timezone on first render — auto-detect is the sensible default
  // and reduces this step to zero clicks for most users.
  React.useEffect(() => {
    if (!data.timezone) update('timezone', detectedTz);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  let tzPreview;
  try {
    tzPreview = new Intl.DateTimeFormat('en-CA', {
      timeZone: data.timezone || detectedTz,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hour12: false,
      timeZoneName: 'shortOffset',
    }).format(new Date());
  } catch { tzPreview = '(invalid IANA name)'; }
  return (
    <>
      <h2 style={{ marginTop: 0 }}>Smith identity</h2>
      <p style={{ color: 'var(--ink-2)' }}>The slug becomes part of Smith's namespace in TEMPER (<code>agent:&lt;your-user&gt;/&lt;slug&gt;</code>). Lowercase letters, digits, underscore, dash. Default <code>smith</code> matches the README quickstart.</p>
      <Field label="Agent slug" hint="agent:me/<slug>">
        <input value={data.agent_slug} onChange={e => update('agent_slug', e.target.value)} style={input} placeholder="smith" />
        {data.agent_slug && !slugOk && <div style={{ color:'var(--danger)', fontSize: 11, marginTop: 4 }}>Lowercase alnum + _ -; must start alnum.</div>}
      </Field>
      <Field label="Timezone" hint={`auto-detected from your browser; change anytime in /settings`}>
        <input value={data.timezone || ''} onChange={e => update('timezone', e.target.value)}
               style={input} placeholder={detectedTz} list="setup-tz-suggestions" />
        <datalist id="setup-tz-suggestions">
          {['Asia/Shanghai','Asia/Hong_Kong','Asia/Tokyo','Asia/Singapore','Europe/London','America/Los_Angeles','America/New_York','UTC'].map(z =>
            <option key={z} value={z} />)}
        </datalist>
        <div className="muted" style={{ fontSize: 11, marginTop: 4, fontFamily: 'var(--mono)' }}>现在: {tzPreview}</div>
      </Field>
      <Actions next="Next" onNext={onNext} onBack={onBack} nextDisabled={!slugOk} />
    </>
  );
};

// ----------------------- Step 3: temper ------------------------------

const Step3Temper = ({ data, update, onNext, onBack, onError }) => {
  const [testing, setTesting] = React.useState(false);
  const [result, setResult] = React.useState(null);

  const doTest = async () => {
    setTesting(true);
    setResult(null);
    onError(null);
    try {
      const r = await fetch('/setup/test/temper', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base_url: data.temper_base_url, api_key: data.temper_api_key }),
      });
      const j = await r.json();
      setResult(j);
    } catch (e) {
      onError(e.message);
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Connect to TEMPER</h2>
      <p style={{ color: 'var(--ink-2)' }}>
        TEMPER is Smith's long-term memory. You need a running TEMPER instance and an API key
        (mint one at <code>{data.temper_base_url}/admin/integrate</code> with the agent slug <code>{data.agent_slug}</code>).
      </p>
      <Field label="Base URL">
        <input value={data.temper_base_url} onChange={e => update('temper_base_url', e.target.value)} style={input} placeholder="http://127.0.0.1:18088" />
      </Field>
      <Field label="API key" hint="Format: mk_...">
        <input type="password" value={data.temper_api_key} onChange={e => update('temper_api_key', e.target.value)} style={input} placeholder="mk_..." />
      </Field>

      <button className="btn" disabled={testing || !data.temper_api_key} onClick={doTest} style={{ marginTop: 4 }}>
        {testing ? 'Testing…' : 'Test connection'}
      </button>

      {result && (
        <div style={{ marginTop: 12, padding: '10px 12px', background:'var(--panel-2)', border:'1px solid var(--line)', borderRadius:6, fontSize: 12 }}>
          {result.ok
            ? <div style={{ color:'var(--good)' }}>✓ Authenticated as <strong>{result.email}</strong> (id: <code>{result.user_id?.slice(0, 8)}…</code>)</div>
            : <div style={{ color:'var(--danger)' }}>✗ {result.error}</div>}
        </div>
      )}

      <Actions next="Next" onNext={onNext} onBack={onBack} nextDisabled={!result?.ok} />
    </>
  );
};

// ----------------------- Step 4: LLM ---------------------------------

const Step4Llm = ({ data, update, onNext, onBack, onError }) => {
  const [testing, setTesting] = React.useState(false);
  const [result, setResult] = React.useState(null);

  const doTest = async () => {
    setTesting(true);
    setResult(null);
    onError(null);
    try {
      const r = await fetch('/setup/test/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          provider: data.llm_provider, model: data.llm_model,
          base_url: data.llm_base_url || null, api_key: data.llm_api_key,
        }),
      });
      const j = await r.json();
      setResult(j);
    } catch (e) {
      onError(e.message);
    } finally {
      setTesting(false);
    }
  };

  return (
    <>
      <h2 style={{ marginTop: 0 }}>LLM provider</h2>
      <p style={{ color: 'var(--ink-2)' }}>
        Smith uses pi-ai for chat. Built-in providers: <code>openai</code>, <code>anthropic</code>,
        <code>deepseek</code>, <code>google</code>. For corporate gateways (e.g. an OpenAI-compatible
        proxy), set <code>llm.base_url</code> and pick <code>openai</code> or <code>deepseek</code>
        as the protocol shim.
      </p>
      <Field label="Provider">
        <select value={data.llm_provider} onChange={e => update('llm_provider', e.target.value)} style={input}>
          <option value="openai">openai</option>
          <option value="anthropic">anthropic</option>
          <option value="deepseek">deepseek (OpenAI-compatible)</option>
          <option value="google">google (Gemini)</option>
        </select>
      </Field>
      <Field label="Base URL" hint="Optional. Set for custom / corporate proxies. Leave blank for the official provider URL.">
        <input value={data.llm_base_url} onChange={e => update('llm_base_url', e.target.value)} style={input} placeholder="https://your-internal-llm-gateway/v1" />
      </Field>
      <Field label="Model ID" hint="Provider-specific. Examples: claude-sonnet-4-20250514, gpt-4o, forti-k2, gemini-1.5-pro">
        <input value={data.llm_model} onChange={e => update('llm_model', e.target.value)} style={input} placeholder="forti-k2" />
      </Field>
      <Field label="API key">
        <input type="password" value={data.llm_api_key} onChange={e => update('llm_api_key', e.target.value)} style={input} placeholder="sk-... / mk_... / etc." />
      </Field>

      <button className="btn" disabled={testing || !data.llm_api_key || !data.llm_model} onClick={doTest} style={{ marginTop: 4 }}>
        {testing ? 'Testing chat completion…' : 'Test LLM (real call, costs tokens)'}
      </button>

      {result && (
        <div style={{ marginTop: 12, padding: '10px 12px', background:'var(--panel-2)', border:'1px solid var(--line)', borderRadius:6, fontSize: 12 }}>
          {result.ok
            ? <div style={{ color:'var(--good)' }}>
                ✓ {result.ms}ms · {result.tokens_used ?? '—'} tokens · reply: <code style={{ background:'var(--panel)', padding:'1px 4px', borderRadius:3 }}>{(result.reply ?? '').slice(0, 80)}</code>
              </div>
            : <div style={{ color:'var(--danger)' }}>✗ {result.error}</div>}
        </div>
      )}

      <Actions next="Next" onNext={onNext} onBack={onBack} nextDisabled={!result?.ok} />
    </>
  );
};

// ----------------------- Step 5: extras ------------------------------

const Step5Security = ({ data, update, onNext, onBack }) => (
  <>
    <h2 style={{ marginTop: 0 }}>Security & extras</h2>
    <p style={{ color: 'var(--ink-2)' }}>
      Smith binds to <code>127.0.0.1</code> by default so only your machine reaches it.
      If you'd ever expose Smith beyond localhost, set a bearer secret below — it gates
      <code> /chat</code>, <code>/conversations</code>, <code>/plugins</code> et al.
    </p>
    <Field label="Bearer secret" hint="Leave blank to skip — wizard will auto-generate one. Submit step shows it once for you to copy.">
      <input type="password" value={data.bearer_secret} onChange={e => update('bearer_secret', e.target.value)} style={input} placeholder="(auto-generate on submit if blank)" />
    </Field>

    <h3 style={{ marginTop: 24, fontSize: 14 }}>Consolidate schedule</h3>
    <p style={{ color: 'var(--ink-2)', fontSize: 12.5 }}>
      Smith can periodically call TEMPER's memory consolidate to dedup + cleanup
      its own namespace. <code>0</code> = disabled (default).
    </p>
    <Field label="Hours between consolidate runs">
      <input type="number" min="0" value={data.consolidate_schedule_hours}
             onChange={e => update('consolidate_schedule_hours', Number(e.target.value))} style={input} />
    </Field>

    <h3 style={{ marginTop: 24, fontSize: 14 }}>Recall log verbosity</h3>
    <Field label="SMITH_RECALL_LOG" hint="quiet = one line per turn; verbose = + per-hit details; full = + dump the whole recall block; dump = write to .data/recall/*.txt">
      <select value={data.recall_log_level} onChange={e => update('recall_log_level', e.target.value)} style={input}>
        <option value="quiet">quiet</option>
        <option value="verbose">verbose</option>
        <option value="full">full</option>
        <option value="dump">dump</option>
      </select>
    </Field>

    <Actions next="Review" onNext={onNext} onBack={onBack} />
  </>
);

// ----------------------- Step 6: finish ------------------------------

const Step6Finish = ({ data, onBack, onError }) => {
  const [busy, setBusy] = React.useState(false);
  const [done, setDone] = React.useState(null);

  const doSave = async () => {
    setBusy(true);
    onError(null);
    try {
      const r = await fetch('/setup/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        throw new Error(j.error || `HTTP ${r.status}`);
      }
      const j = await r.json();
      setDone(j);
      // Bearer in URL hash → main app's bootstrap reads it.
      const hash = j.bearer_secret ? `#secret=${encodeURIComponent(j.bearer_secret)}` : '';
      setTimeout(() => { location.href = `/chat${hash}`; }, 3000);
    } catch (e) {
      onError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (done) {
    return (
      <>
        <h2 style={{ marginTop: 0, color: 'var(--good)' }}>Setup complete</h2>
        <p style={{ color: 'var(--ink-2)' }}>Wrote {done.settings_written} settings to <code>.data/smith.db</code>. Redirecting to chat in 3s…</p>
        {done.bearer_secret && (
          <div style={{ marginTop: 14, padding: 14, background:'var(--warn-soft)', border:'1px solid var(--warn)', borderRadius:6 }}>
            <strong style={{ color:'var(--warn)' }}>One-time: your bearer token</strong>
            <div className="mono" style={{ marginTop: 8, padding: 8, background:'var(--panel)', borderRadius: 4, fontSize: 12, wordBreak:'break-all' }}>
              {done.bearer_secret}
            </div>
            <p style={{ fontSize: 12, color:'var(--ink-2)', marginTop: 8, marginBottom: 0 }}>
              The chat page picks it up automatically from the URL hash this time.
              For future sessions in fresh browsers, append <code>#secret=&lt;token&gt;</code> to the URL,
              or rotate from <code>/settings</code>.
            </p>
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <h2 style={{ marginTop: 0 }}>Ready to save</h2>
      <p style={{ color: 'var(--ink-2)' }}>Review and confirm. After save, Smith is ready — chat is at <code>/chat</code>.</p>

      <table style={{ width:'100%', fontSize: 12.5, borderCollapse:'collapse', marginTop: 12 }}>
        <tbody>
          <Row k="Agent slug" v={data.agent_slug} />
          <Row k="TEMPER URL" v={data.temper_base_url} />
          <Row k="TEMPER key" v={data.temper_api_key ? '(set)' : '—'} secret />
          <Row k="LLM" v={`${data.llm_provider}/${data.llm_model}`} />
          <Row k="LLM URL" v={data.llm_base_url || '(default)'} />
          <Row k="LLM key" v={data.llm_api_key ? '(set)' : '—'} secret />
          <Row k="Bearer" v={data.bearer_secret ? '(provided)' : '(auto-generate)'} secret />
          <Row k="Consolidate" v={data.consolidate_schedule_hours > 0 ? `every ${data.consolidate_schedule_hours}h` : 'disabled'} />
          <Row k="Recall log" v={data.recall_log_level} />
        </tbody>
      </table>

      <Actions
        back="Back"
        next={busy ? 'Saving…' : 'Save & finish'}
        onBack={onBack}
        onNext={doSave}
        nextDisabled={busy}
      />
    </>
  );
};

const Row = ({ k, v, secret }) => (
  <tr><td style={{ padding:'5px 0', color:'var(--ink-3)', width: 140 }}>{k}</td><td style={{ padding:'5px 0', fontFamily: secret ? 'var(--mono)' : 'inherit' }}>{v}</td></tr>
);

// ----------------------- shared atoms --------------------------------

const Actions = ({ next = 'Next', back = 'Back', onNext, onBack, nextDisabled }) => (
  <div className="row" style={{ marginTop: 28, gap: 8, justifyContent: 'flex-end' }}>
    {onBack && <button className="btn subtle" onClick={onBack}>{back}</button>}
    {onNext && <button className="btn primary" onClick={onNext} disabled={nextDisabled}>{next}</button>}
  </div>
);

const Field = ({ label, hint, children }) => (
  <div style={{ marginBottom: 14 }}>
    <label style={{ display:'block', fontSize: 12, color:'var(--ink-2)', marginBottom: 4, fontWeight: 500 }}>{label}</label>
    {children}
    {hint && <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{hint}</div>}
  </div>
);

const input = {
  width:'100%', padding:'7px 10px', border:'1px solid var(--line-strong)',
  borderRadius:5, fontSize: 13, background:'var(--panel)',
  fontFamily: 'inherit',
};

window.SetupApp = SetupApp;
