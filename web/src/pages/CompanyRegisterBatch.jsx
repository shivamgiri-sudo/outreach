import { useEffect, useRef, useState } from "react";
import { register } from "../registerApi.js";
import { Button } from "../ui/index.js";

// Owners: match up to 500 leads (of the chosen workspace) to the local company register in the background.
export default function CompanyRegisterBatch({ empty }) {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const timer = useRef(null);
  const poll = () => register.batchState().then((s) => {
    setState(s);
    if (s.status === "running") timer.current = setTimeout(poll, 2000);
  }).catch((e) => setError(e.message));
  useEffect(() => { poll(); return () => clearTimeout(timer.current); }, []);
  const start = async () => {
    setError("");
    try { const s = await register.batchStart(500); setState(s); if (s.status === "running") timer.current = setTimeout(poll, 2000); }
    catch (e) { setError(e.message); }
  };
  const running = state?.status === "running";
  return (
    <div className="reg-ask" aria-label="Match leads to the register">
      <b>Match leads</b>
      <p className="sub u-m-4-0">Looks up to 500 leads with a company name in the register. Sure matches are attached; likely ones wait in each lead's
        drawer as "Is this the company?"; weak ones are never attached. Only empty lead fields are filled. A lead is checked again after 30 days.</p>
      <div className="reg-actions">
        <Button variant="secondary" size="sm" onClick={start} disabled={running || empty}>{running ? "Matching…" : "Match 500 leads"}</Button>
      </div>
      {empty && <p className="reg-note">Import the register first.</p>}
      {state && state.status !== "idle" && (
        <p className="sub" role="status">
          {running ? `Working: ${state.done} of ${state.total} leads.` : state.note && !state.total ? state.note
            : `Last run: ${state.done} lead(s) checked, ${state.attached} matched, ${state.suggested} to confirm, ${state.low} without a sure match${state.errors ? `, ${state.errors} error(s)` : ""}.${state.note ? ` ${state.note}` : ""}`}
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
