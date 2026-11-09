// Setup > Accounts dialogs: a new client account, and managing one (name, status, limits, platform integrations granted,
// its own sending identity, its workspaces and logins). Server: app/routes_accounts.py.
import { useState } from "react";
import { api } from "../../api.js";
import { Modal } from "./common.jsx";

export const LIMIT_LABEL = { seats: "Logins", workspaces: "Workspaces", daily_sends: "Emails a day", imports_per_day: "Imports a day",
  enrich_credits: "Lookup credits", ai_credits: "AI credits", linkedin_daily_connections_max: "LinkedIn requests a day (max)",
  linkedin_weekly_connections_max: "LinkedIn requests a week (max)", linkedin_daily_messages_max: "LinkedIn messages a day (max)" };
export const changed = () => window.dispatchEvent(new Event("accounts-changed"));

function Field({ id, label, children, hint }) {
  return <div className="field"><label htmlFor={id}>{label}</label>{children}{hint && <p className="hint">{hint}</p>}</div>;
}

export function useSave(action, onDone) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e) => {
    e?.preventDefault?.();
    setBusy(true); setError("");
    try { onDone(await action()); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  return { busy, error, submit };
}

function Buttons({ onClose, busy, label }) {
  return (
    <div className="dialog-actions">
      <button type="button" className="btn ghost" onClick={onClose}>Close</button>
      <button type="submit" className="btn" disabled={busy} data-testid="dialog-submit">{busy ? "Saving…" : label}</button>
    </div>
  );
}

export function NewAccount({ onClose, onDone }) {
  const [form, setForm] = useState({ name: "", status: "active" });
  const { busy, error, submit } = useSave(() => api.createAccount(form), (row) => { onDone(`Account “${row.name}” created.`); onClose(); });
  return (
    <Modal title="New client account" onClose={onClose} testId="account-new-dialog">
      <form onSubmit={submit} className="setup-form" autoComplete="off">
        <Field id="ac-name" label="Company name"><input id="ac-name" data-autofocus required maxLength={80} value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        <Field id="ac-status" label="Status"><select id="ac-status" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
          <option value="active">Active</option><option value="pending">Pending</option><option value="suspended">Suspended</option></select></Field>
        {error && <p className="field-error" role="alert">{error}</p>}
        <Buttons onClose={onClose} busy={busy} label="Create account" />
      </form>
    </Modal>
  );
}

export function EditAccount({ row, grantable, limitKeys = [], onClose, onDone }) {
  const mcn = row.id === "mcn";
  const keys = [...new Set([...Object.keys(LIMIT_LABEL), ...limitKeys])];     // + LinkedIn ceilings etc. when the server has them
  const [form, setForm] = useState({ name: row.name, status: row.status, notes: row.notes || "",
    limits: Object.fromEntries(keys.map((k) => [k, row.limits?.[k] ?? ""])), grants: row.integrations_granted || [] });
  const s = row.settings || {};
  const [ident, setIdent] = useState({ sender_name: s.sender_name || "", sender_company: s.sender_company || "", sender_address: s.sender_address || "",
    from_email: s.sending?.from_email || "", from_name: s.sending?.from_name || "", api_key: s.sending?.api_key || "", live_sending: !!s.live_sending });
  const [extra, setExtra] = useState({ ws: "", email: "", name: "", role: "admin", password: "", wsId: row.workspaces?.[0]?.id || "" });
  const save = useSave(() => api.updateAccount(row.id, { name: form.name, status: form.status, notes: form.notes, limits: form.limits,
    ...(mcn ? {} : { integrations_granted: form.grants }) }), () => onDone("Account saved."));
  const saveIdent = useSave(() => api.saveAccountSettings(row.id, { sender_name: ident.sender_name, sender_company: ident.sender_company,
    sender_address: ident.sender_address, live_sending: ident.live_sending,
    sending: { from_email: ident.from_email, from_name: ident.from_name, api_key: ident.api_key } }), () => onDone("Sending settings saved."));
  const addWs = useSave(() => api.createWorkspace(extra.ws, row.id), (w) => { setExtra({ ...extra, ws: "", wsId: w.id }); onDone(`Workspace “${w.name}” added.`); });
  const addLogin = useSave(() => api.addUser({ email: extra.email, name: extra.name, role: extra.role, password: extra.password,
    account_id: row.id, workspaces: extra.wsId ? [extra.wsId] : [] }), () => { setExtra({ ...extra, email: "", name: "", password: "" }); onDone("Login added."); });
  const toggle = (id) => setForm({ ...form, grants: form.grants.includes(id) ? form.grants.filter((g) => g !== id) : [...form.grants, id] });
  return (
    <Modal title={`Account: ${row.name}`} onClose={onClose} wide testId="account-edit-dialog">
      <form onSubmit={save.submit} className="setup-form" autoComplete="off">
        <Field id="ae-name" label="Company name"><input id="ae-name" data-autofocus maxLength={80} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
        {!mcn && <Field id="ae-status" label="Status" hint="Pending and suspended accounts cannot sign in or send.">
          <select id="ae-status" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
            <option value="active">Active</option><option value="pending">Pending</option><option value="suspended">Suspended</option></select></Field>}
        <fieldset className="field"><legend>Limits (blank = unlimited)</legend>
          <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>{keys.map((k) => [k, LIMIT_LABEL[k] || k.replace(/_/g, " ")]).map(([k, label]) => (
            <label key={k} style={{ minWidth: 130 }}>{label}<input type="number" min="0" value={form.limits[k]} aria-label={label}
              onChange={(e) => setForm({ ...form, limits: { ...form.limits, [k]: e.target.value } })} /></label>))}</div></fieldset>
        {!mcn && <fieldset className="field" data-testid="account-grants"><legend>Platform integrations this account may use</legend>
          <div className="row" style={{ flexWrap: "wrap", gap: 10 }}>{grantable.map((g) => (
            <label key={g.id}><input type="checkbox" checked={form.grants.includes(g.id)} onChange={() => toggle(g.id)} /> {g.title}</label>))}</div></fieldset>}
        <Field id="ae-notes" label="Notes"><input id="ae-notes" maxLength={500} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></Field>
        {save.error && <p className="field-error" role="alert">{save.error}</p>}
        <Buttons onClose={onClose} busy={save.busy} label="Save account" />
      </form>
      {!mcn && <form onSubmit={saveIdent.submit} className="setup-form" autoComplete="off" data-testid="account-identity">
        <h3>Sending identity</h3>
        <p className="hint">{row.email_hold || "Emails can go out for this account."}</p>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          {[["sender_name", "Sender name"], ["sender_company", "Company in the footer"], ["sender_address", "Postal address"],
            ["from_email", "From email (own domain)"], ["from_name", "From name"], ["api_key", "Resend API key"]].map(([k, label]) => (
            <label key={k} style={{ minWidth: 200 }}>{label}<input value={ident[k]} aria-label={label} onChange={(e) => setIdent({ ...ident, [k]: e.target.value })} /></label>))}
          <label><input type="checkbox" checked={ident.live_sending} onChange={(e) => setIdent({ ...ident, live_sending: e.target.checked })} /> Send for real</label>
        </div>
        {saveIdent.error && <p className="field-error" role="alert">{saveIdent.error}</p>}
        <div className="dialog-actions"><button type="submit" className="btn" disabled={saveIdent.busy}>Save sending settings</button></div>
      </form>}
      {!mcn && <div className="setup-form" data-testid="account-people">
        <h3>Workspaces and logins</h3>
        <p className="hint">{(row.workspaces || []).map((w) => `${w.name}${w.status !== "active" ? ` (${w.status})` : ""}`).join(", ") || "No workspace yet."}</p>
        <form className="row" onSubmit={addWs.submit} style={{ gap: 8 }}>
          <input aria-label="New workspace name" placeholder="Workspace name" maxLength={60} value={extra.ws} onChange={(e) => setExtra({ ...extra, ws: e.target.value })} />
          <button className="btn ghost" type="submit" disabled={!extra.ws.trim() || addWs.busy}>Add workspace</button>
        </form>
        {addWs.error && <p className="field-error" role="alert">{addWs.error}</p>}
        <form className="row" onSubmit={addLogin.submit} style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          <input aria-label="Login email" type="email" placeholder="Email" value={extra.email} onChange={(e) => setExtra({ ...extra, email: e.target.value })} />
          <input aria-label="Login name" placeholder="Name" value={extra.name} onChange={(e) => setExtra({ ...extra, name: e.target.value })} />
          <select aria-label="Login role" value={extra.role} onChange={(e) => setExtra({ ...extra, role: e.target.value })}>
            <option value="admin">Workspace admin</option><option value="caller">Caller</option><option value="viewer">Viewer</option></select>
          <select aria-label="Login workspace" value={extra.wsId} onChange={(e) => setExtra({ ...extra, wsId: e.target.value })}>
            {(row.workspaces || []).filter((w) => w.status === "active").map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
          <input aria-label="Login password" type="password" autoComplete="new-password" placeholder="Password (10+ characters)" value={extra.password}
            onChange={(e) => setExtra({ ...extra, password: e.target.value })} />
          <button className="btn ghost" type="submit" disabled={addLogin.busy || !extra.email || !extra.wsId}>Add login</button>
        </form>
        {addLogin.error && <p className="field-error" role="alert">{addLogin.error}</p>}
      </div>}
    </Modal>
  );
}

