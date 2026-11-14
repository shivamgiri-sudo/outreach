import { useEffect, useState } from "react";
import { isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";
import { Badge, Button, Card } from "../ui/index.js";

// "Send for real": its own switch, apart from the settings form. On needs the typed phrase; off is one click.
export default function LiveSendingSwitch({ onChanged }) {
  const [state, setState] = useState(null);
  const [asking, setAsking] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = () => ops.liveSending().then(setState).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  if (!state) return error ? <div className="tip error" role="alert">{error}</div> : null;
  const phrase = state.confirm_phrase || "SEND FOR REAL";
  const owner = isOwner();
  const set = async (on) => {
    setBusy(true); setError("");
    try { setState(await ops.setLiveSending(on, on ? typed : "")); setAsking(false); setTyped(""); onChanged?.(); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };
  const changed = state.changed;
  return (
    <Card className={`live-card${state.on ? " is-on" : ""}`} data-testid="live-switch">
      <div className="ui-card-head">
        <div>
          <h3>Send for real <Badge tone={state.on ? "success" : "neutral"}>{state.on ? "On: emails go to real people" : "Off: practice mode"}</Badge></h3>
          <div className="sub">
            {changed ? `Switched ${changed.on ? "on" : "off"} by ${changed.by} on ${istLabel(changed.at)}.` : "Never switched on yet."}
          </div>
        </div>
        {owner && (state.on
          ? <Button variant="danger" disabled={busy} onClick={() => set(false)}>Switch off</Button>
          : !asking && <Button disabled={busy || state.blockers.length > 0} onClick={() => setAsking(true)}
            title={state.blockers.length ? "Finish the steps listed first" : ""}>Switch on…</Button>)}
      </div>
      {!state.on && state.blockers.length > 0 && <p className="sub">Finish these first: {state.blockers.join(" ")}</p>}
      {asking && !state.on && (
        <div className="tip" role="alert">
          Real emails will go to real people, within the daily cap, warm-up and business hours. To confirm, type <b>{phrase}</b>.
          <div className="row card-actions">
            <input aria-label={`Type ${phrase} to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={phrase} className="confirm-input" autoFocus />
            <Button disabled={busy || typed.trim().toUpperCase() !== phrase} onClick={() => set(true)}>Switch on real sending</Button>
            <Button variant="secondary" onClick={() => { setAsking(false); setTyped(""); }}>Cancel</Button>
          </div>
        </div>
      )}
      {!owner && <p className="sub">Only an owner can change this.</p>}
      {error && <div className="tip error" role="alert">{error}</div>}
    </Card>
  );
}
