import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";

export default function Insights() {
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
      <h2>Insights</h2>
      <p className="sub">What is working. Rates are of leads we have emailed.</p>
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

      <div className="grid g2" style={{ marginTop: 14 }}>
        <div className="card" style={{ padding: 0, overflowX: "auto" }}>
          <h3 style={{ margin: 16 }}>By lead source</h3>
          {d.by_source.length === 0 ? <p className="empty">No data yet.</p> : (
            <table>
              <thead><tr><th>Source</th><th>Leads</th><th>Replied</th><th>Interested</th><th>Customers</th></tr></thead>
              <tbody>{d.by_source.map((s) => (
                <tr key={s.source}><td>{s.source}</td><td>{s.leads}</td><td>{s.replied}</td><td>{s.interested}</td><td>{s.customers}</td></tr>
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
    </>
  );
}
