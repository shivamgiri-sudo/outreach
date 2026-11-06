import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useConfirm } from "../Dialog.jsx";
import { istLabel } from "../time.js";

// The cloud LinkedIn agent: runs on the portal's server, so nobody installs anything. The owner adds an account, logs it in
// here (password goes to the server's memory for at most 10 minutes, then it is gone), and the server does the sending
// under the same switch, caps, working hours and safety pause as the agent below.
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
    try { onDone(await api.loginLinkedinCloud(slot.id, email.trim(), secret)); onMessage("Logging in on the server. This page updates by itself."); }
    catch (err) { onMessage(err.message); }
    finally { setBusy(false); }
  };
  return (
    <form className="inline-form" onSubmit={submit} autoComplete="off" data-testid="cloud-login-form">
      <label>LinkedIn email
        <input type="email" required maxLength={254} autoComplete="off" value={email} onChange={(e) => setEmail(e.target.value)} data-testid="cloud-email" /></label>
      <label>LinkedIn password
        <input type="password" required maxLength={200} autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} data-testid="cloud-password" /></label>
      <small className="sub-inline">Sent once to the server's memory for the agent to type into LinkedIn, then deleted. It is never saved or shown again.</small>
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

function Slot({ s, onDone, onMessage }) {
  const confirm = useConfirm();
  const [login, setLogin] = useState(false);
  const [tone, words] = statusOf(s);
  const act = async (fn, done) => { try { onDone(await fn()); onMessage(done); } catch (e) { onMessage(e.message); } };
  const showForm = s.state !== "needs_code" && (login || (s.enabled && ["needs_login", "needs_manual", "error"].includes(s.state)));
  useEffect(() => { if (s.state === "connected" || s.state === "logging_in") setLogin(false); }, [s.state]);
  return (
    <div className="card" style={{ marginBottom: 10 }} data-testid="cloud-slot">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <b>{s.label}</b>
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
        <button className="btn ghost" onClick={() => act(() => api.restartLinkedinCloud(s.id), "Restarting the cloud browser.")}>Restart</button>
        {s.account && <button className="btn ghost" onClick={async () => { if (await confirm(`Log ${s.label} out? The server deletes this account's LinkedIn session; you will need to log in again.`))
          act(() => api.logoutLinkedinCloud(s.id), "Logged out. The session is deleted on the server."); }}>Log out</button>}
        <button className="btn ghost" onClick={async () => { if (await confirm(`Remove ${s.label}? The server stops it and deletes its LinkedIn session. The people it contacted and its counts stay.`))
          act(() => api.removeLinkedinCloud(s.id), `${s.label} removed.`); }}>Remove</button>
      </div>
    </div>
  );
}

export default function LinkedInCloud({ onMessage }) {
  const [st, setSt] = useState(null);
  const [label, setLabel] = useState("");
  const timer = useRef(null);
  const load = () => api.linkedinCloud().then(setSt).catch((e) => onMessage(e.message));
  useEffect(() => { load(); return () => clearTimeout(timer.current); }, []);
  useEffect(() => {                                    // every 5 s while something is happening, else every 30 s
    clearTimeout(timer.current);
    if (!st) return undefined;
    const busy = st.slots.some((s) => BUSY.has(s.state) && s.enabled);
    timer.current = setTimeout(load, busy ? 5000 : 30000);
    return () => clearTimeout(timer.current);
  }, [st]);
  if (!st) return null;
  const add = async (e) => {
    e.preventDefault();
    try { setSt(await api.addLinkedinCloud(label.trim())); setLabel(""); onMessage("Cloud account added. Log it in below."); } catch (err) { onMessage(err.message); }
  };
  const full = st.slots.length >= st.max;
  return (
    <section className="card" style={{ marginBottom: 14 }} data-testid="linkedin-cloud" aria-labelledby="li-cloud-title">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <h3 id="li-cloud-title" style={{ margin: 0 }}>Cloud agent (no installation)</h3>
        {st.slots.length > 0 && !st.supervisor.running && <span className="pill warn" data-testid="cloud-runner-off">The cloud runner is not running on the server</span>}
      </div>
      <p className="sub">The LinkedIn agent can run on this portal's server, so nothing needs to be installed on anyone's computer. Add a LinkedIn account, log it in here
        once, and the server sends the connection requests and follow-up messages from this page, one at a time, inside the working hours and caps set in the agent box below.</p>
      <p className="tip" role="note">LinkedIn may ask for extra security checks because the login comes from a cloud server; the agent stops by itself if it sees one.
        LinkedIn does not allow automation and can restrict accounts that use it. Use an account you can afford to lose.</p>
      {st.slots.map((s) => <Slot key={s.id} s={s} onDone={setSt} onMessage={onMessage} />)}
      {full ? <p className="sub">{st.max === 0 ? "Cloud accounts are switched off on this server." : `This server runs ${st.max} cloud account${st.max === 1 ? "" : "s"} at most (each needs its own browser).`}</p> : (
        <form className="row" onSubmit={add} style={{ flexWrap: "wrap" }}>
          <input aria-label="Name for the LinkedIn account" placeholder="Name, e.g. Sales LinkedIn" maxLength={60} value={label} onChange={(e) => setLabel(e.target.value)} />
          <button className="btn" type="submit" data-testid="cloud-add">Add LinkedIn account</button>
        </form>
      )}
    </section>
  );
}
