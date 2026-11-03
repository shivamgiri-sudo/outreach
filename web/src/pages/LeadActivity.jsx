import { useEffect, useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";

const TYPES = [["call", "Call"], ["meeting", "Meeting"], ["note", "Note"], ["email", "Email (sent outside the app)"],
  ["whatsapp", "WhatsApp (sent outside the app)"], ["linkedin", "LinkedIn message"], ["other", "Other"]];
const OUTCOMES = [["", "—"], ["connected", "Connected"], ["no_answer", "No answer"], ["busy", "Busy"], ["call_back", "Asked to call back"],
  ["interested", "Interested"], ["not_interested", "Not interested"], ["meeting_booked", "Meeting booked"], ["wrong_number", "Wrong number"]];
const FILTERS = [["all", "All"], ["email", "Emails"], ["reply", "Replies"], ["call", "Calls"], ["whatsapp", "WhatsApp"], ["meeting", "Meetings"],
  ["task", "Tasks"], ["note", "Notes"]];
const ICON = { email: "✉", whatsapp: "🟢", call: "📞", meeting: "📅", task: "☑", note: "📝", reply: "↩", linkedin: "in", sms: "💬" };

// "2026-11-04T10:30" from a datetime-local input is IST; send it with the +05:30 offset.
const withIst = (local) => (local ? `${local}:00+05:30` : null);
const nowLocalIst = () => new Date(Date.now() + 330 * 60000).toISOString().slice(0, 16);

function matches(item, filter) {
  if (filter === "all") return true;
  if (filter === "reply") return item.direction === "in";
  return item.type === filter;
}

export default function LeadActivity({ lead, onChanged }) {
  const [items, setItems] = useState([]);
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState(null);
  const [logging, setLogging] = useState(false);
  const [form, setForm] = useState({ type: "call", outcome: "", at: nowLocalIst(), notes: "", duration_minutes: "", follow_up_at: "", follow_up_text: "" });
  const [taskForm, setTaskForm] = useState(null);
  const [team, setTeam] = useState([]);
  const [message, setMessage] = useState("");
  const load = () => api.leadActivity(lead.id).then(setItems).catch((e) => setMessage(e.message));
  useEffect(() => { load(); api.team().then((t) => setTeam(t.team)).catch(() => {}); }, [lead.id]);

  const run = async (fn, done) => {
    setMessage("");
    try { await fn(); setMessage(done); await load(); onChanged?.(); }
    catch (e) { setMessage(e.message); }
  };

  const saveActivity = (e) => {
    e.preventDefault();
    run(() => api.logActivity(lead.id, {
      ...form, at: withIst(form.at), follow_up_at: withIst(form.follow_up_at),
      duration_minutes: form.duration_minutes ? Number(form.duration_minutes) : null,
    }), form.follow_up_at ? "Saved, and a follow-up task was created." : "Saved.").then(() => {
      setLogging(false);
      setForm({ type: "call", outcome: "", at: nowLocalIst(), notes: "", duration_minutes: "", follow_up_at: "", follow_up_text: "" });
    });
  };

  const saveTask = (e) => {
    e.preventDefault();
    run(() => api.createTask({ lead_id: lead.id, title: taskForm.title, text: taskForm.text, due_at: withIst(taskForm.due_at),
      owner: taskForm.owner || null, priority: taskForm.priority }), "Task added.").then(() => setTaskForm(null));
  };

  const openTasks = items.filter((i) => i.type === "task" && i.status === "open");
  const shown = items.filter((i) => matches(i, filter));

  return (
    <section aria-label="Activity and tasks">
      <h3>Tasks &amp; follow-ups</h3>
      {openTasks.length === 0 ? <p className="sub">No open tasks.</p> : (
        <ul className="task-list">
          {openTasks.map((t) => (
            <li key={t.task_id} className={t.due_at && new Date(t.due_at) < new Date() ? "overdue" : ""}>
              <div><b>{t.title}</b>{t.body && <span className="sub"> — {t.body.slice(0, 120)}</span>}</div>
              <div className="sub">{t.due_at ? `Due ${istLabel(t.due_at)}` : "No due time"}{t.owner ? ` · ${t.owner}` : ""}</div>
              <div className="row">
                <button className="btn ghost" onClick={() => run(() => api.taskDoneById(t.task_id, ""), "Marked done.")}>Done</button>
                <button className="btn ghost" onClick={() => run(() => api.updateTask(t.task_id, { snooze_minutes: 60 }), "Snoozed 1 hour.")}>+1 hour</button>
                <button className="btn ghost" onClick={() => run(() => api.updateTask(t.task_id, { snooze_minutes: 1440 }), "Moved to tomorrow.")}>Tomorrow</button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <div className="row" style={{ margin: "8px 0" }}>
        <button className="btn ghost" onClick={() => setTaskForm({ title: "", text: "", due_at: "", owner: "", priority: "normal" })}>Add a task</button>
        <button className="btn ghost" onClick={() => setLogging(true)}>Log a call / meeting / note</button>
      </div>

      {taskForm && (
        <form className="inline-form" onSubmit={saveTask}>
          <label>What needs doing?<input required value={taskForm.title} onChange={(e) => setTaskForm({ ...taskForm, title: e.target.value })}
            placeholder="e.g. Send the pricing deck" /></label>
          <label>Due (IST)<input type="datetime-local" value={taskForm.due_at} onChange={(e) => setTaskForm({ ...taskForm, due_at: e.target.value })} /></label>
          <label>Owner
            <select value={taskForm.owner} onChange={(e) => setTaskForm({ ...taskForm, owner: e.target.value })}>
              <option value="">Unassigned</option>{team.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </select>
          </label>
          <label>Priority
            <select value={taskForm.priority} onChange={(e) => setTaskForm({ ...taskForm, priority: e.target.value })}>
              <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
            </select>
          </label>
          <label>Notes<input value={taskForm.text} onChange={(e) => setTaskForm({ ...taskForm, text: e.target.value })} /></label>
          <div className="row"><button className="btn" type="submit" disabled={!taskForm.title.trim()}>Save task</button>
            <button className="btn ghost" type="button" onClick={() => setTaskForm(null)}>Cancel</button></div>
        </form>
      )}

      {logging && (
        <form className="inline-form" onSubmit={saveActivity}>
          <label>What happened?
            <select value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
              {TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          {form.type === "call" && (
            <label>Outcome
              <select value={form.outcome} onChange={(e) => setForm({ ...form, outcome: e.target.value })}>
                {OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
          )}
          <label>When (IST)<input type="datetime-local" value={form.at} onChange={(e) => setForm({ ...form, at: e.target.value })} /></label>
          {["call", "meeting"].includes(form.type) && (
            <label>Minutes<input type="number" min="0" max="600" value={form.duration_minutes} onChange={(e) => setForm({ ...form, duration_minutes: e.target.value })} /></label>
          )}
          <label>Notes (what was said, next steps)<textarea rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
          <label>Follow up on (IST, optional)<input type="datetime-local" value={form.follow_up_at} onChange={(e) => setForm({ ...form, follow_up_at: e.target.value })} /></label>
          {form.follow_up_at && <label>Follow-up task<input value={form.follow_up_text} placeholder="e.g. Call back about 5 roles"
            onChange={(e) => setForm({ ...form, follow_up_text: e.target.value })} /></label>}
          <div className="row"><button className="btn" type="submit">Save</button>
            <button className="btn ghost" type="button" onClick={() => setLogging(false)}>Cancel</button></div>
        </form>
      )}
      {message && <p className="sub" role="status">{message}</p>}

      <div className="row dl-head">
        <h3>Full activity</h3>
        <DownloadButton path={exportPath("activity", { lead_id: lead.id })} label="Download activity" testId="download-activity"
          title="Every email, reply, call, meeting, task and note for this lead, with full text" />
      </div>
      <div className="chips" role="tablist" aria-label="Filter activity">
        {FILTERS.map(([v, l]) => (
          <button key={v} role="tab" aria-selected={filter === v} className={`chip ${filter === v ? "on" : ""}`} onClick={() => setFilter(v)}>{l}</button>
        ))}
      </div>
      {shown.length === 0 ? <p className="sub">Nothing here yet.</p> : (
        <ol className="trail">
          {shown.map((item, i) => {
            const key = `${item.type}-${item.at}-${i}`;
            const long = (item.body || "").length > 140;
            return (
              <li key={key} className={`trail-item ${item.direction}`}>
                <span className="trail-icon" aria-hidden="true">{ICON[item.type] || "•"}</span>
                <div className="trail-body">
                  <div className="row" style={{ justifyContent: "space-between", gap: 6 }}>
                    <b>{item.title}</b>
                    <span className="sub">{istLabel(item.at)}</span>
                  </div>
                  <div className="sub">
                    {item.direction === "in" ? "Received" : item.direction === "out" ? "Sent / done" : ""}
                    {item.status ? ` · ${String(item.status).replace(/_/g, " ")}` : ""}
                    {item.to ? ` · to ${item.to}` : ""}{item.detail ? ` · ${item.detail}` : ""}
                    {item.due_at ? ` · due ${istLabel(item.due_at)}` : ""}{item.start ? ` · ${istLabel(item.start)}` : ""}
                  </div>
                  {item.body && (
                    <p className="trail-text">{open === key || !long ? item.body : `${item.body.slice(0, 140)}…`}
                      {long && <button className="link" onClick={() => setOpen(open === key ? null : key)}>{open === key ? " Show less" : " Show all"}</button>}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
