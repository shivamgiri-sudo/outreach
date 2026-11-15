// The only page a pending (self sign-up, not yet approved) account sees, and the banners every login sees:
// "Stop all sending" is on, and a super admin's read-only "View as" session. Server: app/routes_signup.py.
import { useEffect, useState } from "react";
import { platform, stopViewAs } from "../platformApi.js";
import OwnPassword from "./setup/OwnPassword.jsx";
import TwoStepCard from "./TwoStep.jsx";

export function PendingApproval({ me, onSignOut }) {
  const [info, setInfo] = useState(null);
  useEffect(() => { platform.pending().then(setInfo).catch(() => setInfo({})); }, []);
  return (
    <main className="pending-page" data-testid="pending-page">
      <h2>Waiting for approval</h2>
      <p className="sub">Thanks{me?.name ? `, ${me.name}` : ""}. The account for <b>{info?.account || "your company"}</b> was created and our team is
        reviewing it. You will get an email the moment it is approved; until then nothing can be imported or sent.</p>
      <section className="card u-mt-16">
        <h3>What happens next</h3>
        <ol>{(info?.steps || []).map((s) => <li key={s.label}>{s.done ? "✓ " : "… "}{s.label}</li>)}</ol>
        {info?.submitted_at && <p className="hint">Submitted {new Date(info.submitted_at).toLocaleString()}</p>}
      </section>
      {(info?.questions || []).length > 0 && (
        <section className="card u-mt-16" data-testid="pending-questions">
          <h3>We need a few details</h3>
          {info.questions.map((q) => <p key={q.at}>{q.message} <span className="hint">— reply to our email with the answer.</span></p>)}
        </section>
      )}
      <section className="setup-stack u-mt-16"><OwnPassword /><TwoStepCard /></section>
      <p className="u-mt-16"><button className="btn ghost" onClick={onSignOut}>Sign out</button></p>
    </main>
  );
}

export function PlatformBanners({ status }) {
  if (!status) return null;
  const va = status.view_as;
  const stop = status.stop_all_sending;
  const minutes = va ? Math.max(0, Math.round((va.until * 1000 - Date.now()) / 60000)) : 0;
  return (
    <>
      {va && (
        <div className="tip banner banner-view" role="alert" data-testid="view-as-banner">
          Viewing as {va.name || va.as} (read-only) — {minutes} min left.{" "}
          <button className="btn small" onClick={stopViewAs} data-testid="view-as-stop">Stop</button>
        </div>
      )}
      {stop && (
        <div className="tip banner banner-stop" role="alert" data-testid="stop-all-banner">
          All sending is stopped by the platform admin{stop.reason ? `: ${stop.reason}` : ""}. Nothing is emailed, sent on WhatsApp or LinkedIn, and flows wait.
        </div>
      )}
    </>
  );
}

export function usePlatformStatus(signedIn) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    if (!signedIn) { setStatus(null); return undefined; }
    const load = () => platform.status().then(setStatus).catch(() => {});
    load();
    const timer = setInterval(load, 60000);
    window.addEventListener("platform-changed", load);
    return () => { clearInterval(timer); window.removeEventListener("platform-changed", load); };
  }, [signedIn]);
  return status;
}
