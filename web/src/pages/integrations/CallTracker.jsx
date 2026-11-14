import { useEffect, useState } from "react";
import { api, getWorkspace, isOwner } from "../../api.js";
import { istLabel } from "../../time.js";
import { CopyRow } from "./Fields.jsx";

// Integrations → DialHalo: one connection per workspace (owners: any workspace; a workspace admin: its own).
// Leads with a new call task go to DialHalo's phone dial queue; calls, outcomes and opt-outs come back by webhook.
const LABEL = (s) => String(s || "").replace(/_/g, " ");

function Status({ title, row }) {
  if (!row) return <span className="sub-inline">{title}: never</span>;
  return (
    <span className={`result ${row.ok ? "ok" : "bad"}`} data-testid={`ct-${title.toLowerCase().replace(/\s/g, "-")}`}>
      {row.ok ? "✓" : "✗"} {title} {istLabel(row.at)}{row.detail ? ` — ${row.detail}` : ""}
    </span>
  );
}

function MappingEditor({ mapping, outcomes, stages, onSave, onReset, busy }) {
  const [rows, setRows] = useState(() => Object.entries(mapping).map(([name, r]) => ({ name, ...r })));
  useEffect(() => setRows(Object.entries(mapping).map(([name, r]) => ({ name, ...r }))), [mapping]);
  const set = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = () => onSave(Object.fromEntries(rows.filter((r) => r.name.trim()).map(({ name, ...r }) => [name.trim().toLowerCase(), r])));
  return (
    <div className="card" data-testid="ct-mapping">
      <b>What each DialHalo outcome does here</b>
      <p className="sub">Outcome names as your callers pick them in DialHalo. Stages only move forward; Unsubscribed is never undone.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>DialHalo outcome</th><th>Logged as</th><th>Move stage to</th><th>Stop emails</th><th>Do not contact</th><th /></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><input aria-label="DialHalo outcome name" value={r.name} onChange={(e) => set(i, { name: e.target.value })} /></td>
                <td>
                  <select aria-label={`Logged as for ${r.name}`} value={r.outcome || ""} onChange={(e) => set(i, { outcome: e.target.value })}>
                    <option value="">connected / no answer</option>
                    {outcomes.map((o) => <option key={o} value={o}>{LABEL(o)}</option>)}
                  </select>
                </td>
                <td>
                  <select aria-label={`Stage for ${r.name}`} value={r.stage || ""} onChange={(e) => set(i, { stage: e.target.value })}>
                    <option value="">leave as it is</option>
                    {stages.map((s) => <option key={s} value={s}>{LABEL(s)}</option>)}
                  </select>
                </td>
                <td><input type="checkbox" aria-label={`Stop emails for ${r.name}`} checked={!!r.stop_sequence} onChange={(e) => set(i, { stop_sequence: e.target.checked })} /></td>
                <td><input type="checkbox" aria-label={`Do not contact for ${r.name}`} checked={!!r.opt_out} onChange={(e) => set(i, { opt_out: e.target.checked })} /></td>
                <td><button type="button" className="btn ghost danger" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row">
        <button type="button" className="btn ghost" onClick={() => setRows([...rows, { name: "", outcome: "", stage: "", stop_sequence: false, opt_out: false }])}>+ Add outcome</button>
        <button type="button" className="btn" disabled={busy} onClick={save} data-testid="ct-save-mapping">Save mapping</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={onReset}>Use the defaults</button>
      </div>
    </div>
  );
}

export default function CallTracker() {
  const [workspaces, setWorkspaces] = useState([]);
  const [ws, setWs] = useState(getWorkspace() || "main");
  const [card, setCard] = useState(null);
  const [form, setForm] = useState({ base_url: "", public_url: "", api_key: "", webhook_secret: "", enabled: true });
  const [msg, setMsg] = useState(null);
  const [owners, setOwners] = useState(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);

  const apply = (c) => {
    setCard(c);
    const conn = c.connection || {};
    setForm({ base_url: conn.base_url || "", public_url: conn.public_url || "", api_key: "", webhook_secret: "", enabled: conn.enabled !== false });
  };
  const load = () => api.calltracker(ws).then(apply).catch((e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { api.workspaces().then((w) => setWorkspaces(Array.isArray(w) ? w : [])).catch(() => {}); }, []);
  useEffect(() => { setOwners(null); setMsg(null); load(); }, [ws]);

  const run = async (fn, okText) => {
    setBusy(true);
    try {
      const out = await fn();
      if (out?.card) apply(out.card); else if (out?.workspace) apply(out);
      if (out?.owners) setOwners(out.owners);
      setMsg(out?.message ? { ok: out.ok !== false, text: out.message } : okText ? { ok: true, text: okText } : null);
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };
  const save = (extra = {}) => run(() => api.saveCalltracker({ workspace: ws, ...form, ...extra }), "Saved.");

  if (!card) return msg ? <div className="tip error">{msg.text}</div> : <p className="empty">Loading…</p>;
  const conn = card.connection;
  const q = card.queue;
  return (
    <div data-testid="ct-card">
      <p className="sub">
        Leads that get a call task (an employer replied yes, or someone added a call) go straight into the caller's phone dial queue in DialHalo.
        Calls, outcomes and AI summaries come back to the lead's timeline; opt-outs work both ways. Recordings and transcripts stay in DialHalo.
      </p>
      {workspaces.length > 1 && (
        <label className="row">Workspace{" "}
          <select value={ws} onChange={(e) => setWs(e.target.value)} data-testid="ct-workspace">
            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>
      )}
      {msg && <div className={`tip ${msg.ok ? "" : "error"}`} role="status" data-testid="ct-msg">{msg.text}</div>}
      <div className="card">
        <div className="row prov-head">
          <b>DialHalo</b>
          <span className={`pill ${conn ? (conn.enabled ? "ok" : "warn") : ""}`} data-testid="ct-status">{conn ? (conn.enabled ? "Connected" : "Paused") : "Not set up"}</span>
        </div>
        <ol className="sub">
          <li>In DialHalo: Settings → Integrations → API keys → "Fill in the MCN Outreach preset" → Create key, and paste it below.</li>
          <li>Save, then add a webhook in DialHalo with the receiver address below and the MCN Outreach events; paste its signing secret here.</li>
          <li>Press Test connection. Callers are matched by email; anyone else's leads are shared out by DialHalo's distribution.</li>
        </ol>
        <div className="grid g2">
          <label>DialHalo address (server to server)
            <input value={form.base_url} placeholder="https://calls.example.com or http://127.0.0.1:8081" data-testid="ct-base-url"
              onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
          </label>
          <label>Web address for links (https)
            <input value={form.public_url} placeholder="https://calls.example.com" data-testid="ct-public-url"
              onChange={(e) => setForm({ ...form, public_url: e.target.value })} />
          </label>
          <label>API key {conn?.api_key_set && <small className="sub-inline">saved ({conn.api_key_hint}); leave empty to keep</small>}
            <input type="password" autoComplete="off" value={form.api_key} placeholder="ctk_…" data-testid="ct-api-key"
              onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
          </label>
          <label>Webhook signing secret {conn?.webhook_secret_set && <small className="sub-inline">saved; leave empty to keep</small>}
            <input type="password" autoComplete="off" value={form.webhook_secret} placeholder="whsec_…" data-testid="ct-secret"
              onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })} />
          </label>
        </div>
        <label className="row"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Send leads and accept calls</label>
        <div className="row prov-actions">
          <button className="btn" disabled={busy} onClick={() => save()} data-testid="ct-save">Save</button>
          {conn && <button className="btn ghost" disabled={busy} onClick={() => run(() => api.testCalltracker(ws))} data-testid="ct-test">Test connection</button>}
          {conn && <button className="btn ghost danger" disabled={busy}
            onClick={() => window.confirm("Disconnect DialHalo for this workspace? The key, the secret and unsent pushes are removed.") && run(() => api.deleteCalltracker(ws), "Disconnected.")}>Disconnect</button>}
        </div>
        {conn && (
          <>
            <CopyRow label="Receiver address for the DialHalo webhook" value={card.receiver.public} testid="ct-receiver" />
            {isOwner() && <CopyRow label="Same server (DialHalo on this machine; needs WEBHOOK_ALLOW_PRIVATE_HOSTS there)" value={card.receiver.internal} />}
            <p className="sub">Events to tick in DialHalo: {card.events.join(", ")}.</p>
            <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
              <Status title="Last test" row={conn.last_test} />
              <Status title="Last push" row={conn.last_push} />
              <Status title="Last call event" row={conn.last_event} />
            </div>
          </>
        )}
      </div>

      {conn && (
        <div className="card" data-testid="ct-queue">
          <b>Sending to DialHalo</b>
          <p className="sub">{q.pending} waiting · {q.failed} failed · {q.done} sent recently. Failed sends are retried with back-off, then wait here.</p>
          {q.failed_rows.length > 0 && (
            <>
              <ul>{q.failed_rows.map((j) => <li key={j.id}>{j.company || j.lead_id} ({j.kind === "dnc" ? "do-not-call" : "lead"}): {j.error}</li>)}</ul>
              <button className="btn" disabled={busy} onClick={() => run(() => api.retryCalltracker(ws))} data-testid="ct-retry">Retry failed</button>
            </>
          )}
        </div>
      )}

      {owners && owners.some((o) => !o.matched) && (
        <div className="card" data-testid="ct-unmatched">
          <b>{owners.filter((o) => !o.matched).length} {owners.filter((o) => !o.matched).length === 1 ? "person has" : "people have"} no matching DialHalo member</b>
          <p className="sub">Leads owned by these people are not put in their own dial queue; DialHalo shares them out by its distribution instead. Add each email below as a member in DialHalo (Members → Add), then press Test connection again.</p>
          <ul>
            {owners.filter((o) => !o.matched).map((o) => <li key={o.email}><b>{o.name}</b> · {o.email} <span className="sub-inline">({o.reason || "no DialHalo member has this email"})</span></li>)}
          </ul>
          <button type="button" className="btn ghost" data-testid="ct-copy-unmatched"
            onClick={() => navigator.clipboard?.writeText(owners.filter((o) => !o.matched).map((o) => o.email).join("\n")).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500); }).catch(() => setMsg({ ok: false, text: "Copy was blocked; select the emails above and copy them." }))}>
            {copied ? "Copied" : "Copy the emails"}
          </button>
        </div>
      )}

      {owners && (
        <div className="card" data-testid="ct-owners">
          <b>Who gets the calls</b>
          <table>
            <thead><tr><th>Outreach person</th><th>Email</th><th>DialHalo member</th></tr></thead>
            <tbody>{owners.map((o) => <tr key={o.email}><td>{o.name}</td><td>{o.email}</td><td>{o.matched ? o.calltracker : "no match: DialHalo shares these out"}</td></tr>)}</tbody>
          </table>
        </div>
      )}

      {conn && (
        <MappingEditor mapping={conn.mapping} outcomes={card.outcomes} stages={card.stages} busy={busy}
          onSave={(mapping) => save({ mapping })} onReset={() => save({ reset_mapping: true })} />
      )}
    </div>
  );
}
