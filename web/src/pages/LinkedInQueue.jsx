import { useEffect, useRef, useState } from "react";
import { api, isOwner } from "../api.js";
import { copyText } from "../clipboard.js";
import { ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";

const PERSONA = { ceo_founder: "CEO / Founder", chro: "CHRO / HR Director", ta_head: "Head of TA", recruitment_head: "Recruitment Head",
  business_head: "Business Head", general: "General" };
const BRANCHES = [["what_does_it_do", "They asked what the platform does"], ["meeting_yes", "They want a meeting (offer 2 slots)"],
  ["slot_confirmed", "They picked a time"], ["case_study_yes", "They want the case study"]];

function Settings({ cfg, onSave }) {
  const [form, setForm] = useState(cfg);
  useEffect(() => setForm(cfg), [cfg]);
  return (
    <details className="card" style={{ marginBottom: 14 }}>
      <summary><b>LinkedIn settings</b> <span className="sub">— daily limit, who sends, case-study link</span></summary>
      <label>New connection requests per day (start with 10–15)
        <input type="number" min="1" max="40" value={form.linkedin_daily_connections} onChange={(e) => setForm({ ...form, linkedin_daily_connections: e.target.value })} /></label>
      <label>Who sends from the LinkedIn account
        <input value={form.linkedin_owner} placeholder="Priya" onChange={(e) => setForm({ ...form, linkedin_owner: e.target.value })} /></label>
      <label>Case-study link (step 4 is skipped until a real link is set)
        <input value={form.case_study_url} placeholder="https://…" onChange={(e) => setForm({ ...form, case_study_url: e.target.value })} /></label>
      <button className="btn" style={{ marginTop: 8 }} onClick={() => onSave({ ...form, linkedin_daily_connections: Number(form.linkedin_daily_connections) })}>Save</button>
    </details>
  );
}

// Hand-off to a third-party LinkedIn tool (Waalaxy, Dux-Soup, PhantomBuster...): export the list, import its results.
function ToolBridge({ onDone }) {
  const file = useRef(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const pick = async (f) => {
    if (!f) return;
    setError(""); setResult(null);
    try { setResult(await api.linkedinResults(await f.text())); onDone(); } catch (e) { setError(e.message); }
    if (file.current) file.current.value = "";
  };
  return (
    <section className="card" style={{ marginBottom: 14 }} aria-labelledby="li-tool-title">
      <h3 id="li-tool-title" style={{ marginTop: 0 }}>Use a LinkedIn tool</h3>
      <p className="sub" style={{ marginTop: 0 }}>Export the people who have a LinkedIn link, with the connection note and follow-up messages already written, and load the file into a
        tool such as Waalaxy, Dux-Soup or PhantomBuster. When it has run, import its results file here to mark who accepted or replied.</p>
      <div className="row">
        {isOwner() && <DownloadButton path={exportPath("linkedin_tool")} label="Export list for a LinkedIn tool" testId="download-linkedin-tool"
          title="CSV: firstName, lastName, companyName, title, profileUrl, message1 to message5" />}
        <button type="button" className="btn ghost" data-testid="import-linkedin-results" onClick={() => file.current?.click()}>Import its results</button>
        <input ref={file} type="file" accept=".csv,text/csv" hidden aria-label="Results file from the LinkedIn tool" onChange={(e) => pick(e.target.files?.[0])} />
      </div>
      <div className="tip" style={{ marginTop: 10 }} role="note">
        Automating LinkedIn is against LinkedIn's terms and can get an account restricted. If you use a tool, that is your own decision and risk;
        use a separate LinkedIn account, not the one you rely on. This app never connects to LinkedIn itself.
      </div>
      {error && <div className="tip error" role="alert" style={{ marginTop: 10 }}>{error}</div>}
      {result && (
        <div className="tip" role="status" style={{ marginTop: 10 }} data-testid="linkedin-results">
          Read {result.rows} row(s): {result.matched} matched, {result.accepted} marked accepted, {result.replied} marked replied,
          {" "}{result.unmatched} not found in your leads.
          {result.problems.map((p) => <div key={p}>{p}</div>)}
          {result.unmatched_links.length > 0 && <details><summary>Links not found</summary>{result.unmatched_links.map((u) => <div key={u} style={{ overflowWrap: "anywhere" }}>{u}</div>)}</details>}
        </div>
      )}
    </section>
  );
}

function BranchHelper({ leadId, onMessage }) {
  const [key, setKey] = useState("what_does_it_do");
  const [vals, setVals] = useState({ option_1: "", option_2: "", date: "", time: "" });
  const [text, setText] = useState("");
  const make = async () => {
    try { setText((await api.linkedinBranch(leadId, { key, ...vals })).text); } catch (e) { onMessage(e.message); }
  };
  return (
    <div className="inline-form">
      <label>Reply helper
        <select value={key} onChange={(e) => { setKey(e.target.value); setText(""); }}>{BRANCHES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      </label>
      {key === "meeting_yes" && <div className="row">
        <input aria-label="Option 1" placeholder="Tue 4 pm" value={vals.option_1} onChange={(e) => setVals({ ...vals, option_1: e.target.value })} />
        <input aria-label="Option 2" placeholder="Wed 11 am" value={vals.option_2} onChange={(e) => setVals({ ...vals, option_2: e.target.value })} /></div>}
      {key === "slot_confirmed" && <div className="row">
        <input aria-label="Date" placeholder="Wed 5 Nov" value={vals.date} onChange={(e) => setVals({ ...vals, date: e.target.value })} />
        <input aria-label="Time" placeholder="11:00 am IST" value={vals.time} onChange={(e) => setVals({ ...vals, time: e.target.value })} /></div>}
      <div className="row"><button className="btn ghost" onClick={make}>Write it</button>
        {text && <button className="btn ghost" onClick={() => copyText(text).then(() => onMessage("Copied."))}>Copy</button>}</div>
      {text && <p className="trail-text">{text}</p>}
    </div>
  );
}

export default function LinkedInQueue() {
  const [q, setQ] = useState(null);
  const [cfg, setCfg] = useState({ linkedin_daily_connections: 12, linkedin_owner: "", case_study_url: "" });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [helper, setHelper] = useState(null);
  const load = () => { setError(""); api.linkedinQueue().then(setQ).catch((e) => setError(e.message)); api.linkedinSettings().then(setCfg).catch(() => {}); };
  useEffect(() => { load(); }, []);
  const act = async (fn, done) => { try { await fn(); setMessage(done); load(); } catch (e) { setMessage(e.message); } };

  return (
    <>
      <h2>LinkedIn outreach</h2>
      <p className="sub">Messages are sent by a person from the LinkedIn account (LinkedIn doesn't allow automation). This page gives the day's
        list with every message already written for that person. Mark each one sent and the next step is scheduled. Messages only start after
        they accept the connection, and any reply stops the sequence.</p>
      {error && <ErrorState message="The LinkedIn queue could not load." detail={error} onRetry={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}
      <ToolBridge onDone={load} />
      <Settings cfg={cfg} onSave={(body) => act(() => api.saveLinkedinSettings(body), "Settings saved.")} />
      {q && !q.case_study_set && <p className="sub">Step 4 (case study) is skipped until you add a real case-study link in the settings above.</p>}

      <div className="row dl-head">
        <h3>To send today {q && <span className="pill">{q.due.length}</span>}</h3>
        <DownloadButton path={exportPath("linkedin_queue")} label="Download today's list" testId="download-linkedin"
          title="Messages to send, people waiting to accept and suggestions, with the ready-to-paste text" />
      </div>
      {q && q.due.length === 0 && <p className="empty">Nothing due. Start some people from the suggestions below.</p>}
      {q && q.due.map((d) => (
        <div className="card" key={d.task_id} style={{ marginBottom: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{d.name || "Contact"} · {d.company}</b>
            <span className="sub">Step {d.step} · {PERSONA[d.persona] || ""}{d.due_at ? ` · ${istLabel(d.due_at)}` : ""}</span>
          </div>
          <p className="trail-text">{d.message}</p>
          <div className="row">
            <button className="btn ghost" onClick={() => copyText(d.message).then(() => setMessage("Message copied. Paste it on LinkedIn."))}>Copy message</button>
            {/^https?:\/\//i.test(d.linkedin_url || "") && <a className="btn ghost" href={d.linkedin_url} target="_blank" rel="noopener noreferrer">Open profile</a>}
            <button className="btn" onClick={() => act(() => api.linkedinSent(d.lead_id, d.step), `Step ${d.step} marked sent.`)}>Mark sent</button>
            <button className="btn ghost" onClick={() => act(() => api.linkedinReplied(d.lead_id, ""), "Stopped. A task to answer them was added.")}>They replied</button>
            <button className="btn ghost" onClick={() => setHelper(helper === d.lead_id ? null : d.lead_id)}>Reply helper</button>
          </div>
          {helper === d.lead_id && <BranchHelper leadId={d.lead_id} onMessage={setMessage} />}
        </div>
      ))}

      <h3>Waiting for them to accept {q && <span className="pill">{q.waiting_for_acceptance.length}</span>}</h3>
      {q && q.waiting_for_acceptance.length === 0 ? <p className="sub">Nobody waiting.</p> : (
        <ul className="task-list">{q?.waiting_for_acceptance.map((w) => (
          <li key={w.lead_id}><div><b>{w.name}</b> · {w.company}</div>
            <div className="sub">Request sent {w.since ? istLabel(w.since) : ""}</div>
            <div className="row">
              <button className="btn" onClick={() => act(() => api.linkedinAccepted(w.lead_id), "Accepted. The first message is scheduled.")}>They accepted</button>
              <button className="btn ghost" onClick={() => act(() => api.linkedinStop(w.lead_id), "Stopped.")}>Stop</button>
            </div></li>))}
        </ul>
      )}

      <h3>Suggested people to connect with today {q && <span className="sub">({q.started_today} of {q.daily_limit} started today)</span>}</h3>
      {q && q.suggestions.length === 0 ? <p className="sub">No suggestions: either today's limit is reached or no lead has a LinkedIn profile link yet.</p> : (
        <ul className="task-list">{q?.suggestions.map((s) => (
          <li key={s.lead_id}><div><b>{s.name || "Contact"}</b> · {s.company}{s.title ? ` · ${s.title}` : ""}</div>
            <div className="sub">Angle: {PERSONA[s.persona]}</div>
            <div className="row"><button className="btn ghost" onClick={() => act(() => api.linkedinStart(s.lead_id), "Added. Step 1 is in today's list.")}>Start LinkedIn sequence</button></div></li>))}
        </ul>
      )}
    </>
  );
}
