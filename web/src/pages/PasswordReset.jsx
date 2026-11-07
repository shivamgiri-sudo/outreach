import { useState } from "react";
import { api } from "../api.js";

// "Forgot password?" (send me a reset link) and the page the emailed link opens (#reset?token=...).
export function resetTokenFromHash() {
  const m = /^#\/?reset\?token=([A-Za-z0-9_-]{10,200})/.exec(window.location.hash || "");
  return m ? m[1] : "";
}

export function ForgotForm({ onBack, initialEmail = "" }) {
  const [email, setEmail] = useState(initialEmail);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const send = async (e) => {
    e.preventDefault();
    setBusy(true);
    try { setMsg((await api.forgotPassword(email.trim())).message); } catch (err) { setMsg(err.message); }
    setBusy(false);
  };
  return (
    <form className="login-card" onSubmit={send} aria-labelledby="forgot-heading" data-testid="forgot-form">
      <p className="login-eyebrow">Forgot your password?</p>
      <h2 id="forgot-heading">Get a reset link</h2>
      <p className="login-help">Enter the work email of your login. We email a link that lets you set a new password (it works for 30 minutes).</p>
      <div className="login-field">
        <label htmlFor="fe">Work email</label>
        <div className="login-input"><input id="fe" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@company.com" /></div>
      </div>
      {msg && <p className="login-note" role="status">{msg}</p>}
      <button className="login-submit" type="submit" disabled={busy || !email.trim()}>{busy ? "Sending…" : "Email me a reset link"}</button>
      <button type="button" className="link" onClick={onBack} style={{ marginTop: 10 }}>Back to sign in</button>
    </form>
  );
}

export function ResetForm({ token, onDone }) {
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [msg, setMsg] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const save = async (e) => {
    e.preventDefault();
    if (pw !== again) { setMsg("The two passwords are not the same."); return; }
    setBusy(true); setMsg("");
    try { await api.resetPassword(token, pw); setDone(true); } catch (err) { setMsg(err.message); }
    setBusy(false);
  };
  const back = () => { window.history.replaceState(null, "", window.location.pathname); onDone(); };
  if (done) return (
    <div className="login-card" data-testid="reset-done">
      <h2>Password changed</h2>
      <p className="login-help">Sign in with your new password. Any other places you were signed in have been signed out.</p>
      <button className="login-submit" onClick={back}>Go to sign in</button>
    </div>
  );
  return (
    <form className="login-card" onSubmit={save} aria-labelledby="reset-heading" data-testid="reset-form">
      <p className="login-eyebrow">Reset password</p>
      <h2 id="reset-heading">Choose a new password</h2>
      <p className="login-help">At least 10 characters.</p>
      <div className="login-field"><label htmlFor="np">New password</label>
        <div className="login-input"><input id="np" type="password" autoComplete="new-password" required minLength={10} value={pw} onChange={(e) => setPw(e.target.value)} /></div></div>
      <div className="login-field"><label htmlFor="np2">New password again</label>
        <div className="login-input"><input id="np2" type="password" autoComplete="new-password" required minLength={10} value={again} onChange={(e) => setAgain(e.target.value)} /></div></div>
      {msg && <p className="login-error" role="alert">{msg}</p>}
      <button className="login-submit" type="submit" disabled={busy || pw.length < 10}>{busy ? "Saving…" : "Set new password"}</button>
      <button type="button" className="link" onClick={back} style={{ marginTop: 10 }}>Back to sign in</button>
    </form>
  );
}
