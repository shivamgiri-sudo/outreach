// Super-admin console pieces for Setup > Accounts: the pending queue, the global "Stop all sending" switch and the
// filter bar. Server: app/routes_console.py.
import { useState } from "react";
import { platform } from "../../../platformApi.js";
import { istDate } from "../common.jsx";

export const flagText = (a) => (a.flags || []).join(", ");

export function PendingQueue({ rows, onAction, onDetails }) {
  const pending = rows.filter((a) => a.status === "pending");
  if (!pending.length) return null;
  return (
    <section className="card" data-testid="pending-queue" style={{ marginBottom: 14, borderLeft: "4px solid #d97706" }}>
      <h3>Waiting for approval ({pending.length})</h3>
      {pending.map((a) => (
        <div key={a.id} className="row" data-testid={`pending-${a.name}`} style={{ gap: 10, flexWrap: "wrap", alignItems: "center", padding: "8px 0", borderTop: "1px solid var(--line, #e2e8f0)" }}>
          <div style={{ flex: "1 1 260px" }}>
            <b>{a.name}</b> <span className="hint">{a.signup?.name} &lt;{a.signup?.email}&gt;{a.signup?.phone ? ` · ${a.signup.phone}` : ""}</span>
            <div className="hint">Signed up {istDate(a.signup?.at || a.created_at)}{flagText(a) && <> · <span className="pill tiny">{flagText(a)}</span></>}</div>
          </div>
          <button className="btn small" onClick={() => onAction("approve", a)} aria-label={`Approve ${a.name}`}>Approve</button>
          <button className="btn ghost small" onClick={() => onAction("ask", a)} aria-label={`Ask ${a.name} for details`}>Ask for details</button>
          <button className="btn ghost small" onClick={() => onAction("reject", a)} aria-label={`Reject ${a.name}`}>Reject</button>
          <button className="btn ghost small" onClick={() => onDetails(a)} aria-label={`Details ${a.name}`}>Details</button>
        </div>
      ))}
    </section>
  );
}

export function StopAllSwitch({ stop, onChanged }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const flip = async (on) => {
    if (on && !window.confirm("Stop ALL sending for every account (email, WhatsApp, LinkedIn, flows) right now?")) return;
    setBusy(true); setError("");
    try { await platform.stopSending(on, reason); setReason(""); onChanged(); window.dispatchEvent(new Event("platform-changed")); }
    catch (err) { setError(err.message); }
    setBusy(false);
  };
  return (
    <section className="card" data-testid="stop-all" style={{ marginBottom: 14, borderLeft: `4px solid ${stop ? "#b91c1c" : "#16a34a"}` }}>
      <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ flex: "1 1 280px" }}>
          <b>Stop all sending</b>
          <div className="hint">{stop ? `ON since ${istDate(stop.at)} by ${stop.by}${stop.reason ? ` — ${stop.reason}` : ""}. Nothing goes out for any account.`
            : "Off. Every account sends as set up."}</div>
        </div>
        {!stop && <input aria-label="Reason for stopping" placeholder="Reason (shown to everyone)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={300} />}
        {stop ? <button className="btn" disabled={busy} onClick={() => flip(false)} data-testid="stop-all-off">Resume sending</button>
          : <button className="btn danger" disabled={busy} onClick={() => flip(true)} data-testid="stop-all-on">Stop all sending</button>}
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}
    </section>
  );
}

export function SignupSwitch({ open, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const flip = async (on) => {
    if (on && !window.confirm("Open public sign-up? Anyone can then ask for an account (you still approve each one). Make sure the Terms, Privacy and DPA pages are ready.")) return;
    setBusy(true); setError("");
    try { await platform.setSignupOpen(on); onChanged(); } catch (err) { setError(err.message); }
    setBusy(false);
  };
  return (
    <section className="card" data-testid="signup-switch" style={{ marginBottom: 14, borderLeft: `4px solid ${open ? "#b45309" : "#16a34a"}` }}>
      <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ flex: "1 1 280px" }}>
          <b>Public sign-up</b>
          <div className="hint">{open ? "OPEN. The sign-in page shows “Create an account”; every new account still waits for your approval."
            : "Closed. The sign-in page has no sign-up link and the sign-up form refuses requests. Accounts are created by you in this console."}</div>
        </div>
        <button className={open ? "btn" : "btn"} disabled={busy} onClick={() => flip(!open)} data-testid="signup-switch-button">{open ? "Close sign-up" : "Open sign-up"}</button>
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}
    </section>
  );
}

export function StatusFilter({ value, onChange }) {
  return (
    <label className="row" style={{ gap: 6, alignItems: "center" }}>Show
      <select aria-label="Filter by status" value={value} onChange={(e) => onChange(e.target.value)} data-testid="status-filter">
        <option value="">All accounts</option><option value="pending">Pending</option><option value="active">Active</option><option value="suspended">Suspended</option>
      </select>
    </label>
  );
}

// The abuse and limit alerts (server: app/abuse_alerts.py): sign-up surges, accounts at a limit, bounce / complaint holds,
// failed sign-ins on client logins and sending spikes. Newest first; the same list reaches the super admins' bell and email.
export function AlertsStrip({ alerts }) {
  if (!alerts?.length) return null;
  return (
    <section className="card" data-testid="console-alerts" style={{ marginBottom: 14, borderLeft: "4px solid #b45309" }}>
      <h3>Alerts ({alerts.length})</h3>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        {alerts.slice(0, 8).map((a) => (
          <li key={a.id} data-testid={`console-alert-${a.kind}`}><b>{a.label}</b>: {a.title}{" "}
            <span className="hint">{istDate(a.at)}{a.account ? ` · ${a.account}` : ""}</span></li>))}
      </ul>
    </section>
  );
}
