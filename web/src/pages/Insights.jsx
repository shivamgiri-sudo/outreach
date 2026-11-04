import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { sourceLabel, stageLabel, useSourceRegistry } from "../labels.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";

const STAGE_COLUMNS = ["new", "contacted", "engaged", "interested", "call_done", "customer", "unsubscribed", "disqualified"];
const money = (n) => (n == null ? "—" : `₹${Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`);

// The owner types what each source costs per month; the page then shows what a lead and a customer cost.
function SourceSpend({ rows, saved, onSaved }) {
  const [values, setValues] = useState(() => Object.fromEntries(rows.map((r) => [r.source, saved[r.source] ?? ""])));
  const [state, setState] = useState("");
  const save = async () => {
    setState("Saving…");
    try {
      await api.saveSourceSpend(values);
      setState("Saved");
      onSaved();
    } catch (e) { setState(e.message); }
  };
  return (
    <details className="card" style={{ marginTop: 14 }}>
      <summary><b>Cost per lead and per customer</b> <small className="sub-inline">type what you spend on each source per month</small></summary>
      <div className="grid g2" style={{ marginTop: 10 }}>
        {rows.map((r) => (
          <div className="field" key={r.source}>
            <label htmlFor={`spend-${r.source}`}>{sourceLabel(r.source)}: monthly spend (₹)</label>
            <input id={`spend-${r.source}`} inputMode="decimal" value={values[r.source]} onChange={(e) => setValues({ ...values, [r.source]: e.target.value })} />
          </div>
        ))}
      </div>
      <div className="row"><button className="btn" onClick={save}>Save spend</button> <span role="status">{state}</span></div>
      <p className="sub">Cost per lead is the monthly spend divided by the leads that source brought in over the last 30 days; cost per customer divides by those that became customers.</p>
    </details>
  );
}

export default function Insights() {
  useSourceRegistry();
  const [d, setD] = useState(null);
  const [error, setError] = useState("");
  const load = () => { setError(""); api.insights().then(setD).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, []);
  if (error) return <ErrorState message="Insights could not load." detail={error} onRetry={load} />;
  if (!d) return <p className="empty">Loading…</p>;
  const o = d.outreach;
  const maxSector = Math.max(1, ...d.by_sector.map((s) => s.leads));

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>Insights</h2><p className="sub">What is working. Rates are of leads we have emailed.</p></div>
        <DownloadButton path={exportPath("insights")} label="Download insights" testId="download-insights"
          title="Every number on this page as one table: section, item, measure, value" />
      </div>
      <div className="grid g4">
        <div className="card stat"><b>{d.totals.leads}</b><span>Leads</span></div>
        <div className="card stat"><b>{d.totals.emails_sent}</b><span>Emails sent</span></div>
        <div className="card stat"><b>{o.reply_rate}%</b><span>Reply rate</span></div>
        <div className="card stat"><b>{o.interested}</b><span>Interested or further</span></div>
        <div className="card stat"><b>{o.unsubscribe_rate}%</b><span>Unsubscribe rate</span></div>
        <div className="card stat"><b>{d.totals.whatsapp_sent}</b><span>WhatsApp sent</span></div>
        <div className="card stat"><b>{d.totals.customers}</b><span>Customers</span></div>
        <div className="card stat"><b>{d.by_band.hot} / {d.by_band.warm} / {d.by_band.cold}</b><span>Hot / warm / cold</span></div>
      </div>

      <div className="grid gw" style={{ marginTop: 14 }}>
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <h3 style={{ margin: 16 }}>By lead source</h3>
          {d.by_source.length === 0 ? <p className="empty">No data yet.</p> : (
            <table>
              <thead><tr><th>Source</th><th>Leads</th><th>Replied</th><th>Interested</th><th>Customers</th><th>Cost per lead</th><th>Cost per customer</th></tr></thead>
              <tbody>{d.by_source.map((s) => (
                <tr key={s.source}><td>{sourceLabel(s.source)}</td><td>{s.leads}</td><td>{s.replied}</td><td>{s.interested}</td><td>{s.customers}</td>
                  <td>{money(s.cost_per_lead)}</td><td>{money(s.cost_per_customer)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </div>
        <div className="card" style={{ padding: 0, overflowX: "auto" }} data-testid="stage-by-source">
          <h3 style={{ margin: 16 }}>Stage by source</h3>
          {d.by_source.length === 0 ? <p className="empty">No data yet.</p> : (
            <table>
              <thead><tr><th>Source</th>{STAGE_COLUMNS.map((st) => <th key={st}>{stageLabel(st)}</th>)}</tr></thead>
              <tbody>{d.by_source.map((s) => (
                <tr key={s.source}><td>{sourceLabel(s.source)}</td>{STAGE_COLUMNS.map((st) => <td key={st}>{s.stages?.[st] || 0}</td>)}</tr>
              ))}</tbody>
            </table>
          )}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>By sector</h3>
          {d.by_sector.length === 0 && <p className="empty">No data yet.</p>}
          {d.by_sector.map((s) => (
            <div className="funnel-row" key={s.sector}>
              <span>{s.sector}</span>
              <div className="bar" style={{ width: `${(s.leads / maxSector) * 100}%` }} />
              <b>{s.leads}</b>
            </div>
          ))}
        </div>
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <h3 style={{ margin: 16 }}>Top leads right now</h3>
          {d.top_leads.length === 0 ? <p className="empty">No leads yet.</p> : (
            <table>
              <thead><tr><th>Company</th><th>Score</th><th>Stage</th></tr></thead>
              <tbody>{d.top_leads.map((l) => <tr key={l.id}><td>{l.company}</td><td>{l.score}</td><td>{l.stage}</td></tr>)}</tbody>
            </table>
          )}
        </div>
      </div>
      {isOwner() && d.by_source.length > 0 && <SourceSpend rows={d.by_source} saved={d.source_spend || {}} onSaved={load} />}
    </>
  );
}
