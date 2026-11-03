import { useEffect, useState } from "react";
import { api } from "../api.js";
import GoLive from "./GoLive.jsx";
import BackupRestore from "./BackupRestore.jsx";

const STATUS = { configured: ["ok", "Connected"], incomplete: ["warn", "Missing details"], not_configured: ["", "Not set up"] };

function Connector({ item, onSaved }) {
  const [mode, setMode] = useState(item.mode || item.modes[0]);
  const [values, setValues] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [testTo, setTestTo] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);
  const [pill, label] = STATUS[item.status];

  const save = async () => {
    setSaving(true); setError("");
    try { await api.saveIntegration(item.id, mode, values); setValues({}); onSaved(); }
    catch (e) { setError(e.message); }
    setSaving(false);
  };

  const test = async () => {
    setTesting(true); setTestResult(null);
    try { setTestResult(await api.testIntegration(item.id, testTo)); onSaved(); }
    catch (e) { setTestResult({ ok: false, message: e.message }); }
    setTesting(false);
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{item.title}</b><span className={`pill ${pill}`}>{label}</span>
      </div>
      <p className="sub" style={{ margin: "2px 0 8px" }}>{item.why}</p>
      <details open={item.status !== "configured"}>
        <summary>Set up in {item.steps.length} easy steps</summary>
        <ol className="steps">{item.steps.map((s) => <li key={s}>{s}</li>)}</ol>
        {item.modes.length > 1 && (
          <div className="row">
            {item.modes.map((m) => (
              <label key={m} className="row" style={{ fontWeight: 400, margin: 0 }}>
                <input type="radio" style={{ width: "auto" }} checked={mode === m} onChange={() => setMode(m)} />
                {m === "api" ? "Connect directly (API)" : "Manual (CSV / copy-paste)"}
              </label>
            ))}
          </div>
        )}
        {mode === "api" && item.fields.map((f) => (
          <div key={f.key}>
            <label>{f.label}{f.required && " *"}</label>
            <input type={f.secret ? "password" : "text"} placeholder={item.values[f.key] || f.placeholder || ""}
              value={values[f.key] || ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
          </div>
        ))}
        {error && <p style={{ color: "var(--hot)" }}>{error}</p>}
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" disabled={saving} onClick={save}>{mode === "manual" ? "Use manual mode" : "Save"}</button>
        </div>
      </details>
      <div className="row" style={{ marginTop: 10 }}>
        {item.id === "sending_domain" && (
          <input style={{ flex: 1, minWidth: 200 }} placeholder="Send the test email to (your address)" value={testTo} onChange={(e) => setTestTo(e.target.value)} />
        )}
        <button className="btn ghost" disabled={testing} onClick={test}>{testing ? "Testing…" : "Test connection"}</button>
      </div>
      {testResult && (
        <p style={{ margin: "8px 0 0", color: testResult.ok ? "var(--ok)" : "var(--hot)" }}>
          {testResult.ok ? "✓ " : "✗ "}{testResult.message}
        </p>
      )}
    </div>
  );
}

function Basics({ onSaved }) {
  const [cfg, setCfg] = useState(null);
  const [message, setMessage] = useState("");
  useEffect(() => { api.settings().then(setCfg); }, []);
  if (!cfg) return null;

  const randomSecret = () => {
    const bytes = crypto.getRandomValues(new Uint8Array(18));
    setCfg({ ...cfg, webhook_secret: Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("") });
  };
  const save = async () => {
    try {
      const saved = await api.saveSettings({
        public_base_url: cfg.public_base_url || "", webhook_secret: cfg.webhook_secret || "", confirm_bmi_limits: !!cfg.confirm_bmi_limits,
        privacy_url: cfg.privacy_url || "", retention_days_cold: Number(cfg.retention_days_cold) || 365,
      });
      setCfg(saved);
      setMessage("Saved.");
      onSaved();
    } catch (e) {
      setMessage(e.message);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <b>The basics</b>
      <p className="sub" style={{ margin: "2px 0 8px" }}>Three things every live launch needs.</p>
      <label>Your public web address</label>
      <input value={cfg.public_base_url || ""} placeholder="https://outreach.yourcompany.in" onChange={(e) => setCfg({ ...cfg, public_base_url: e.target.value })} />
      <p className="sub" style={{ margin: "4px 0" }}>Unsubscribe and demo links in emails point here, so it must open from outside your office. Not localhost.</p>
      <label>Secret for product activity events</label>
      <div className="row">
        <input style={{ flex: 1 }} value={cfg.webhook_secret || ""} placeholder="Click Generate" onChange={(e) => setCfg({ ...cfg, webhook_secret: e.target.value })} />
        <button className="btn ghost" onClick={randomSecret}>Generate</button>
      </div>
      <p className="sub" style={{ margin: "4px 0" }}>
        Give this to your tech team. The product sends <code>POST {cfg.public_base_url || "<your address>"}/api/activation/event</code> with header
        <code> X-Webhook-Secret</code> and body <code>{"{\"email\": \"…\", \"event\": \"role_published\"}"}</code>.
      </p>
      <label className="row" style={{ fontWeight: 400 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={!!cfg.confirm_bmi_limits} onChange={(e) => setCfg({ ...cfg, confirm_bmi_limits: e.target.checked })} />
        The free limits (candidates, interviews, days) are set inside BookMyInterview itself
      </label>
      <label htmlFor="privacy-url">Privacy notice link (optional)</label>
      <input id="privacy-url" value={cfg.privacy_url || ""} placeholder="https://bookmyinterview.ai/privacy" onChange={(e) => setCfg({ ...cfg, privacy_url: e.target.value })} />
      <p className="sub" style={{ margin: "4px 0" }}>Added to the footer of every email (India's DPDP Act asks for a clear notice).</p>
      <label htmlFor="retention">Review cold leads after (days)</label>
      <input id="retention" type="number" min={30} max={3650} style={{ maxWidth: 140 }} value={cfg.retention_days_cold || 365}
        onChange={(e) => setCfg({ ...cfg, retention_days_cold: e.target.value })} />
      <p className="sub" style={{ margin: "4px 0" }}>Leads who never replied are listed for review after this long. Nothing is deleted automatically.</p>
      <button className="btn" style={{ marginTop: 10 }} onClick={save}>Save basics</button>
      {message && <span className="sub" style={{ marginLeft: 10 }}>{message}</span>}
    </div>
  );
}

const ROLE_HELP = { owner: "everything", caller: "leads, calls, meetings, tasks", viewer: "read only" };

// Team logins (owner only). Without OUTREACH_ADMIN_PASSWORD, the first login must be an owner and turns sign-in on.
function Users() {
  const [rows, setRows] = useState(null);
  const [form, setForm] = useState({ email: "", name: "", role: "caller", password: "" });
  const [message, setMessage] = useState("");
  const load = () => api.users().then(setRows).catch((e) => setMessage(e.message));
  useEffect(() => { load(); }, []);
  const run = async (fn, done) => {
    setMessage("");
    try { await fn(); setMessage(done); load(); return true; } catch (e) { setMessage(e.message); return false; }
  };
  const add = (e) => {
    e.preventDefault();
    run(() => api.addUser(form), `Login added for ${form.email}.`).then((ok) => ok && setForm({ email: "", name: "", role: "caller", password: "" }));
  };
  const reset = (email) => {
    const password = window.prompt(`New password for ${email} (at least 10 characters)`);
    if (password) run(() => api.changeUser(email, { password }), `Password changed for ${email}; their old sessions are signed out.`);
  };
  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="users-card">
      <b>Team logins</b>
      <p className="sub" style={{ margin: "2px 0 8px" }}>
        Give each person their own login. Owner: {ROLE_HELP.owner}. Caller: {ROLE_HELP.caller} (no settings, integrations or CSV exports). Viewer: {ROLE_HELP.viewer}.
      </p>
      {rows && rows.length > 0 && (
        <ul className="today-list">
          {rows.map((u) => (
            <li key={u.email} className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <span><b>{u.name}</b> <small>{u.email}</small></span>
              <span className="row">
                <select aria-label={`Role for ${u.email}`} value={u.role} onChange={(e) => run(() => api.changeUser(u.email, { role: e.target.value }), "Role changed.")}>
                  {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <button className="btn ghost" onClick={() => reset(u.email)}>Reset password</button>
                <button className="btn ghost danger" onClick={() => window.confirm(`Remove ${u.email}?`) && run(() => api.removeUser(u.email), "Login removed.")}>Remove</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <form className="inline-form" onSubmit={add}>
        <div className="split-2">
          <label>Email<input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
          <label>Name<input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        </div>
        <div className="split-2">
          <label>Role
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {Object.keys(ROLE_HELP).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </label>
          <label>Password (10+ characters)<input type="password" required minLength={10} autoComplete="new-password" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
        </div>
        <button className="btn" type="submit">Add login</button>
      </form>
      {message && <p className="sub" role="status">{message}</p>}
    </div>
  );
}

export default function Setup({ onSetupChanged, go, tab: initialTab, role = "owner" }) {
  const [items, setItems] = useState([]);
  const [tab, setTab] = useState(initialTab || "checklist");
  const load = () => api.integrations().then((list) => { setItems(list); onSetupChanged?.(); });
  useEffect(() => { load(); }, []);
  const done = items.filter((i) => i.status === "configured").length;
  const groups = [...new Set(items.map((i) => i.group))];

  return (
    <>
      <h2>Setup</h2>
      <p className="sub">{done} of {items.length} connected. Everything works without these; connect each one when you're ready. Keys are stored on your server and shown masked.</p>
      <div className="tabs" role="tablist" aria-label="Setup sections">
        <button role="tab" aria-selected={tab === "checklist"} className={tab === "checklist" ? "on" : ""} onClick={() => setTab("checklist")}>Go-live checklist</button>
        <button role="tab" aria-selected={tab === "connections"} className={tab === "connections" ? "on" : ""} onClick={() => setTab("connections")}>Connections</button>
        <button role="tab" aria-selected={tab === "backup"} className={tab === "backup" ? "on" : ""} onClick={() => setTab("backup")}>Backup &amp; restore</button>
      </div>
      {tab === "checklist" && <GoLive embedded go={(page) => (page === "setup" ? setTab("connections") : go?.(page))} />}
      {tab === "backup" && <BackupRestore />}
      {tab === "connections" && (
        <>
          <div className="tip" style={{ marginBottom: 16 }}>
            <b>New: the Integrations page.</b> Step-by-step setup for email, reply capture, bounces, WhatsApp, SMS, calls,
            calendars and meetings, plus a custom option for any other provider.
            <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => go?.("integrations")}>Open Integrations</button></div>
          </div>
          {role === "owner" && <Basics onSaved={() => onSetupChanged?.()} />}
          {role === "owner" && <Users />}
          {groups.map((g) => (
            <div key={g}>
              <h3>{g}</h3>
              {items.filter((i) => i.group === g).map((i) => <Connector key={i.id} item={i} onSaved={load} />)}
            </div>
          ))}
        </>
      )}
    </>
  );
}
