import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";
import { Button, Field, Input, Switch } from "../ui/index.js";

// Owners: the scheduled re-check of active leads' websites (app/recrawl.py), shown inside the hiring batch card.
export default function RecrawlSettings() {
  const [state, setState] = useState(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState("");
  const show = (s) => { setState(s); setDays(s.days); };
  useEffect(() => { ops.recrawl().then(show).catch((e) => setError(e.message)); }, []);
  if (!state) return error ? <p className="error" role="alert">{error}</p> : null;
  const save = (on, d) => { setError(""); ops.setRecrawl(on, d).then(show).catch((e) => setError(e.message)); };
  const last = state.last_run || {};
  const bad = Number(days) < 7 || Number(days) > 180;
  return (
    <div className="switch-block" data-testid="recrawl-settings">
      <Switch showState label="Re-check active leads' websites on a schedule" checked={state.enabled} onChange={(on) => save(on, days)} />
      <p className="sub">
        Every hour, up to {state.batch} websites of active leads (in a running sequence, on a list or from a running campaign, and not closed) whose site was last read more than the days below ago are read again.
        Real changes go on the lead's timeline: started or stopped hiring, new roles, a new phone or social link. The lead's owner gets an alert when a company starts hiring.
      </p>
      <div className="dp-toolbar">
        <Field label="Days between checks"><Input type="number" min="7" max="180" value={days} onChange={(e) => setDays(e.target.value)} /></Field>
        <Button variant="secondary" disabled={bad || Number(days) === state.days} onClick={() => save(state.enabled, days)}>Save days</Button>
      </div>
      {last.at && (
        <p className="sub" role="status">
          Last scheduled run: {istLabel(last.at)}. {last.note === "nothing due" ? "Nothing was due." : `${last.domains} website(s) read, ${last.changes} change(s), ${last.started} started hiring${last.errors ? `, ${last.errors} could not be read` : ""}.`}
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
