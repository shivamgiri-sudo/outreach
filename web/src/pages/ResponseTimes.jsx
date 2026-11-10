import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { apiRequest as request } from "../api.js";
import { rangeQuery } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import Abbr from "../components/Abbr.jsx";

// Response times: how fast new leads get a first attempt and replies get an answer, against the targets, with the
// leads that are waiting right now. Owners set the targets and who gets alerts, and can switch email tracking off.
const hrs = (h) => (h == null ? "—" : h < 1 ? `${Math.round(h * 60)} min` : `${h} h`);
const ago = (iso) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });

function Settings({ targets, onSaved }) {
  const [first, setFirst] = useState(targets.first_touch_hours);
  const [reply, setReply] = useState(targets.reply_hours);
  const [s, setS] = useState(null);
  const [managers, setManagers] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api.settings().then((x) => { setS(x); setManagers((x.alert_managers || []).join(", ")); }).catch(() => {}); }, []);
  const save = async (e) => {
    e.preventDefault(); setMsg("");
    try {
      await request("/api/reports/sla-targets", { method: "PUT", body: { first_touch_hours: Number(first), reply_hours: Number(reply) } });
      await api.saveSettings({ alert_managers: managers.split(",").map((x) => x.trim()).filter(Boolean),
        alerts_email: s?.alerts_email !== false, track_email: s?.track_email !== false });
      setMsg("Saved."); onSaved();
    } catch (err) { setMsg(err.message); }
  };
  if (!s) return null;
  return (
    <form className="card inline-form" onSubmit={save} style={{ marginTop: 14 }} data-testid="sla-settings">
      <b>Targets, alerts and tracking</b>
      <div className="split-2">
        <label>First attempt within (hours)<input type="number" min="0.25" step="0.25" value={first} onChange={(e) => setFirst(e.target.value)} /></label>
        <label>Answer a reply within (hours)<input type="number" min="0.25" step="0.25" value={reply} onChange={(e) => setReply(e.target.value)} /></label>
      </div>
      <label>Also alert these people about yes / meetings / missed targets (names, comma separated)
        <input value={managers} onChange={(e) => setManagers(e.target.value)} placeholder="e.g. Shivam Giri, Rajesh Ramachandran" /></label>
      <label className="check-row"><input type="checkbox" checked={s.alerts_email !== false} onChange={(e) => setS({ ...s, alerts_email: e.target.checked })} /> Send alerts by email too (always shown on the bell)</label>
      <label className="check-row"><input type="checkbox" checked={s.track_email !== false} onChange={(e) => setS({ ...s, track_email: e.target.checked })} /> Track email opens and link clicks</label>
      <div className="row"><button className="btn">Save</button>{msg && <small className="sub-inline" role="status">{msg}</small>}</div>
    </form>
  );
}

function Waiting({ title, rows, unit, onOpen, testid }) {
  return (
    <div className="card table-card" style={{ padding: 0 }}>
      <h3 style={{ margin: "14px 16px 6px" }}>{title} ({rows.length})</h3>
      {rows.length === 0 ? <p className="empty">Nobody is waiting. Well done.</p> : (
        <div className="table-scroll"><table data-testid={testid}>
          <thead><tr><th>Lead</th><th>Owner</th><th>Since (<Abbr term="IST">IST</Abbr>)</th><th>Waiting</th>{unit === "reply" && <th>Their reply</th>}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={`${r.lead_id}-${r.since}`}><td><button className="link" onClick={() => onOpen(r.lead_id)}>{r.company || "—"}</button></td><td>{r.owner}</td>
              <td>{ago(r.since)}</td><td><b>{hrs(r.hours)}</b></td>{unit === "reply" && <td><small>{r.reply}</small></td>}</tr>
          ))}</tbody>
        </table></div>
      )}
    </div>
  );
}

export default function ResponseTimes({ period }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [lead, setLead] = useState(null);
  const load = () => { setError(""); request(`/api/reports/sla?${rangeQuery(period)}`).then(setData).catch((e) => setError(e.message)); };
  useEffect(load, [JSON.stringify(period)]);
  if (error) return <ErrorState message="Response times could not load." detail={error} onRetry={load} />;
  if (!data) return <p className="empty">Loading…</p>;
  const t = data.targets;
  return (
    <>
      <div className="stat-grid" data-testid="sla-tiles">
        <div className="card stat"><b>{hrs(data.first_touch.median_hours)}</b><span className="sub-inline">Typical time to first attempt (target {t.first_touch_hours} h)</span></div>
        <div className="card stat"><b>{data.first_touch.within_pct ?? "—"}{data.first_touch.within_pct != null && "%"}</b><span className="sub-inline">First attempts on time ({data.first_touch.measured} leads)</span></div>
        <div className="card stat"><b>{hrs(data.reply.median_hours)}</b><span className="sub-inline">Typical time to answer a reply (target {t.reply_hours} h)</span></div>
        <div className="card stat"><b>{data.reply.within_pct ?? "—"}{data.reply.within_pct != null && "%"}</b><span className="sub-inline">Replies answered on time ({data.reply.measured})</span></div>
        <div className="card stat"><b>{data.first_touch.breaches}</b><span className="sub-inline">Leads with no attempt, past target</span></div>
        <div className="card stat"><b>{data.reply.breaches}</b><span className="sub-inline">Replies waiting, past target</span></div>
      </div>
      <p className="sub">Clock hours. Owners and managers get one alert per lead when a target is missed. "Typical" is the median.</p>
      <div className="grid g2" style={{ marginTop: 10 }}>
        <Waiting title="Replies waiting for an answer" rows={data.waiting} unit="reply" onOpen={setLead} testid="sla-waiting" />
        <Waiting title="New leads with no attempt yet" rows={data.untouched} unit="lead" onOpen={setLead} testid="sla-untouched" />
      </div>
      {data.owners.length > 0 && (
        <div className="card table-card" style={{ padding: 0, marginTop: 14 }}>
          <div className="table-scroll"><table data-testid="sla-owners">
            <thead><tr><th>Owner</th><th>Typical first attempt</th><th>On time</th><th>Typical reply answer</th><th>On time</th><th>No attempt</th><th>Waiting replies</th></tr></thead>
            <tbody>{data.owners.map((o) => (
              <tr key={o.owner}><td><b>{o.owner}</b></td><td>{hrs(o.median_first_touch_hours)}</td><td>{o.first_touch_within_pct ?? "—"}{o.first_touch_within_pct != null && "%"}</td>
                <td>{hrs(o.median_reply_hours)}</td><td>{o.reply_within_pct ?? "—"}{o.reply_within_pct != null && "%"}</td><td>{o.untouched}</td><td>{o.waiting}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      {isOwner() && <Settings targets={t} onSaved={load} />}
      {lead && <LeadDrawer id={lead} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}
