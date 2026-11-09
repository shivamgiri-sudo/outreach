import { useEffect, useRef, useState } from "react";
import { api, setToken } from "../api.js";
import Icon from "../icons.jsx";
import { RecoveryCodes, SecretPanel, codeInputProps } from "./TwoStep.jsx";

// The second step of signing in: the code from the authenticator app (or a recovery code), or, when the owner
// requires two-step login and this login has none yet, setting it up right here. `step` = {two_step, ticket, name}.
export default function LoginTwoStep({ step, onSignedIn, onBack }) {
  const setupMode = step.two_step === "setup";
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState(null);
  const [codes, setCodes] = useState(null);
  const input = useRef(null);
  const fail = (err) => {
    setError(err.message === "Failed to fetch" ? "The app server is not answering. Check your connection and try again." : err.message);
    setBusy(false);
    input.current?.focus();
    input.current?.select();
  };
  useEffect(() => { if (setupMode) api.loginSetupStart(step.ticket).then(setSecret).catch(fail); }, []);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (!code.trim()) { setError("Enter the code from your authenticator app."); input.current?.focus(); return; }
    setError("");
    setBusy(true);
    try {
      if (setupMode) {
        const r = await api.loginSetupConfirm(step.ticket, code.trim());
        setToken(r.token);
        setCodes(r.recovery_codes);
        setBusy(false);
      } else {
        const r = await api.loginCode(step.ticket, code.trim());
        setToken(r.token);
        onSignedIn();
      }
    } catch (err) { fail(err); }
  };

  if (codes) {
    return (
      <div className="login-card" data-testid="login-recovery">
        <p className="login-eyebrow">Two-step login is on</p>
        <h2>Keep your recovery codes</h2>
        <RecoveryCodes codes={codes} />
        <button className="login-submit" type="button" onClick={onSignedIn}>I saved them, continue<Icon name="arrowRight" size={18} /></button>
      </div>
    );
  }
  return (
    <form className="login-card" onSubmit={submit} aria-labelledby="two-step-heading" noValidate data-testid="login-two-step">
      <p className="login-eyebrow">{step.name ? `Hello, ${step.name}` : "One more step"}</p>
      <h2 id="two-step-heading">{setupMode ? "Set up two-step login" : "Enter your code"}</h2>
      <p className="login-help">{setupMode ? "Your login needs a code from your phone after the password (required for owners and super admins, or set by the owner). Set it up once, now."
        : "Open your authenticator app and type the 6-digit code. Lost your phone? Type one of your recovery codes."}</p>
      {setupMode && (secret ? <SecretPanel secret={secret.secret} uri={secret.uri} /> : !error && <p className="login-help">Making your key…</p>)}
      <div className="login-field">
        <label htmlFor="otp">{setupMode ? "Code the app shows" : "Code"}</label>
        <div className="login-input">
          <Icon name="lock" size={18} />
          <input id="otp" ref={input} {...codeInputProps} autoFocus={!setupMode} value={code} onChange={(e) => setCode(e.target.value)}
            aria-invalid={error ? true : undefined} aria-describedby={error ? "two-step-error" : undefined} />
        </div>
      </div>
      <div id="two-step-error" className="login-error" role="alert">
        {error && <><Icon name="alert" size={18} /><span>{error}</span></>}
      </div>
      <button className="login-submit" type="submit" disabled={busy || (setupMode && !secret)} aria-busy={busy || undefined}>
        {busy ? <><span className="login-spinner" aria-hidden="true" />Checking…</> : <>{setupMode ? "Turn on and sign in" : "Sign in"}<Icon name="arrowRight" size={18} /></>}
      </button>
      <button type="button" className="link" onClick={onBack} style={{ marginTop: 8 }}>Back to email and password</button>
    </form>
  );
}
