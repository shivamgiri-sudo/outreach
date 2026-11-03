import { useEffect, useState } from "react";
import { api } from "../api.js";
import Icon from "../icons.jsx";
import { ErrorState } from "../states.jsx";
import { STAGE_LABELS } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";

const KIND_ICON = { problem: "flame", task: "mail", call: "phone", nudge: "gift", start: "send" };

function TodayList({ today, onOpen, onDone, go }) {
  return (
    <div className="card today">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>Today</h3>
        <span className="sub" style={{ margin: 0 }}>{today.summary}</span>
      </div>
      {today.items.length === 0 ? (
        <p className="empty">{today.summary}</p>
      ) : (
        <ul className="today-list">
          {today.items.map((item, i) => (
            <li key={`${item.kind}-${item.lead_id}-${item.task_index ?? i}`} className={`today-item k-${item.kind}`}>
              <span className="stat-icon"><Icon name={KIND_ICON[item.kind] || "check"} /></span>
              <div className="today-text">
                <b>{item.title}</b> <span className="sub-inline">· {item.company}</span>
                <p>{item.detail}</p>
              </div>
              <div className="row today-actions">
                {item.kind === "call"
                  ? <button className="btn ghost" onClick={() => go("calls")}>Open call desk</button>
                  : <button className="btn ghost" onClick={() => onOpen(item.lead_id)}>Open lead</button>}
                {item.kind === "task" && <button className="btn ghost" onClick={() => onDone(item.task_index)}>Done</button>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Dashboard({ go }) {
  const [data, setData] = useState(null);
  const [today, setToday] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);

  const load = () => {
    setError("");
    api.dashboard().then(setData).catch((e) => setError(e.message));
    api.today().then(setToday).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  if (error) return <ErrorState message="The dashboard could not load." detail={error} onRetry={load} />;
  if (!data) return <p className="empty">Loading…</p>;
  const max = Math.max(1, ...data.funnel.map((f) => f.count));

  return (
    <>
      <section className="hero">
        <div className="hero-text">
          <span className="hero-kicker">MCN Outreach</span>
          <h2>Dashboard</h2>
          <p className="sub">Who to talk to today, and where every lead stands.</p>
          <div className="row">
            <button className="btn cta" onClick={() => go("leads")}><Icon name="plus" size={16} /> Add leads</button>
            <button className="btn glass" onClick={() => go("setup")}><Icon name="rocket" size={16} /> Go-live checklist</button>
          </div>
        </div>
        <div className="hero-orb" aria-hidden="true" />
      </section>

      {data.setup_done < data.setup_total && (
        <div className="tip" style={{ margin: "16px 0" }}>
          {data.setup_done} of {data.setup_total} connections are set up.{" "}
          <button className="btn ghost" onClick={() => go("setup")}>See the go-live checklist</button>
        </div>
      )}

      {today && <TodayList today={today} go={go} onOpen={setOpen}
        onDone={async (index) => { await api.taskDone(index); load(); }} />}

      <div className="grid g4 kpis">
        <div className="card stat tone-blue"><span className="stat-icon"><Icon name="users" /></span><b>{data.total_leads}</b><span>Total leads</span></div>
        <div className="card stat tone-amber"><span className="stat-icon"><Icon name="flame" /></span><b>{data.hot}</b><span>Hot leads (score 70+)</span></div>
        <div className="card stat tone-violet"><span className="stat-icon"><Icon name="mail" /></span><b>{data.by_stage.interested}</b><span>Said yes</span></div>
        <div className="card stat tone-green"><span className="stat-icon"><Icon name="check" /></span><b>{data.by_stage.customer}</b><span>Customers</span></div>
      </div>

      <div className="grid g2" style={{ marginTop: 16 }}>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Funnel</h3>
          {data.funnel.map((f) => (
            <div className="funnel-row" key={f.stage}>
              <span>{f.stage === "interested" ? "Interested (said yes)" : STAGE_LABELS[f.stage]}</span>
              <div className="bar" role="img" aria-label={`${f.count} leads`} style={{ width: `${(f.count / max) * 100}%` }} />
              <b>{f.count}</b>
            </div>
          ))}
        </div>
        <div className="card">
          <h3 style={{ marginTop: 0 }}>Reminders</h3>
          {data.reminders.length === 0 && (
            <p className="empty">{data.total_leads ? "No hot leads waiting right now." : "No hot leads waiting. Import leads to get started."}</p>
          )}
          {data.reminders.map((r) => (
            <p key={r.lead_id}>
              <button className="linkish" onClick={() => setOpen(r.lead_id)}><b>{r.company}</b></button> — {r.text}
            </p>
          ))}
        </div>
      </div>
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
