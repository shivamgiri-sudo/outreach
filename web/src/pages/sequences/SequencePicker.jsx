import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { humanDay } from "./lint.js";

// Choose which email sequence to start for one lead, then start it. `onStart(sequenceId)` does the enrol call
// (the drawer's own action wrapper refreshes the lead); without it the picker calls the API itself.
export default function SequencePicker({ lead, onStart }) {
  const [list, setList] = useState([]);
  const [chosen, setChosen] = useState("default");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.sequences().then(setList).catch(() => setList([])); }, []);
  if (!lead?.email) return <button className="btn ghost" disabled>No email — cannot start</button>;
  const pick = list.find((s) => s.id === chosen);
  const start = async () => {
    setMsg("");
    try { await (onStart ? onStart(chosen) : api.enrollIn(lead.id, chosen)); }
    catch (err) { setMsg(err.message); }
  };
  return (
    <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      <select aria-label="Email sequence to start" value={chosen} onChange={(e) => setChosen(e.target.value)} style={{ flex: "1 1 200px", width: "auto", minWidth: 0 }}>
        {(list.length ? list : [{ id: "default", name: "Default (6 emails)", steps: [] }]).map((s) => (
          <option key={s.id} value={s.id}>{s.name}{s.steps.length ? ` · ${s.steps.length} emails` : ""}</option>
        ))}
      </select>
      <button className="btn ghost" onClick={start}>Start {pick ? `${pick.steps.length}-step ` : ""}email sequence</button>
      {pick && pick.steps.length > 0 && <span className="sub" style={{ flexBasis: "100%", margin: 0 }}>Emails on day {pick.steps.map((s) => humanDay(s.day)).join(", ")}.</span>}
      {msg && <p className="error" role="alert" style={{ flexBasis: "100%", margin: 0 }}>{msg}</p>}
    </div>
  );
}
