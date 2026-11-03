import { useRef, useState } from "react";
import { api, setToken } from "../api.js";
import Icon from "../icons.jsx";
import "../login.css";

// The pipeline drawn in the brand panel, in order. Tone = which colour of the Mas Callnet logo tints the step.
const STAGES = [
  { label: "Find", icon: "search", tone: "blue", note: "Employers hiring now" },
  { label: "Enrich", icon: "idcard", tone: "green", note: "Contacts found and checked" },
  { label: "Reach", icon: "send", tone: "red", note: "Email · WhatsApp · LinkedIn" },
  { label: "Reply", icon: "reply", tone: "blue", note: "Replies read and sorted" },
  { label: "Meeting", icon: "calendar", tone: "green", note: "Demo on the calendar" },
  { label: "Customer", icon: "customer", tone: "goal", note: "Signed up and hiring" },
];

const FEATURES = [
  { icon: "layers", tone: "blue", title: "Multi-channel sequences", text: "Email, WhatsApp and LinkedIn, paced and tracked in one place." },
  { icon: "inbox", tone: "green", title: "Reply reading & tasks for Priya", text: "Replies are read and sorted; a yes becomes a call task." },
  { icon: "shield", tone: "red", title: "DPDP-ready consent & unsubscribe", text: "Consent recorded, one-click unsubscribe, erasure on request." },
];

function Pipeline() {
  return (
    <ol className="flow">
      {STAGES.map((s, i) => (
        <li key={s.label} className={`flow-step tone-${s.tone}`} style={{ "--i": i }}>
          <span className="flow-dot"><Icon name={s.icon} size={20} /></span>
          <span className="flow-text"><b>{s.label}</b><small>{s.note}</small></span>
          {i < STAGES.length - 1 && <span className={`flow-link to-${STAGES[i + 1].tone}`} aria-hidden="true"><i /></span>}
        </li>
      ))}
    </ol>
  );
}

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState(false);
  const [caps, setCaps] = useState(false);
  const pw = useRef(null);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!password) { setError("Enter your password to sign in."); pw.current?.focus(); return; }
    setError("");
    setBusy(true);
    try {
      const { token } = await api.login(password, email.trim());
      setToken(token);
      onSignedIn();
    } catch (err) {
      setError(err.message === "Failed to fetch" ? "The app server is not answering. Check your connection and try again." : err.message);
      setBusy(false);
      pw.current?.focus();
      pw.current?.select();
    }
  };
  const readCaps = (e) => setCaps(!!e.getModifierState?.("CapsLock"));
  const describedBy = [caps && "pw-caps", error && "login-error"].filter(Boolean).join(" ") || undefined;

  return (
    <div className="login">
      <div className="login-art" aria-hidden="true">
        <svg className="login-rings" viewBox="0 0 600 600" focusable="false">
          <circle cx="300" cy="300" r="250" stroke="#d83038" strokeDasharray="1100 471" transform="rotate(30 300 300)" />
          <circle cx="300" cy="300" r="196" stroke="#2078b8" strokeDasharray="560 672" transform="rotate(190 300 300)" />
          <circle cx="300" cy="300" r="142" stroke="#70c050" strokeDasharray="330 562" transform="rotate(250 300 300)" />
        </svg>
      </div>

      <header className="login-brand">
        <img className="login-logo" src="/mcn-logo.png" width="138" height="50" alt="Mas Callnet" />
        <h1 className="login-title">MCN <span>Outreach</span></h1>
        <p className="login-lede">Find hiring employers, start honest conversations, book demos — on autopilot, with consent built in.</p>
      </header>

      <main className="login-main">
        <form className="login-card" onSubmit={submit} aria-labelledby="login-heading" noValidate>
          <p className="login-eyebrow">Welcome back</p>
          <h2 id="login-heading">Sign in to MCN Outreach</h2>
          <p className="login-help">Use the admin password, or your work email and password if the owner added you to the team.</p>

          <div className="login-field">
            <label htmlFor="em">Work email <span className="login-opt">Optional</span></label>
            <div className="login-input">
              <Icon name="mail" size={18} />
              <input id="em" type="email" inputMode="email" autoComplete="username" placeholder="name@company.com"
                value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
          </div>

          <div className="login-field">
            <label htmlFor="pw">Password</label>
            <div className="login-input has-toggle">
              <Icon name="lock" size={18} />
              <input id="pw" ref={pw} type={show ? "text" : "password"} autoComplete="current-password" autoFocus required
                value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={error ? true : undefined}
                aria-describedby={describedBy} onKeyDown={readCaps} onKeyUp={readCaps} onBlur={() => setCaps(false)} />
              <button type="button" className="login-reveal" aria-controls="pw" aria-pressed={show}
                onClick={() => { setShow(!show); pw.current?.focus(); }}>
                <Icon name={show ? "eyeOff" : "eye"} size={18} /><span>{show ? "Hide" : "Show"}</span><span className="sr-only"> password</span>
              </button>
            </div>
            {caps && <p id="pw-caps" className="login-note warn" role="status"><Icon name="capslock" size={16} />Caps Lock is on</p>}
          </div>

          <div id="login-error" className="login-error" role="alert">
            {error && <><Icon name="alert" size={18} /><span>{error}</span></>}
          </div>

          <button className="login-submit" type="submit" disabled={busy} aria-busy={busy || undefined}>
            {busy ? <><span className="login-spinner" aria-hidden="true" />Signing in…</> : <>Sign in<Icon name="arrowRight" size={18} /></>}
          </button>
          <p className="login-fine"><Icon name="lock" size={14} />Sessions stay on this browser tab only.</p>
        </form>
      </main>

      <section className="login-story" aria-labelledby="story-title">
        <div className="login-glass">
          <h2 id="story-title" className="login-eyebrow">From first find to first customer</h2>
          <Pipeline />
        </div>
        <ul className="login-tiles">
          {FEATURES.map((f) => (
            <li key={f.title} className={`login-tile tone-${f.tone}`}>
              <span className="login-tile-icon"><Icon name={f.icon} size={20} /></span>
              <div><h3>{f.title}</h3><p>{f.text}</p></div>
            </li>
          ))}
        </ul>
        <p className="login-for">Built for <img src="/bmi-logo.png" width="145" height="22" alt="BookMyInterview.ai" /></p>
      </section>

      <footer className="login-foot">© {new Date().getFullYear()} Mas Callnet · MCN Outreach</footer>
    </div>
  );
}
