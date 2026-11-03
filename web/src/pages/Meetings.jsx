import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Empty, ErrorState } from "../states.jsx";
import { istDayLabel, istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";

const STATUS_TONE = { scheduled: "ok", cancelled: "", done: "warm" };

export default function Meetings({ go }) {
  const [rows, setRows] = useState(null);
  const [calendar, setCalendar] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);
  const [showCancelled, setShowCancelled] = useState(false);

  const load = () => Promise.all([api.meetings(), api.channels()])
    .then(([list, ch]) => { setRows(list); setCalendar(ch.channels.find((c) => c.id === "calendar")); setError(""); })
    .catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  if (error && !rows) return <ErrorState message="Could not load meetings" detail={error} onRetry={load} />;
  if (!rows) return <p className="empty">Loading…</p>;
  const active = calendar?.providers.find((p) => p.active);
  const shown = rows.filter((m) => showCancelled || m.status !== "cancelled");
  const days = [...new Set(shown.map((m) => istDayLabel(m.start)))];

  return (
    <>
      <h2>Meetings</h2>
      <p className="sub">
        Upcoming demos and calls, in IST. {active ? <>Booking through <b>{active.title}</b>.</> : "No calendar connected yet."} Book one from a lead's panel.
      </p>
      {!active && (
        <div className="tip" style={{ marginBottom: 16 }}>
          Connect Google Calendar, Microsoft 365, Zoom or a booking page (Cal.com, Calendly) to book meetings with a video link.
          <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={() => go?.("integrations")}>Open Integrations</button></div>
        </div>
      )}
      <label className="row" style={{ fontWeight: 400 }}>
        <input type="checkbox" checked={showCancelled} onChange={(e) => setShowCancelled(e.target.checked)} /> Show cancelled
      </label>
      {shown.length === 0 && <Empty>No upcoming meetings.</Empty>}
      {days.map((day) => (
        <div key={day} className="card" style={{ marginBottom: 12 }}>
          <h3 style={{ marginTop: 0 }}>{day}</h3>
          <ul className="today-list">
            {shown.filter((m) => istDayLabel(m.start) === day).map((m) => (
              <li key={m.id} className="today-item meet-item" data-testid="meeting-row">
                <div className="today-text">
                  <b>{istLabel(m.start, false)}</b> · <button className="linkish" onClick={() => setOpen(m.lead_id)}>{m.company || "Lead"}</button>
                  {m.contact_name && <span className="sub-inline"> ({m.contact_name})</span>}
                  <p>{m.title} · {m.provider} <span className={`pill ${STATUS_TONE[m.status] || ""}`}>{m.status}</span></p>
                </div>
                <div className="today-actions">
                  {m.join_url && m.status === "scheduled" && <a className="btn ghost" href={m.join_url} target="_blank" rel="noreferrer">Join</a>}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
