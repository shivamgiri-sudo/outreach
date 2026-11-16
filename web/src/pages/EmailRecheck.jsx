import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Button, Field, Input, Switch } from "../ui/index.js";

// Part of the "Email checks before the first send" card: re-check addresses whose last check is too old.
// state = the GET /api/email-gate answer; owner = may change the rule; manager = may start "Re-check now".
export default function EmailRecheck({ state, owner, manager, onChange }) {
  const [days, setDays] = useState(state.recheck_days);
  const [run, setRun] = useState(null);
  const [error, setError] = useState("");
  const timer = useRef(null);
  useEffect(() => setDays(state.recheck_days), [state.recheck_days]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const follow = () => api.emailRecheckState().then((r) => {
    setRun(r);
    if (r.status === "running") timer.current = setTimeout(follow, 2000);
    else onChange();
  }).catch((e) => setError(e.message));
  const save = async (on, d) => {
    setError("");
    try { await api.setEmailGateRecheck(state, on, Number(d)); onChange(); } catch (e) { setError(e.message); }
  };
  const start = async () => {
    setError("");
    try { const r = await api.emailRecheckStart(); setRun(r); if (r.status === "running") timer.current = setTimeout(follow, 2000); else onChange(); }
    catch (e) { setError(e.message); }
  };
  const stale = state.counts.stale ?? 0;
  const res = run?.result;
  return (
    <div className="switch-block" data-testid="email-recheck">
      <p className="sub"><b>{stale.toLocaleString()}</b> address(es) not checked in {state.recheck_days} days (or never checked).</p>
      {manager && stale > 0 && (
        <Button variant="secondary" disabled={run?.status === "running"} onClick={start}>
          {run?.status === "running" ? `Re-checking… ${run.done} of ${run.total}` : "Re-check now (max 500)"}
        </Button>
      )}
      {res && run.status === "done" && <p className="sub" role="status">Re-checked {res.checked}: {res.valid} valid, {res.risky} risky, {res.invalid} invalid{res.unverified ? `, ${res.unverified} could not be checked now` : ""}.</p>}
      {owner && (
        <>
          <Switch showState label="Re-check old addresses before sending" checked={Boolean(state.recheck_enabled)} onChange={(on) => save(on, days)} />
          <p className="sub">When an address was last checked more than the days below ago, its next email waits for a fresh check (by your verifier, or the built-in checker). Later emails of a running sequence only wait while a checker is connected.</p>
          <div className="dp-toolbar">
            <Field label="Days before a check is old"><Input type="number" min="7" max="365" value={days} onChange={(e) => setDays(e.target.value)} /></Field>
            <Button variant="secondary" disabled={Number(days) === state.recheck_days || Number(days) < 7 || Number(days) > 365} onClick={() => save(state.recheck_enabled, days)}>Save days</Button>
          </div>
        </>
      )}
      {(error || run?.error) && <div className="tip error" role="alert">{error || run.error}</div>}
    </div>
  );
}
