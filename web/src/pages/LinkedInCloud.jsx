import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useConfirm } from "../Dialog.jsx";
import { istLabel } from "../time.js";
import OfficeRunner from "./LinkedInOfficeRunner.jsx";

// The portal-run LinkedIn agent. The browser runs on one office computer (default: LinkedIn blocks our cloud server) or on
// the portal's server. Either way the owner adds an account, logs it in here (password goes to the server's memory for at
// most 10 minutes, then it is gone), and the agent does the sending under the same switch, caps, working hours and safety
// pause as the agent below.
const BUSY = new Set(["starting", "logging_in", "needs_login", "needs_code", "needs_app_approval", "logging_out"]);
const WORDS = {
  starting: ["", "Starting…"], needs_login: ["warn", "Needs login"], logging_in: ["", "Logging in…"],
  needs_code: ["warn", "Enter the code LinkedIn sent"], needs_app_approval: ["warn", "Approve the sign-in in the LinkedIn app"],
  needs_manual: ["err", "Needs a person"], paused: ["warn", "Paused"], stopped: ["", "Stopped"], logging_out: ["", "Logging out…"],
  error: ["err", "Problem"],
};

function statusOf(s) {
  if (s.state === "connected") return ["ok", `Connected as ${s.name || s.account || "LinkedIn"}`];
  return WORDS[s.state] || ["", s.state];
}

function LoginForm({ slot, onDone, onMessage }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    const secret = password;
    setPassword("");                                   // never kept in the page after it was sent
    try { onDone(await api.loginLinkedinCloud(slot.id, email.trim(), secret)); onMessage("Logging in. This page updates by itself."); }
    catch (err) { onMessage(err.message); }
    finally { setBusy(false); }
  };
  return (
    <form className="inline-form" onSubmit={submit} autoComplete="off" data-testid="cloud-login-form">
      <label>LinkedIn email
        <input type="email" required maxLength={254} autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="cloud-email" /></label>
      <label>LinkedIn password
        <input type="password" required maxLength={200} autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} data-testid="cloud-password" /></label>
      <small className="sub-inline">Kept in the portal's memory only until the agent types it into LinkedIn, then deleted. It is never saved or shown again.</small>
      <div className="row"><button className="btn" type="submit" disabled={busy || !email || !password}>{busy ? "Sending…" : "Log in"}</button></div>
    </form>
  );
}

function CodeForm({ slot, onDone, onMessage }) {
  const [code, setCode] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    try { onDone(await api.codeLinkedinCloud(slot.id, code)); setCode(""); onMessage("Code sent to the agent."); } catch (err) { onMessage(err.message); }
  };
  return (
    <form className="inline-form" onSubmit={submit} autoComplete="off" data-testid="cloud-code-form">
      <label>Verification code from LinkedIn (email or SMS)
        <input inputMode="numeric" pattern="[0-9 ]{4,10}" maxLength={10} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} data-testid="cloud-code" /></label>
      <div className="row"><button className="btn" type="submit" disabled={!/^\d{4,8}$/.test(code.replace(/\s/g, ""))}>Send code</button></div>
    </form>
  );
}

function Slot({ s, onDone, onMessage, overview }) {
  const confirm = useConfirm();
  const [login, setLogin] = useState(false);
  const [tone, words] = statusOf(s);
  const act = async (fn, done) => { try { onDone(await fn()); onMessage(done); } catch (e) { onMessage(e.message); } };
  const showForm = s.state !== "needs_code" && (login || (s.enabled && ["needs_login", "needs_manual", "error"].includes(s.state)));
  useEffect(() => { if (s.state === "connected" || s.state === "logging_in") setLogin(false); }, [s.state]);
  return (
    <div className="card" style={{ marginBottom: 10 }} data-testid="cloud-slot">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <b>{s.label}{overview && s.account_name ? <span className="sub"> · {s.account_name}</span> : null}</b>
        <span className={`pill ${tone}`} data-testid="cloud-state">{words}</span>
      </div>
      {s.note && <p className="sub" style={{ marginBottom: 4 }}>{s.note}</p>}
      <p className="sub" style={{ marginTop: 0 }}>{s.last_seen ? `Last heard from ${istLabel(s.last_seen)}` : "Not heard from yet"}{s.account ? ` · LinkedIn: ${s.account}` : ""}</p>
      {s.state === "needs_code" && <CodeForm slot={s} onDone={onDone} onMessage={onMessage} />}
      {s.state === "needs_app_approval" && <p className="tip" role="status">Open the LinkedIn app on the phone of this account and tap <b>Yes</b> to approve the sign-in. The agent waits up to 3 minutes.</p>}
      {showForm && <LoginForm slot={s} onDone={(r) => { setLogin(false); onDone(r); }} onMessage={onMessage} />}
      <div className="row" style={{ flexWrap: "wrap" }}>
        {!showForm && !["connected", "needs_code", "logging_in"].includes(s.state) && <button className="btn ghost" onClick={() => setLogin(true)}>Log in</button>}
        {s.enabled
          ? <button className="btn ghost" onClick={() => act(() => api.changeLinkedinCloud(s.id, { enabled: false }), `${s.label} switched off. Its browser stops within a minute.`)}>Switch off</button>
          : <button className="btn ghost" onClick={() => act(() => api.changeLinkedinCloud(s.id, { enabled: true }), `${s.label} switched on.`)}>Switch on</button>}
        <button className="btn ghost" onClick={() => act(() => api.restartLinkedinCloud(s.id), "Restarting the LinkedIn browser.")}>Restart</button>
        {s.account && <button className="btn ghost" onClick={async () => { if (await confirm(`Log ${s.label} out? Its LinkedIn browser session is deleted; you will need to log in again.`))
          act(() => api.logoutLinkedinCloud(s.id), "Logged out. The browser session is deleted."); }}>Log out</button>}
        <button className="btn ghost" onClick={async () => { if (await confirm(`Remove ${s.label}? Its browser stops and its LinkedIn session is deleted. The people it contacted and its counts stay.`))
          act(() => api.removeLinkedinCloud(s.id), `${s.label} removed.`); }}>Remove</button>
      </div>
    </div>
  );
}

export default function LinkedInCloud({ onMessage }) {
  const [st, setSt] = useState(null);
  const [label, setLabel] = useState("");
  const confirm = useConfirm();
  const timer = useRef(null);
  const load = () => api.linkedinCloud().then(setSt).catch((e) => onMessage(e.message));
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, []);
  useEffect(() => {                                    // every 5 s while something is happening, else every 30 s
    clearTimeout(timer.current);
    if (!st) return undefined;
    const busy = st.slots.some((s) => BUSY.has(s.state) && s.enabled) || !!st.pairing;
    timer.current = setTimeout(load, busy ? 5000 : 30000);
    return () => clearTimeout(timer.current);
  }, [st]);
  if (!st) return null;
  const add = async (e) => {
    e.preventDefault();
    try { setSt(await api.addLinkedinCloud(label.trim())); setLabel(""); onMessage("LinkedIn account added. Log it in below."); } catch (err) { onMessage(err.message); }
  };
  const full = st.slots.length >= st.max;
  const office = st.where !== "server";
  const owner = st.is_owner !== false;
  const overview = st.account_id === null;          // an owner's "all accounts" view: read the list, change it per account
  const where = async (value) => {
    if (value === st.where) return;
    const ok = await confirm(value === "server"
      ? "Run LinkedIn on the portal's server instead? LinkedIn currently blocks our cloud server, so logins will probably fail there. The office computer stops its LinkedIn browsers."
      : "Run LinkedIn on the office computer? The server stops its LinkedIn browsers and the office computer takes over.");
    if (!ok) return;
    try { setSt(await api.setLinkedinWhere(value)); onMessage(value === "server" ? "LinkedIn now runs on the server." : "LinkedIn now runs on the office computer."); }
    catch (e) { onMessage(e.message); }
  };
  return (
    <section className="card" style={{ marginBottom: 14 }} data-testid="linkedin-cloud" aria-labelledby="li-cloud-title">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <h3 id="li-cloud-title" style={{ margin: 0 }}>LinkedIn agent run from this portal</h3>
        {!office && st.slots.length > 0 && !st.supervisor.running && <span className="pill warn" data-testid="cloud-runner-off">The cloud runner is not running on the server</span>}
      </div>
      <p className="sub">Add a LinkedIn account, log it in here once, and the agent sends the connection requests and follow-up messages, one at a time, inside the working
        hours and caps set in the agent box below. You do everything from this page.</p>
      {owner && <div className="row" style={{ flexWrap: "wrap", alignItems: "center", gap: 8, margin: "4px 0 10px" }}>
        <span className="sub" style={{ margin: 0 }}>Where the LinkedIn browser runs:</span>
        <div className="tab-chips" role="radiogroup" aria-label="Where the LinkedIn browser runs" style={{ margin: 0 }} data-testid="cloud-where">
          <button type="button" role="radio" aria-checked={office} className={`tab-chip${office ? " on" : ""}`} onClick={() => where("office")}>Office computer (recommended)</button>
          <button type="button" role="radio" aria-checked={!office} className={`tab-chip${!office ? " on" : ""}`} onClick={() => where("server")}>Portal server</button>
        </div>
      </div>}
      {overview ? <p className="sub" data-testid="cloud-overview">Every account's LinkedIn accounts. Pick one account above to add accounts or connect its office computer.</p>
        : office ? <OfficeRunner st={st} onDone={setSt} onMessage={onMessage} />
        : <p className="tip" role="note">LinkedIn blocks logins from our cloud server (it shows "Sorry, you have been blocked"), so the server can only be used if that changes.
          Switch to Office computer to run it on an ordinary office connection.</p>}
      {office && st.slots.length > 0 && !st.runner?.online && <p className="tip" role="note" data-testid="cloud-waiting-runner">The LinkedIn accounts below start as soon as the office computer is online.</p>}
      <p className="tip" role="note">LinkedIn does not allow automation and can restrict accounts that use it; the agent stops by itself at any security check.
        Use an account you can afford to lose.</p>
      {st.slots.map((s) => <Slot key={s.id} s={s} onDone={setSt} onMessage={onMessage} overview={overview} />)}
      {overview ? null : full ? <p className="sub">{st.max === 0 ? "LinkedIn accounts are switched off here." : `${office ? "The office computer" : "This server"} runs ${st.max} LinkedIn account${st.max === 1 ? "" : "s"} at most (each needs its own browser).`}</p> : (
        <form className="row" onSubmit={add} style={{ flexWrap: "wrap" }}>
          <input aria-label="Name for the LinkedIn account" placeholder="Name, e.g. Sales LinkedIn" maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} />
          <button className="btn" type="submit" data-testid="cloud-add">Add LinkedIn account</button>
        </form>
      )}
    </section>
  );
}
