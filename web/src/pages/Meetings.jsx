import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";
import { istDayLabel, istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import Abbr from "../components/Abbr.jsx";
import { PageHeader, Card, CardHeader, Button, Badge, EmptyState, Skeleton, Switch } from "../ui/index.js";
import "../inbox-pages.css";

const STATUS_TONE = { scheduled: "success", cancelled: "muted", done: "warning" };

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
  if (!rows) return <div aria-busy="true"><PageHeader title="Meetings" /><div className="stack"><Skeleton height={90} /><Skeleton height={90} /></div></div>;
  const active = calendar?.providers.find((p) => p.active);
  const shown = rows.filter((m) => showCancelled || m.status !== "cancelled");
  const days = [...new Set(shown.map((m) => istDayLabel(m.start)))];

  return (
    <>
      <PageHeader title="Meetings"
        description={<>Upcoming demos and calls, in <Abbr term="IST">IST</Abbr>. {active ? <>Booking through <b>{active.title}</b>.</> : "No calendar connected yet."} Book one from a lead's panel.</>} />
      {!active && (
        <div className="tip u-mb-16">
          Connect Google Calendar, Microsoft 365, Zoom or a booking page (Cal.com, Calendly) to book meetings with a video link.
          <div className="row u-mt-8"><Button onClick={() => go?.("integrations")}>Open Integrations</Button></div>
        </div>
      )}
      <div className="filter-bar">
        <Switch checked={showCancelled} onChange={setShowCancelled} label="Show cancelled" />
        <span className="sub-inline">Show cancelled</span>
        <span className="push">
          <DownloadButton path={exportPath("meetings")} label="Download all meetings" testId="download-meetings"
            title="Past, upcoming and cancelled meetings with times in IST" />
        </span>
      </div>
      {shown.length === 0 && <Card><EmptyState icon="calendar" title="No upcoming meetings">Book one from a lead's panel: open a lead, then Meetings.</EmptyState></Card>}
      <div className="stack">
      {days.map((day) => (
        <Card key={day}>
          <CardHeader title={day} />
          <div className="meet-day">
            {shown.filter((m) => istDayLabel(m.start) === day).map((m) => (
              <div key={m.id} className="meet-row" data-testid="meeting-row">
                <span className="meet-time">{istLabel(m.start, false)}</span>
                <div className="meet-main">
                  <span><button className="linkish" onClick={() => setOpen(m.lead_id)}><b>{m.company || "Lead"}</b></button>
                    {m.contact_name && <span className="sub-inline"> ({m.contact_name})</span>}</span>
                  <p>{m.title} · {m.provider} <Badge tone={STATUS_TONE[m.status] || "neutral"}>{m.status}</Badge></p>
                </div>
                <div>
                  {m.join_url && m.status === "scheduled" && <a className="btn ghost" href={m.join_url} target="_blank" rel="noreferrer">Join</a>}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
      </div>
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
