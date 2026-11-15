import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { apiRequest as request } from "../api.js";
import { rangeQuery } from "../opsApi.js";
import { ErrorState } from "../states.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import Abbr from "../components/Abbr.jsx";
import "../analytics-pages.css";
import { Button, Card, DataTable, EmptyState, Skeleton, Stat, Stats } from "../ui/index.js";

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
    <form className="card inline-form an-section" onSubmit={save} data-testid="sla-settings">
      <b>Targets, alerts and tracking</b>
      <div className="split-2">
        <label>First attempt within (hours)<input type="number" min="0.25" step="0.25" value={first} onChange={(e) => setFirst(e.target.value)} /></label>
        <label>Answer a reply within (hours)<input type="number" min="0.25" step="0.25" value={reply} onChange={(e) => setReply(e.target.value)} /></label>
      </div>
      <label>Also alert these people about yes / meetings / missed targets (names, comma separated)
        <input value={managers} onChange={(e) => setManagers(e.target.value)} placeholder="e.g. Shivam Giri, Rajesh Ramachandran" /></label>
      <label className="check-row"><input type="checkbox" checked={s.alerts_email !== false} onChange={(e) => setS({ ...s, alerts_email: e.target.checked })} /> Send alerts by email too (always shown on the bell)</label>
      <label className="check-row"><input type="checkbox" checked={s.track_email !== false} onChange={(e) => setS({ ...s, track_email: e.target.checked })} /> Track email opens and link clicks</label>
      <div className="row"><Button type="submit">Save</Button>{msg && <small className="sub-inline" role="status">{msg}</small>}</div>
    </form>
  );
}

function Waiting({ title, rows, unit, onOpen, testid }) {
  return (
    <Card className="table-card an-card-table">
      <h3>{title} ({rows.length})</h3>
      {rows.length === 0 ? <EmptyState icon="check" title="Nobody is waiting">Every lead has had its answer in time.</EmptyState> : (
        <div className="table-scroll"><DataTable data-testid={testid}>
          <thead><tr><th>Lead</th><th>Owner</th><th>Since (<Abbr term="IST">IST</Abbr>)</th><th>Waiting</th>{unit === "reply" && <th>Their reply</th>}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={`${r.lead_id}-${r.since}`}><td><button className="link" onClick={() => onOpen(r.lead_id)}>{r.company || "—"}</button></td><td>{r.owner}</td>
              <td>{ago(r.since)}</td><td><b>{hrs(r.hours)}</b></td>{unit === "reply" && <td><small>{r.reply}</small></td>}</tr>
          ))}</tbody>
        </DataTable></div>
      )}
    </Card>
  );
}

export default function ResponseTimes({ period }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [lead, setLead] = useState(null);
  const load = () => { setError(""); request(`/api/reports/sla?${rangeQuery(period)}`).then(setData).catch((e) => setError(e.message)); };
  useEffect(load, [JSON.stringify(period)]);
  if (error) return <ErrorState message="Response times could not load." detail={error} onRetry={load} />;
  if (!data) return <div className="an-skel" aria-busy="true"><div className="an-skel-tiles">{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={96} />)}</div></div>;
  const t = data.targets;
  return (
    <>
      <div className="ui-stats" data-testid="sla-tiles">
        <Stat value={<>{hrs(data.first_touch.median_hours)}</>} label={<>Typical time to first attempt (target {t.first_touch_hours} h)</>} />
        <Stat value={<>{data.first_touch.within_pct ?? "—"}{data.first_touch.within_pct != null && "%"}</>} label={<>First attempts on time ({data.first_touch.measured} leads)</>} />
        <Stat value={<>{hrs(data.reply.median_hours)}</>} label={<>Typical time to answer a reply (target {t.reply_hours} h)</>} />
        <Stat value={<>{data.reply.within_pct ?? "—"}{data.reply.within_pct != null && "%"}</>} label={<>Replies answered on time ({data.reply.measured})</>} />
        <Stat value={<>{data.first_touch.breaches}</>} label={<>Leads with no attempt, past target</>} />
        <Stat value={<>{data.reply.breaches}</>} label={<>Replies waiting, past target</>} />
      </div>
      <p className="an-notes">Clock hours. Owners and managers get one alert per lead when a target is missed. "Typical" is the median.</p>
      <div className="an-grid">
        <Waiting title="Replies waiting for an answer" rows={data.waiting} unit="reply" onOpen={setLead} testid="sla-waiting" />
        <Waiting title="New leads with no attempt yet" rows={data.untouched} unit="lead" onOpen={setLead} testid="sla-untouched" />
      </div>
      {data.owners.length > 0 && (
        <Card className="table-card u-p0 an-section">
          <div className="table-scroll"><DataTable label="Owners" data-testid="sla-owners">
            <thead><tr><th>Owner</th><th>Typical first attempt</th><th>On time</th><th>Typical reply answer</th><th>On time</th><th>No attempt</th><th>Waiting replies</th></tr></thead>
            <tbody>{data.owners.map((o) => (
              <tr key={o.owner}><td><b>{o.owner}</b></td><td>{hrs(o.median_first_touch_hours)}</td><td>{o.first_touch_within_pct ?? "—"}{o.first_touch_within_pct != null && "%"}</td>
                <td>{hrs(o.median_reply_hours)}</td><td>{o.reply_within_pct ?? "—"}{o.reply_within_pct != null && "%"}</td><td>{o.untouched}</td><td>{o.waiting}</td></tr>
            ))}</tbody>
          </DataTable></div>
        </Card>
      )}
      {isOwner() && <Settings targets={t} onSaved={load} />}
      {lead && <LeadDrawer id={lead} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}
