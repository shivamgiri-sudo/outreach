import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { istLabel } from "../time.js";
import { useConfirm } from "../Dialog.jsx";

// Setup > Security. Owners: the "require two-step login" switch, who has it on (with a reset), and every login's
// history. Workspace admins: the history of their own workspaces' logins only (the server filters it too).
const EMPTY = { person: "", result: "", event: "", date_from: "", date_to: "" };
const PAGE = 50;
const EVENTS = { login: "Sign in", logout: "Sign out", password_reset: "Password reset", password_reset_request: "Reset link asked", password_change: "Password changed" };
const RESULTS = { success: ["ok", "Success"], failure: ["hot", "Failed"], pending: ["warn", "Waiting for code"] };

function TwoStepPolicy() {
  const [data, setData] = useState(null);
  const [message, setMessage] = useState("");
  const confirm = useConfirm();
  const load = () => api.securitySettings().then(setData).catch((e) => setMessage(e.message));
  useEffect(() => { load(); }, []);
  if (!data) return <p className="sub">{message || "Loading…"}</p>;
  const without = data.people.filter((p) => !p.two_step_on);
  const toggle = async (on) => {
    if (on && !(await confirm({ message: `Require two-step login for every team login? ${without.length} login(s) without it will be asked to set it up at their next sign-in.`, confirmLabel: "Require it" }))) return;
    setMessage("");
    api.saveSecuritySettings({ require_two_step: on }).then((d) => { setData(d); setMessage(on ? "Two-step login is now required." : "Two-step login is now optional."); }).catch((e) => setMessage(e.message));
  };
  const reset = async (p) => {
    if (!(await confirm({ message: `Reset two-step login for ${p.email}? They sign in with the password only${data.require_two_step ? " and set it up again" : ""}.`, confirmLabel: "Reset", danger: true }))) return;
    api.resetTwoStep(p.email).then(() => { setMessage(`Two-step login reset for ${p.email}.`); load(); }).catch((e) => setMessage(e.message));
  };
  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="two-step-policy">
      <b>Two-step login</b>
      <p className="sub" style={{ margin: "2px 0 8px" }}>Each person turns it on under Setup &gt; My password. A code from their phone is asked after the password.</p>
      <label className="check-row"><input type="checkbox" checked={data.require_two_step} onChange={(e) => toggle(e.target.checked)} data-testid="require-two-step" /> Require two-step login for everyone</label>
      {data.admin_password_login && <p className="sub" style={{ margin: "4px 0" }}>Not covered: the server admin password sign-in (OUTREACH_ADMIN_PASSWORD). It is the server's break-glass key; keep it long and private, or remove it once owners have their own logins.</p>}
      <ul className="today-list" style={{ marginTop: 8 }}>
        {data.people.map((p) => (
          <li key={p.email} className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <span><b>{p.name}</b> <small>{p.email} · {p.role}</small></span>
            <span className="row"><span className={`pill ${p.two_step_on ? "ok" : ""}`}>{p.two_step_on ? "Two-step on" : "Password only"}</span>
              {p.two_step_on && <button className="btn ghost" onClick={() => reset(p)}>Reset two-step</button>}</span>
          </li>
        ))}
      </ul>
      {message && <p className="sub" role="status">{message}</p>}
    </div>
  );
}

function LoginHistory() {
  const [f, setF] = useState(EMPTY);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    api.loginHistory({ ...f, limit: PAGE, offset }).then(setData).catch((e) => setError(e.message));
  }, [JSON.stringify(f), offset]);
  const set = (k, v) => { setF({ ...f, [k]: v }); setOffset(0); };
  return (
    <div data-testid="login-history">
      <h3 style={{ margin: "0 0 4px" }}>Login history</h3>
      <p className="sub" style={{ margin: 0 }}>Every sign-in attempt, sign-out and password reset (times in IST). Five failed sign-ins in a row for one login within 15 minutes alert the owners.</p>
      <div className="card" style={{ margin: "10px 0", padding: 12 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <select aria-label="Person" value={f.person} onChange={(e) => set("person", e.target.value)} style={{ width: "auto" }}>
            <option value="">Everyone</option>{(data?.people || []).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <select aria-label="Result" value={f.result} onChange={(e) => set("result", e.target.value)} style={{ width: "auto" }}>
            <option value="">Success or failed</option><option value="success">Success</option><option value="failure">Failed</option><option value="pending">Waiting for code</option>
          </select>
          <select aria-label="Event" value={f.event} onChange={(e) => set("event", e.target.value)} style={{ width: "auto" }}>
            <option value="">Every event</option>{Object.entries(EVENTS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
          <label className="row" style={{ gap: 4, margin: 0 }}>From <input type="date" aria-label="From (IST day)" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} style={{ width: "auto" }} /></label>
          <label className="row" style={{ gap: 4, margin: 0 }}>To <input type="date" aria-label="To (IST day)" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} style={{ width: "auto" }} /></label>
          {Object.values(f).some(Boolean) && <button className="btn ghost" onClick={() => { setF(EMPTY); setOffset(0); }}>Clear</button>}
        </div>
      </div>
      {error && <div className="tip error" role="alert">{error}</div>}
      <div className="card table-card" style={{ padding: 0 }}>
        {!data ? <p className="empty">Loading…</p> : data.items.length === 0 ? <p className="empty">Nothing recorded for these filters.</p> : (
          <div className="table-scroll"><table data-testid="login-table">
            <thead><tr><th>When (IST)</th><th>Login</th><th>Event</th><th>Result</th><th>Two-step</th><th>Address</th><th>Browser</th></tr></thead>
            <tbody>{data.items.map((r) => {
              const [tone, label] = RESULTS[r.result] || ["", r.result];
              return (
                <tr key={r.id}>
                  <td data-label="When"><small>{istLabel(r.at)}</small></td>
                  <td data-label="Login">{r.login}</td>
                  <td data-label="Event">{EVENTS[r.event] || r.event}</td>
                  <td data-label="Result"><span className={`pill ${tone}`}>{label}</span>{r.reason && <><br /><small>{r.reason}</small></>}</td>
                  <td data-label="Two-step">{r.two_step ? (r.method === "recovery" ? "Recovery code" : "Yes") : "No"}</td>
                  <td data-label="Address"><small>{r.ip}</small></td>
                  <td data-label="Browser"><small title={r.agent}>{(r.agent || "").slice(0, 60)}</small></td>
                </tr>
              );
            })}</tbody>
          </table></div>
        )}
      </div>
      {data && data.total > PAGE && (
        <div className="row" style={{ justifyContent: "space-between", marginTop: 8 }}>
          <small>{offset + 1}–{Math.min(offset + PAGE, data.total)} of {data.total}</small>
          <span className="row">
            <button className="btn ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
            <button className="btn ghost" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)}>Older</button>
          </span>
        </div>
      )}
    </div>
  );
}

export default function Security() {
  return (
    <div data-testid="security-tab">
      {isOwner() && <TwoStepPolicy />}
      <LoginHistory />
    </div>
  );
}
