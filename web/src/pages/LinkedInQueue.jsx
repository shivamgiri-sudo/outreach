import { useEffect, useRef, useState } from "react";
import { api, canWrite, getRole, isLimitedLogin, isOwner } from "../api.js";
import LinkedInSequenceEditor from "./LinkedInSequenceEditor.jsx";
import LinkedInCloud from "./LinkedInCloud.jsx";
import LinkedInStageBoard from "./LinkedInStageBoard.jsx";
import { copyText } from "../clipboard.js";
import { ErrorState } from "../states.jsx";
import { istLabel } from "../time.js";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import { useConfirm } from "../Dialog.jsx";
import { GuideButton } from "../GuidePanel.jsx";

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
      <fieldset className="ro-fieldset" disabled={!canWrite()}>
      <label>New connection requests per day (start with 10–15)
        <input type="number" min="1" max="40" value={form.linkedin_daily_connections} onChange={(e) => setForm({ ...form, linkedin_daily_connections: e.target.value })} /></label>
      <label>Who sends from the LinkedIn account
        <input value={form.linkedin_owner} placeholder="Name of the representative" onChange={(e) => setForm({ ...form, linkedin_owner: e.target.value })} /></label>
      <label>Case-study link (step 4 is skipped until a real link is set)
        <input value={form.case_study_url} placeholder="https://…" onChange={(e) => setForm({ ...form, case_study_url: e.target.value })} /></label>
      <label className="check-row"><input type="checkbox" data-testid="li-auto-start" checked={!!form.linkedin_auto_start} onChange={(e) => setForm({ ...form, linkedin_auto_start: e.target.checked })} /> Start the sequence automatically: each working day the agent puts the suggested people (with a LinkedIn link, not contacted yet) on the sequence, up to the daily limit above</label>
      </fieldset>
      {canWrite() && <button className="btn" style={{ marginTop: 8 }} onClick={() => onSave({ ...form, linkedin_daily_connections: Number(form.linkedin_daily_connections) })}>Save</button>}
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
        {canWrite() && <button type="button" className="btn ghost" data-testid="import-linkedin-results" onClick={() => file.current?.click()}>Import its results</button>}
        {canWrite() && <input ref={file} type="file" accept=".csv,text/csv" hidden aria-label="Results file from the LinkedIn tool" onChange={(e) => pick(e.target.files?.[0])} />}
      </div>
      <div className="tip" style={{ marginTop: 10 }} role="note">
        Automating LinkedIn is against LinkedIn's terms and can get an account restricted. If you use a tool, that is your own decision and risk;
        use a separate LinkedIn account, not the one you rely on. The portal only logs in to LinkedIn if you add a cloud account above; otherwise the LinkedIn agent below works from your own computer in a window you log in to yourself.
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

const KIND_WORDS = { connect: "Connection request sent", message: "Message sent", accepted: "Accepted", replied: "Replied", error: "Problem", paused: "Waiting", started: "Agent started", auto_start: "Started automatically" };

function LinkedInAgent({ onMessage }) {
  const confirm = useConfirm();
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
      <p className="sub">Sends the connection requests and follow-up messages from this page for you, either from the <b>cloud agent</b> above (nothing to install) or
        from <b>your own computer</b> in a Chrome window you log in to yourself (see "Advanced" below; then your password never reaches this portal). The switch, limits and hours here apply to both. It sends one action at a time, with a fixed gap, inside working hours and your daily and weekly
        caps, and it stops by itself if LinkedIn shows a security check, a login wall or the weekly invitation limit.</p>
      <p className="tip">LinkedIn does not allow automation and can restrict accounts that use it, even at low volume. Keep the numbers low and use an account you
        can live without if the worst happens.</p>
      <p className="sub">Today: {c.counts.connect_today} connection requests, {c.counts.message_today} messages · this week: {c.counts.connect_week} requests ·
        left today: {c.connections_left_today} requests, {c.messages_left_today} messages{c.why_not ? ` · ${c.why_not}` : ""}.</p>
      {(st.accounts || []).length > 0 && (
        <div className="table-wrap" style={{ marginBottom: 8 }}>
          <table><thead><tr><th>LinkedIn account</th><th>Status</th><th>Today</th><th>This week</th><th>People</th><th></th></tr></thead>
            <tbody>{st.accounts.map((a) => (
              <tr key={a.account}>
                <td><b>{a.name || a.account}</b><br /><small>{a.account}</small></td>
                <td>{a.paused ? <span className="pill warn" title={a.paused.note}>Paused: {a.paused.note}</span> : a.online ? <span className="pill ok">Running</span> : <span className="pill">Not running</span>}
                  {a.last_seen && <><br /><small>seen {istLabel(a.last_seen)}</small></>}</td>
                <td>{a.counts.connect_today} requests · {a.counts.message_today} messages</td>
                <td>{a.counts.connect_week} requests</td>
                <td>{a.leads}</td>
                <td>{a.paused && <button className="btn ghost" onClick={() => save({ resume_account: a.account }, `${a.name || a.account} resumed.`)}>Resume</button>}
                  {st.accounts.length > 1 && <select aria-label={`${a.account} is the same LinkedIn account as`} value="" onChange={async (e) => { const into = e.target.value;
                    if (into && await confirm(`${a.account} is the same LinkedIn account as ${into} (its profile link changed)? Its people and counts move over.`))
                      save({ merge_from: a.account, merge_into: into }, `Merged ${a.account} into ${into}.`); }}>
                    <option value="">Same account as…</option>{st.accounts.filter((x) => x.account !== a.account).map((x) => <option key={x.account} value={x.account}>{x.name || x.account}</option>)}</select>}</td>
              </tr>))}</tbody></table>
          <small className="sub-inline">Each account has its own daily and weekly caps and its own pause. A person belongs to the account that contacted them first; only that account sends them messages.</small>
        </div>
      )}
      <p><GuideButton keys="app:linkedin_agent" label="How do I set up the agent? (step-by-step guide)" testId="agent-guide" /></p>
      <details className="section" data-testid="local-agent-advanced"><summary>Advanced: run on your own computer</summary>
        <ol className="steps">
          <li>Create a pairing token in the Chrome assistant box below and copy it.</li>
          <li>On the computer where you are logged in to LinkedIn, open a terminal in the project folder and run, one time:<br /><code>cd web && npm ci && npx playwright install chromium && cd ..</code></li>
          <li>Start the agent (keep it running while you want it to work):<br /><code>{command}</code></li>
          <li>A Chrome window opens. Log in to LinkedIn in that window yourself (including any code LinkedIn sends). The agent waits, then starts.</li>
          <li>Try it first without clicking anything: add <code>--dry</code> to the command. It says what it would do.</li>
          <li>More LinkedIn accounts: run one agent per account, each with its own window profile, for example <code>--profile ~/.mcn-li-rahul</code>. The same token can be used; the portal tells the accounts apart by who is logged in.</li>
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
        <label className="check-row"><input type="checkbox" checked={!!form.connect_without_note} onChange={(e) => setForm({ ...form, connect_without_note: e.target.checked })} /> When LinkedIn will not allow a note (free accounts get only a few a month), send the connection request without one</label>
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
      <p><GuideButton keys="app:chrome_extension" label="How do I install and pair it?" testId="extension-guide" /></p>
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

function Score({ s }) {
  if (!s) return null;
  const tone = s.score >= 75 ? "good" : s.score >= 50 ? "" : "warn";
  return <small className="sub-inline" title={(s.reasons || []).join(" ")}><span className={`pill ${tone}`}>{s.score}/100</span> {s.length} chars · {(s.reasons || []).filter((r) => !/^(Good length|Greets|No sales|About their)/.test(r))[0] || "looks good"}</small>;
}

function ConnectionNotes({ onMessage }) {
  const [cur, setCur] = useState(null);
  const [draft, setDraft] = useState({ A: "", B: "" });
  const [scores, setScores] = useState({});
  const [ideas, setIdeas] = useState(null);
  const [context, setContext] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.linkedinVariants().then((r) => { setCur(r); setDraft({ A: r.notes.A.text, B: r.notes.B.text }); setScores({ A: r.notes.A, B: r.notes.B }); }).catch(() => {});
  useEffect(() => { load(); }, []);
  const rescore = (v, text) => api.scoreLinkedinNote(text).then((s) => setScores((x) => ({ ...x, [v]: s }))).catch(() => {});
  const write = () => { setBusy(true); api.generateLinkedinNotes(context).then(setIdeas).catch((e) => onMessage(e.message)).finally(() => setBusy(false)); };
  const save = () => api.saveLinkedinVariants({ ...draft, workspace: cur.workspace }).then((r) => { setCur(r); onMessage("Connection notes saved. New leads get the new A or B note."); }).catch((e) => onMessage(e.message));
  if (!cur) return null;
  // Main's notes: owners (and callers with every workspace, as before); a workspace's: owners and its own admins.
  const editable = !!cur.can_edit || (cur.workspace === "main" && getRole() === "caller" && !isLimitedLogin());
  return (
    <details className="card" style={{ marginBottom: 12 }}>
      <summary><b>Connection notes A / B</b> <small className="sub-inline">each new lead gets one of the two; compare accept rates on the Intelligence page
        {(isOwner() || cur.workspace !== "main") && <> · <span className="pill">{cur.workspace_name}</span></>}</small></summary>
      {!editable && <p className="sub">Read-only: only an owner or an admin of this workspace changes these notes.</p>}
      {["A", "B"].map((v) => (
        <div key={v} style={{ marginTop: 10 }}>
          <label htmlFor={`note-${v}`}><b>Note {v}</b></label> <Score s={scores[v]} />
          <textarea id={`note-${v}`} rows={3} value={draft[v]} maxLength={600} readOnly={!editable} onChange={(e) => setDraft({ ...draft, [v]: e.target.value })} onBlur={(e) => editable && rescore(v, e.target.value)} style={{ width: "100%" }} />
        </div>
      ))}
      {editable && <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
        <button className="btn" onClick={save}>Save notes</button>
        {!isLimitedLogin() && <>
        <input aria-label="Who you are writing to" placeholder="Optional: who it is for (e.g. CHROs at IT services firms)" value={context} maxLength={500} onChange={(e) => setContext(e.target.value)} style={{ flex: 1, minWidth: 220 }} />
        <button className="btn ghost" onClick={write} disabled={busy}>{busy ? "Writing…" : "Write ideas with Claude"}</button>
        </>}
      </div>}
      {cur.changed_at && <small className="sub-inline">Last changed by {cur.changed_by} · {istLabel(cur.changed_at)}. Accept rates before this date belong to the old notes.</small>}
      {ideas && (
        <div style={{ marginTop: 10 }}>
          <small className="sub-inline">{ideas.source === "claude" ? "Written by Claude, scored by simple rules:" : "Claude is not connected; built-in ideas, scored:"}</small>
          {ideas.candidates.map((c, i) => (
            <div key={i} className="trail-text" style={{ marginTop: 6 }}>
              <div>{c.text}</div><Score s={c} />
              <div className="row"><button className="btn ghost" onClick={() => { setDraft({ ...draft, A: c.text }); setScores((x) => ({ ...x, A: c })); }}>Use as A</button>
                <button className="btn ghost" onClick={() => { setDraft({ ...draft, B: c.text }); setScores((x) => ({ ...x, B: c })); }}>Use as B</button></div>
            </div>
          ))}
        </div>
      )}
    </details>
  );
}

export default function LinkedInQueue() {
  const [q, setQ] = useState(null);
  const [cfg, setCfg] = useState({ linkedin_daily_connections: 12, linkedin_owner: "", case_study_url: "", linkedin_auto_start: false });
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [helper, setHelper] = useState(null);
  const limited = isLimitedLogin();      // a workspace login: its own leads and templates; account, agent and tool settings stay with owners
  const load = () => { setError(""); api.linkedinQueue().then(setQ).catch((e) => setError(e.message)); if (!limited) api.linkedinSettings().then(setCfg).catch(() => {}); };
  useEffect(() => { load(); }, []);
  const act = async (fn, done) => { try { await fn(); setMessage(done); load(); } catch (e) { setMessage(e.message); } };

  return (
    <>
      <h2>LinkedIn outreach</h2>
      <p className="sub">Send by hand from this list, or switch on the LinkedIn agent below to have the server (cloud agent) or your own computer do the sending for you (LinkedIn does not allow
        automation, so keep the numbers low). This page gives the day's list with every message already written. Mark each one sent and the next step is scheduled. Messages only start after
        they accept the connection, and any reply stops the sequence.</p>
      {error && <ErrorState message="The LinkedIn queue could not load." detail={error} onRetry={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}
      <LinkedInStageBoard onMessage={setMessage} />
      {isOwner() && <LinkedInCloud onMessage={setMessage} />}
      {!limited && <ToolBridge onDone={load} />}
      {isOwner() && <LinkedInAgent onMessage={setMessage} />}
      <LinkedInSequenceEditor onMessage={setMessage} />
      <ConnectionNotes onMessage={setMessage} />
      {!limited && <Settings cfg={cfg} onSave={(body) => act(() => api.saveLinkedinSettings(body), "Settings saved.")} />}
      {canWrite() && <ChromeAssistant onMessage={setMessage} />}
      {q && !q.case_study_set && <p className="sub">Step 4 (case study) is skipped until you add a real case-study link in the settings above.</p>}

      <div className="row dl-head">
        <h3>To send today {q && <span className="pill">{q.due.length}</span>}</h3>
        {!limited && <DownloadButton path={exportPath("linkedin_queue")} label="Download today's list" testId="download-linkedin"
          title="Messages to send, people waiting to accept and suggestions, with the ready-to-paste text" />}
      </div>
      {q && q.due.length === 0 && <p className="empty">Nothing due. Start some people from the suggestions below.</p>}
      {q && q.due.map((d) => (
        <div className="card" key={d.task_id} style={{ marginBottom: 10 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{d.name || "Contact"} · {d.company}{d.open_profile && <span className="pill" title="Premium member who accepts messages from anyone" style={{ marginLeft: 6 }}>Open Profile: message without connecting</span>}</b>
            <span className="sub">Step {d.step} · {PERSONA[d.persona] || d.persona || ""}{d.due_at ? ` · ${istLabel(d.due_at)}` : ""}</span>
          </div>
          <p className="trail-text">{d.message}</p>
          <div className="row">
            <button className="btn ghost" onClick={() => copyText(d.message).then(() => setMessage("Message copied. Paste it on LinkedIn."))}>Copy message</button>
            {/^https?:\/\//i.test(d.linkedin_url || "") && <a className="btn ghost" href={d.linkedin_url} target="_blank" rel="noopener noreferrer">Open profile</a>}
            {canWrite() && <>
              <button className="btn" onClick={() => act(() => api.linkedinSent(d.lead_id, d.step), `Step ${d.step} marked sent.`)}>Mark sent</button>
              <button className="btn ghost" onClick={() => act(() => api.linkedinReplied(d.lead_id, ""), "Stopped. A task to answer them was added.")}>They replied</button>
              <button className="btn ghost" onClick={() => setHelper(helper === d.lead_id ? null : d.lead_id)}>Reply helper</button>
            </>}
          </div>
          {helper === d.lead_id && <BranchHelper leadId={d.lead_id} onMessage={setMessage} />}
        </div>
      ))}

      <h3>Waiting for them to accept {q && <span className="pill">{q.waiting_for_acceptance.length}</span>}</h3>
      {q && q.waiting_for_acceptance.length === 0 ? <p className="sub">Nobody waiting.</p> : (
        <ul className="task-list">{q?.waiting_for_acceptance.map((w) => (
          <li key={w.lead_id}><div><b>{w.name}</b> · {w.company}</div>
            <div className="sub">Request sent {w.since ? istLabel(w.since) : ""}</div>
            {canWrite() && <div className="row">
              <button className="btn" onClick={() => act(() => api.linkedinAccepted(w.lead_id), "Accepted. The first message is scheduled.")}>They accepted</button>
              <button className="btn ghost" onClick={() => act(() => api.linkedinStop(w.lead_id), "Stopped.")}>Stop</button>
            </div>}</li>))}
        </ul>
      )}

      <h3>Suggested people to connect with today {q && <span className="sub">({q.started_today} of {q.daily_limit} started today)</span>}</h3>
      {q && q.suggestions.length === 0 ? <p className="sub">No suggestions: either today's limit is reached or no lead has a LinkedIn profile link yet.</p> : (
        <ul className="task-list">{q?.suggestions.map((s) => (
          <li key={s.lead_id}><div><b>{s.name || "Contact"}</b> · {s.company}{s.title ? ` · ${s.title}` : ""}</div>
            <div className="sub">Angle: {PERSONA[s.persona] || s.persona}</div>
            {canWrite() && <div className="row"><button className="btn ghost" onClick={() => act(() => api.linkedinStart(s.lead_id), "Added. Step 1 is in today's list.")}>Start LinkedIn sequence</button></div>}</li>))}
        </ul>
      )}
    </>
  );
}
