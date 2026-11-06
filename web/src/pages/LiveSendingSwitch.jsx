import { useEffect, useState } from "react";
import { isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";

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
    <div className="card" style={{ marginBottom: 14, borderLeft: `4px solid ${state.on ? "var(--ok, #15803d)" : "var(--line)"}` }} data-testid="live-switch">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <div>
          <b>Send for real</b> <span className={`pill ${state.on ? "ok" : ""}`}>{state.on ? "On: emails go to real people" : "Off: practice mode"}</span>
          <div className="sub" style={{ margin: "4px 0 0" }}>
            {changed ? `Switched ${changed.on ? "on" : "off"} by ${changed.by} on ${istLabel(changed.at)}.` : "Never switched on yet."}
          </div>
        </div>
        {owner && (state.on
          ? <button className="btn ghost danger" disabled={busy} onClick={() => set(false)}>Switch off</button>
          : !asking && <button className="btn" disabled={busy || state.blockers.length > 0} onClick={() => setAsking(true)}
            title={state.blockers.length ? "Finish the steps listed first" : ""}>Switch on…</button>)}
      </div>
      {!state.on && state.blockers.length > 0 && <p className="sub" style={{ margin: "8px 0 0" }}>Finish these first: {state.blockers.join(" ")}</p>}
      {asking && !state.on && (
        <div className="tip" role="alert" style={{ marginTop: 10 }}>
          Real emails will go to real people, within the daily cap, warm-up and business hours. To confirm, type <b>{phrase}</b>.
          <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
            <input aria-label={`Type ${phrase} to confirm`} value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={phrase} style={{ maxWidth: 220 }} autoFocus />
            <button className="btn" disabled={busy || typed.trim().toUpperCase() !== phrase} onClick={() => set(true)}>Switch on real sending</button>
            <button className="btn ghost" onClick={() => { setAsking(false); setTyped(""); }}>Cancel</button>
          </div>
        </div>
      )}
      {!owner && <p className="sub" style={{ margin: "8px 0 0" }}>Only an owner can change this.</p>}
      {error && <div className="tip error" role="alert" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  );
}
