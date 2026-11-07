import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { istLabel } from "../time.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import { useConfirm } from "../Dialog.jsx";

const TYPES = [["call", "Call"], ["meeting", "Meeting"], ["note", "Note"], ["email", "Email (sent outside the app)"],
  ["whatsapp", "WhatsApp (sent outside the app)"], ["linkedin", "LinkedIn message"], ["other", "Other"]];
const OUTCOMES = [["", "—"], ["connected", "Connected"], ["no_answer", "No answer"], ["busy", "Busy"], ["call_back", "Asked to call back"],
  ["interested", "Interested"], ["not_interested", "Not interested"], ["meeting_booked", "Meeting booked"], ["wrong_number", "Wrong number"],
  ["call_disconnected", "Call disconnected"], ["inactive_number", "Inactive number"]];
const FILTERS = [["all", "All"], ["email", "Emails"], ["reply", "Replies"], ["call", "Calls"], ["linkedin", "LinkedIn"], ["whatsapp", "WhatsApp"], ["meeting", "Meetings"],
  ["task", "Tasks"], ["note", "Notes"]];
const ICON = { email: "✉", whatsapp: "🟢", call: "📞", meeting: "📅", task: "☑", note: "📝", reply: "↩", linkedin: "in", sms: "💬" };

// "2026-11-04T10:30" from a datetime-local input is IST; send it with the +05:30 offset.
const withIst = (local) => (local ? `${local}:00+05:30` : null);
const nowLocalIst = () => new Date(Date.now() + 330 * 60000).toISOString().slice(0, 16);

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const QUICK_CALLS = [["no_answer", "No answer"], ["busy", "Busy"], ["call_disconnected", "Call disconnected"], ["call_back", "Call back"], ["connected", "Connected"],
  ["not_interested", "Not interested"], ["wrong_number", "Wrong number"], ["inactive_number", "Inactive number"]];
const QUICK_CONFIRM = { not_interested: "Mark them not interested? They move to Disqualified and their emails stop.",
  wrong_number: "Mark this number as wrong? They leave the call queue until the number is changed.",
  inactive_number: "Mark this number inactive (not in service / switched off)? They leave the call queue until the number is changed." };

// One printable page with everything about the contact: details, attempt counts and the full history (Save as PDF from print).
function openReport(win, lead, items, s) {
  const rows = items.map((i) => `<tr><td>${esc(istLabel(i.at))}</td><td>${esc(i.type)}${i.direction === "in" ? " ↩" : ""}</td><td><b>${esc(i.title)}</b>${i.body ? `<div class="b">${esc(i.body).replace(/\n/g, "<br>")}</div>` : ""}</td><td>${esc(String(i.status || "").replace(/_/g, " "))}</td><td>${esc(i.detail || i.owner || "")}</td></tr>`).join("");
  const outcomes = Object.entries(s.calls.outcomes || {}).map(([k, v]) => `${esc(k.replace(/_/g, " "))}: ${v}`).join(", ") || "none";
  win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>Contact report — ${esc(lead.contact_name || lead.company)}</title>
<style>body{font:13px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#0f172a;margin:28px}h1{margin:0 0 4px;font-size:22px}.sub{color:#475569}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:16px 0}.card{border:1px solid #e2e8f0;border-radius:10px;padding:10px}.card b{font-size:18px;display:block}
table{width:100%;border-collapse:collapse;margin-top:12px}td,th{border-bottom:1px solid #e2e8f0;padding:6px 8px;text-align:left;vertical-align:top}th{background:#f1f5f9}.b{color:#475569;white-space:normal;margin-top:2px}
.bar{display:flex;gap:8px;margin-bottom:14px}@media print{.bar{display:none}body{margin:10mm}}</style></head><body>
<div class="bar"><button onclick="print()">Print / Save as PDF</button></div>
<h1>${esc(lead.contact_name || "—")} · ${esc(lead.company || "")}</h1>
<div class="sub">${esc(lead.title || "")} · ${esc(lead.email || "no email")} · ${esc(lead.phone || "no phone")} · ${esc(lead.linkedin_url || "no LinkedIn")}<br>
Stage: <b>${esc(lead.stage)}</b> · score ${esc(lead.score ?? "")} · source ${esc(lead.source || "")}${lead.workspace ? " · workspace " + esc(lead.workspace) : ""} · report made ${esc(istLabel(new Date().toISOString()))}</div>
<div class="grid">
<div class="card"><b>${s.total_touches}</b>touches in total<div class="sub">first ${esc(istLabel(s.first_touch) || "—")}<br>last ${esc(istLabel(s.last_touch) || "—")}</div></div>
<div class="card"><b>${s.calls.attempts}</b>call attempts<div class="sub">${s.calls.unanswered} unanswered · ${s.calls.connected} connected<br>${outcomes}</div></div>
<div class="card"><b>${s.emails.sent}</b>emails sent<div class="sub">${s.emails.opens} opens · ${s.emails.clicks} clicks · ${s.emails.replies} replies${s.emails.practice ? `<br>${s.emails.practice} practice` : ""}</div></div>
<div class="card"><b>${esc(s.linkedin.status || "—")}</b>LinkedIn<div class="sub">steps done ${esc((s.linkedin.steps_done || []).join(", ") || "none")}${s.linkedin.account ? "<br>account " + esc(s.linkedin.account) : ""}${s.linkedin.open_profile ? "<br>Open Profile" : ""}</div></div>
</div>
<table><thead><tr><th>When (IST)</th><th>Channel</th><th>What</th><th>Result</th><th>Who / detail</th></tr></thead><tbody>${rows || "<tr><td colspan=5>Nothing yet</td></tr>"}</tbody></table>
</body></html>`);
  win.document.close();
}

// One-off email composer. Also used by Replies (prefilled "Re: subject") and the call desk.
// SNIPPETS HOOK: when the snippets API lands, load canned replies here and render them next to the template picker;
// a picked snippet should be inserted into `body` at the cursor (keep {{greeting}}-style variables as they are).
export function Composer({ lead, onDone, onCancel, initialSubject = "", initialBody = "{{greeting}}\n\n", note = "" }) {
  const [steps, setSteps] = useState([]);
  const [subject, setSubject] = useState(initialSubject);
  const [body, setBody] = useState(initialBody);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [snippets, setSnippets] = useState([]);
  useEffect(() => { api.sequence().then(setSteps).catch(() => {}); api.snippets("email").then(setSnippets).catch(() => {}); }, []);
  const applyTemplate = (value) => {
    const [kind, i] = String(value).split(":");
    const t = kind === "s" ? steps[Number(i)] : kind === "n" ? snippets[Number(i)] : null;
    if (!t) return;
    if (t.subject) setSubject(t.subject);
    setBody(kind === "s" ? t.body.replace(/\n\nRegards,[\s\S]*$/, "") : t.body);
  };
  const confirm = useConfirm();
  const send = async (e) => {
    e.preventDefault();
    if (!(await confirm({ message: `Send this email to ${lead.email} now? (It goes for real if real sending is on; an unsubscribe line is added.)`, confirmLabel: "Send" }))) return;
    setBusy(true); setMsg("");
    try { const r = await api.emailCompose(lead.id, { subject, body }); onDone(`${r.mode === "live" ? "Sent" : "Practised (not sent)"}: “${r.subject}” to ${r.to}.`); }
    catch (err) { setMsg(err.message); } finally { setBusy(false); }
  };
  return (
    <form className="inline-form" onSubmit={send} aria-label="Write a one-off email">
      <label>Start from a template (optional)
        <select value="" onChange={(e) => applyTemplate(e.target.value)}>
          <option value="">Blank email</option>
          {snippets.length > 0 && <optgroup label="Snippets">{snippets.map((t, i) => <option key={t.id} value={`n:${i}`}>{t.name}</option>)}</optgroup>}
          <optgroup label="Sequence emails">{steps.map((t, i) => <option key={t.name} value={`s:${i}`}>{t.name}</option>)}</optgroup>
        </select></label>
      <label>Subject<input required maxLength={200} value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
      <label>Message (paste or write; {"{{greeting}}"}, {"{{first_name}}"}, {"{{company}}"}, {"{{sign_off}}"}, brand and {"{{custom.…}}"} fields are filled in)<textarea required rows={8} value={body} onChange={(e) => setBody(e.target.value)} /></label>
      {note && <p className="sub">{note}</p>}
      <p className="sub">Goes to <b>{lead.email}</b> as one email outside the sequence, designed with the logo, your footer and an unsubscribe line (write {"{{sign_off}}"} where your sign-off should go). Logged on this contact with your name.</p>
      {msg && <p className="error" role="alert">{msg}</p>}
      <div className="row"><button className="btn" type="submit" disabled={busy || !subject.trim() || !body.trim()}>{busy ? "Sending…" : "Send email"}</button>
        <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

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
  const [summary, setSummary] = useState(null);
  const [composing, setComposing] = useState(false);
  const [callback, setCallback] = useState(null);
  const [remarks, setRemarks] = useState("");
  const phones = [lead.phone, ...(lead.phones || [])].filter((v, i, a) => v && a.indexOf(v) === i);
  const [dialled, setDialled] = useState("");
  const to = phones.includes(dialled) ? dialled : (lead.phone || "");
  const confirm = useConfirm();
  const load = () => Promise.all([api.leadActivity(lead.id).then(setItems), api.historySummary(lead.id).then(setSummary)]).catch((e) => setMessage(e.message));
  const report = () => { const win = window.open("", "_blank"); if (!win) { setMessage("Allow pop-ups to open the report."); return; }
    Promise.all([api.leadActivity(lead.id), api.historySummary(lead.id)]).then(([it, s]) => openReport(win, lead, it, s)).catch((e) => { win.close(); setMessage(e.message); }); };
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
      {summary && (
        <div className="card" style={{ marginBottom: 12 }} data-testid="history-summary">
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <b>Every attempt</b>
            <button className="btn ghost" onClick={report}>Contact report (print / PDF)</button>
          </div>
          <p className="sub" style={{ margin: "6px 0" }}>
            {summary.total_touches} touches · calls {summary.calls.attempts} ({summary.calls.unanswered} unanswered, {summary.calls.connected} connected)
            · emails {summary.emails.sent} sent, {summary.emails.opens} opens, {summary.emails.replies} replies · LinkedIn {summary.linkedin.status || "—"}
            {summary.whatsapp.sent ? ` · WhatsApp ${summary.whatsapp.sent}` : ""}{summary.last_touch ? ` · last touch ${istLabel(summary.last_touch)}` : ""}
          </p>
          {canWrite() && <div className="row" style={{ flexWrap: "wrap", gap: 6 }} aria-label="Log a call in one click">
            <small className="sub">Log a call{phones.length > 1 ? " to" : ""}:</small>
            {phones.length > 1 && <select value={to} onChange={(e) => setDialled(e.target.value)} aria-label="Number called" data-testid="call-number" style={{ width: "auto" }}>
              {phones.map((p) => <option key={p} value={p}>{p}{p === lead.phone ? " (main)" : ""}</option>)}</select>}
            {QUICK_CALLS.map(([v, l]) => <button key={v} className={`btn ghost${QUICK_CONFIRM[v] ? " danger" : ""}`} onClick={async () => {
              if (v === "call_back") { setCallback(new Date(Date.now() + 390 * 60000).toISOString().slice(0, 16)); return; }
              if (QUICK_CONFIRM[v] && !(await confirm({ message: QUICK_CONFIRM[v], confirmLabel: l }))) return;
              run(() => api.callOutcome(lead.id, v, remarks.trim(), null, to), `Call logged: ${l}${phones.length > 1 ? ` (${to})` : ""}.`).then(() => setRemarks(""));
            }}>{l}</button>)}
            {lead.email && !["unsubscribed", "disqualified"].includes(lead.stage) && <button className="btn ghost" onClick={() => setComposing(true)}>Write an email</button>}
          </div>}
          {canWrite() && <label className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>Remarks
            <input data-testid="call-remarks" style={{ flex: "1 1 240px", minWidth: 0 }} value={remarks} onChange={(e) => setRemarks(e.target.value)}
              placeholder="Optional: what they said; saved with the call you log" maxLength={500} /></label>}
          {callback !== null && canWrite() && (
            <form className="row" style={{ marginTop: 6 }} onSubmit={(e) => { e.preventDefault();
              run(() => api.callOutcome(lead.id, "call_back", remarks.trim(), withIst(callback), to), "Call logged; a call-back task was created.").then(() => { setCallback(null); setRemarks(""); }); }}>
              <label className="row" style={{ gap: 6 }}>Call back at (IST)<input type="datetime-local" required value={callback} onChange={(e) => setCallback(e.target.value)} /></label>
              <button className="btn" type="submit">Save</button><button className="btn ghost" type="button" onClick={() => setCallback(null)}>Cancel</button>
            </form>
          )}
          {composing && canWrite() && <Composer lead={lead} onCancel={() => setComposing(false)} onDone={(m) => { setComposing(false); setMessage(m); load(); onChanged?.(); }} />}
        </div>
      )}
      <h3>Tasks &amp; follow-ups</h3>
      {openTasks.length === 0 ? <p className="sub">No open tasks.</p> : (
        <ul className="task-list">
          {openTasks.map((t) => (
            <li key={t.task_id} className={t.due_at && new Date(t.due_at) < new Date() ? "overdue" : ""}>
              <div><b>{t.title}</b>{t.body && <span className="sub"> — {t.body.slice(0, 120)}</span>}</div>
              <div className="sub">{t.due_at ? `Due ${istLabel(t.due_at)}` : "No due time"}{t.owner ? ` · ${t.owner}` : ""}</div>
              {canWrite() && <div className="row">
                <button className="btn ghost" onClick={() => run(() => api.taskDoneById(t.task_id, ""), "Marked done.")}>Done</button>
                <button className="btn ghost" onClick={() => run(() => api.updateTask(t.task_id, { snooze_minutes: 60 }), "Snoozed 1 hour.")}>+1 hour</button>
                <button className="btn ghost" onClick={() => run(() => api.updateTask(t.task_id, { snooze_minutes: 1440 }), "Moved to tomorrow.")}>Tomorrow</button>
              </div>}
            </li>
          ))}
        </ul>
      )}
      {canWrite() && <div className="row" style={{ margin: "8px 0" }}>
        <button className="btn ghost" onClick={() => setTaskForm({ title: "", text: "", due_at: "", owner: "", priority: "normal" })}>Add a task</button>
        <button className="btn ghost" onClick={() => setLogging(true)}>Log a call / meeting / note</button>
      </div>}

      {taskForm && canWrite() && (
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

      {logging && canWrite() && (
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
      <div className="tab-chips" role="tablist" aria-label="Filter activity">
        {FILTERS.map(([v, l]) => (
          <button key={v} role="tab" aria-selected={filter === v} className={`tab-chip${filter === v ? " on" : ""}`} onClick={() => setFilter(v)}>{l}</button>
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
                    {/^practice/.test(String(item.status || "")) ? "Practice only (not sent)" : <>
                      {item.direction === "in" ? "Received" : item.direction === "out" ? "Sent / done" : ""}
                      {item.status ? ` · ${String(item.status).replace(/_/g, " ")}` : ""}</>}
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
