// Setup > Connections: the basics (public address, webhook secret, privacy link) and one collapsible card per provider.
// Fields carry names that do not look like a sign-in form and keys are password fields with show/hide, so the browser
// never fills a saved email or password into a WhatsApp number or an API key.
import { useEffect, useState } from "react";
import { api, isOwner } from "../../api.js";
import { FieldHint, GuideButton, useTouched } from "../../GuidePanel.jsx";
import { fieldHelp, useGuides } from "../../guides.js";
import { useBrand } from "../../brand.js";
import Icon from "../../icons.jsx";

const STATUS = { configured: ["ok", "Connected"], incomplete: ["warn", "Missing details"], not_configured: ["", "Not set up"] };
const NO_FILL = { "data-lpignore": "true", "data-1p-ignore": "true", "data-bwignore": "true", "data-form-type": "other" };

function SecretInput({ id, name, value, placeholder, onChange, onBlur, describedBy }) {
  const [show, setShow] = useState(false);
  return (
    <div className="secret-wrap">
      <input id={id} name={name} type={show ? "text" : "password"} autoComplete="new-password" spellCheck={false} placeholder={placeholder}
        value={value} onChange={onChange} onBlur={onBlur} aria-describedby={describedBy} {...NO_FILL} />
      <button type="button" className="icon-btn reveal" aria-label={show ? "Hide value" : "Show value"} aria-pressed={show} onClick={() => setShow(!show)}>
        <Icon name={show ? "eyeOff" : "eye"} size={18} />
      </button>
    </div>
  );
}

function Connector({ item, onSaved }) {
  const [mode, setMode] = useState(item.mode || item.modes[0]);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testTo, setTestTo] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [pill, label] = STATUS[item.status];
  const { data: guides } = useGuides();
  const [touched, touch] = useTouched();
  const gkey = `integration:${item.id}`;
  const owner = isOwner();                     // connections are owner-only on the server

  const save = async (e) => {
    e?.preventDefault?.();
    setSaving(true); setError("");
    try { await api.saveIntegration(item.id, mode, values); setValues({}); onSaved(); }
    catch (err) { setError(err.message); }
    setSaving(false);
  };
  const test = async () => {
    setTesting(true); setTestResult(null);
    try { setTestResult(await api.testIntegration(item.id, testTo)); onSaved(); }
    catch (e) { setTestResult({ ok: false, message: e.message }); }
    setTesting(false);
  };

  return (
    <details className="card connector" data-testid={`connector-${item.id}`}>
      <summary>
        <span className="connector-title"><b>{item.title}</b><small>{item.why}</small></span>
        <span className={`pill ${pill}`}>{label}</span>
      </summary>
      <div className="connector-body">
        <GuideButton keys={gkey} testId={`setup-guide-${item.id}`} />
        <ol className="steps">{item.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        <form onSubmit={save} autoComplete="off" {...NO_FILL}>
          <fieldset className="ro-fieldset" disabled={!owner}>
            {item.modes.length > 1 && (
              <div className="row" role="radiogroup" aria-label={`${item.title}: how to connect`}>
                {item.modes.map((m) => (
                  <label key={m} className="check-row">
                    <input type="radio" name={`mode-${item.id}`} checked={mode === m} onChange={() => setMode(m)} />
                    {m === "api" ? "Connect directly (API)" : "Manual (CSV / copy-paste)"}
                  </label>
                ))}
              </div>
            )}
            {mode === "api" && item.fields.map((f) => {
              const id = `c-${item.id}-${f.key}`;
              const name = `cfg_${item.id}_${f.key}`.replace(/(user|mail|login|pass)/gi, "x");
              const help = fieldHelp(guides, gkey, f.key);
              const common = { id, name, value: values[f.key] || "", placeholder: item.values[f.key] || f.placeholder || "",
                onChange: (e) => setValues({ ...values, [f.key]: e.target.value }), onBlur: () => touch(f.key) };
              const describedBy = help ? `${id}-hint ${id}-problem` : undefined;
              return (
                <div key={f.key} className="field">
                  <label htmlFor={id}>{f.label}{f.required && <span className="req" aria-hidden="true"> *</span>}</label>
                  {f.secret ? <SecretInput {...common} describedBy={describedBy} />
                    : <input {...common} type="text" autoComplete="off" spellCheck={false} aria-describedby={describedBy} {...NO_FILL} />}
                  <FieldHint id={id} help={help} value={values[f.key]} touched={touched[f.key]} />
                </div>
              );
            })}
          </fieldset>
          {error && <p className="field-error" role="alert">{error}</p>}
          {owner && <div className="row"><button className="btn" disabled={saving}>{mode === "manual" ? "Use manual mode" : saving ? "Saving…" : "Save"}</button></div>}
        </form>
        {owner && (
          <div className="row test-row">
            {item.id === "sending_domain" && (
              <>
                <label htmlFor={`test-${item.id}`} className="sr-only">Send the test email to</label>
                <input id={`test-${item.id}`} name="test-recipient" type="text" inputMode="email" autoComplete="off" {...NO_FILL}
                  placeholder="Send the test email to (your address)" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
              </>
            )}
            <button className="btn ghost" disabled={testing} onClick={test}>{testing ? "Testing…" : "Test connection"}</button>
          </div>
        )}
        {testResult && <p className={`setup-status${testResult.ok ? "" : " bad"}`} role="status">{testResult.ok ? "Works: " : "Failed: "}{testResult.message}</p>}
      </div>
    </details>
  );
}

function Basics({ onSaved }) {
  const [cfg, setCfg] = useState(null);
  const product = useBrand().public.product_name;
  const [message, setMessage] = useState("");
  const { data: guides } = useGuides();
  const [touched, touch] = useTouched();
  useEffect(() => { api.settings().then(setCfg); }, []);
  if (!cfg) return null;
  const randomSecret = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(18));
    setCfg({ ...cfg, webhook_secret: Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("") });
  };
  const save = async (e) => {
    e.preventDefault();
    try {
      const saved = await api.saveSettings({
        public_base_url: cfg.public_base_url || "", webhook_secret: cfg.webhook_secret || "", confirm_bmi_limits: !!cfg.confirm_bmi_limits,
        privacy_url: cfg.privacy_url || "", retention_days_cold: Number(cfg.retention_days_cold) || 365,
      });
      setCfg(saved); setMessage("Saved."); onSaved();
    } catch (err) { setMessage(err.message); }
  };
  return (
    <details className="card connector basics" open={!cfg.public_base_url}>
      <summary><span className="connector-title"><b>The basics</b><small>Public web address, webhook secret, privacy link and cold-lead review.</small></span>
        <span className={`pill ${cfg.public_base_url ? "ok" : "warn"}`}>{cfg.public_base_url ? "Set" : "Needed"}</span></summary>
      <form className="connector-body setup-form" onSubmit={save} autoComplete="off" {...NO_FILL}>
        <div className="field"><label htmlFor="public-url">Your public web address</label>
          <input id="public-url" name="public-base-url" type="url" autoComplete="off" value={cfg.public_base_url || ""} placeholder="https://outreach.yourcompany.in"
            onChange={(e) => setCfg({ ...cfg, public_base_url: e.target.value })} onBlur={() => touch("public_base_url")} aria-describedby="public-url-hint public-url-problem" />
          <FieldHint id="public-url" help={fieldHelp(guides, "setup:public_url", "public_base_url")} value={cfg.public_base_url} touched={touched.public_base_url} />
          <GuideButton keys="setup:public_url" label="What is my public web address?" testId="basics-guide-url" />
          <p className="hint">Unsubscribe and demo links in emails point here, so it must open from outside your office. Not localhost.</p></div>
        <div className="field"><label htmlFor="webhook-secret">Secret for product activity events</label>
          <GuideButton keys="setup:webhook" label="What is this for?" testId="basics-guide-webhook" />
          <div className="row">
            <div className="grow"><SecretInput id="webhook-secret" name="activity-webhook-key" value={cfg.webhook_secret || ""} placeholder="Click Generate"
              onChange={(e) => setCfg({ ...cfg, webhook_secret: e.target.value })} /></div>
            <button type="button" className="btn ghost" onClick={randomSecret}>Generate</button>
          </div>
          <p className="hint">Give this to your tech team. The product sends <code>POST {cfg.public_base_url || "<your address>"}/api/activation/event</code> with header
            <code> X-Webhook-Secret</code> and body <code>{"{\"email\": \"…\", \"event\": \"role_published\"}"}</code>.</p></div>
        <label className="check-row">
          <input type="checkbox" checked={!!cfg.confirm_bmi_limits} onChange={(e) => setCfg({ ...cfg, confirm_bmi_limits: e.target.checked })} />
          The free limits (candidates, interviews, days) are set inside {product} itself
        </label>
        <div className="split-2">
          <div className="field"><label htmlFor="privacy-url">Privacy notice link (optional)</label>
            <input id="privacy-url" name="privacy-notice-url" type="url" autoComplete="off" value={cfg.privacy_url || ""} placeholder="https://yourcompany.com/privacy" onChange={(e) => setCfg({ ...cfg, privacy_url: e.target.value })} />
            <p className="hint">Added to the footer of every email (India's DPDP Act asks for a clear notice).</p></div>
          <div className="field"><label htmlFor="retention">Review cold leads after (days)</label>
            <input id="retention" name="cold-review-days" type="number" min={30} max={3650} value={cfg.retention_days_cold || 365}
              onChange={(e) => setCfg({ ...cfg, retention_days_cold: e.target.value })} />
            <p className="hint">Leads who never replied are listed for review after this long. Nothing is deleted automatically.</p></div>
        </div>
        <div className="row"><button className="btn">Save basics</button>{message && <span className="hint" role="status">{message}</span>}</div>
      </form>
    </details>
  );
}

export default function Connections({ items, onReload, onSetupChanged, go }) {
  const groups = [...new Set(items.map((i) => i.group))];
  const slug = (g) => `conn-${String(g).toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <>
      <div className="tip setup-tip">
        <span><b>Integrations page:</b> step-by-step setup for email, reply capture, bounces, WhatsApp, SMS, calls, calendars and meetings, plus a custom provider.</span>
        <button className="btn ghost" onClick={() => go?.("integrations")}>Open Integrations</button>
      </div>
      {isOwner() && <Basics onSaved={() => onSetupChanged?.()} />}
      {groups.length > 1 && (
        <nav className="jump" aria-label="Connection groups">
          {groups.map((g) => {
            const list = items.filter((i) => i.group === g);
            return <a key={g} href={`#${slug(g)}`} onClick={(e) => { e.preventDefault(); document.getElementById(slug(g))?.scrollIntoView({ block: "start" }); }}>
              {g} <span className="count">{list.filter((i) => i.status === "configured").length}/{list.length}</span></a>;
          })}
        </nav>
      )}
      {groups.map((g) => (
        <section key={g} className="conn-group" aria-labelledby={slug(g)}>
          <h3 id={slug(g)}>{g}</h3>
          {items.filter((i) => i.group === g).map((i) => <Connector key={i.id} item={i} onSaved={onReload} />)}
        </section>
      ))}
    </>
  );
}
