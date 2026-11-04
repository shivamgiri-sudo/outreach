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
        use a separate LinkedIn account, not the one you rely on. The portal never logs in to LinkedIn; the LinkedIn agent below works from your own computer in a window you log in to yourself.
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

const KIND_WORDS = { connect: "Connection request sent", message: "Message sent", accepted: "Accepted", replied: "Replied", error: "Problem", paused: "Waiting", started: "Agent started" };

function LinkedInAgent({ onMessage }) {
  const [st, setSt] = useState(null);
  const [form, setForm] = useState(null);
  const load = () => api.linkedinAgent().then((s) => { setSt(s); setForm((f) => f || s.config); }).catch((e) => onMessage(e.message));
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, []);
  if (!st || !form) return null;
  const c = st.control;
  const save = async (changes, done) => {
    try { const s = await api.saveLinkedinAgent(changes); setSt(s); setForm(s.config); onMessage(done); } catch (e) { onMessage(e.message); }
  };
  const num = (key) => (e) => setForm({ ...form, [key]: e.target.value === "" ? "" : Number(e.target.value) });
  const command = `node tools/linkedin_agent.mjs --portal ${window.location.origin} --token PASTE_THE_TOKEN`;
  const pill = !st.config.enabled ? ["", "Switched off"] : st.online ? ["ok", `Running · ${st.state || "online"}`] : ["warn", "On, but the agent program is not running"];
  return (
    <section className="card" style={{ marginBottom: 14 }} data-testid="linkedin-agent">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>LinkedIn agent</b>
        <span className="row">
          <span className={`pill ${pill[0]}`}>{pill[1]}</span>
          {st.config.enabled
            ? <button className="btn ghost" onClick={() => save({ enabled: false }, "Paused. The agent will do nothing until you switch it on.")}>Pause</button>
            : <button className="btn" onClick={() => save({ enabled: true }, "Switched on. Start the agent program on your computer if it is not running.")}>Switch on</button>}
        </span>
      </div>
      <p className="sub">Sends the connection requests and follow-up messages from this page for you, from <b>your own computer</b>, in a Chrome window you log in
        to yourself. Your LinkedIn password never reaches this portal. It sends one action at a time, with a fixed gap, inside working hours and your daily and weekly
        caps, and it stops by itself if LinkedIn shows a security check, a login wall or the weekly invitation limit.</p>
      <p className="tip">LinkedIn does not allow automation and can restrict accounts that use it, even at low volume. Keep the numbers low and use an account you
        can live without if the worst happens.</p>
      <p className="sub">Today: {c.counts.connect_today} connection requests, {c.counts.message_today} messages · this week: {c.counts.connect_week} requests ·
        left today: {c.connections_left_today} requests, {c.messages_left_today} messages{c.why_not ? ` · ${c.why_not}` : ""}.</p>
      <details className="section"><summary>Set it up on your computer (once)</summary>
        <ol className="steps">
          <li>Create a pairing token in the Chrome assistant box below and copy it.</li>
          <li>On the computer where you are logged in to LinkedIn, open a terminal in the project folder and run, one time:<br /><code>cd web && npm ci && npx playwright install chromium && cd ..</code></li>
          <li>Start the agent (keep it running while you want it to work):<br /><code>{command}</code></li>
          <li>A Chrome window opens. Log in to LinkedIn in that window yourself (including any code LinkedIn sends). The agent waits, then starts.</li>
          <li>Try it first without clicking anything: add <code>--dry</code> to the command. It says what it would do.</li>
        </ol>
        <button className="btn ghost" onClick={() => copyText(command).then(() => onMessage("Command copied."))}>Copy the command</button>
      </details>
      <details className="section"><summary>Limits</summary>
        <div className="split-2">
          <label>Connection requests per day<input type="number" disabled value="" placeholder="set in LinkedIn settings below" /></label>
          <label>Per week (LinkedIn allows about 100)<input type="number" min="1" max="100" value={form.weekly_connections} onChange={num("weekly_connections")} /></label>
          <label>Messages per day<input type="number" min="0" max="100" value={form.daily_messages} onChange={num("daily_messages")} /></label>
          <label>Minutes between two actions<input type="number" min="3" max="60" value={form.gap_minutes} onChange={num("gap_minutes")} /></label>
          <label>Start hour (India time)<input type="number" min="0" max="23" value={form.start_hour} onChange={num("start_hour")} /></label>
          <label>End hour (India time)<input type="number" min="1" max="24" value={form.end_hour} onChange={num("end_hour")} /></label>
        </div>
        <label className="check-row"><input type="checkbox" checked={form.weekdays_only} onChange={(e) => setForm({ ...form, weekdays_only: e.target.checked })} /> Monday to Friday only</label>
        <button className="btn" style={{ marginTop: 8 }} onClick={() => save({ ...form, enabled: undefined }, "Limits saved.")}>Save limits</button>
      </details>
      {st.log.length > 0 && (
        <details className="section" open><summary>What it did lately</summary>
          <ul className="task-list">{st.log.slice(0, 12).map((r, i) => (
            <li key={i}><div><b>{KIND_WORDS[r.kind] || r.kind}</b>{r.note ? ` — ${r.note}` : ""}</div><div className="sub">{istLabel(r.at)}</div></li>))}
          </ul>
        </details>
      )}
    </section>
  );
}

function ChromeAssistant({ onMessage }) {
  const [tokens, setTokens] = useState([]);
  const [label, setLabel] = useState("");
  const [fresh, setFresh] = useState(null);       // the raw token, shown once
  const load = () => api.extensionTokens().then(setTokens).catch(() => {});
  useEffect(() => { load(); }, []);
  const create = async () => {
    try { setFresh(await api.extensionPair(label.trim())); setLabel(""); load(); } catch (e) { onMessage(e.message); }
  };
  const revoke = async (id) => {
    try { await api.extensionRevoke(id); if (fresh && fresh.id === id) setFresh(null); onMessage("Pairing token revoked."); load(); } catch (e) { onMessage(e.message); }
  };
  return (
    <details className="card" style={{ marginBottom: 14 }} data-testid="chrome-assistant">
      <summary><b>Chrome assistant</b> <span className="sub">— shows these messages next to the LinkedIn profile you have open</span></summary>
      <p className="sub">A Chrome extension reads the one profile you open, shows the ready message and lets you insert it. You press LinkedIn's Send
        yourself and confirm in the panel. It never clicks, scrolls or sends for you. Install steps: <code>extensions/linkedin-assistant/README.md</code> in the project.</p>
      <div className="row">
        <input aria-label="Name for this computer" placeholder="Name for this computer (optional)" value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)} />
        <button className="btn" onClick={create}>Create pairing token</button>
      </div>
      {fresh && (
        <div className="tip" role="status" data-testid="pair-token-box" style={{ marginTop: 8 }}>
          <b>Copy this token now. It is shown only once.</b>
          <p className="trail-text" data-testid="pair-token" style={{ wordBreak: "break-all" }}>{fresh.token}</p>
          <button className="btn ghost" onClick={() => copyText(fresh.token).then(() => onMessage("Token copied. Paste it into the extension's settings."))}>Copy token</button>
          <button className="btn ghost" onClick={() => setFresh(null)}>I have copied it</button>
        </div>
      )}
      {tokens.length === 0 ? <p className="sub">No computers paired yet.</p> : (
        <ul className="task-list">{tokens.map((t) => (
          <li key={t.id} data-testid="pair-row"><div><b>{t.label}</b> · …{t.hint} <span className="sub">({t.owner})</span></div>
            <div className="sub">Made {istLabel(t.created_at)} · {t.last_used_at ? `last used ${istLabel(t.last_used_at)}` : "never used"} · expires {istLabel(t.expires_at)}</div>
            <div className="row"><button className="btn ghost" onClick={() => revoke(t.id)}>Revoke</button></div></li>))}
        </ul>
      )}
    </details>
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
      <p className="sub">Send by hand from this list, or switch on the LinkedIn agent below to have your own computer do the sending for you (LinkedIn does not allow
        automation, so keep the numbers low). This page gives the day's list with every message already written. Mark each one sent and the next step is scheduled. Messages only start after
        they accept the connection, and any reply stops the sequence.</p>
      {error && <ErrorState message="The LinkedIn queue could not load." detail={error} onRetry={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}
      <ToolBridge onDone={load} />
      <LinkedInAgent onMessage={setMessage} />
      <Settings cfg={cfg} onSave={(body) => act(() => api.saveLinkedinSettings(body), "Settings saved.")} />
      <ChromeAssistant onMessage={setMessage} />
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
