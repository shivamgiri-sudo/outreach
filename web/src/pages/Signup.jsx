// Create an account (self sign-up), the email confirmation page, email sign-in links and Google / Microsoft sign-in.
// Server: app/routes_signup.py. All of these live on the sign-in screen (Login.jsx picks the mode).
import { useEffect, useRef, useState } from "react";
import { setToken } from "../api.js";
import { platform } from "../platformApi.js";
import Abbr from "../components/Abbr.jsx";

const tokenIn = (kind) => {
  const m = new RegExp(`^#/?${kind}\\?token=([A-Za-z0-9_.-]{10,400})`).exec(window.location.hash || "");
  return m ? m[1] : "";
};
export const verifyTokenFromHash = () => tokenIn("verify");
export const magicTokenFromHash = () => tokenIn("magic");
export const isOidcReturn = () => window.location.pathname === "/oidc/callback";
const STATE_KEY = "outreach-oidc-state";
const clearUrl = () => window.history.replaceState(null, "", "/");
const niceError = (err) => (err.message === "Failed to fetch" ? "The app server is not answering. Check your connection and try again." : err.message);

function Field({ id, label, optional, children }) {
  return (
    <div className="login-field">
      <label htmlFor={id}>{label}{optional && <span className="login-opt"> Optional</span>}</label>
      <div className="login-input">{children}</div>
    </div>
  );
}

// After the server answers: a token (signed in), a two-step step, or an OIDC sign-up that needs the company name.
export function useFinish(onSignedIn, onTwoStep) {
  return (res) => {
    if (res.two_step) { onTwoStep(res); return; }
    setToken(res.token);
    clearUrl();
    onSignedIn();
  };
}

export function OidcButtons({ intent = "login" }) {
  const [providers, setProviders] = useState([]);
  const [error, setError] = useState("");
  useEffect(() => { platform.signupConfig().then((c) => setProviders(c.providers || [])).catch(() => {}); }, []);
  if (!providers.length) return null;
  const go = async (id) => {
    try {
      const { url, state } = await platform.oidcStart(id, intent);
      try { sessionStorage.setItem(STATE_KEY, state); } catch { /* storage blocked */ }
      window.location.assign(url);
    } catch (err) { setError(niceError(err)); }
  };
  return (
    <div className="oidc-buttons" data-testid="oidc-buttons" style={{ display: "grid", gap: 8, marginTop: 12 }}>
      {providers.map((p) => (
        <button key={p.id} type="button" className="btn ghost" onClick={() => go(p.id)} data-testid={`oidc-${p.id}`}>
          {intent === "signup" ? "Sign up" : "Sign in"} with {p.title}
        </button>
      ))}
      <small className="sub">This is single sign-on (<Abbr term="SSO">SSO</Abbr>), built on <Abbr term="OIDC">OIDC</Abbr>: we never see your Google or Microsoft password.</small>
      {error && <p className="login-error" role="alert">{error}</p>}
    </div>
  );
}

function Terms({ accept, setAccept, terms }) {
  return (
    <label className="check-row" style={{ margin: "6px 0 10px", display: "block" }}>
      <input type="checkbox" checked={accept} onChange={(e) => setAccept(e.target.checked)} data-testid="signup-accept" required />
      {" "}I accept the <a href="/terms" target="_blank" rel="noreferrer">Terms</a>, the <a href="/privacy" target="_blank" rel="noreferrer">Privacy Policy</a> and
      the <a href="/dpa" target="_blank" rel="noreferrer">Data Processing Agreement</a>{terms ? ` (version ${terms.terms})` : ""}.
    </label>
  );
}

export function SignupForm({ onBack }) {
  const [f, setF] = useState({ company: "", name: "", email: "", password: "", phone: "", website: "" });
  const [accept, setAccept] = useState(false);
  const [ticket, setTicket] = useState("");
  const [terms, setTerms] = useState(null);
  const [challenge, setChallenge] = useState(null);
  const [answer, setAnswer] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    platform.signupForm().then((r) => setTicket(r.ticket)).catch(() => {});
    platform.signupConfig().then((c) => setTerms(c.terms)).catch(() => {});
  }, []);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (f.password.length < 10) { setError("Use at least 10 characters for the password."); return; }
    if (!accept) { setError("Please accept the Terms, the Privacy Policy and the Data Processing Agreement."); return; }
    setBusy(true); setError("");
    try {
      const res = await platform.signup({ ...f, accept, ticket, ...(challenge ? { challenge_token: challenge.token, challenge_answer: answer } : {}) });
      setMsg(res.message); setChallenge(null);
    } catch (err) {
      if (err.challenge) { setChallenge(err.challenge); setAnswer(""); }
      setError(niceError(err));
    }
    setBusy(false);
  };
  if (msg) {
    return (
      <div className="login-card" data-testid="signup-sent" role="status">
        <p className="login-eyebrow">Check your inbox</p>
        <h2>Confirm your email</h2>
        <p className="login-help">{msg}</p>
        <button type="button" className="link" onClick={onBack}>Back to sign in</button>
      </div>
    );
  }
  return (
    <form className="login-card" onSubmit={submit} aria-labelledby="signup-heading" data-testid="signup-form" noValidate>
      <p className="login-eyebrow">New here?</p>
      <h2 id="signup-heading">Create an account</h2>
      <p className="login-help">Our team reviews every new company account, usually within one working day.</p>
      <Field id="su-company" label="Company name"><input id="su-company" required maxLength={80} value={f.company} onChange={set("company")} autoComplete="organization" /></Field>
      <Field id="su-name" label="Your name"><input id="su-name" required maxLength={80} value={f.name} onChange={set("name")} autoComplete="name" /></Field>
      <Field id="su-email" label="Work email"><input id="su-email" type="email" required value={f.email} onChange={set("email")} autoComplete="email" placeholder="name@company.com" /></Field>
      <Field id="su-pw" label="Password"><input id="su-pw" type="password" required minLength={10} value={f.password} onChange={set("password")} autoComplete="new-password" placeholder="At least 10 characters" /></Field>
      <Field id="su-phone" label="Phone" optional><input id="su-phone" type="tel" value={f.phone} onChange={set("phone")} autoComplete="tel" /></Field>
      <div aria-hidden="true" style={{ position: "absolute", left: "-10000px", width: 1, height: 1, overflow: "hidden" }}>
        <label htmlFor="su-website">Website</label><input id="su-website" tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} />
      </div>
      <Terms accept={accept} setAccept={setAccept} terms={terms} />
      {challenge && <Field id="su-sum" label={challenge.question}>
        <input id="su-sum" inputMode="numeric" value={answer} onChange={(e) => setAnswer(e.target.value)} data-testid="signup-challenge" /></Field>}
      <div className="login-error" role="alert">{error && <span>{error}</span>}</div>
      <button className="login-submit" type="submit" disabled={busy}>{busy ? "Sending…" : "Create account"}</button>
      <OidcButtons intent="signup" />
      <button type="button" className="link" onClick={onBack} style={{ marginTop: 10 }}>I already have a login</button>
    </form>
  );
}

export function VerifyPage({ token, onSignedIn, onTwoStep, onBack }) {
  const [error, setError] = useState("");
  const finish = useFinish(onSignedIn, onTwoStep);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    platform.verifySignup(token).then(finish).catch((err) => setError(niceError(err)));
  }, []);
  return (
    <div className="login-card" data-testid="verify-page" role="status">
      <p className="login-eyebrow">Confirming your email</p>
      <h2>{error ? "This link did not work" : "One moment…"}</h2>
      {error && <><p className="login-error" role="alert">{error}</p><button type="button" className="link" onClick={() => { clearUrl(); onBack(); }}>Back to sign in</button></>}
    </div>
  );
}

export function MagicForm({ onBack, initialEmail = "" }) {
  const [email, setEmail] = useState(initialEmail);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { setMsg((await platform.magicLink(email.trim())).message); } catch (err) { setMsg(niceError(err)); }
    setBusy(false);
  };
  return (
    <form className="login-card" onSubmit={send} data-testid="magic-form">
      <p className="login-eyebrow">No password needed</p>
      <h2>Email me a sign-in link</h2>
      <p className="login-help">For callers and viewers. Owners and admins sign in with their password and two-step login.</p>
      <Field id="mg-email" label="Work email"><input id="mg-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" /></Field>
      {msg && <p className="login-note" role="status">{msg}</p>}
      <button className="login-submit" type="submit" disabled={busy || !email.trim()}>{busy ? "Sending…" : "Email me a link"}</button>
      <button type="button" className="link" onClick={onBack} style={{ marginTop: 10 }}>Back to sign in</button>
    </form>
  );
}

export function MagicVerify({ token, onSignedIn, onTwoStep, onBack }) {
  const [error, setError] = useState("");
  const finish = useFinish(onSignedIn, onTwoStep);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    platform.magicVerify(token).then(finish).catch((err) => setError(niceError(err)));
  }, []);
  return (
    <div className="login-card" role="status" data-testid="magic-verify">
      <h2>{error ? "This link did not work" : "Signing you in…"}</h2>
      {error && <><p className="login-error" role="alert">{error}</p><button type="button" className="link" onClick={() => { clearUrl(); onBack(); }}>Back to sign in</button></>}
    </div>
  );
}

// The provider sent the browser back to /oidc/callback?code=…&state=…
export function OidcReturn({ onSignedIn, onTwoStep, onBack }) {
  const [error, setError] = useState("");
  const [signup, setSignup] = useState(null);
  const [company, setCompany] = useState("");
  const [phone, setPhone] = useState("");
  const [accept, setAccept] = useState(false);
  const finish = useFinish(onSignedIn, onTwoStep);
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const q = new URLSearchParams(window.location.search);
    let mine = "";
    try { mine = sessionStorage.getItem(STATE_KEY) || ""; sessionStorage.removeItem(STATE_KEY); } catch { /* storage blocked */ }
    if (q.get("error")) { setError(`The provider said: ${q.get("error_description") || q.get("error")}`); return; }
    if (!q.get("state") || q.get("state") !== mine) { setError("This sign-in was not started in this browser tab. Start again."); return; }
    platform.oidcCallback(q.get("code") || "", q.get("state")).then((res) => (res.signup ? setSignup(res.signup) : finish(res)))
      .catch((err) => setError(niceError(err)));
  }, []);
  const create = async (e) => {
    e.preventDefault();
    try { finish(await platform.oidcSignup({ ticket: signup.ticket, company, phone, accept })); } catch (err) { setError(niceError(err)); }
  };
  if (signup) {
    return (
      <form className="login-card" onSubmit={create} data-testid="oidc-signup">
        <p className="login-eyebrow">Almost done</p>
        <h2>Create the account for {signup.email}</h2>
        <Field id="os-company" label="Company name"><input id="os-company" required value={company} onChange={(e) => setCompany(e.target.value)} /></Field>
        <Field id="os-phone" label="Phone" optional><input id="os-phone" value={phone} onChange={(e) => setPhone(e.target.value)} /></Field>
        <Terms accept={accept} setAccept={setAccept} />
        {error && <p className="login-error" role="alert">{error}</p>}
        <button className="login-submit" type="submit" disabled={!company.trim() || !accept}>Create account</button>
      </form>
    );
  }
  return (
    <div className="login-card" role="status">
      <h2>{error ? "Sign-in did not work" : "Signing you in…"}</h2>
      {error && <><p className="login-error" role="alert">{error}</p><button type="button" className="link" onClick={() => { clearUrl(); onBack(); }}>Back to sign in</button></>}
    </div>
  );
}
