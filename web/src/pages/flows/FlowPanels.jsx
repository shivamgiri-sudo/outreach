import { useEffect, useMemo, useState } from "react";
import { api, isOwner } from "../../api.js";
import { istLabel } from "../../time.js";
import { BRANCH_HEADINGS } from "./catalog.js";

const ASSUME = ["timeout", "interested", "question", "not_interested", "not_now", "wrong_person", "other"];
const STATUS_PILL = { running: "ok", waiting: "ok", done: "", stopped: "warn", error: "warn" };

// Dry run for one lead: the path is highlighted on the canvas and listed step by step. Nothing is saved or sent.
export function SimulatePanel({ leads, result, busy, onRun, onClear }) {
  const [leadId, setLeadId] = useState("");
  const [assume, setAssume] = useState("timeout");
  useEffect(() => { if (!leadId && leads.length) setLeadId(leads[0].id); }, [leads]);
  return (
    <section className="card fb-panel" aria-labelledby="fb-sim-title">
      <h3 id="fb-sim-title">Simulate on a lead</h3>
      <p className="field-help">Shows the path this lead would take. Nothing is saved or sent.</p>
      <label htmlFor="fb-sim-lead">Lead
        <select id="fb-sim-lead" value={leadId} onChange={(e) => setLeadId(e.target.value)}>
          {leads.map((l) => <option key={l.id} value={l.id}>{l.company || l.domain} {l.contact_name ? `— ${l.contact_name}` : ""}</option>)}
        </select>
      </label>
      <label htmlFor="fb-sim-reply">If the flow waits for a reply, assume
        <select id="fb-sim-reply" value={assume} onChange={(e) => setAssume(e.target.value)}>
          {ASSUME.map((a) => <option key={a} value={a}>{a === "timeout" ? "No reply" : BRANCH_HEADINGS[a]?.replace(/^If (they )?/, "") || a}</option>)}
        </select>
      </label>
      <div className="row" style={{ marginTop: 10 }}>
        <button type="button" className="btn" disabled={!leadId || busy} onClick={() => onRun(leadId, assume)}>Run simulation</button>
        {result && <button type="button" className="btn ghost" onClick={onClear}>Clear</button>}
      </div>
      {result && (
        <div className="fb-sim" data-testid="sim-result" aria-live="polite">
          <ol>
            {result.path.map((p, i) => (
              <li key={`${p.node}-${i}`} className={p.ok === false ? "bad" : ""}><b>{p.title}</b>: {p.result}</li>
            ))}
          </ol>
          <p className="sub" style={{ margin: 0 }}>{result.ended}</p>
        </div>
      )}
    </section>
  );
}

// Bulk enrolment for an active flow: pick leads, start them. Already-enrolled and closed leads are refused with a reason.
export function EnrolPanel({ flow, leads, onDone }) {
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState(new Set());
  const [result, setResult] = useState(null);
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return leads.filter((l) => !q || [l.company, l.contact_name, l.email, l.city].join(" ").toLowerCase().includes(q)).slice(0, 50);
  }, [leads, query]);
  const toggle = (id) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const enrol = async () => {
    try {
      const r = await api.enrollFlow(flow.id, [...picked]);
      setResult(r);
      setPicked(new Set());
      onDone?.();
    } catch (e) { setResult({ error: e.message }); }
  };
  if (flow.status !== "active") {
    return <section className="card fb-panel"><h3>Enrol leads</h3><p className="field-help">Activate the flow to start leads on it.</p></section>;
  }
  return (
    <section className="card fb-panel" aria-labelledby="fb-enrol-title">
      <h3 id="fb-enrol-title">Enrol leads</h3>
      <input aria-label="Search leads to enrol" placeholder="Search company, name, city…" value={query} onChange={(e) => setQuery(e.target.value)} />
      <ul className="fb-pick">
        {shown.map((l) => (
          <li key={l.id}><label className="check-row"><input type="checkbox" checked={picked.has(l.id)} onChange={() => toggle(l.id)} />
            {l.company || l.domain} <small className="sub-inline">{l.stage}</small></label></li>
        ))}
      </ul>
      <button type="button" className="btn" disabled={!picked.size} onClick={enrol}>Enrol {picked.size || ""} lead{picked.size === 1 ? "" : "s"}</button>
      {result?.error && <div className="tip error" role="alert">{result.error}</div>}
      {result && !result.error && (
        <div className="tip" role="status">Started {result.enrolled.length}.
          {result.refused.map((r) => <div key={r.lead_id}><small>{leads.find((l) => l.id === r.lead_id)?.company || r.lead_id}: {r.reason}</small></div>)}
        </div>
      )}
    </section>
  );
}

// Leads on this flow with their current step; a run can be stopped.
export function RunsPanel({ flowId, refreshKey }) {
  const [rows, setRows] = useState([]);
  const load = () => api.flowRuns(flowId).then(setRows).catch(() => setRows([]));
  useEffect(() => { load(); }, [flowId, refreshKey]);
  return (
    <section className="card fb-panel" aria-labelledby="fb-runs-title">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 id="fb-runs-title" style={{ margin: 0 }}>Leads in this flow ({rows.length})</h3>
        <button type="button" className="btn ghost" onClick={load}>Refresh</button>
      </div>
      {rows.length === 0 ? <p className="field-help">No leads yet.</p> : (
        <ul className="meet-mini" style={{ marginTop: 10 }}>
          {rows.slice(0, 30).map((r) => (
            <li key={r.id}>
              <div>
                <b>{r.company || r.lead_id}</b> <span className={`pill ${STATUS_PILL[r.status] || ""}`}>{r.status}</span>
                <br /><small>{r.current_title ? `At: ${r.current_title}` : r.stop_reason}{r.deferred ? ` (${r.deferred})` : ""}
                  {r.wait_until ? ` · until ${istLabel(r.wait_until)}` : ""} · v{r.flow_version}</small>
              </div>
              {isOwner() && ["running", "waiting"].includes(r.status) && (
                <button type="button" className="btn ghost" onClick={() => api.stopFlowRun(r.id).then(load)}>Stop</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
