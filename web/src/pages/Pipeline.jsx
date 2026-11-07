import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import { sourceLabel, stageLabel } from "../labels.js";
import { Donut, HBars, SERIES, TrendChart } from "../charts.jsx";
import LeadDrawer from "./LeadDrawer.jsx";

// Where every lead stands now: total -> enriched -> attempted -> connected, what the connected ones said, call
// outcomes, channels, stages, a daily trend, and the same funnel per owner and per source. Every number opens its leads.
const STEP = { total: "All leads", contactable: "Have phone / email / LinkedIn", enriched: "Enriched", attempted: "Attempted", connected: "Connected",
  interested: "Interested", not_interested: "Not interested", follow_up: "Asked for follow-up", awaiting: "Connected, no decision yet",
  not_attempted: "Not attempted yet", no_connect: "Attempted, not connected", bad_number: "Wrong / inactive number", not_connected: "Not connected" };
const CALL = { connected: "Connected", no_answer: "No answer", busy: "Busy", call_disconnected: "Call disconnected", call_back: "Call back",
  interested: "Interested", not_interested: "Not interested", meeting_booked: "Meeting booked", wrong_number: "Wrong number",
  inactive_number: "Inactive number", no_outcome: "No outcome" };
const CALL_COLOR = { connected: SERIES.blue, interested: SERIES.green, meeting_booked: SERIES.teal, call_back: SERIES.amber, not_interested: SERIES.red,
  wrong_number: SERIES.slate, inactive_number: SERIES.slate };
const CHANNEL = { email: "Email", call: "Call", linkedin: "LinkedIn", whatsapp: "WhatsApp", sms: "SMS" };
const COLS = ["total", "enriched", "attempted", "connected", "interested", "not_interested", "follow_up"];

function GroupTable({ title, rows, nameOf, onPick, testid }) {
  return (
    <div className="card table-card" style={{ padding: 0, marginTop: 14 }}>
      <h3 style={{ margin: "14px 16px 6px" }}>{title}</h3>
      {rows.length === 0 ? <p className="empty">No leads.</p> : (
        <div className="table-scroll"><table data-testid={testid}>
          <thead><tr><th>{title.replace("By ", "")}</th>{COLS.map((c) => <th key={c}>{STEP[c].replace("Asked for follow-up", "Follow-up")}</th>)}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.name}><td><b>{nameOf(r.name)}</b></td>
              {COLS.map((c) => <td key={c}>{r[c] ? <button className="link" onClick={() => onPick(c, r.name)}>{r[c]}</button> : 0}
                {r.rates[c] !== undefined && r[c] > 0 && <small className="sub-inline"> {r.rates[c]}%</small>}</td>)}
            </tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

function Drill({ pick, period, added, onClose }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [lead, setLead] = useState(null);
  const load = () => { setError(""); setData(null); ops.pipelineLeads({ ...pick, added }, period).then(setData).catch((e) => setError(e.message)); };
  useEffect(load, [JSON.stringify(pick), JSON.stringify(period), added]);
  const by = pick.owner ? ` · owner ${pick.owner}` : pick.source ? ` · source ${sourceLabel(pick.source)}` : "";
  return (
    <div className="card" style={{ marginTop: 14 }} data-testid="pipeline-drill">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>{STEP[pick.step]}{by}{data ? ` — ${data.count}` : ""}</h3>
        <button className="btn ghost" onClick={onClose}>Close</button>
      </div>
      {error && <ErrorState message="Could not load these leads." detail={error} onRetry={load} />}
      {!data && !error && <p className="empty">Loading…</p>}
      {data && (data.leads.length === 0 ? <p className="empty">No leads.</p> : (
        <div className="table-scroll"><table>
          <thead><tr><th>Company</th><th>Name</th><th>Phone</th><th>Email</th><th>Stage</th><th>Owner</th></tr></thead>
          <tbody>{data.leads.map((l) => (
            <tr key={l.id}><td><button className="link" onClick={() => setLead(l.id)}>{l.company || "—"}</button></td><td>{l.name}</td>
              <td>{l.phone}</td><td>{l.email}</td><td>{stageLabel(l.stage)}</td><td>{l.owner || "—"}</td></tr>
          ))}</tbody>
        </table></div>
      ))}
      {data && data.count > data.leads.length && <p className="sub">Showing the newest {data.leads.length} of {data.count}.</p>}
      {lead && <LeadDrawer id={lead} ids={data.leads.map((l) => l.id)} onClose={() => setLead(null)} />}
    </div>
  );
}

export default function Pipeline({ period }) {
  const [added, setAdded] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [pick, setPick] = useState(null);
  const load = () => { setError(""); ops.pipeline(period, added).then(setData).catch((e) => setError(e.message)); };
  useEffect(load, [JSON.stringify(period), added]);
  const open = (step, extra = {}) => setPick({ step, ...extra });
  if (error) return <ErrorState message="The pipeline could not load." detail={error} onRetry={load} />;
  if (!data) return <p className="empty">Loading…</p>;
  const o = data.overall;
  const funnel = [
    { key: "total", label: STEP.total, value: o.total, color: SERIES.slate },
    { key: "enriched", label: STEP.enriched, value: o.enriched, color: SERIES.violet, note: `${o.rates.enriched}% of all` },
    { key: "attempted", label: STEP.attempted, value: o.attempted, color: SERIES.blue, note: `${o.rates.attempted}% of all` },
    { key: "connected", label: STEP.connected, value: o.connected, color: SERIES.teal, note: `${o.rates.connected}% of attempted` },
    { key: "interested", label: STEP.interested, value: o.interested, color: SERIES.green, note: `${o.rates.interested}% of connected` },
  ];
  const outcomes = [
    { key: "interested", label: "Interested", value: o.interested, color: SERIES.green },
    { key: "not_interested", label: "Not interested", value: o.not_interested, color: SERIES.red },
    { key: "follow_up", label: "Follow-up", value: o.follow_up, color: SERIES.amber },
    { key: "awaiting", label: "No decision yet", value: o.awaiting, color: SERIES.slate },
  ];
  const reach = [
    { key: "not_attempted", label: "Not attempted", value: o.not_attempted, color: SERIES.slate },
    { key: "no_connect", label: "Tried, no connect", value: o.no_connect, color: SERIES.amber },
    { key: "connected", label: "Connected", value: o.connected, color: SERIES.teal },
  ];
  const calls = Object.entries(data.calls.by_outcome).map(([k, v]) => ({ key: k, label: CALL[k] || k, value: v, color: CALL_COLOR[k] || SERIES.amber }));
  const channels = Object.entries(data.channels).map(([k, v]) => ({ key: k, label: CHANNEL[k] || k, value: v }));
  const stages = Object.entries(data.stages).map(([k, v]) => ({ key: k, label: stageLabel(k), value: v, color: SERIES.violet }));
  return (
    <>
      <div className="row" style={{ gap: 14, flexWrap: "wrap", margin: "4px 0 12px" }} role="radiogroup" aria-label="Which leads to count">
        <label className="row" style={{ gap: 6, margin: 0 }}><input type="radio" checked={!added} onChange={() => setAdded(false)} /> All leads (now)</label>
        <label className="row" style={{ gap: 6, margin: 0 }}><input type="radio" checked={added} onChange={() => setAdded(true)} data-testid="pipeline-added" /> Only leads added in this period</label>
      </div>
      <div className="stat-grid pipeline-tiles">
        {[["total", o.total], ["contactable", o.contactable], ["enriched", o.enriched], ["attempted", o.attempted], ["connected", o.connected],
          ["interested", o.interested], ["not_interested", o.not_interested], ["follow_up", o.follow_up], ["bad_number", o.bad_number]].map(([k, v]) => (
          <button key={k} type="button" className="card stat tile-btn" onClick={() => open(k)} data-testid={`pipe-${k}`}><b>{v}</b><span>{STEP[k]}</span></button>
        ))}
      </div>
      {pick && <Drill pick={pick} period={period} added={added} onClose={() => setPick(null)} />}
      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h3 style={{ marginTop: 0 }}>Lead funnel</h3>
          <p className="sub">Where every lead is right now. Click a bar to see the leads.</p>
          <HBars rows={funnel} onPick={open} testid="pipeline-funnel" /></div>
        <div className="card"><h3 style={{ marginTop: 0 }}>What connected leads said</h3>
          <Donut parts={outcomes} label="outcomes" onPick={open} />
          <h4>Reach</h4><HBars rows={reach} onPick={open} /></div>
      </div>
      <div className="card" style={{ marginTop: 14 }}><h3 style={{ marginTop: 0 }}>Daily trend</h3>
        <p className="sub">{data.range ? `${data.range.from} to ${data.range.to}` : "Last 30 days"} (India time): leads added, first attempt, first connect, first interest.</p>
        <TrendChart days={data.trend} series={[{ key: "new", label: "Added", color: SERIES.slate }, { key: "attempted", label: "Attempted", color: SERIES.blue },
          { key: "connected", label: "Connected", color: SERIES.teal }, { key: "interested", label: "Interested", color: SERIES.green }]} /></div>
      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card"><h3 style={{ marginTop: 0 }}>Call outcomes</h3>
          <p className="sub">{data.calls.total} calls to {data.calls.leads} leads{data.range ? " in this period" : ""}.</p>
          {calls.length ? <HBars rows={calls} testid="call-outcomes" /> : <p className="empty">No calls logged.</p>}</div>
        <div className="card"><h3 style={{ marginTop: 0 }}>Channels tried</h3><p className="sub">Leads attempted on each channel.</p>
          <HBars rows={channels} /><h4>Leads by stage</h4><HBars rows={stages} /></div>
      </div>
      <GroupTable title="By owner" rows={data.by_owner} nameOf={(n) => n} onPick={(step, name) => open(step, { owner: name })} testid="pipeline-by-owner" />
      <GroupTable title="By source" rows={data.by_source} nameOf={sourceLabel} onPick={(step, name) => open(step, { source: name })} testid="pipeline-by-source" />
    </>
  );
}
