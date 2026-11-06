import { useEffect, useState } from "react";
import { api } from "../api.js";
import GoLive from "./GoLive.jsx";
import BackupRestore from "./BackupRestore.jsx";
import { useConfirm, usePrompt } from "../Dialog.jsx";
import ScoringCard from "./ScoringCard.jsx";
import AuditLog from "./AuditLog.jsx";
import SystemHealth from "./SystemHealth.jsx";
import { FieldHint, GuideButton, useTouched } from "../GuidePanel.jsx";
import { fieldHelp, useGuides } from "../guides.js";

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
  const { data: guides } = useGuides();
  const [touched, touch] = useTouched();
  const gkey = `integration:${item.id}`;

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
      <GuideButton keys={gkey} testId={`setup-guide-${item.id}`} />
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
        {mode === "api" && item.fields.map((f) => {
          const id = `c-${item.id}-${f.key}`;
          const help = fieldHelp(guides, gkey, f.key);
          return (
            <div key={f.key}>
              <label htmlFor={id}>{f.label}{f.required && " *"}</label>
              <input id={id} type={f.secret ? "password" : "text"} autoComplete="off" placeholder={item.values[f.key] || f.placeholder || ""}
                value={values[f.key] || ""} onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} onBlur={() => touch(f.key)}
                aria-describedby={help ? `${id}-hint ${id}-problem` : undefined} />
              <FieldHint id={id} help={help} value={values[f.key]} touched={touched[f.key]} />
            </div>
          );
        })}
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
  const { data: guides } = useGuides();
  const [touched, touch] = useTouched();
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
      <label htmlFor="public-url">Your public web address</label>
      <input id="public-url" value={cfg.public_base_url || ""} placeholder="https://outreach.yourcompany.in" onChange={(e) => setCfg({ ...cfg, public_base_url: e.target.value })}
        onBlur={() => touch("public_base_url")} aria-describedby="public-url-hint public-url-problem" />
      <FieldHint id="public-url" help={fieldHelp(guides, "setup:public_url", "public_base_url")} value={cfg.public_base_url} touched={touched.public_base_url} />
      <GuideButton keys="setup:public_url" label="What is my public web address?" testId="basics-guide-url" />
      <p className="sub" style={{ margin: "4px 0" }}>Unsubscribe and demo links in emails point here, so it must open from outside your office. Not localhost.</p>
      <label htmlFor="webhook-secret">Secret for product activity events</label>
      <GuideButton keys="setup:webhook" label="What is this for?" testId="basics-guide-webhook" />
      <div className="row">
        <input id="webhook-secret" style={{ flex: 1 }} value={cfg.webhook_secret || ""} placeholder="Click Generate" onChange={(e) => setCfg({ ...cfg, webhook_secret: e.target.value })} />
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

// Workspaces (owner only): one per client, so their leads, lists and numbers stay apart.
function Workspaces({ onChanged }) {
  const [rows, setRows] = useState([]);
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const load = () => api.workspaces().then((w) => { setRows(w); onChanged?.(w); }).catch((e) => setMessage(e.message));
  useEffect(() => { load(); }, []);
  const run = (fn, done) => fn().then(() => { setMessage(done); load(); }).catch((e) => setMessage(e.message));
  const confirm = useConfirm();
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <b>Workspaces</b> <GuideButton keys="app:workspaces" label="How do workspaces work?" testId="workspaces-guide" />
      <p className="sub">One workspace per client. Pick the workspace in the left menu: imports go into it, and Leads, Lists and Intelligence show only its leads.
        A contact can sit in only one workspace, so two clients never contact the same person twice from here. Move leads from the Leads page (select, then “Move to workspace”).</p>
      <ul className="today-list">{rows.map((w) => (
        <li key={w.id} className="row" style={{ justifyContent: "space-between" }}><span><b>{w.name}</b> <small>{w.leads} leads{w.created_by ? ` · made by ${w.created_by}` : ""}</small></span>
          {w.id !== "main" && <button className="btn ghost danger" onClick={async () => (await confirm({ message: `Remove workspace ${w.name}? It must be empty.`, confirmLabel: "Remove", danger: true })) && run(() => api.deleteWorkspace(w.id), "Workspace removed.")}>Remove</button>}</li>))}</ul>
      <form className="row" onSubmit={(e) => { e.preventDefault(); run(() => api.createWorkspace(name), `Workspace “${name}” added.`).then(() => setName("")); }}>
        <input aria-label="New workspace name" placeholder="Client name" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        <button className="btn" disabled={!name.trim()}>Add workspace</button>
      </form>
      {message && <p className="sub" role="status">{message}</p>}
    </div>
  );
}

// Team logins (owner only). Without OUTREACH_ADMIN_PASSWORD, the first login must be an owner and turns sign-in on.
function Users() {
  const [rows, setRows] = useState(null);
  const [form, setForm] = useState({ email: "", name: "", role: "caller", password: "" });
  const [message, setMessage] = useState("");
  const [spaces, setSpaces] = useState([]);
  const [isSuper, setIsSuper] = useState(true);
  useEffect(() => { api.workspaces().then(setSpaces).catch(() => {}); api.me().then((m) => setIsSuper(m.super_admin !== false)).catch(() => {}); }, []);
  const toggleSpace = (u, id, on) => {
    const next = on ? [...new Set([...(u.workspaces || []), id])] : (u.workspaces || []).filter((x) => x !== id);
    run(() => api.changeUser(u.email, { workspaces: next }), next.length ? `${u.name} now sees only: ${next.map((x) => spaces.find((w) => w.id === x)?.name || x).join(", ")}.` : `${u.name} sees every workspace.`);
  };
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
  const confirm = useConfirm();
  const ask = usePrompt();
  const reset = async (email) => {
    const password = await ask(`New password for ${email} (at least 10 characters)`);
    if (password) run(() => api.changeUser(email, { password }), `Password changed for ${email}; their old sessions are signed out.`);
  };
  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="users-card">
      <b>Team logins</b> <GuideButton keys={["app:team_logins", "setup:login"]} label="How do I add my team?" testId="users-guide" />
      <p className="sub" style={{ margin: "2px 0 8px" }}>
        Give each person their own login. Owner (full access to all data and settings; only the super admin adds or removes owners): {ROLE_HELP.owner}. Caller: {ROLE_HELP.caller} (no settings, integrations or CSV exports). Viewer: {ROLE_HELP.viewer}.
      </p>
      {rows && rows.length > 0 && (
        <ul className="today-list">
          {rows.map((u) => (
            <li key={u.email} className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
              <span><b>{u.name}</b> <small>{u.email}</small>{u.super_admin && <span className="pill ok" style={{ marginLeft: 6 }}>Super admin</span>}</span>
              <span className="row">
                <select aria-label={`Role for ${u.email}`} value={u.role} disabled={u.super_admin || (!isSuper && u.role === "owner")}
                  onChange={(e) => run(() => api.changeUser(u.email, { role: e.target.value }), "Role changed.")}>
                  {Object.keys(ROLE_HELP).filter((r) => isSuper || r !== "owner" || u.role === "owner").map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                {u.role === "caller" && <label className="check-row"><input type="checkbox" checked={u.can_edit_contacts !== false}
                  onChange={(e) => run(() => api.changeUser(u.email, { can_edit_contacts: e.target.checked }), e.target.checked ? "May edit contact details." : "Can no longer edit contact details.")} /> Can edit contacts</label>}
                {u.role !== "owner" && spaces.length > 1 && <span className="row" title="Tick to limit this login to these workspaces; none ticked = all">
                  <small>Workspaces:</small>{spaces.map((w) => <label key={w.id} className="check-row"><input type="checkbox" checked={(u.workspaces || []).includes(w.id)} onChange={(e) => toggleSpace(u, w.id, e.target.checked)} /> {w.name}</label>)}</span>}
                <button className="btn ghost" onClick={() => reset(u.email)}>Reset password</button>
                {!u.super_admin && (isSuper || u.role !== "owner") && <button className="btn ghost danger" onClick={async () => (await confirm({ message: `Remove ${u.email}?`, confirmLabel: "Remove", danger: true })) && run(() => api.removeUser(u.email), "Login removed.")}>Remove</button>}
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
              {Object.keys(ROLE_HELP).filter((r) => isSuper || r !== "owner").map((r) => <option key={r} value={r}>{r}</option>)}
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
  const [tab, setTab] = useState(initialTab || (/[?&]tab=([a-z]+)/.exec(window.location.hash) || [])[1] || "checklist");
  const load = () => api.integrations().then((list) => { setItems(list); onSetupChanged?.(); });
  useEffect(() => { load(); }, []);
  const done = items.filter((i) => i.status === "configured").length;
  const groups = [...new Set(items.map((i) => i.group))];

  return (
    <>
      <h2>Setup</h2>
      <p className="sub">{done} of {items.length} connected. Everything works without these; connect each one when you're ready. Keys are stored on your server and shown masked.</p>
      <div className="tabs scroll" role="tablist" aria-label="Setup sections">
        <button role="tab" aria-selected={tab === "checklist"} className={tab === "checklist" ? "on" : ""} onClick={() => setTab("checklist")}>Go-live checklist</button>
        <button role="tab" aria-selected={tab === "connections"} className={tab === "connections" ? "on" : ""} onClick={() => setTab("connections")}>Connections</button>
        {role === "owner" && <button role="tab" aria-selected={tab === "team"} className={tab === "team" ? "on" : ""} onClick={() => setTab("team")}>Team &amp; workspaces</button>}
        <button role="tab" aria-selected={tab === "backup"} className={tab === "backup" ? "on" : ""} onClick={() => setTab("backup")}>Backup &amp; restore</button>
        {role === "owner" && <button role="tab" aria-selected={tab === "audit"} className={tab === "audit" ? "on" : ""} onClick={() => setTab("audit")}>Audit</button>}
        {role === "owner" && <button role="tab" aria-selected={tab === "health"} className={tab === "health" ? "on" : ""} onClick={() => setTab("health")}>System health</button>}
      </div>
      {tab === "checklist" && <GoLive embedded go={(page) => (page === "setup" ? setTab("connections") : go?.(page))} />}
      {tab === "backup" && <><p><GuideButton keys="app:backup" label="How do backups work?" testId="backup-guide" /></p><BackupRestore /></>}
      {tab === "audit" && role === "owner" && <AuditLog />}
      {tab === "health" && role === "owner" && <SystemHealth />}
      {tab === "team" && role === "owner" && <><Workspaces /><Users /><ScoringCard /></>}
      {tab === "connections" && (
        <>
          <div className="tip" style={{ marginBottom: 16 }}>
            <b>New: the Integrations page.</b> Step-by-step setup for email, reply capture, bounces, WhatsApp, SMS, calls,
            calendars and meetings, plus a custom option for any other provider.
            <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => go?.("integrations")}>Open Integrations</button></div>
          </div>
          {role === "owner" && <Basics onSaved={() => onSetupChanged?.()} />}

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
