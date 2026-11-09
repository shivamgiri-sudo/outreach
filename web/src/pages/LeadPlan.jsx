import { useEffect, useState } from "react";
import { api } from "../api.js";

// The lead's communication plan: each email and LinkedIn step (done when and by whom, next and when, skipped, not
// sent because it stopped), open follow-ups, and what the system did by itself. Read-only; reloads with the lead.
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }) : "");
const day = (iso) => (iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "Asia/Kolkata" }) : "");
const PILL = { done: ["ok", "Done"], next: ["warm", "Next"], waiting: ["", "Waiting"], skipped: ["", "Skipped"], not_sent: ["", "Not sent"], later: ["", "Later"] };

function Step({ label, row }) {
  const [cls, word] = PILL[row.state] || ["", row.state];
  const detail = row.state === "done" ? `${when(row.at)}${row.by ? ` · ${row.by}` : ""}${row.opens ? ` · opened ${row.opens}×` : ""}${row.clicks ? ` · clicked ${row.clicks}×` : ""}`
    : row.state === "next" ? `due ${day(row.due_at) || "now"}${row.note ? ` · held: ${row.note}` : ""}` : row.note || "";
  return (
    <li><span className={`pill ${cls}`}>{word}</span> {label}{detail && <small className="sub-inline"> — {detail}</small>}</li>
  );
}

export default function LeadPlan({ lead }) {
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { setError(""); api.leadPlan(lead.id).then(setPlan).catch((e) => setError(e.message)); }, [lead.id, lead.timeline?.length]);
  if (error) return <p className="sub">Communication plan could not load: {error}</p>;
  if (!plan) return null;
  const { linkedin: li, email, follow_ups: tasks, automatic } = plan;
  return (
    <details className="card" open style={{ margin: "10px 0" }} data-testid="lead-plan">
      <summary><b>Communication plan</b> <span className="sub">— what is done, what is next, what the system did</span></summary>
      <div className="split-2">
        <div data-testid="plan-email">
          <h4 style={{ margin: "8px 0 4px" }}>Email {email?.practice && <span className="pill">practice</span>}</h4>
          {!email ? <p className="sub">Not on an email sequence.</p> : <>
            {email.stopped && <p className="sub">Stopped: {email.stopped}</p>}
            <ul className="plain-list">{email.steps.map((r) => <Step key={r.day} label={`Day ${r.day}: ${r.subject || r.name}`} row={r} />)}</ul>
          </>}
        </div>
        <div data-testid="plan-linkedin">
          <h4 style={{ margin: "8px 0 4px" }}>LinkedIn</h4>
          {!li ? <p className="sub">Not on the LinkedIn sequence.</p> : <>
            <p className="sub">{li.status_text}{li.stopped ? `: ${li.stopped}` : ""}{li.account ? ` · account ${li.account}` : ""}
              <br />Started {when(li.started_at)}{li.auto_started ? " (automatically)" : ""}{li.accepted_at ? ` · accepted ${when(li.accepted_at)}` : ""}</p>
            <ul className="plain-list">{li.steps.map((r) => <Step key={r.n} label={`Step ${r.n}: ${r.kind === "connect" ? "connection request" : r.purpose}`} row={r} />)}</ul>
          </>}
        </div>
      </div>
      <h4 style={{ margin: "10px 0 4px" }}>Follow-ups to do ({tasks.length})</h4>
      {tasks.length === 0 ? <p className="sub">Nothing open.</p> : (
        <ul className="plain-list" data-testid="plan-followups">{tasks.map((t, i) => <li key={i}>{t.title} <small className="sub-inline">— due {day(t.due_at) || "now"}{t.owner ? ` · ${t.owner}` : ""}</small></li>)}</ul>
      )}
      <h4 style={{ margin: "10px 0 4px" }}>Done automatically by the system ({automatic.length})</h4>
      {automatic.length === 0 ? <p className="sub">Nothing yet.</p> : (
        <ul className="plain-list" data-testid="plan-automatic">{automatic.slice(0, 15).map((a, i) => <li key={i}><small className="sub-inline">{when(a.at)} · {a.who}:</small> {a.what}</li>)}</ul>
      )}
    </details>
  );
}
