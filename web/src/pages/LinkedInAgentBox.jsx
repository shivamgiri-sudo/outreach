import { useEffect, useState } from "react";
import { api, getLinkedinAccount, isOwner, setLinkedinAccount } from "../api.js";
import { copyText } from "../clipboard.js";
import { istLabel } from "../time.js";
import { useConfirm } from "../Dialog.jsx";
import { GuideButton } from "../GuidePanel.jsx";

// The LinkedIn agent box, per client account (app/linkedin_scope.py): owners see any account (filter below, or every
// account read-only) and hold the platform-wide stop; an account's admins run their own account's agent, with caps up to
// the ceilings the platform admin set; callers and viewers see their account's status only.
const KIND_WORDS = { connect: "Connection request sent", message: "Message sent", accepted: "Accepted", replied: "Replied", error: "Problem", paused: "Waiting", started: "Agent started", auto_start: "Started automatically" };

function Ceiling({ max }) {
  return max == null ? null : <small className="sub-inline" data-testid="li-ceiling">max {max} set by the platform admin</small>;
}

function AccountFilter({ value, onChange }) {
  const [list, setList] = useState([]);
  useEffect(() => { api.accounts().then((r) => setList(Array.isArray(r) ? r : r.accounts || [])).catch(() => {}); }, []);
  if (list.length < 2) return null;
  return (
    <label className="row" style={{ gap: 6, margin: 0 }}>Account
      <select data-testid="li-account-filter" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Picked in the header</option>
        <option value="all">All accounts (overview)</option>
        {list.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
      </select>
    </label>
  );
}

export default function LinkedInAgent({ onMessage, onAccount }) {
  const confirm = useConfirm();
  const [st, setSt] = useState(null);
  const [form, setForm] = useState(null);
  const [acct, setAcct] = useState(getLinkedinAccount());
  const load = (reset) => api.linkedinAgent().then((s) => { setSt(s); setForm((f) => (reset || !f ? s.config : f)); }).catch((e) => onMessage(e.message));
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, []);
  if (!st || !form) return null;
  const c = st.control;
  const manage = !!st.can_manage && st.account_id !== null;        // the owners' "all accounts" overview is read only
  const ceil = st.ceilings || {};
  const pick = (value) => { setLinkedinAccount(value); setAcct(value); load(true); onAccount && onAccount(value); };
  const save = async (changes, done) => {
    try {
      const s = await api.saveLinkedinAgent(changes); setSt(s); setForm(s.config);
      const low = Object.entries(s.clamped || {}).map(([k, v]) => `${k.replace(/_/g, " ")} lowered to ${v} (the platform maximum)`);
      onMessage(low.length ? `${done} ${low.join("; ")}.` : done);
    } catch (e) { onMessage(e.message); }
  };
  const kill = async (on) => {
    if (on && !(await confirm("Stop every LinkedIn agent of every account now? Nothing is sent on LinkedIn until you switch this off."))) return;
    try { setSt(await api.killLinkedinAgents(on)); onMessage(on ? "Every LinkedIn agent is stopped." : "LinkedIn agents may run again (each account's own switch decides)."); }
    catch (e) { onMessage(e.message); }
  };
  const num = (key) => (e) => setForm({ ...form, [key]: e.target.value === "" ? "" : Number(e.target.value) });
  const command = `node tools/linkedin_agent.mjs --portal ${window.location.origin} --token PASTE_THE_TOKEN`;
  const pill = st.kill ? ["err", "Stopped by the platform admin"] : !st.config.enabled ? ["", "Switched off"] : st.online ? ["ok", `Running · ${st.state || "online"}`] : ["warn", "On, but the agent program is not running"];
  return (
    <section className="card" style={{ marginBottom: 14 }} data-testid="linkedin-agent">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>LinkedIn agent{st.account_name ? <span className="sub" data-testid="li-agent-account"> · {st.account_name}</span> : null}</b>
        <span className="row">
          {st.is_owner && <AccountFilter value={acct} onChange={pick} />}
          <span className={`pill ${pill[0]}`} data-testid="li-agent-state">{pill[1]}</span>
          {!manage ? null : st.config.enabled
            ? <button className="btn ghost" onClick={() => save({ enabled: false }, "Paused. The agent will do nothing until you switch it on.")}>Pause</button>
            : <button className="btn" onClick={() => save({ enabled: true }, "Switched on. Start the agent program on your computer if it is not running.")}>Switch on</button>}
        </span>
      </div>
      {st.is_owner && <p className="row" style={{ gap: 8 }}>
        {st.kill ? <><span className="pill err" data-testid="li-kill-on">Every LinkedIn agent is stopped ({st.kill.by}, {istLabel(st.kill.at)})</span>
          <button className="btn ghost" onClick={() => kill(false)}>Let agents run again</button></>
          : <button className="btn ghost" data-testid="li-kill" onClick={() => kill(true)}>Stop every LinkedIn agent (all accounts)</button>}</p>}
      {!st.is_owner && st.kill && <p className="tip error" role="status">The platform admin has stopped every LinkedIn agent for now.</p>}
      <p className="sub">Sends the connection requests and follow-up messages from this page for you, either from the <b>cloud agent</b> above (nothing to install) or
        from <b>your own computer</b> in a Chrome window you log in to yourself (see "Advanced" below; then your password never reaches this portal). The switch, limits and hours here apply to both. It sends one action at a time, with a fixed gap, inside working hours and your daily and weekly
        caps, and it stops by itself if LinkedIn shows a security check, a login wall or the weekly invitation limit.</p>
      <p className="tip">LinkedIn does not allow automation and can restrict accounts that use it, even at low volume. Keep the numbers low and use an account you
        can live without if the worst happens.</p>
      <p className="sub">Today: {c.counts.connect_today} connection requests, {c.counts.message_today} messages · this week: {c.counts.connect_week} requests ·
        left today: {c.connections_left_today} requests, {c.messages_left_today} messages{c.why_not ? ` · ${c.why_not}` : ""}.</p>
      {(st.accounts || []).length > 0 && (
        <div className="table-wrap" style={{ marginBottom: 8 }}>
          <table data-testid="li-agent-accounts"><thead><tr><th>LinkedIn account</th>{st.account_id === null && <th>Client account</th>}<th>Status</th><th>Today</th><th>This week</th><th>People</th><th></th></tr></thead>
            <tbody>{st.accounts.map((a) => (
              <tr key={a.account}>
                <td><b>{a.name || a.account}</b><br /><small>{a.account}</small></td>
                {st.account_id === null && <td>{a.account_name}</td>}
                <td>{a.paused ? <span className="pill warn" title={a.paused.note}>Paused: {a.paused.note}</span> : a.online ? <span className="pill ok">Running</span> : <span className="pill">Not running</span>}
                  {a.last_seen && <><br /><small>seen {istLabel(a.last_seen)}</small></>}</td>
                <td>{a.counts.connect_today} requests · {a.counts.message_today} messages</td>
                <td>{a.counts.connect_week} requests</td>
                <td>{a.leads}</td>
                <td>{manage && a.paused && <button className="btn ghost" onClick={() => save({ resume_account: a.account }, `${a.name || a.account} resumed.`)}>Resume</button>}
                  {manage && st.accounts.length > 1 && <select aria-label={`${a.account} is the same LinkedIn account as`} value="" onChange={async (e) => { const into = e.target.value;
                    if (into && await confirm(`${a.account} is the same LinkedIn account as ${into} (its profile link changed)? Its people and counts move over.`))
                      save({ merge_from: a.account, merge_into: into }, `Merged ${a.account} into ${into}.`); }}>
                    <option value="">Same account as…</option>{st.accounts.filter((x) => x.account !== a.account).map((x) => <option key={x.account} value={x.account}>{x.name || x.account}</option>)}</select>}</td>
              </tr>))}</tbody></table>
          <small className="sub-inline">Each account has its own daily and weekly caps and its own pause. A person belongs to the account that contacted them first; only that account sends them messages.</small>
        </div>
      )}
      {manage && <p><GuideButton keys="app:linkedin_agent" label="How do I set up the agent? (step-by-step guide)" testId="agent-guide" /></p>}
      {manage && <details className="section" data-testid="local-agent-advanced"><summary>Advanced: run on your own computer</summary>
        <ol className="steps">
          <li>Create a pairing token in the Chrome assistant box below and copy it.</li>
          <li>On the computer where you are logged in to LinkedIn, open a terminal in the project folder and run, one time:<br /><code>cd web && npm ci && npx playwright install chromium && cd ..</code></li>
          <li>Start the agent (keep it running while you want it to work):<br /><code>{command}</code></li>
          <li>A Chrome window opens. Log in to LinkedIn in that window yourself (including any code LinkedIn sends). The agent waits, then starts.</li>
          <li>Try it first without clicking anything: add <code>--dry</code> to the command. It says what it would do.</li>
          <li>More LinkedIn accounts: run one agent per account, each with its own window profile, for example <code>--profile ~/.mcn-li-rahul</code>. The same token can be used; the portal tells the accounts apart by who is logged in.</li>
        </ol>
        <button className="btn ghost" onClick={() => copyText(command).then(() => onMessage("Command copied."))}>Copy the command</button>
      </details>}
      {manage && <details className="section" data-testid="li-agent-limits"><summary>Limits</summary>
        <div className="split-2">
          {st.account_id === "mcn" && ceil.daily_connections == null
            ? <label>Connection requests per day<input type="number" disabled value="" placeholder="set in LinkedIn settings below" /></label>
            : <label>Connection requests per day<input type="number" min="1" max={Math.min(40, ceil.daily_connections ?? 40)} value={form.daily_connections}
              onChange={num("daily_connections")} data-testid="li-daily-connections" disabled={st.account_id === "mcn"} /><Ceiling max={ceil.daily_connections} /></label>}
          <label>Per week (LinkedIn allows about 100)<input type="number" min="1" max={Math.min(100, ceil.weekly_connections ?? 100)} value={form.weekly_connections}
            onChange={num("weekly_connections")} data-testid="li-weekly-connections" /><Ceiling max={ceil.weekly_connections} /></label>
          <label>Messages per day<input type="number" min="0" max={Math.min(100, ceil.daily_messages ?? 100)} value={form.daily_messages}
            onChange={num("daily_messages")} data-testid="li-daily-messages" /><Ceiling max={ceil.daily_messages} /></label>
          <label>Minutes between two actions<input type="number" min="3" max="60" value={form.gap_minutes} onChange={num("gap_minutes")} /></label>
          <label>Start hour (India time)<input type="number" min="0" max="23" value={form.start_hour} onChange={num("start_hour")} /></label>
          <label>End hour (India time)<input type="number" min="1" max="24" value={form.end_hour} onChange={num("end_hour")} /></label>
        </div>
        <label className="check-row"><input type="checkbox" checked={form.weekdays_only} onChange={(e) => setForm({ ...form, weekdays_only: e.target.checked })} /> Monday to Friday only</label>
        <label className="check-row"><input type="checkbox" checked={!!form.connect_without_note} onChange={(e) => setForm({ ...form, connect_without_note: e.target.checked })} /> When LinkedIn will not allow a note (free accounts get only a few a month), send the connection request without one</label>
        <label className="check-row"><input type="checkbox" checked={!!form.check_replies} onChange={(e) => setForm({ ...form, check_replies: e.target.checked })} /> Check the LinkedIn messages for replies</label>
        {st.account_id !== "mcn" && <label className="check-row"><input type="checkbox" checked={!!form.auto_start} onChange={(e) => setForm({ ...form, auto_start: e.target.checked })} /> Start the sequence automatically each working day for people with a LinkedIn link, up to the daily limit</label>}
        <button className="btn" style={{ marginTop: 8 }} data-testid="li-save-limits" onClick={() => save({ ...form, enabled: undefined,
          ...(st.account_id === "mcn" ? { daily_connections: undefined, auto_start: undefined } : {}) }, "Limits saved.")}>Save limits</button>
      </details>}
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

