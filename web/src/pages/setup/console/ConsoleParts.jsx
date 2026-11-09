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

export function StatusFilter({ value, onChange }) {
  return (
    <label className="row" style={{ gap: 6, alignItems: "center" }}>Show
      <select aria-label="Filter by status" value={value} onChange={(e) => onChange(e.target.value)} data-testid="status-filter">
        <option value="">All accounts</option><option value="pending">Pending</option><option value="active">Active</option><option value="suspended">Suspended</option>
      </select>
    </label>
  );
}
