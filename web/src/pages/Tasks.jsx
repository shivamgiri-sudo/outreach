import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { EmptyArt, ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import Abbr from "../components/Abbr.jsx";
import { PageHeader } from "../ui/index.js";

const BUCKETS = [["overdue", "Overdue"], ["today", "Today"], ["upcoming", "Upcoming"], ["no_date", "No due time"], ["done", "Done recently"]];
const withIst = (local) => (local ? `${local}:00+05:30` : null);

function TeamSettings({ onSaved }) {
  const [cfg, setCfg] = useState(null);
  const [message, setMessage] = useState("");
  useEffect(() => { api.team().then(setCfg).catch((e) => setMessage(e.message)); }, []);
  if (!cfg) return null;
  const setMember = (i, key, value) => setCfg({ ...cfg, team: cfg.team.map((m, j) => (i === j ? { ...m, [key]: value } : m)) });
  const save = async () => {
    try { setCfg(await api.saveTeam(cfg)); setMessage("Saved."); onSaved?.(); }
    catch (e) { setMessage(e.message); }
  };
  return (
    <details className="card" style={{ marginBottom: 14 }}>
      <summary><b>Team &amp; reminders</b> <span className="sub">— who owns tasks, and email reminders</span></summary>
      <label>Caller's name (gets the "call them" tasks when a lead says yes)</label>
      <input value={cfg.caller_name} onChange={(e) => setCfg({ ...cfg, caller_name: e.target.value })} />
      <p className="sub" style={{ margin: "8px 0 4px" }}>Team members</p>
      {cfg.team.map((m, i) => (
        <div className="row" key={i} style={{ marginBottom: 6 }}>
          <input aria-label="Name" placeholder="Name" value={m.name} onChange={(e) => setMember(i, "name", e.target.value)} />
          <input aria-label="Email" placeholder="Email for reminders" value={m.email || ""} onChange={(e) => setMember(i, "email", e.target.value)} />
          <input aria-label="Role" placeholder="Role" value={m.role || ""} onChange={(e) => setMember(i, "role", e.target.value)} />
          <button className="btn ghost" onClick={() => setCfg({ ...cfg, team: cfg.team.filter((_, j) => j !== i) })} aria-label={`Remove ${m.name}`}>Remove</button>
        </div>
      ))}
      <button className="btn ghost" onClick={() => setCfg({ ...cfg, team: [...cfg.team, { name: "", email: "", role: "" }] })}>Add a person</button>
      <label className="row" style={{ fontWeight: 400, marginTop: 10 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={!!cfg.notify_email} onChange={(e) => setCfg({ ...cfg, notify_email: e.target.checked })} />
        Also email each person their reminders (needs an email provider connected under Integrations)
      </label>
      <div className="row" style={{ marginTop: 8 }}><button className="btn" onClick={save}>Save team</button>{message && <span className="sub">{message}</span>}</div>
    </details>
  );
}

const PRIORITY_OPTIONS = [["high", "High"], ["normal", "Normal"], ["low", "Low"]];

export default function Tasks() {
  const [board, setBoard] = useState(null);
  const [owner, setOwner] = useState("");
  const [bucket, setBucket] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [lead, setLead] = useState(null);
  const [sort, setSort] = useState("due");
  const [newTask, setNewTask] = useState({ title: "", due_at: "", owner: "", priority: "normal" });
  const load = () => { setError(""); api.taskBoard(owner, sort).then(setBoard).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, [owner, sort]);

  const act = async (fn, done) => {
    try { await fn(); setMessage(done); load(); } catch (e) { setMessage(e.message); }
  };
  const add = (e) => {
    e.preventDefault();
    act(() => api.createTask({ ...newTask, due_at: withIst(newTask.due_at), owner: newTask.owner || null }), "Task added.")
      .then(() => setNewTask({ title: "", due_at: "", owner: "", priority: "normal" }));
  };

  return (
    <>
      <PageHeader title={<>Tasks &amp; follow-ups</>}
        description={<>Everything that needs doing, by due time (<Abbr term="IST">IST</Abbr>). When a lead says yes, a call task is added for the caller automatically.
        Reminders arrive 15 minutes before a task is due, and again if it's an hour overdue.</>} />
      {error && <ErrorState message="Tasks could not load." detail={error} onRetry={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}
      {isOwner() && <TeamSettings onSaved={load} />}
      {canWrite() && <form className="card row" style={{ marginBottom: 14 }} onSubmit={add}>
        <input aria-label="New task" placeholder="New task, e.g. Send case study to Acme" value={newTask.title} style={{ flex: 2, minWidth: 200 }}
          onChange={(e) => setNewTask({ ...newTask, title: e.target.value })} />
        <input aria-label="Due (IST)" type="datetime-local" value={newTask.due_at} onChange={(e) => setNewTask({ ...newTask, due_at: e.target.value })} />
        <select aria-label="Owner" value={newTask.owner} onChange={(e) => setNewTask({ ...newTask, owner: e.target.value })}>
          <option value="">Unassigned</option>{(board?.team || []).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
        </select>
        <select aria-label="Priority" value={newTask.priority} onChange={(e) => setNewTask({ ...newTask, priority: e.target.value })}>
          {PRIORITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label} priority</option>)}
        </select>
        <button className="btn" type="submit" disabled={!newTask.title.trim()}>Add</button>
      </form>}
      <div className="row" style={{ marginBottom: 10 }}>
        <label htmlFor="owner-filter" style={{ margin: 0 }}>Show tasks for</label>
        <select id="owner-filter" value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Everyone</option>{(board?.team || []).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
        </select>
        <label htmlFor="task-sort" style={{ margin: 0 }}>Order by</label>
        <select id="task-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="due">Due time</option><option value="priority">Priority</option>
        </select>
        <span className="dl-group">
          <select aria-label="Tasks to download" value={bucket} onChange={(e) => setBucket(e.target.value)}>
            <option value="">All tasks</option>{BUCKETS.map(([key, label]) => <option key={key} value={key}>{key === "done" ? "Done" : label}</option>)}
          </select>
          <DownloadButton path={exportPath("tasks", { owner, bucket })} label="Download CSV" testId="download-tasks"
            title={`${owner ? `${owner}'s` : "Everyone's"} tasks${bucket ? ` (${bucket.replace("_", " ")})` : ""}, with due times in IST`} />
        </span>
      </div>
      {board && BUCKETS.every(([key]) => board[key].length === 0) && (
        <div className="card empty"><EmptyArt name="tasks" />
          <p>{owner ? `No tasks for ${owner}.` : `No tasks yet.${canWrite() ? " Add one above;" : ""} When a lead says yes, a call task for the caller appears here automatically.`}</p></div>
      )}
      {board && !BUCKETS.every(([key]) => board[key].length === 0) && BUCKETS.map(([key, label]) => (
        <div key={key} className="card" style={{ marginBottom: 12 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{label}</b><span className={`pill ${key === "overdue" && board[key].length ? "warn" : ""}`}>{board[key].length}</span>
          </div>
          {board[key].length === 0 ? <p className="sub" style={{ margin: "6px 0 0" }}>Nothing here.</p> : (
            <ul className="task-list">
              {board[key].map((t) => (
                <li key={t.id} className={key === "overdue" ? "overdue" : ""}>
                  <div>
                    <b>{t.title}</b>{t.company && <> · <button className="link" onClick={() => setLead(t.lead_id)}>{t.company}</button></>}
                    {t.priority === "high" && <span className="pill warn" style={{ marginLeft: 6 }}>High</span>}
                    {t.priority === "low" && <span className="pill" style={{ marginLeft: 6 }}>Low</span>}
                    {t.lead_stage === "unsubscribed" && <span className="pill" style={{ marginLeft: 6 }}>lead unsubscribed</span>}
                  </div>
                  <div className="sub">
                    {t.due_at ? istLabel(t.due_at) : "No due time"}{t.owner ? ` · ${t.owner}` : ""}{t.text ? ` · ${t.text.slice(0, 140)}` : ""}
                    {t.done_at ? ` · done ${istLabel(t.done_at)}` : ""}
                  </div>
                  {key !== "done" && canWrite() && (
                    <div className="row">
                      <button className="btn ghost" onClick={() => act(() => api.taskDoneById(t.id, ""), "Marked done.")}>Done</button>
                      <button className="btn ghost" onClick={() => act(() => api.updateTask(t.id, { snooze_minutes: 60 }), "Snoozed 1 hour.")}>+1 hour</button>
                      <button className="btn ghost" onClick={() => act(() => api.updateTask(t.id, { snooze_minutes: 1440 }), "Moved to tomorrow.")}>Tomorrow</button>
                      <select aria-label="Change priority" value={t.priority || "normal"} onChange={(e) => act(() => api.updateTask(t.id, { priority: e.target.value }), "Priority changed.")}>
                        {PRIORITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                      <select aria-label="Reassign" value={t.owner || ""} onChange={(e) => act(() => api.updateTask(t.id, { owner: e.target.value }), "Reassigned.")}>
                        <option value="">Unassigned</option>{board.team.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
      {lead && <LeadDrawer id={lead} ids={board ? [...new Set(BUCKETS.flatMap(([k]) => board[k]).map((t) => t.lead_id).filter(Boolean))] : undefined} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}
