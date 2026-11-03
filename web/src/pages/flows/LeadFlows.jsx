import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { istLabel } from "../../time.js";

const PILL = { running: "ok", waiting: "ok", done: "", stopped: "warn", error: "warn" };

// "Flows" in the lead panel: which flows this lead is in, where they are now, Stop; and start an active flow.
export default function LeadFlows({ lead, onChanged }) {
  const [runs, setRuns] = useState([]);
  const [flows, setFlows] = useState([]);
  const [pick, setPick] = useState("");
  const [message, setMessage] = useState({ text: "", bad: false });
  const load = () => api.leadFlows(lead.id).then(setRuns).catch(() => setRuns([]));
  useEffect(() => {
    load();
    api.flows().then((all) => setFlows(all.filter((f) => f.status === "active"))).catch(() => setFlows([]));
  }, [lead.id]);
  const inFlow = new Set(runs.map((r) => r.flow_id));
  const startable = flows.filter((f) => !inFlow.has(f.id));
  const start = async () => {
    try {
      const r = await api.enrollFlow(pick, [lead.id]);
      setMessage(r.refused.length ? { text: r.refused[0].reason, bad: true } : { text: "Started.", bad: false });
      setPick("");
      load();
      onChanged?.();
    } catch (e) { setMessage({ text: e.message, bad: true }); }
  };
  const stop = async (id) => {
    try { await api.stopFlowRun(id); load(); onChanged?.(); } catch (e) { setMessage({ text: e.message, bad: true }); }
  };
  return (
    <section data-testid="lead-flows">
      <h3>Flows</h3>
      {message.text && <div className={`tip${message.bad ? " error" : ""}`} role={message.bad ? "alert" : "status"}>{message.text}</div>}
      {runs.length === 0 ? <p className="sub">Not in any flow.</p> : (
        <ul className="meet-mini">
          {runs.map((r) => (
            <li key={r.id}>
              <div>
                <b>{r.flow_name}</b> <span className={`pill ${PILL[r.status] || ""}`}>{r.status}</span>
                <br /><small>{r.current_title ? `Now: ${r.current_title}` : r.stop_reason}{r.wait_until ? ` · until ${istLabel(r.wait_until)}` : ""}</small>
              </div>
              {["running", "waiting"].includes(r.status) && <button type="button" className="btn ghost" onClick={() => stop(r.id)}>Stop</button>}
            </li>
          ))}
        </ul>
      )}
      {startable.length > 0 && (
        <div className="row">
          <label htmlFor={`lead-flow-${lead.id}`} className="sr-only">Start a flow</label>
          <select id={`lead-flow-${lead.id}`} style={{ flex: 1, width: "auto" }} value={pick} onChange={(e) => setPick(e.target.value)}>
            <option value="">Start a flow…</option>
            {startable.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
          </select>
          <button type="button" className="btn ghost" disabled={!pick} onClick={start}>Start</button>
        </div>
      )}
    </section>
  );
}
