import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import LeadDrawer from "./LeadDrawer.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import Abbr from "../components/Abbr.jsx";
import { PageHeader, Card, CardHeader, Button, Badge, EmptyState, Skeleton, Switch } from "../ui/index.js";
import "../inbox-pages.css";

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
    <details className="card u-mb-16 team-settings">
      <summary><b>Team &amp; reminders</b> <span className="sub-inline">— who owns tasks, and email reminders</span></summary>
      <div className="stack u-mt-12">
        <label className="field"><span className="field-label">Caller's name (gets the "call them" tasks when a lead says yes)</span>
          <input value={cfg.caller_name} onChange={(e) => setCfg({ ...cfg, caller_name: e.target.value })} /></label>
        <div className="field"><span className="field-label">Team members</span></div>
        {cfg.team.map((m, i) => (
          <div className="team-grid" key={i}>
            <input aria-label="Name" placeholder="Name" value={m.name} onChange={(e) => setMember(i, "name", e.target.value)} />
            <input aria-label="Email" placeholder="Email for reminders" value={m.email || ""} onChange={(e) => setMember(i, "email", e.target.value)} />
            <input aria-label="Role" placeholder="Role" value={m.role || ""} onChange={(e) => setMember(i, "role", e.target.value)} />
            <Button variant="secondary" onClick={() => setCfg({ ...cfg, team: cfg.team.filter((_, j) => j !== i) })} aria-label={`Remove ${m.name}`}>Remove</Button>
          </div>
        ))}
        <div><Button variant="secondary" onClick={() => setCfg({ ...cfg, team: [...cfg.team, { name: "", email: "", role: "" }] })}>Add a person</Button></div>
        <div className="row">
          <Switch checked={!!cfg.notify_email} onChange={(v) => setCfg({ ...cfg, notify_email: v })} label="Also email each person their reminders" />
          <span className="sub-inline">Also email each person their reminders (needs an email provider connected under Integrations)</span>
        </div>
        <div className="row"><Button onClick={save}>Save team</Button>{message && <span className="sub-inline">{message}</span>}</div>
      </div>
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
      {message && <div className="tip u-mb-12" role="status">{message}</div>}
      {isOwner() && <TeamSettings onSaved={load} />}
      {canWrite() && <Card className="u-mb-16"><form className="task-add" onSubmit={add}>
        <label className="field span2"><span className="field-label">New task</span>
          <input aria-label="New task" placeholder="New task, e.g. Send case study to Acme" value={newTask.title}
            onChange={(e) => setNewTask({ ...newTask, title: e.target.value })} /></label>
        <label className="field"><span className="field-label">Due (IST)</span>
          <input aria-label="Due (IST)" type="datetime-local" value={newTask.due_at} onChange={(e) => setNewTask({ ...newTask, due_at: e.target.value })} /></label>
        <label className="field"><span className="field-label">Owner</span>
          <select aria-label="Owner" value={newTask.owner} onChange={(e) => setNewTask({ ...newTask, owner: e.target.value })}>
            <option value="">Unassigned</option>{(board?.team || []).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
          </select></label>
        <label className="field"><span className="field-label">Priority</span>
          <select aria-label="Priority" value={newTask.priority} onChange={(e) => setNewTask({ ...newTask, priority: e.target.value })}>
            {PRIORITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label} priority</option>)}
          </select></label>
        <Button type="submit" disabled={!newTask.title.trim()}>Add</Button>
      </form></Card>}
      <div className="filter-bar">
        <label htmlFor="owner-filter" className="u-m0">Show tasks for</label>
        <select id="owner-filter" value={owner} onChange={(e) => setOwner(e.target.value)}>
          <option value="">Everyone</option>{(board?.team || []).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
        </select>
        <label htmlFor="task-sort" className="u-m0">Order by</label>
        <select id="task-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="due">Due time</option><option value="priority">Priority</option>
        </select>
        <span className="dl-group push">
          <select aria-label="Tasks to download" value={bucket} onChange={(e) => setBucket(e.target.value)}>
            <option value="">All tasks</option>{BUCKETS.map(([key, label]) => <option key={key} value={key}>{key === "done" ? "Done" : label}</option>)}
          </select>
          <DownloadButton path={exportPath("tasks", { owner, bucket })} label="Download CSV" testId="download-tasks"
            title={`${owner ? `${owner}'s` : "Everyone's"} tasks${bucket ? ` (${bucket.replace("_", " ")})` : ""}, with due times in IST`} />
        </span>
      </div>
      {!board && !error && <div className="stack" aria-busy="true"><Skeleton height={96} /><Skeleton height={96} /></div>}
      {board && BUCKETS.every(([key]) => board[key].length === 0) && (
        <Card><EmptyState icon="tasks" title={owner ? `No tasks for ${owner}` : "No tasks yet"}>
          {owner ? "Pick Everyone to see the whole team's tasks." : `${canWrite() ? "Add one above; " : ""}when a lead says yes, a call task for the caller appears here automatically.`}</EmptyState></Card>
      )}
      <div className="stack">
      {board && !BUCKETS.every(([key]) => board[key].length === 0) && BUCKETS.map(([key, label]) => (
        <Card key={key}>
          <CardHeader title={label} actions={<Badge tone={key === "overdue" && board[key].length ? "warning" : "neutral"}>{board[key].length}</Badge>} />
          {board[key].length === 0 ? <p className="sub u-m0">Nothing here.</p> : (
            <div className="task-group">
              {board[key].map((t) => (
                <div key={t.id} className={`task-row${key === "overdue" ? " overdue" : ""}`}>
                  <div className="task-main">
                    <div className="task-title">
                      <b>{t.title}</b>{t.company && <>· <button className="link" onClick={() => setLead(t.lead_id)}>{t.company}</button></>}
                      {t.priority === "high" && <Badge tone="warning">High</Badge>}
                      {t.priority === "low" && <Badge>Low</Badge>}
                      {t.lead_stage === "unsubscribed" && <Badge>lead unsubscribed</Badge>}
                    </div>
                    <div className="sub">
                      {t.due_at ? istLabel(t.due_at) : "No due time"}{t.owner ? ` · ${t.owner}` : ""}{t.text ? ` · ${t.text.slice(0, 140)}` : ""}
                      {t.done_at ? ` · done ${istLabel(t.done_at)}` : ""}
                    </div>
                  </div>
                  {key !== "done" && canWrite() && (
                    <div className="task-actions">
                      <Button size="sm" variant="secondary" onClick={() => act(() => api.taskDoneById(t.id, ""), "Marked done.")}>Done</Button>
                      <Button size="sm" variant="secondary" onClick={() => act(() => api.updateTask(t.id, { snooze_minutes: 60 }), "Snoozed 1 hour.")}>+1 hour</Button>
                      <Button size="sm" variant="secondary" onClick={() => act(() => api.updateTask(t.id, { snooze_minutes: 1440 }), "Moved to tomorrow.")}>Tomorrow</Button>
                      <select aria-label="Change priority" value={t.priority || "normal"} onChange={(e) => act(() => api.updateTask(t.id, { priority: e.target.value }), "Priority changed.")}>
                        {PRIORITY_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                      <select aria-label="Reassign" value={t.owner || ""} onChange={(e) => act(() => api.updateTask(t.id, { owner: e.target.value }), "Reassigned.")}>
                        <option value="">Unassigned</option>{board.team.map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
                      </select>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </Card>
      ))}
      </div>
      {lead && <LeadDrawer id={lead} ids={board ? [...new Set(BUCKETS.flatMap(([k]) => board[k]).map((t) => t.lead_id).filter(Boolean))] : undefined} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}
