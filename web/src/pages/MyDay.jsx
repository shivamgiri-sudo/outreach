import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { istDayLabel, istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";
import { Button, PageHeader, Card, CardHeader, Badge, Skeleton, Stat } from "../ui/index.js";
import "../myday.css";

const TARGETS = [["calls", "Calls"], ["connects", "Connects"], ["emails", "Emails"], ["linkedin", "LinkedIn"], ["whatsapp", "WhatsApp"]];
const ACTION_ICON = { call: "📞", email: "✉", whatsapp: "🟢", open: "→" };

function Progress({ label, done, target }) {
  const pct = target ? Math.min(100, Math.round((done / target) * 100)) : 0;
  return (
    <div className="kpi md-kpi" data-testid={`target-${label.toLowerCase()}`}>
      <b>{done}<small className="sub md-of"> / {target}</small></b>
      <span>{label} today</span>
      <div className="md-track" role="progressbar" aria-label={`${label}: ${done} of ${target}`} aria-valuenow={done} aria-valuemin={0} aria-valuemax={target || 0}>
        <div className={`md-fill${pct >= 100 ? " done" : ""}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function TargetEditor({ day, onSaved, onCancel }) {
  const [values, setValues] = useState({ ...day.targets });
  const [scope, setScope] = useState("me");
  const [msg, setMsg] = useState("");
  const save = async (e) => {
    e.preventDefault();
    const clean = Object.fromEntries(TARGETS.map(([k]) => [k, Math.max(0, parseInt(values[k], 10) || 0)]));
    try { await api.saveTargets(clean, scope); onSaved(); } catch (err) { setMsg(err.message); }
  };
  return (
    <form className="inline-form" onSubmit={save} aria-label="Daily targets">
      <div className="row u-wrap">
        {TARGETS.map(([k, l]) => (
          <label key={k} className="u-maxwidth-120">{l}<input type="number" min="0" max="1000" value={values[k] ?? 0} onChange={(e) => setValues({ ...values, [k]: e.target.value })} /></label>
        ))}
      </div>
      {isOwner() && <label>Save for<select value={scope} onChange={(e) => setScope(e.target.value)}>
        <option value="me">Just me</option><option value="team">The whole team (default for everyone)</option></select></label>}
      {msg && <p className="error" role="alert">{msg}</p>}
      <div className="row"><Button type="submit">Save targets</Button><Button variant="secondary" onClick={onCancel}>Cancel</Button></div>
    </form>
  );
}

// One row: what it is, why, and one-click buttons for the next best action.
function Item({ title, sub, item, onOpen, go, overdue }) {
  const next = item.next || { action: "open", label: "Open" };
  const leadId = item.lead_id;
  const lead = (v) => (v === next.action ? "primary" : "secondary");
  return (
    <li className={`md-item${overdue ? " overdue" : ""}`} data-testid="myday-item">
      <div className="md-text">
        <b>{title}</b>{sub && <span className="sub"> — {sub}</span>}
        {next.why && <div className="sub">{ACTION_ICON[next.action] || ""} {next.why}</div>}
      </div>
      <div className="md-actions">
        {item.phone && <a className={`btn sm ${next.action === "call" ? "" : "ghost"}`} href={`tel:${item.phone}`} title="Dial now">Call</a>}
        {item.email && canWrite() && <Button size="sm" variant={lead("email")} onClick={() => onOpen(leadId, "activity")}>Write email</Button>}
        {item.phone && next.action === "whatsapp" && canWrite() && <Button size="sm" onClick={() => onOpen(leadId, "outreach")}>WhatsApp</Button>}
        {next.action === "call" && item.phone && <Button size="sm" variant="secondary" onClick={() => go("calls")} title="Call it from the call desk with the script">Call desk</Button>}
        {leadId && <Button size="sm" variant="ghost" onClick={() => onOpen(leadId)}>Open</Button>}
      </div>
    </li>
  );
}

function Section({ title, count, empty, children, testId }) {
  return (
    <Card data-testid={testId}>
      <CardHeader title={title} actions={count > 0 ? <Badge>{count}</Badge> : null} />
      {count === 0 ? <p className="sub u-m0">{empty}</p> : <ul className="md-list">{children}</ul>}
    </Card>
  );
}

export default function MyDay({ go }) {
  const [day, setDay] = useState(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(null);
  const [editing, setEditing] = useState(false);
  const load = () => { setError(""); api.myDay().then(setDay).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, []);
  const openLead = (id, tab) => {
    if (tab) { try { localStorage.setItem("lead-drawer-tab", tab); } catch { /* storage blocked */ } }
    setOpen(id);
  };
  if (error) return <ErrorState message="Your day could not load." detail={error} onRetry={load} />;
  if (!day) return <div aria-busy="true"><PageHeader title="My day" description="Loading your day…" /><div className="md-grid"><Skeleton height={120} /><Skeleton height={120} /></div></div>;
  const c = day.counts;
  const ids = [...new Set([...day.callbacks, ...day.calls_due, ...day.replies, ...day.hot_leads, ...day.tasks].map((x) => x.lead_id).filter(Boolean))];
  const callable = c.callbacks + c.calls_due + c.hot_leads;
  return (
    <>
      <PageHeader title="My day" description={`${istDayLabel(new Date().toISOString())} · ${day.me.name}`} actions={<>
        <Button onClick={() => go("calls")} data-testid="myday-start-calling">Start calling{callable ? ` (${callable})` : ""}</Button>
        <Button variant="secondary" onClick={() => go("replies")}>Replies{c.replies ? ` (${c.replies})` : ""}</Button>
      </>} />

      <div className="kpi-row" data-testid="myday-targets">
        {TARGETS.filter(([k]) => day.targets[k] > 0 || day.today[k] > 0).map(([k, l]) => <Progress key={k} label={l} done={day.today[k] || 0} target={day.targets[k] || 0} />)}
      </div>
      <p className="sub u-mt-0 u-mb-16">
        This week: {day.week.calls} calls, {day.week.connects} connects, {day.week.emails} emails, {day.week.linkedin} LinkedIn, {day.week.whatsapp} WhatsApp, {day.week.tasks_done} tasks done.
        {canWrite() && <>{" "}<button className="link" onClick={() => setEditing(!editing)}>{editing ? "Close targets" : "Change targets"}</button></>}
        {!day.personal_targets && " (team targets)"}
      </p>
      {editing && canWrite() && <TargetEditor day={day} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); load(); }} />}

      <div className="md-grid">
        <Section title="Call-backs due" count={c.callbacks} empty="No call-backs due today." testId="myday-callbacks">
          {day.callbacks.map((t) => <Item key={t.id} item={t} title={t.company || t.title} sub={`${t.overdue || new Date(t.due_at) < new Date() ? "was due" : "due"} ${istLabel(t.due_at)}${t.text ? ` · ${t.text.slice(0, 80)}` : ""}`}
            overdue={new Date(t.due_at) < new Date()} onOpen={openLead} go={go} />)}
        </Section>
        <Section title="Replies to handle" count={c.replies} empty="No replies waiting for you." testId="myday-replies">
          {day.replies.map((r) => <Item key={r.key} item={r} title={r.company || r.contact} sub={`${r.label.replace(/_/g, " ")} · ${r.text.slice(0, 90)}`} onOpen={openLead} go={go} />)}
        </Section>
        <Section title="Calls due" count={c.calls_due} empty="No call tasks due." testId="myday-calls">
          {day.calls_due.map((t) => <Item key={t.id} item={t} title={t.company || t.title} sub={t.due_at ? `due ${istLabel(t.due_at)}` : ""} overdue={t.due_at && new Date(t.due_at) < new Date()} onOpen={openLead} go={go} />)}
        </Section>
        <Section title="My tasks due" count={c.tasks} empty={c.upcoming ? `Nothing due today; ${c.upcoming} later.` : "No tasks due."} testId="myday-tasks">
          {day.tasks.map((t) => (
            <li key={t.id} className={`md-item${t.due_at && new Date(t.due_at) < new Date() ? " overdue" : ""}`} data-testid="myday-task">
              <div className="md-text"><b>{t.title}</b>{t.company && <span className="sub"> — {t.company}</span>}
                <div className="sub">{t.due_at ? `Due ${istLabel(t.due_at)}` : ""}{t.text ? ` · ${t.text.slice(0, 90)}` : ""}</div></div>
              <div className="md-actions">
                {canWrite() && <Button size="sm" variant="secondary" onClick={() => api.taskDoneById(t.id, "").then(load)}>Done</Button>}
                {canWrite() && <Button size="sm" variant="secondary" onClick={() => api.updateTask(t.id, { snooze_minutes: 1440 }).then(load)}>Tomorrow</Button>}
                {t.lead_id && <Button size="sm" variant="ghost" onClick={() => openLead(t.lead_id)}>Open</Button>}
              </div>
            </li>
          ))}
        </Section>
        <Section title="Hot leads to call" count={c.hot_leads} empty="No hot leads with a phone waiting." testId="myday-hot">
          {day.hot_leads.map((l) => <Item key={l.lead_id} item={l} title={l.company || l.contact_name} sub={`score ${l.score}${l.last_touch ? ` · last touch ${istLabel(l.last_touch)}` : ""}`} onOpen={openLead} go={go} />)}
        </Section>
        <Section title="Meetings today" count={c.meetings} empty="No meetings today." testId="myday-meetings">
          {day.meetings.map((m, i) => (
            <li key={i} className="md-item">
              <div className="md-text"><b>{istLabel(m.start, false)} · {m.title}</b><span className="sub"> — {m.company}</span></div>
              <div className="md-actions">
                {/^https:\/\//.test(m.join_url) && <a className="btn sm" href={m.join_url} target="_blank" rel="noopener noreferrer">Join</a>}
                <Button size="sm" variant="ghost" onClick={() => openLead(m.lead_id)}>Open</Button>
              </div>
            </li>
          ))}
        </Section>
      </div>
      {open && <LeadDrawer id={open} ids={ids} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
