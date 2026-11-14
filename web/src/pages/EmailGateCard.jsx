import { useEffect, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";
import { istLabel } from "../time.js";

const COUNT_LABELS = [
  ["waiting_for_check", "Waiting for a check"], ["held", "Waiting to be emailed"], ["valid", "Valid"],
  ["risky", "Risky"], ["invalid", "Invalid"], ["unverified", "Not checked yet"],
];

// "Email checks before the first send": a live first email goes only to an address a mailbox check said works.
export default function EmailGateCard() {
  const [state, setState] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const owner = isOwner();
  const manager = owner || isWorkspaceAdmin();
  const load = () => api.emailGate().then(setState).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  if (!state) return error ? <div className="tip error" role="alert">{error}</div> : null;
  const v = state.verifier;
  const run = async (fn, done) => {
    setBusy(true); setError(""); setNote("");
    try { const r = await fn(); if (done) setNote(done(r)); }
    catch (e) { setError(e.message); }
    await load();
    setBusy(false);
  };
  const checkNow = () => run(api.emailGateCheckNow, (r) => (r.error ? "" : `Checked ${r.checked} address(es): ${r.valid} valid, ${r.risky} risky, ${r.invalid} invalid.`));
  const verifierText = v.builtin ? "Using the built-in checker (no paid verifier). It checks the spelling, the domain and what bounced before; it cannot look inside a mailbox."
    : !v.connected ? "No verifier connected: connect one under Integrations so new leads can be checked."
    : !v.supported ? `The verifier "${v.provider || "unnamed"}" cannot be called automatically yet, so leads wait for a manual check.`
    : `Verifier connected (${v.provider}).`;
  const paid = v.connected && !v.builtin;
  return (
    <div className="card" style={{ marginBottom: 14 }} data-testid="email-gate">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <b>Email checks before the first send</b> <span className={`pill ${state.enabled ? "ok" : ""}`}>{state.enabled ? "On" : "Off"}</span>
          <div className="sub" style={{ margin: "4px 0 0" }}>
            The first real email to a lead goes only to an address a mailbox check said works. This keeps bounces down. {verifierText}
          </div>
        </div>
        {manager && state.enabled && <button className="btn" disabled={busy} onClick={checkNow}>Check now</button>}
      </div>
      <p className="sub" style={{ margin: "8px 0 0" }} data-testid="email-gate-counts">
        {COUNT_LABELS.map(([k, label]) => `${label}: ${state.counts[k] ?? 0}`).join(" · ")}
      </p>
      {state.last_run && <p className="sub" style={{ margin: "4px 0 0" }}>Last check: {istLabel(state.last_run)}.</p>}
      {note && <div className="tip" role="status" style={{ marginTop: 8 }}>{note}</div>}
      {(error || state.error) && <div className="tip error" role="alert" style={{ marginTop: 8 }}>{((e) => e.charAt(0).toUpperCase() + e.slice(1))(error || state.error)}</div>}
      {owner ? (
        <div style={{ marginTop: 10 }}>
          <div className="tab-chips" role="group" aria-label="Email checks before the first send">
            <button className={`tab-chip${state.enabled ? " on" : ""}`} disabled={busy} onClick={() => state.enabled || run(() => api.setEmailGate(true, state.approve_risky))}>On</button>
            <button className={`tab-chip${state.enabled ? "" : " on"}`} disabled={busy} onClick={() => !state.enabled || run(() => api.setEmailGate(false, state.approve_risky))}>Off</button>
          </div>
          <p className="sub" style={{ margin: 0 }}>
            Turning this off sends first emails to addresses nobody has checked, as the app did before. More of them can bounce, and too many bounces pause all sending.
            Risky addresses wait for an owner or admin to approve them on the lead.
          </p>
          <div style={{ marginTop: 8 }}>
            <b>Use the built-in checker</b>
            <div className="tab-chips" role="group" aria-label="Use the built-in checker">
              <button className={`tab-chip${state.builtin ? " on" : ""}`} disabled={busy || paid} onClick={() => state.builtin || run(() => api.setEmailGate(state.enabled, state.approve_risky, true))}>On</button>
              <button className={`tab-chip${state.builtin ? "" : " on"}`} disabled={busy || paid} onClick={() => !state.builtin || run(() => api.setEmailGate(state.enabled, state.approve_risky, false))}>Off</button>
            </div>
            <p className="sub" style={{ margin: 0 }}>
              {paid ? "A paid verifier is connected, so it is used instead." : "No paid verifier? Switch this on: the app then checks spelling (gmial.com), throwaway inboxes, shared mailboxes (info@), the domain's mail records and your own bounce history."}
            </p>
          </div>
        </div>
      ) : <p className="sub" style={{ margin: "8px 0 0" }}>Only an owner can change this.</p>}
    </div>
  );
}
