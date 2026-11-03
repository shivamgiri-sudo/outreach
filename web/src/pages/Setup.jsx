import { useEffect, useState } from "react";
import { api } from "../api.js";
import GoLive from "./GoLive.jsx";

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
      <button className="btn" style={{ marginTop: 10 }} onClick={save}>Save basics</button>
      {message && <span className="sub" style={{ marginLeft: 10 }}>{message}</span>}
    </div>
  );
}

export default function Setup({ onSetupChanged, go, tab: initialTab }) {
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
      </div>
      {tab === "checklist" && <GoLive embedded go={(page) => (page === "setup" ? setTab("connections") : go?.(page))} />}
      {tab === "connections" && (
        <>
          <div className="tip" style={{ marginBottom: 16 }}>
            <b>New: the Integrations page.</b> Step-by-step setup for email, reply capture, bounces, WhatsApp, SMS, calls,
            calendars and meetings, plus a custom option for any other provider.
            <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => go?.("integrations")}>Open Integrations</button></div>
          </div>
          <Basics onSaved={() => onSetupChanged?.()} />
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
