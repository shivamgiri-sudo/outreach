// Setup > Usage & limits (a client account's admins): usage against every limit, the account's own WhatsApp provider
// (keys are never shown back) and who hears about its leads. Server: app/routes_account_usage.py.
import { useEffect, useState } from "react";
import { platform } from "../../platformApi.js";
import { Status } from "./common.jsx";
import UsageTable from "./UsageTable.jsx";

function WhatsAppCard() {
  const [data, setData] = useState(null);
  const [pid, setPid] = useState("");
  const [values, setValues] = useState({});
  const [message, setMessage] = useState(null);
  const load = () => platform.whatsapp().then((d) => { setData(d); setPid(d.provider || d.providers?.[0]?.id || ""); setValues(d.values || {}); })
    .catch((e) => setMessage({ text: e.message, error: true }));
  useEffect(() => { load(); }, []);
  const provider = data?.providers?.find((p) => p.id === pid);
  const save = async (e, live) => {
    e.preventDefault();
    try { const d = await platform.saveWhatsapp({ provider: pid, values, ...(live === undefined ? {} : { live }) }); setData(d); setValues(d.values || {}); setMessage({ text: "WhatsApp provider saved." }); }
    catch (err) { setMessage({ text: err.message, error: true }); }
  };
  const remove = () => platform.removeWhatsapp().then((d) => { setData(d); setValues({}); setMessage({ text: "WhatsApp provider removed." }); }).catch((err) => setMessage({ text: err.message, error: true }));
  return (
    <section className="card setup-card" data-testid="account-whatsapp">
      <h3>Your WhatsApp provider</h3>
      <p className="hint">{data?.configured ? `Connected: ${data.provider}. ${data.live ? "Sending for real." : "Practice mode until you switch it on."}`
        : data?.granted ? "Using the platform's WhatsApp (granted by the platform admin)." : data?.hold || "Not connected."}</p>
      <Status message={message} />
      {data && <form className="setup-form" onSubmit={save} autoComplete="off">
        <div className="field"><label htmlFor="wa-provider">Provider</label>
          <select id="wa-provider" value={pid} onChange={(e) => { setPid(e.target.value); setValues({}); }}>
            {data.providers.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></div>
        {(provider?.fields || []).filter((f) => f.kind !== "headers" && f.kind !== "textarea").map((f) => (
          <div className="field" key={f.key}><label htmlFor={`wa-${f.key}`}>{f.label}{f.required ? " *" : ""}</label>
            <input id={`wa-${f.key}`} type={f.secret ? "password" : "text"} autoComplete="off" placeholder={f.placeholder || ""} value={values[f.key] || ""}
              onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
            {f.secret && data.values?.[f.key] && <p className="hint">Saved. Leave as is to keep it; it is never shown again.</p>}</div>))}
        <div className="dialog-actions">
          {data.configured && <button type="button" className="btn ghost danger" onClick={remove}>Remove</button>}
          {data.configured && <button type="button" className="btn ghost" onClick={(e) => save(e, !data.live)} data-testid="wa-live">{data.live ? "Back to practice" : "Send for real"}</button>}
          <button type="submit" className="btn" data-testid="wa-save">Save provider</button>
        </div>
      </form>}
    </section>
  );
}

function AlertsCard() {
  const [form, setForm] = useState({ names: "", email: true, url: "" });
  const [message, setMessage] = useState(null);
  const save = async (e) => {
    e.preventDefault();
    try {
      await platform.saveAlerts({ alert_managers: form.names.split(",").map((n) => n.trim()).filter(Boolean), alerts_email: form.email, public_base_url: form.url.trim() });
      setMessage({ text: "Saved." });
    } catch (err) { setMessage({ text: err.message, error: true }); }
  };
  return (
    <section className="card setup-card" data-testid="account-alerts">
      <h3>Alerts and links</h3>
      <p className="hint">Your leads' replies, yes answers and response-time digests reach your own team only. Managers also hear about yes answers and meetings.</p>
      <Status message={message} />
      <form className="setup-form" onSubmit={save}>
        <div className="field"><label htmlFor="al-mgr">Managers (names of your logins, comma separated)</label>
          <input id="al-mgr" value={form.names} onChange={(e) => setForm({ ...form, names: e.target.value })} /></div>
        <div className="field"><label htmlFor="al-url">Public address for links in emails (optional)</label>
          <input id="al-url" placeholder="https://crm.yourcompany.com" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} /></div>
        <label><input type="checkbox" checked={form.email} onChange={(e) => setForm({ ...form, email: e.target.checked })} /> Also email alerts</label>
        <div className="dialog-actions"><button type="submit" className="btn" data-testid="alerts-save">Save</button></div>
      </form>
    </section>
  );
}

export default function AccountUsage() {
  const [data, setData] = useState(null);
  const [message, setMessage] = useState(null);
  useEffect(() => { platform.usage().then(setData).catch((e) => setMessage({ text: e.message, error: true })); }, []);
  return (
    <div className="setup-stack" data-testid="account-usage">
      <section className="card setup-card">
        <h3>Usage and limits</h3>
        <p className="hint">Limits are set by the platform admin. When one is reached the work stops with a message; ask them to raise it.</p>
        <Status message={message} />
        {data?.hold && <p className="hint" role="status">{data.hold}</p>}
        {!data ? <p className="hint">Loading…</p> : <UsageTable rows={data.limits} />}
      </section>
      <WhatsAppCard />
      <AlertsCard />
    </div>
  );
}
