import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { sourceLabel, useSourceRegistry } from "../../labels.js";
import { istLabel } from "../../time.js";
import { CopyRow } from "./Fields.jsx";
import { useDialog } from "./Wizard.jsx";

const OUTCOMES = { new: "New lead", merged: "Added to an existing lead", refused: "Refused (opted out or erased)", replay: "Repeat ignored", error: "Problem" };
const lines = (v) => (Array.isArray(v) ? v.join("\n") : v || "");

function curlExample(url, token) {
  return `curl -X POST "${url}" \\\n  -H "Content-Type: application/json" \\\n  -H "X-Capture-Token: ${token || "YOUR_TOKEN"}" \\\n` +
    `  -d '{"name":"Asha Rao","email":"asha@example.com","phone":"9810012345","company":"Acme","utm_campaign":"diwali"}'`;
}

function ConnectorPanel({ connector: start, type, onClose, onChanged, go }) {
  const [c, setC] = useState(start);
  const [form, setForm] = useState(() => Object.fromEntries((type.fields || []).map((f) => [f.key, f.secret ? "" : (f.kind === "lines" ? lines(start[f.key]) : start[f.key] ?? "")])));
  const [meta, setMeta] = useState({ name: start.name, owner: start.default_owner || "", tags: (start.default_tags || []).join(", ") });
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState(null);
  const [sure, setSure] = useState(false);
  const panel = useRef(null);
  useDialog(panel, onClose);
  const token = c.token;                                   // only present right after create or rotate
  const url = c.urls.capture || c.urls.callback || c.urls.webhook || c.urls.sync;
  const run = async (fn) => {
    setError(""); setMessage("");
    try { return await fn(); } catch (e) { setError(e.message); return null; }
  };
  const save = () => run(async () => {
    const body = { name: meta.name, default_owner: meta.owner, default_tags: meta.tags };
    for (const f of type.fields || []) {
      if (f.secret) { if (form[f.key]) body[f.key] = form[f.key]; } else body[f.key] = f.kind === "lines" ? form[f.key] : form[f.key];
    }
    const saved = await api.saveCaptureConnector(c.id, body);
    setC({ ...saved, token }); setMessage("Saved."); onChanged();
  });
  const toggle = () => run(async () => { const saved = await api.saveCaptureConnector(c.id, { enabled: !c.enabled }); setC({ ...saved, token }); onChanged(); });
  const rotate = (which) => run(async () => { setC(await api.rotateCaptureToken(c.id, which)); setMessage("New token made. The old one stopped working."); onChanged(); });
  const test = () => run(async () => { setTestResult(await api.captureTest(c.id)); onChanged(); });
  const sync = () => run(async () => { const r = await api.captureSync(c.id); setMessage(`Checked the sheet: ${r.new} new, ${r.merged} added to existing leads, ${r.already_seen} already seen.`); onChanged(); });
  const remove = () => run(async () => { await api.deleteCaptureConnector(c.id); onChanged(); onClose(); });

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer wizard" role="dialog" aria-modal="true" aria-labelledby="capture-title" ref={panel} data-testid="capture-panel">
        <div className="drawer-head">
          <div><h2 id="capture-title" tabIndex={-1}>{c.name}</h2><span className="sub-inline">{type.title}</span></div>
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
        {error && <div className="tip error" role="alert">{error}</div>}
        {message && <div className="tip" role="status">{message}</div>}

        <h3>Set it up</h3>
        <p>{type.why}</p>
        <ol className="steps">{type.steps.map((s) => <li key={s}>{s}</li>)}</ol>

        {url && <CopyRow label={c.urls.callback ? "Callback URL" : c.urls.webhook ? "Webhook URL" : "Address to send leads to"} value={url} testid="capture-url" />}
        {c.urls.embed && <CopyRow label="Form snippet (paste into your page)" value={c.snippet} testid="capture-snippet" />}
        {c.form_key && c.mode === "form" && <CopyRow label="Public form key (safe to show on a web page)" value={c.form_key} />}
        {c.verify_token && <CopyRow label="Verify token" value={c.verify_token} testid="capture-verify" />}
        {c.mode !== "whatsapp" && c.mode !== "sheet" && (
          <div className="copy-block">
            <span className="copy-label">{c.mode === "google" ? "Key" : "Capture token (secret)"}</span>
            {token ? <CopyRow value={token} testid="capture-token" /> : <p className="sub" data-testid="capture-token-masked">{c.secrets.capture || "—"} · shown in full only once, when it is made. Make a new one if you lost it.</p>}
            <button type="button" className="btn ghost" onClick={() => rotate("capture")}>Make a new token (the old one stops working)</button>
            {c.mode === "meta" && <button type="button" className="btn ghost" onClick={() => rotate("verify")}>Make a new verify token</button>}
          </div>
        )}
        {c.mode === "generic" && url && (
          <details>
            <summary>Example (curl) and notes for Zapier, Make and n8n</summary>
            <pre className="curl" tabIndex={0}>{curlExample(url, token)}</pre>
            <ul className="field-help">
              <li>Zapier: action Webhooks by Zapier, POST, payload type JSON, add the header X-Capture-Token.</li>
              <li>Make: HTTP, Make a request, method POST, body type Raw JSON, add the header.</li>
              <li>n8n: HTTP Request node, method POST, send body as JSON, add the header.</li>
              <li>If the tool cannot send headers, add ?token=YOUR_TOKEN to the address.</li>
              <li>Send the same lead id twice and it is counted once (set an Idempotency-Key header or a lead_id field).</li>
            </ul>
          </details>
        )}

        <h3>Settings</h3>
        <div className="field"><label htmlFor="cap-name">Connector name</label><input id="cap-name" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} /></div>
        {(type.fields || []).map((f) => (
          <div className="field" key={f.key}>
            {f.kind === "bool" ? (
              <label className="row" style={{ fontWeight: 400 }}><input type="checkbox" checked={!!form[f.key]} onChange={(e) => setForm({ ...form, [f.key]: e.target.checked })} />{f.label}</label>
            ) : (
              <>
                <label htmlFor={`cap-${f.key}`}>{f.label}</label>
                {f.kind === "lines"
                  ? <textarea id={`cap-${f.key}`} rows={3} value={form[f.key]} placeholder={f.placeholder} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
                  : <input id={`cap-${f.key}`} type={f.secret ? "password" : "text"} autoComplete="off" value={form[f.key]}
                      placeholder={f.secret && c.secrets[f.key] ? `${c.secrets[f.key]} (saved; leave empty to keep)` : f.placeholder || ""}
                      onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />}
              </>
            )}
          </div>
        ))}
        <div className="split-2">
          <div className="field"><label htmlFor="cap-owner">Who gets the call task</label><input id="cap-owner" value={meta.owner} placeholder="Leave empty for the default caller" onChange={(e) => setMeta({ ...meta, owner: e.target.value })} /></div>
          <div className="field"><label htmlFor="cap-tags">Tags for these leads</label><input id="cap-tags" value={meta.tags} placeholder="diwali, ads" onChange={(e) => setMeta({ ...meta, tags: e.target.value })} /></div>
        </div>
        <p className="field-help">A new inbound lead gets a high-priority “Call now” task. Nothing is e-mailed or WhatsApped automatically.</p>
        <button className="btn" onClick={save}>Save settings</button>

        <h3>Try it</h3>
        <div className="row">
          <button className="btn cta" onClick={test} data-testid="send-test">Send a test lead</button>
          {c.mode === "sheet" && <button className="btn" onClick={sync}>Sync now</button>}
          <button className="btn ghost" onClick={toggle}>{c.enabled ? "Switch off" : "Switch on"}</button>
        </div>
        {testResult && (
          <div className={`tip ${testResult.ok ? "" : "error"}`} role="status" data-testid="test-result">
            {testResult.ok
              ? <>Test lead arrived ({OUTCOMES[testResult.outcome] || testResult.outcome}). It is marked as a test and not counted in your numbers. <button className="linkish" onClick={() => { onClose(); go("leads"); }}>Open Leads</button> · <button className="linkish" onClick={() => run(async () => { await api.removeTestLeads(c.id); setTestResult(null); setMessage("Test leads removed."); })}>Remove test leads</button></>
              : `The test did not work: ${testResult.error || testResult.reason || testResult.outcome}`}
          </div>
        )}

        <h3>Remove</h3>
        {!sure ? <button className="btn ghost danger" onClick={() => setSure(true)}>Delete this connector</button> : (
          <div className="row"><span>Its address stops working at once. Leads already captured stay.</span>
            <button className="btn danger" onClick={remove}>Yes, delete it</button> <button className="btn ghost" onClick={() => setSure(false)}>Keep it</button></div>
        )}
      </div>
    </>
  );
}

function AddConnector({ types, existing, onCreated, onClose }) {
  const [kind, setKind] = useState("generic");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const panel = useRef(null);
  useDialog(panel, onClose);
  const chosen = types.find((t) => t.id === kind);
  const create = async () => {
    try { onCreated(await api.addCaptureConnector({ type: kind, name: name.trim() || chosen.title })); } catch (e) { setError(e.message); }
  };
  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer wizard" role="dialog" aria-modal="true" aria-labelledby="add-title" ref={panel}>
        <div className="drawer-head"><h2 id="add-title" tabIndex={-1}>Add a lead source</h2><button className="btn ghost" onClick={onClose}>Close</button></div>
        {error && <div className="tip error" role="alert">{error}</div>}
        <fieldset className="type-pick">
          <legend>What do you want to connect?</legend>
          {types.map((t) => (
            <label key={t.id} className="type-option">
              <input type="radio" name="capture-type" checked={kind === t.id} onChange={() => setKind(t.id)} />
              <span><b>{t.title}</b><small>{t.why}</small></span>
            </label>
          ))}
        </fieldset>
        <div className="field"><label htmlFor="new-cap-name">Connector name</label>
          <input id="new-cap-name" value={name} placeholder={chosen?.title} onChange={(e) => setName(e.target.value)} />
          <p className="field-help">Only for you: for example “Diwali Meta ads” or “Website contact form”.</p></div>
        <button className="btn cta" onClick={create}>Create connector</button>
      </div>
    </>
  );
}

export default function LeadCapture({ go }) {
  useSourceRegistry();
  const [cat, setCat] = useState(null);
  const [list, setList] = useState(null);
  const [log, setLog] = useState([]);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);
  const [adding, setAdding] = useState(false);
  const load = () => {
    api.captureConnectors().then(setList).catch((e) => setError(e.message));
    api.captureLog().then(setLog).catch(() => {});
  };
  useEffect(() => { api.captureCatalogue().then(setCat).catch((e) => setError(e.message)); load(); }, []);
  if (error) return <div className="tip error" role="alert">{error}</div>;
  if (!cat || !list) return <p className="empty">Loading…</p>;
  const typeOf = (id) => cat.types.find((t) => t.id === id) || cat.types[0];
  return (
    <section aria-labelledby="capture-heading">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h3 id="capture-heading" style={{ margin: 0 }}>Lead capture</h3>
          <p className="sub">Leads from ads, forms and other tools land in your lead list the moment they arrive, with where they came from. Nothing is sent to them automatically.</p></div>
        <button className="btn cta" onClick={() => setAdding(true)} data-testid="add-capture">Add connector</button>
      </div>
      {list.length === 0 && <div className="card"><p className="empty">No lead sources connected yet. Add Facebook / Instagram Lead Ads, Google Ads, a website form, or any tool that can send a webhook.</p></div>}
      <div className="prov-grid">
        {list.map((c) => (
          <div className="card prov-card" key={c.id} data-testid={`capture-card-${c.id}`}>
            <div className="row prov-head"><b>{c.name}</b>
              <span className={`pill ${!c.enabled ? "" : c.configured ? "ok" : "warn"}`}>{!c.enabled ? "Switched off" : c.configured ? "On" : "Needs details"}</span></div>
            <p className="sub">{typeOf(c.type).title} · tagged {sourceLabel(c.source_id)}</p>
            <small className="sub-inline">{c.counters?.total ? `${c.counters.new || 0} new, ${c.counters.merged || 0} added to existing leads, ${c.counters.refused || 0} refused` : "Nothing captured yet"}
              {c.last_capture_at ? ` · last ${istLabel(c.last_capture_at)}` : ""}</small>
            <div className="row prov-actions"><button className="btn" onClick={() => setOpen(c)}>Open</button></div>
          </div>
        ))}
      </div>
      <div className="card table-card" style={{ padding: 0, overflowX: "auto" }} data-testid="capture-log">
        <h3 style={{ margin: 16 }}>Recent captures</h3>
        {log.length === 0 ? <p className="empty">Nothing captured yet. Use “Send a test lead” on a connector to try it.</p> : (
          <table>
            <thead><tr><th>When</th><th>Connector</th><th>What happened</th><th>Detail</th></tr></thead>
            <tbody>{log.slice(0, 25).map((r) => (
              <tr key={r.id}><td data-label="When">{istLabel(r.at)}</td><td data-label="Connector">{r.connector}{r.is_test && <span className="pill warn" style={{ marginLeft: 6 }}>Test</span>}</td>
                <td data-label="What happened">{OUTCOMES[r.outcome] || r.outcome}</td><td data-label="Detail">{r.error || (r.platform_ids && Object.values(r.platform_ids).join(", ")) || ""}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {adding && <AddConnector types={cat.types} onClose={() => setAdding(false)} onCreated={(c) => { setAdding(false); setOpen(c); load(); }} />}
      {open && <ConnectorPanel key={open.id} connector={open} type={typeOf(open.type)} onClose={() => setOpen(null)} onChanged={load} go={go} />}
    </section>
  );
}
