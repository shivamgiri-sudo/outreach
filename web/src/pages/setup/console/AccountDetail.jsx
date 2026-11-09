// One account in the super-admin console: figures, approve / suspend / reactivate, workspace requests, view as a login,
// export, delete (typed confirmation). Limits, grants and sending identity: the Manage dialog (AccountDialogs.jsx).
// Server: app/routes_console.py.
import { useState } from "react";
import { platform, startViewAs } from "../../../platformApi.js";
import { Modal, istDate } from "../common.jsx";
import { flagText } from "./ConsoleParts.jsx";
import UsageTable from "../UsageTable.jsx";

export function AskDialog({ row, kind, onClose, onDone }) {
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const reject = kind === "reject";
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      if (reject) await platform.reject(row.id, text); else if (kind === "suspend") await platform.suspend(row.id, text); else await platform.ask(row.id, text);
      onDone(reject ? `“${row.name}” rejected; the applicant was emailed.` : kind === "suspend" ? `“${row.name}” suspended: everyone signed out, sending held.`
        : `Asked “${row.name}” for details.`);
      onClose();
    } catch (err) { setError(err.message); }
    setBusy(false);
  };
  const title = reject ? `Reject ${row.name}` : kind === "suspend" ? `Suspend ${row.name}` : `Ask ${row.name} for details`;
  return (
    <Modal title={title} onClose={onClose} testId="console-text-dialog">
      <form onSubmit={submit} className="setup-form">
        <div className="field"><label htmlFor="cd-text">{reject ? "Reason (emailed to the applicant)" : kind === "suspend" ? "Reason (optional, for the change log)" : "What do you need from them? (emailed)"}</label>
          <textarea id="cd-text" data-autofocus rows={4} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} required={kind !== "suspend"} /></div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className={`btn${reject || kind === "suspend" ? " danger" : ""}`} disabled={busy} data-testid="console-text-submit">{title}</button>
        </div>
      </form>
    </Modal>
  );
}

export default function AccountDetail({ row, onClose, onDone, onManage, onText }) {
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const mcn = row.id === "mcn";
  const run = async (fn, message) => {
    setBusy(true); setError("");
    try { await fn(); if (message) onDone(message); } catch (err) { setError(err.message); }
    setBusy(false);
  };
  const figures = [["Status", row.status], ["Created", istDate(row.created_at) || "—"], ["Logins", row.members], ["Workspaces", (row.workspaces || []).length],
    ["Leads", row.leads], ["Sent today", row.sends_today], ["Sent in 7 days", row.sends_7d], ["Bounce rate", `${Math.round((row.bounce_rate || 0) * 100)}%`],
    ["Last activity", istDate(row.last_activity) || "—"], ["Flags", flagText(row) || "none"]];
  const requests = (row.workspaces || []).filter((w) => w.status === "requested");
  return (
    <Modal title={`Account: ${row.name}`} onClose={onClose} wide testId="account-detail">
      <dl className="row" style={{ flexWrap: "wrap", gap: "6px 22px" }}>
        {figures.map(([k, v]) => <div key={k} style={{ minWidth: 120 }}><dt className="hint">{k}</dt><dd data-testid={`fig-${k}`}><b>{String(v)}</b></dd></div>)}
      </dl>
      <p className="hint">Limits: {Object.entries(row.limits || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") || "none (unlimited)"} ·
        Integrations granted: {(row.integrations_granted || []).join(", ") || "none"}</p>
      {!mcn && row.auto_hold && <div className="setup-form" data-testid="detail-auto-hold" style={{ borderLeft: "4px solid #b91c1c", paddingLeft: 10 }}>
        <b>Sending held automatically</b> <span className="hint">{istDate(row.auto_hold.at)}</span>
        <p className="hint">{row.auto_hold.reason}</p>
        <button className="btn small" disabled={busy} onClick={() => run(() => platform.releaseHold(row.id), `Sending hold lifted for “${row.name}”.`)} data-testid="detail-release-hold">Release the hold</button></div>}
      {!mcn && <div className="setup-form" data-testid="detail-usage"><h3>Usage against limits</h3><UsageTable rows={row.usage} /></div>}
      {!mcn && <div className="row" style={{ gap: 8, flexWrap: "wrap", margin: "10px 0" }}>
        {row.status === "pending" && <button className="btn" disabled={busy} onClick={() => run(() => platform.approve(row.id), `“${row.name}” approved.`)}>Approve</button>}
        {row.status === "active" && <button className="btn danger" disabled={busy} onClick={() => onText("suspend", row)} data-testid="detail-suspend">Suspend</button>}
        {row.status === "suspended" && <button className="btn" disabled={busy} onClick={() => run(() => platform.reactivate(row.id), `“${row.name}” reactivated.`)} data-testid="detail-reactivate">Reactivate</button>}
        <button className="btn ghost" onClick={() => onManage(row)} data-testid="detail-manage">Edit limits, integrations & sending</button>
        <button className="btn ghost" disabled={busy} onClick={() => run(() => platform.exportAccount(row.id, row.name))} data-testid="detail-export">Export data (zip)</button>
      </div>}
      {requests.length > 0 && <div className="setup-form" data-testid="detail-ws-requests"><h3>Workspace requests</h3>
        {requests.map((w) => <div key={w.id} className="row" style={{ gap: 8, alignItems: "center" }}>
          <span style={{ flex: 1 }}>{w.name} <span className="hint">asked by {w.requested_by || "?"}</span></span>
          <button className="btn small" onClick={() => run(() => platform.approveWorkspace(w.id), `Workspace “${w.name}” approved.`)}>Approve</button>
          <button className="btn ghost small" onClick={() => run(() => platform.rejectWorkspace(w.id), `Workspace “${w.name}” rejected.`)}>Reject</button>
        </div>)}</div>}
      {!mcn && row.status === "active" && (row.logins || []).length > 0 && <div className="setup-form" data-testid="detail-logins"><h3>View as a login (read-only, 30 minutes)</h3>
        {row.logins.map((u) => <div key={u.email} className="row" style={{ gap: 8, alignItems: "center" }}>
          <span style={{ flex: 1 }}>{u.name} <span className="hint">{u.email} · {u.role}</span></span>
          <button className="btn ghost small" onClick={() => run(() => startViewAs(u.email))} aria-label={`View as ${u.email}`}>View as</button>
        </div>)}</div>}
      {!mcn && <div className="setup-form" data-testid="detail-delete" style={{ borderTop: "1px solid #fecaca", marginTop: 12 }}>
        <h3>Delete account</h3>
        <p className="hint">Erases its leads, messages, workspaces and logins for good. Export the data first. Type <b>{row.name}</b> to confirm.</p>
        <div className="row" style={{ gap: 8 }}>
          <input aria-label="Type the account name to delete it" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          <button className="btn danger" disabled={busy || confirm !== row.name}
            onClick={() => run(async () => { await platform.remove(row.id, confirm); onClose(); }, `“${row.name}” deleted.`)}>Delete account</button>
        </div>
      </div>}
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="dialog-actions"><button type="button" className="btn ghost" onClick={onClose}>Close</button></div>
    </Modal>
  );
}
