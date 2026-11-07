import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { istDayLabel, istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";

const TARGETS = [["calls", "Calls"], ["connects", "Connects"], ["emails", "Emails"], ["linkedin", "LinkedIn"], ["whatsapp", "WhatsApp"]];
// Buttons wrap under the text on a phone instead of pushing the page sideways.
const ACTIONS_STYLE = { flexWrap: "wrap", gap: 6, flex: "0 1 auto", minWidth: 0, justifyContent: "flex-end" };
const ACTION_ICON = { call: "📞", email: "✉", whatsapp: "🟢", open: "→" };

function Progress({ label, done, target }) {
  const pct = target ? Math.min(100, Math.round((done / target) * 100)) : 0;
  return (
    <div className="kpi" data-testid={`target-${label.toLowerCase()}`}>
      <b>{done}<small className="sub" style={{ fontSize: 14, fontWeight: 600 }}> / {target}</small></b>
      <span>{label} today</span>
      <div role="progressbar" aria-label={`${label}: ${done} of ${target}`} aria-valuenow={done} aria-valuemin={0} aria-valuemax={target || 0}
        style={{ height: 6, borderRadius: 4, background: "var(--line)", marginTop: 6, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: pct >= 100 ? "var(--ok)" : "var(--brand)" }} />
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
      <div className="row" style={{ flexWrap: "wrap" }}>
        {TARGETS.map(([k, l]) => (
          <label key={k} style={{ maxWidth: 120 }}>{l}<input type="number" min="0" max="1000" value={values[k] ?? 0} onChange={(e) => setValues({ ...values, [k]: e.target.value })} /></label>
        ))}
      </div>
      {isOwner() && <label>Save for<select value={scope} onChange={(e) => setScope(e.target.value)}>
        <option value="me">Just me</option><option value="team">The whole team (default for everyone)</option></select></label>}
      {msg && <p className="error" role="alert">{msg}</p>}
      <div className="row"><button className="btn" type="submit">Save targets</button><button className="btn ghost" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

// One row: what it is, why, and one-click buttons for the next best action.
function Item({ title, sub, item, onOpen, go, overdue }) {
  const next = item.next || { action: "open", label: "Open" };
  const leadId = item.lead_id;
  return (
    <li className={`today-item${overdue ? " overdue" : ""}`} data-testid="myday-item">
      <div className="today-text">
        <b>{title}</b>{sub && <span className="sub"> — {sub}</span>}
        {next.why && <div className="sub">{ACTION_ICON[next.action] || ""} {next.why}</div>}
      </div>
      <div className="row today-actions" style={ACTIONS_STYLE}>
        {item.phone && <a className={`btn ${next.action === "call" ? "" : "ghost"}`} href={`tel:${item.phone}`} title="Dial now">Call</a>}
        {item.email && canWrite() && <button className={`btn ${next.action === "email" ? "" : "ghost"}`} onClick={() => onOpen(leadId, "activity")}>Write email</button>}
        {item.phone && next.action === "whatsapp" && canWrite() && <button className="btn" onClick={() => onOpen(leadId, "outreach")}>WhatsApp</button>}
        {next.action === "call" && item.phone && <button className="btn ghost" onClick={() => go("calls")} title="Call it from the call desk with the script">Call desk</button>}
        {leadId && <button className="btn ghost" onClick={() => onOpen(leadId)}>Open</button>}
      </div>
    </li>
  );
}

function Section({ title, count, empty, children, testId }) {
  return (
    <div className="card" data-testid={testId}>
      <h3 style={{ marginTop: 0 }}>{title} {count > 0 && <span className="pill">{count}</span>}</h3>
      {count === 0 ? <p className="sub">{empty}</p> : <ul className="today-list">{children}</ul>}
    </div>
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
  if (!day) return <p className="empty">Loading your day…</p>;
  const c = day.counts;
  const ids = [...new Set([...day.callbacks, ...day.calls_due, ...day.replies, ...day.hot_leads, ...day.tasks].map((x) => x.lead_id).filter(Boolean))];
  const callable = c.callbacks + c.calls_due + c.hot_leads;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div>
          <h2 style={{ marginBottom: 2 }}>My day</h2>
          <p className="sub" style={{ marginTop: 0 }}>{istDayLabel(new Date().toISOString())} · {day.me.name}</p>
        </div>
        <div className="row" style={{ gap: 6 }}>
          <button className="btn" onClick={() => go("calls")} data-testid="myday-start-calling">Start calling{callable ? ` (${callable})` : ""}</button>
          <button className="btn ghost" onClick={() => go("replies")}>Replies{c.replies ? ` (${c.replies})` : ""}</button>
        </div>
      </div>

      <div className="kpi-row" data-testid="myday-targets">
        {TARGETS.filter(([k]) => day.targets[k] > 0 || day.today[k] > 0).map(([k, l]) => <Progress key={k} label={l} done={day.today[k] || 0} target={day.targets[k] || 0} />)}
      </div>
      <p className="sub" style={{ marginTop: 0 }}>
        This week: {day.week.calls} calls, {day.week.connects} connects, {day.week.emails} emails, {day.week.linkedin} LinkedIn, {day.week.whatsapp} WhatsApp, {day.week.tasks_done} tasks done.
        {canWrite() && <>{" "}<button className="link" onClick={() => setEditing(!editing)}>{editing ? "Close targets" : "Change targets"}</button></>}
        {!day.personal_targets && " (team targets)"}
      </p>
      {editing && canWrite() && <TargetEditor day={day} onCancel={() => setEditing(false)} onSaved={() => { setEditing(false); load(); }} />}

      <div className="grid gw">
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
            <li key={t.id} className={`today-item${t.due_at && new Date(t.due_at) < new Date() ? " overdue" : ""}`} data-testid="myday-task">
              <div className="today-text"><b>{t.title}</b>{t.company && <span className="sub"> — {t.company}</span>}
                <div className="sub">{t.due_at ? `Due ${istLabel(t.due_at)}` : ""}{t.text ? ` · ${t.text.slice(0, 90)}` : ""}</div></div>
              <div className="row today-actions" style={ACTIONS_STYLE}>
                {canWrite() && <button className="btn ghost" onClick={() => api.taskDoneById(t.id, "").then(load)}>Done</button>}
                {canWrite() && <button className="btn ghost" onClick={() => api.updateTask(t.id, { snooze_minutes: 1440 }).then(load)}>Tomorrow</button>}
                {t.lead_id && <button className="btn ghost" onClick={() => openLead(t.lead_id)}>Open</button>}
              </div>
            </li>
          ))}
        </Section>
        <Section title="Hot leads to call" count={c.hot_leads} empty="No hot leads with a phone waiting." testId="myday-hot">
          {day.hot_leads.map((l) => <Item key={l.lead_id} item={l} title={l.company || l.contact_name} sub={`score ${l.score}${l.last_touch ? ` · last touch ${istLabel(l.last_touch)}` : ""}`} onOpen={openLead} go={go} />)}
        </Section>
        <Section title="Meetings today" count={c.meetings} empty="No meetings today." testId="myday-meetings">
          {day.meetings.map((m, i) => (
            <li key={i} className="today-item">
              <div className="today-text"><b>{istLabel(m.start, false)} · {m.title}</b><span className="sub"> — {m.company}</span></div>
              <div className="row today-actions" style={ACTIONS_STYLE}>
                {/^https:\/\//.test(m.join_url) && <a className="btn" href={m.join_url} target="_blank" rel="noopener noreferrer">Join</a>}
                <button className="btn ghost" onClick={() => openLead(m.lead_id)}>Open</button>
              </div>
            </li>
          ))}
        </Section>
      </div>
      {open && <LeadDrawer id={open} ids={ids} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
