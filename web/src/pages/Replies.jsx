import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";
import { Composer } from "./LeadActivity.jsx";
import { useHashParam } from "../route.js";
import { PageHeader, Card, Stat, Button, Badge, EmptyState, Skeleton, Switch } from "../ui/index.js";
import "../inbox-pages.css";

const LABEL_WORDS = { interested: "Interested", question: "Question", not_now: "Not now", wrong_person: "Wrong person", other: "Needs a look",
  not_interested: "Not interested", auto_reply: "Auto-reply", unsubscribe: "Unsubscribe", bounce: "Bounce" };
const LABEL_TONE = { interested: "success", question: "success", not_now: "neutral", wrong_person: "warning", other: "warning", not_interested: "neutral", auto_reply: "muted", unsubscribe: "warning", bounce: "danger" };
const CHANNEL_WORDS = { email: "Email", linkedin: "LinkedIn", whatsapp: "WhatsApp", sms: "SMS", call: "Call" };
const withIst = (local) => (local ? `${local}:00+05:30` : null);
const tomorrowMorning = () => { const d = new Date(Date.now() + (330 + 24 * 60) * 60000).toISOString().slice(0, 10); return `${d}T11:00`; };

function when(hours) {
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// Every email and message in and out for one lead, oldest first.
function Thread({ leadId }) {
  const [t, setT] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.leadThread(leadId).then(setT).catch((e) => setErr(e.message)); }, [leadId]);
  if (err) return <p className="error">{err}</p>;
  if (!t) return <p className="sub">Loading the conversation…</p>;
  if (!t.messages.length) return <p className="sub">No emails or messages yet.</p>;
  return (
    <ol className="trail" data-testid="reply-thread">
      {t.messages.map((m, i) => (
        <li key={i} className={`trail-item ${m.direction}`}>
          <span className="trail-icon" aria-hidden="true">{m.direction === "in" ? "↩" : "✉"}</span>
          <div className="trail-body">
            <div className="row u-between u-gap-8">
              <b>{m.direction === "in" ? "They wrote" : "We sent"}{m.title ? ` · ${m.title}` : ""}</b><span className="sub">{istLabel(m.at)}</span>
            </div>
            <div className="sub">{CHANNEL_WORDS[m.type] || m.type}{m.status ? ` · ${String(m.status).replace(/_/g, " ")}` : ""}{m.detail ? ` · ${m.detail}` : ""}</div>
            {m.body && <p className="trail-text u-prewrap">{m.body}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function ReplyRow({ r, team, onChanged, onOpen, setMessage }) {
  const [panel, setPanel] = useState("");         // "" | thread | reply | snooze
  const [until, setUntil] = useState(tomorrowMorning());
  const [lead, setLead] = useState(null);
  const run = async (action, extra, done) => {
    try { await api.replyAction(r.key, action, extra); setMessage(done); onChanged(); } catch (e) { setMessage(e.message); }
  };
  const toggle = (p) => {
    const next = panel === p ? "" : p;
    setPanel(next);
    if (next && !r.read && canWrite()) api.replyAction(r.key, "read").catch(() => {});
    if (next === "reply" && !lead) api.lead(r.lead_id).then(setLead).catch((e) => setMessage(e.message));
  };
  const subject = r.subject ? (/^re:/i.test(r.subject) ? r.subject : `Re: ${r.subject}`) : `Re: your reply`;
  const quoted = `{{greeting}}\n\n\n\n— On ${istLabel(r.at)} you wrote:\n> ${r.text.split("\n").join("\n> ")}`;
  return (
    <Card className={`reply-card${r.read ? "" : " unread"}`} data-testid="reply-row">
      <div className="reply-head">
        <div className="reply-who">
          <span className="reply-score" title="Score: how much it deserves a person's time now">{r.score}</span>
          <button className="link" onClick={() => onOpen(r.lead_id)}><b>{r.company || r.contact || "Lead"}</b></button>
          <span className="sub-inline">{r.contact}{r.title ? ` · ${r.title}` : ""}</span>
        </div>
        <div className="reply-meta">
          <Badge tone={LABEL_TONE[r.label] || "neutral"} title={r.label_overridden ? `Corrected by a person (was ${LABEL_WORDS[r.guessed_label] || r.guessed_label})` : "Detected"}>
            {LABEL_WORDS[r.label] || r.label}{r.label_overridden ? " ✎" : ""}</Badge>
          {r.handled && <Badge tone="success">Handled{r.handled_by ? ` by ${r.handled_by}` : ""}</Badge>}
          {r.snoozed_until && <Badge>Snoozed to {istLabel(r.snoozed_until)}</Badge>}
          {r.assignee && <Badge>→ {r.assignee}</Badge>}
          <span>{CHANNEL_WORDS[r.channel] || r.channel} · {when(r.age_hours)}</span>
        </div>
      </div>
      <p className="reply-text">{r.text.slice(0, 400)}{r.text.length > 400 ? "…" : ""}</p>
      <p className="reply-next">Next step: {r.next_step}</p>
      <div className="reply-actions" aria-label="Reply actions">
        {canWrite() && <Button onClick={() => toggle("reply")}>Reply</Button>}
        <Button variant="secondary" onClick={() => toggle("thread")} aria-expanded={panel === "thread"}>{panel === "thread" ? "Hide thread" : "Thread"}</Button>
        {canWrite() && <>{r.handled
          ? <Button variant="secondary" onClick={() => run("unhandled", {}, "Marked not handled.")}>Mark not handled</Button>
          : <Button variant="secondary" onClick={() => run("handled", {}, "Marked handled.")}>Mark handled</Button>}
        <Button variant="secondary" onClick={() => toggle("snooze")}>Snooze…</Button>
        <Button variant="ghost" onClick={() => run(r.read ? "unread" : "read", {}, r.read ? "Marked unread." : "Marked read.")}>{r.read ? "Mark unread" : "Mark read"}</Button>
        <select aria-label="Assign to" value={r.assignee || ""} onChange={(e) => run("assign", { assignee: e.target.value }, e.target.value ? `Assigned to ${e.target.value}.` : "Unassigned.")}>
          <option value="">Assign to…</option>{team.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <select aria-label="Correct the label" value="" onChange={(e) => e.target.value && run("label", { label: e.target.value }, `Label set to ${LABEL_WORDS[e.target.value]}.`)}>
          <option value="">Change label…</option>{Object.entries(LABEL_WORDS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select></>}
      </div>
      {panel === "snooze" && canWrite() && (
        <form className="reply-snooze" onSubmit={(e) => { e.preventDefault(); run("snooze", { until: withIst(until) }, "Snoozed; a task will remind you then.").then(() => setPanel("")); }}>
          <label>Remind me at (IST)<input type="datetime-local" required value={until} onChange={(e) => setUntil(e.target.value)} /></label>
          <Button type="submit">Snooze</Button><Button variant="secondary" onClick={() => setPanel("")}>Cancel</Button>
        </form>
      )}
      {panel === "thread" && <div className="reply-thread-card"><Thread leadId={r.lead_id} /></div>}
      {panel === "reply" && canWrite() && (lead ? (lead.email
        ? <Composer lead={lead} initialSubject={subject} initialBody={quoted} note={`Replying to ${r.contact || r.company}'s ${CHANNEL_WORDS[r.channel] || ""} reply.`}
            onCancel={() => setPanel("")}
            onDone={(m) => { setPanel(""); setMessage(m); api.replyAction(r.key, "handled").then(onChanged).catch(onChanged); }} />
        : <p className="sub">This lead has no email address. Call them or reply on {CHANNEL_WORDS[r.channel] || "their channel"}.</p>)
        : <p className="sub">Loading…</p>)}
    </Card>
  );
}

export default function Replies() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [label, setLabel] = useState("");
  const [channel, setChannel] = useState("");
  const [assignee, setAssignee] = useState("");
  const [onlyAction, setOnlyAction] = useState(true);
  const [lead, setLead] = useHashParam("lead");
  const [message, setMessage] = useState("");
  const load = () => {
    setError("");
    api.replyInbox({ label, channel, only_action: onlyAction, assignee }).then(setData).catch((e) => setError(e.message));
  };
  useEffect(() => { load(); }, [label, channel, onlyAction, assignee]);

  return (
    <>
      <PageHeader title="Replies"
        description={<>Every reply from every channel in one list. The score (0–100) says how much a reply deserves a person's time now. Reply, mark handled, assign or snooze it right here.</>} />
      {error && <ErrorState message="Replies could not load." detail={error} onRetry={load} />}
      {data ? (
        <div className="sum-grid" data-testid="reply-kpis">
          <Stat label="need a person" value={data.needs_action} tone={data.needs_action ? "amber" : undefined} />
          <Stat label="unread" value={data.unread ?? 0} />
          <Stat label="said yes" value={data.counts.interested || 0} tone="green" />
          <Stat label="replies in all" value={data.total} />
        </div>
      ) : !error && <div className="sum-grid" aria-busy="true">{[0, 1, 2, 3].map((i) => <Skeleton key={i} height={72} />)}</div>}
      <div className="filter-bar">
        <Switch checked={onlyAction} onChange={setOnlyAction} label="Only ones that need a person" />
        <span className="sub-inline">Only ones that need a person</span>
        <select aria-label="Kind of reply" value={label} onChange={(e) => setLabel(e.target.value)}>
          <option value="">All kinds</option>
          {(data?.labels || []).map((l) => <option key={l} value={l}>{LABEL_WORDS[l] || l}{data?.counts[l] ? ` (${data.counts[l]})` : ""}</option>)}
        </select>
        <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All channels</option>
          {(data?.channels || []).map((c) => <option key={c} value={c}>{CHANNEL_WORDS[c] || c}</option>)}
        </select>
        <select aria-label="Assigned to" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">Anyone</option><option value="__unassigned">Unassigned</option>
          {(data?.team || []).map((n) => <option key={n} value={n}>{n} (and unassigned)</option>)}
        </select>
      </div>
      {message && <div className="tip u-mb-12" role="status">{message}</div>}
      {data && data.replies.length === 0 && (
        <Card><EmptyState icon="inbox" title={onlyAction ? "Nothing needs a person right now" : "No replies yet"}
          action={onlyAction ? <Button variant="secondary" onClick={() => setOnlyAction(false)}>Show every reply</Button> : undefined}>
          Replies appear here when they are read from your inbox, LinkedIn agent or pasted on a lead.</EmptyState></Card>
      )}
      <div className="stack">
        {data && data.replies.map((r) => (
          <ReplyRow key={r.key} r={r} team={data.team || []} onChanged={load} onOpen={setLead} setMessage={setMessage} />
        ))}
      </div>
      {lead && <LeadDrawer id={lead} ids={[...new Set((data?.replies || []).map((r) => r.lead_id))]} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}
