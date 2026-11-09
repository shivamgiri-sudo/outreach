import { useEffect, useState } from "react";
import { api } from "../api.js";
import { copyText } from "../clipboard.js";

// Two-step login for the signed-in person (Setup > My password), plus the pieces the sign-in page reuses when the
// owner requires it. The key is shown as text and as an otpauth:// link; no outside QR service ever sees it.

export function SecretPanel({ secret, uri }) {
  const [copied, setCopied] = useState("");
  const copy = async (what, text) => { await copyText(text); setCopied(what); setTimeout(() => setCopied(""), 1500); };
  const grouped = (secret || "").replace(/(.{4})/g, "$1 ").trim();
  return (
    <div className="two-step-secret" data-testid="two-step-secret">
      <ol className="steps" style={{ margin: "6px 0" }}>
        <li>Open an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, Authy or 1Password).</li>
        <li>Add an account, choose <b>Enter a setup key</b> and type this key (time based, 6 digits):</li>
      </ol>
      <p style={{ margin: "4px 0" }}><code data-testid="two-step-key" style={{ fontSize: 16, letterSpacing: 1, wordBreak: "break-all" }}>{grouped}</code>{" "}
        <button type="button" className="btn ghost" onClick={() => copy("key", secret)}>{copied === "key" ? "Copied" : "Copy key"}</button></p>
      <details><summary className="sub">Setup link (for password managers that take an otpauth:// link)</summary>
        <p style={{ margin: "4px 0" }}><code style={{ wordBreak: "break-all" }}>{uri}</code>{" "}
          <button type="button" className="btn ghost" onClick={() => copy("uri", uri)}>{copied === "uri" ? "Copied" : "Copy link"}</button></p>
      </details>
    </div>
  );
}

export function RecoveryCodes({ codes }) {
  if (!codes?.length) return null;
  return (
    <div className="tip" data-testid="recovery-codes" style={{ margin: "8px 0" }}>
      <b>Save these recovery codes now.</b> Each one signs you in once if you lose your phone. They are not shown again.
      <ul style={{ columns: 2, margin: "6px 0", fontFamily: "monospace" }}>{codes.map((c) => <li key={c}>{c}</li>)}</ul>
      <button type="button" className="btn ghost" onClick={() => copyText(codes.join("\n"))}>Copy codes</button>
    </div>
  );
}

export const codeInputProps = { inputMode: "numeric", autoComplete: "one-time-code", maxLength: 12, placeholder: "123456" };

export default function TwoStepCard() {
  const [st, setSt] = useState(null);
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [off, setOff] = useState({ password: "", code: "" });
  const [codes, setCodes] = useState(null);
  const [message, setMessage] = useState("");
  const load = () => api.twoStep().then(setSt).catch((e) => setMessage(e.message));
  useEffect(() => { load(); }, []);
  const run = (fn) => { setMessage(""); return fn().catch((e) => setMessage(e.message)); };
  if (!st) return <p className="sub">{message || "Loading…"}</p>;
  if (!st.available) {
    return (
      <div className="card" style={{ maxWidth: 560, marginTop: 16 }} data-testid="two-step-card">
        <b>Two-step login</b>
        <p className="sub">Not available for the server admin password sign-in: that password is the server's break-glass key.
          Keep it long and private, and use your own team login (with two-step login) day to day.</p>
      </div>
    );
  }
  return (
    <div className="card inline-form" style={{ maxWidth: 560, marginTop: 16 }} data-testid="two-step-card">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Two-step login</b><span className={`pill ${st.on ? "ok" : ""}`}>{st.on ? "On" : "Off"}</span>
      </div>
      <p className="sub" style={{ margin: "2px 0 8px" }}>After your password, the app also asks for a 6-digit code from your phone, so a stolen password alone cannot get in.
        {st.required && " The owner requires it for every login."}</p>
      <RecoveryCodes codes={codes} />
      {!st.on && !setup && <button className="btn" type="button" onClick={() => run(() => api.twoStepStart().then(setSetup))}>Set up two-step login</button>}
      {!st.on && setup && (
        <form onSubmit={(e) => { e.preventDefault(); run(() => api.twoStepConfirm(code).then((r) => { setCodes(r.recovery_codes); setSetup(null); setCode(""); setSt(r); })); }}>
          <SecretPanel secret={setup.secret} uri={setup.uri} />
          <label>3. Type the 6-digit code the app shows<input {...codeInputProps} required value={code} onChange={(e) => setCode(e.target.value)} /></label>
          <div className="row"><button className="btn" type="submit">Turn on</button><button className="btn ghost" type="button" onClick={() => setSetup(null)}>Cancel</button></div>
        </form>
      )}
      {st.on && (
        <>
          <p className="sub" style={{ margin: "4px 0" }}>{st.recovery_left} of 8 recovery codes left.</p>
          <form className="row" style={{ flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); run(() => api.twoStepRecovery(off.code).then((r) => { setCodes(r.recovery_codes); setOff({ ...off, code: "" }); load(); })); }}>
            <input aria-label="Code from your app" {...codeInputProps} value={off.code} onChange={(e) => setOff({ ...off, code: e.target.value })} style={{ width: 120 }} />
            <button className="btn ghost" type="submit">Make new recovery codes</button>
          </form>
          {!st.required && (
            <details style={{ marginTop: 8 }}><summary>Turn off two-step login</summary>
              <form onSubmit={(e) => { e.preventDefault(); run(() => api.twoStepOff(off.password, off.code).then((r) => { setSt(r); setOff({ password: "", code: "" }); setCodes(null); setMessage("Two-step login is off."); })); }}>
                <label>Your password<input type="password" autoComplete="current-password" value={off.password} onChange={(e) => setOff({ ...off, password: e.target.value })} /></label>
                <label>Code from your app (or a recovery code)<input {...codeInputProps} value={off.code} onChange={(e) => setOff({ ...off, code: e.target.value })} /></label>
                <button className="btn ghost danger" type="submit">Turn off</button>
              </form>
            </details>
          )}
        </>
      )}
      {message && <p className="sub" role="status">{message}</p>}
    </div>
  );
}
