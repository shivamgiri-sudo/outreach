import { useEffect, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";
import { istLabel } from "../time.js";
import { Badge, Button, Card, Switch } from "../ui/index.js";

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
    <Card data-testid="email-gate">
      <div className="ui-card-head">
        <div>
          <h3>Email checks before the first send <Badge tone={state.enabled ? "success" : "neutral"}>{state.enabled ? "On" : "Off"}</Badge></h3>
          <div className="sub">
            The first real email to a lead goes only to an address a mailbox check said works. This keeps bounces down. {verifierText}
          </div>
        </div>
        {manager && state.enabled && <Button disabled={busy} onClick={checkNow}>Check now</Button>}
      </div>
      <p className="sub" data-testid="email-gate-counts">
        {COUNT_LABELS.map(([k, label]) => `${label}: ${state.counts[k] ?? 0}`).join(" · ")}
      </p>
      {state.last_run && <p className="sub">Last check: {istLabel(state.last_run)}.</p>}
      {note && <div className="tip" role="status">{note}</div>}
      {(error || state.error) && <div className="tip error" role="alert">{((e) => e.charAt(0).toUpperCase() + e.slice(1))(error || state.error)}</div>}
      {owner ? (
        <div className="switch-block">
          <Switch showState label="Email checks before the first send" checked={state.enabled} disabled={busy} onChange={(on) => run(() => api.setEmailGate(on, state.approve_risky))} />
          <p className="sub">
            Turning this off sends first emails to addresses nobody has checked, as the app did before. More of them can bounce, and too many bounces pause all sending.
            Risky addresses wait for an owner or admin to approve them on the lead.
          </p>
          <div className="switch-block">
            <Switch showState label="Use the built-in checker" checked={Boolean(state.builtin)} disabled={busy || paid} onChange={(on) => run(() => api.setEmailGate(state.enabled, state.approve_risky, on))} />
            <p className="sub">
              {paid ? "A paid verifier is connected, so it is used instead." : "No paid verifier? Switch this on: the app then checks spelling (gmial.com), throwaway inboxes, shared mailboxes (info@), the domain's mail records and your own bounce history."}
            </p>
          </div>
        </div>
      ) : <p className="sub">Only an owner can change this.</p>}
    </Card>
  );
}
