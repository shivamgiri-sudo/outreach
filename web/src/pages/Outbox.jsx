import { useEffect, useState } from "react";
import { istLabel } from "../time.js";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";
import { ErrorState } from "../states.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import LiveSendingSwitch from "./LiveSendingSwitch.jsx";
import EmailGateCard from "./EmailGateCard.jsx";
import DeliverabilityCard from "./DeliverabilityCard.jsx";
import OutboxTable from "./OutboxTable.jsx";
import { InternalEmails } from "./SendWhy.jsx";
import HelpLink from "../components/HelpLink.jsx";
import Abbr from "../components/Abbr.jsx";

function Downloads() {
  return (
    <div className="card dl-card" style={{ marginBottom: 14 }}>
      <b>Download</b>
      <span className="dl-group">
        <DownloadButton path={exportPath("outbox")} label="Emails" title="Every email: when, to whom, step, subject, status, delivered" testId="download-outbox" />
        <DownloadButton path={exportPath("outbox", { include_body: 1 })} label="Emails with full text" testId="download-outbox-body" />
        <DownloadButton path={exportPath("inbox")} label="Replies" title="Replies received, how each was read and which lead it matched" testId="download-inbox" />
        <DownloadButton path={exportPath("whatsapp")} label="WhatsApp" testId="download-whatsapp" />
        <DownloadButton path={exportPath("transactional")} label="Reminders & invites" title="Reminder and invite emails sent to the team and leads" testId="download-transactional" />
      </span>
    </div>
  );
}

const STATUS_LABEL = { dry_run: "practice", provider_error: "provider problem" };

// Who sent a WhatsApp: the person, "System" for flows, or "Before tracking" for rows older than 9 Nov 2026.
function sentBy(m) {
  const o = m.origin;
  if (o?.type === "manual") return o.actor?.name || m.by || "A person";
  if (o?.type === "automatic") return `System${o.rule?.flow_name ? ` (flow ${o.rule.flow_name})` : ""}`;
  return m.by ? `${m.by} (before tracking)` : "Before tracking";
}

function SendingBanner({ status, onResume, onClearPractice }) {
  if (!status) return null;
  const pct = (x) => `${(x * 100).toFixed(1)}%`;
  return (
    <div className="card" style={{ marginBottom: 14 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{status.mode === "live" ? "Real sending is ON" : "Practice mode: nothing leaves the app"}</b>
        <span className={`pill ${status.mode === "live" ? "ok" : ""}`}>{status.mode === "live" ? "Live" : "Practice"}</span>
      </div>
      {status.paused && (
        <div className="tip" role="alert" style={{ marginTop: 8 }}>
          <b>Sending is paused.</b> {status.paused.reason}{" "}
          {status.paused.resume_at ? `It restarts by itself at ${istLabel(status.paused.resume_at)}.` : "A person must resume it."}
          {isOwner() && <div className="row" style={{ marginTop: 6 }}><button className="btn" onClick={onResume}>Resume sending now</button></div>}
        </div>
      )}
      {status.mode !== "live" && status.live_blockers?.length > 0 && (
        <>
          <p className="sub" style={{ margin: "8px 0 4px" }}>Before you can switch on real sending:</p>
          <ol className="steps">{status.live_blockers.map((b) => <li key={b}>{b}</li>)}</ol>
        </>
      )}
      <p className="sub" style={{ margin: "6px 0 0" }}>
        Last 7 days: {status.rates.sent} sent · {status.rates.bounces} bounced ({pct(status.rates.bounce_rate)}) · {status.rates.complaints} spam
        complaints. Sending pauses by itself above 2% bounces or 0.3% complaints.
      </p>
      {status.mode !== "live" && isOwner() && (
        <button className="btn ghost" style={{ marginTop: 8 }} onClick={onClearPractice}>Clear practice runs</button>
      )}
    </div>
  );
}

export default function Outbox({ go }) {
  const [refresh, setRefresh] = useState(0);
  const [cfg, setCfg] = useState({ daily_cap: 20, sender_name: "" });
  const [status, setStatus] = useState(null);
  const [message, setMessage] = useState("");
  const [wa, setWa] = useState([]);
  const [loadError, setLoadError] = useState("");
  const [tab, setTab] = useState("emails");
  const seesInternal = isOwner() || isWorkspaceAdmin();
  const load = () => {
    setLoadError("");
    const fail = (e) => setLoadError(e.message);
    setRefresh((n) => n + 1); api.settings().then(setCfg).catch(fail); api.whatsappOutbox().then(setWa).catch(fail);
    api.sendingStatus().then(setStatus).catch(fail);
  };
  useEffect(() => { load(); }, []);

  const act = async (fn, done) => {
    try { const r = await fn(); setMessage(typeof done === "function" ? done(r) : done); }
    catch (e) { setMessage(e.message); }
    load();
  };

  const run = () => act(api.runSequences, (r) => {
    const extras = [
      r.failed && `${r.failed} failed`,
      r.paused && `sending paused: ${r.paused}`,
      r.blocked && `${r.blocked} waiting for a missing detail`,
      r.held && `${r.held} waiting for an email check`,
      r.needs_check && `${r.needs_check} need a check (the app stopped mid-send)`,
    ].filter(Boolean).join(", ");
    const what = r.mode === "practice" ? "practised (not sent)" : "sent";
    return r.skipped ? `Nothing sent: ${r.skipped}.` : `${r.sent} email(s) ${what}${extras ? `, ${extras}` : ""}. Sends are spread out: a few per run, one per company a day, follow-ups 3, 4 and 7 days apart.`;
  });
  const save = () => act(async () => setCfg(await api.saveSettings({
    daily_cap: Number(cfg.daily_cap), sender_name: cfg.sender_name,
    sender_company: cfg.sender_company || "", sender_address: cfg.sender_address || "",
    sender_website: cfg.sender_website || "", sender_contact_email: cfg.sender_contact_email || "",
  })), "Settings saved.");

  return (
    <>
      <h2>Outbox & sending <HelpLink chapter="outbox" label="Outbox and sending" /></h2>
      <p className="sub">Emails stay in practice mode until a sending domain is connected AND "Send for real" is switched on. Practice runs never use up a lead's real first email.</p>
      {loadError && <ErrorState message="The outbox could not load." detail={loadError} onRetry={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}
      <SendingBanner status={status} onResume={() => act(api.resumeSending, "Sending resumed.")}
        onClearPractice={() => act(api.clearPractice, (r) => `Removed ${r.outbox} practice row(s). Live sending will start each lead from Email 1.`)} />
      <LiveSendingSwitch onChanged={load} />
      <EmailGateCard />
      <DeliverabilityCard />
      <Downloads />
      <div className="card" style={{ marginBottom: 14 }}><fieldset className="ro-fieldset" disabled={!isOwner()}>
        <div className="row">
          <div><label>Sender name</label><input value={cfg.sender_name || ""} onChange={(e) => setCfg({ ...cfg, sender_name: e.target.value })} /></div>
          <div><label>Max emails per day</label><input type="number" min="1" max="500" value={cfg.daily_cap} onChange={(e) => setCfg({ ...cfg, daily_cap: e.target.value })} /></div>
        </div>
        <label>Company name (shown in every email footer)</label>
        <input value={cfg.sender_company || ""} placeholder="Your Company" onChange={(e) => setCfg({ ...cfg, sender_company: e.target.value })} />
        <div className="row">
          <div><label>Website (shown in the footer)</label>
            <input value={cfg.sender_website || ""} placeholder="yourcompany.com" onChange={(e) => setCfg({ ...cfg, sender_website: e.target.value })} /></div>
          <div><label>Contact email (shown in the footer)</label>
            <input value={cfg.sender_contact_email || ""} placeholder="hello@yourcompany.com" onChange={(e) => setCfg({ ...cfg, sender_contact_email: e.target.value })} /></div>
        </div>
        <label>Postal address (optional; needed only to email leads in the US or Canada)</label>
        <input value={cfg.sender_address || ""} placeholder="Office address, city, PIN" onChange={(e) => setCfg({ ...cfg, sender_address: e.target.value })} />
        {isOwner() ? (
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn ghost" onClick={save}>Save settings</button>
            <button className="btn" onClick={run}>Run sequences now</button>
          </div>
        ) : <p className="sub" style={{ marginTop: 10 }}>Only an owner changes these settings or runs the sequences by hand.</p>}
      </fieldset></div>
      {status && (status.send_failed.length > 0 || status.stuck.length > 0) && (
        <div className="card" style={{ marginBottom: 14 }}>
          {status.send_failed.length > 0 && (
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span>{status.send_failed.length} lead(s) stopped after 3 failed sends.</span>
              {isOwner() && <button className="btn ghost" onClick={() => act(api.resumeFailed, (r) => `Resumed ${r.resumed} sequence(s).`)}>Resume them</button>}
            </div>
          )}
          {status.stuck.length > 0 && (
            <p className="sub" style={{ margin: "8px 0 0" }}>
              {status.stuck.length} send(s) were interrupted. Check your email provider's log, then open the lead and clear it; the retry can't
              send the same email twice.
            </p>
          )}
        </div>
      )}
      <p className="sub">Sends only Mon–Fri, 9am–6pm <Abbr term="IST">IST</Abbr>, one step per lead. Any reply stops the sequence for that lead.</p>
      {seesInternal && (
        <div className="tabs" role="tablist" aria-label="Outbox lists" style={{ marginBottom: 10 }}>
          <button role="tab" aria-selected={tab === "emails"} className={tab === "emails" ? "on" : ""} onClick={() => setTab("emails")}>Emails to leads</button>
          <button role="tab" aria-selected={tab === "internal"} className={tab === "internal" ? "on" : ""} data-testid="tab-internal" onClick={() => setTab("internal")}>Internal emails</button>
        </div>
      )}
      {tab === "internal" && seesInternal ? <InternalEmails /> : <OutboxTable refreshKey={refresh} />}
      <div className="card" style={{ margin: "14px 0" }}>
        <b>Replies received</b> <span className="sub-inline">are on the Replies page, with how each was read and which lead it matched.</span>{" "}
        <button className="btn ghost" onClick={() => (go ? go("replies") : (window.location.hash = "replies"))}>Open Replies</button>
      </div>
      <h3>WhatsApp messages</h3>
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {wa.length === 0 ? <p className="empty">No WhatsApp messages yet. Record a lead's yes, then send from the lead.</p> : (
          <table>
            <thead><tr><th>When</th><th>To</th><th>Template</th><th>Message</th><th>Sent by</th><th>Status</th></tr></thead>
            <tbody>{wa.map((m, i) => (
              <tr key={i}><td>{istLabel(m.sent_at)}</td><td>{m.to}</td><td>{m.template}{m.ai_drafted && <><br /><span className="pill" title={`Written by the AI; approved and sent by ${m.ai_approved_by || "a person"}`}>AI-drafted</span></>}</td><td>{m.body}</td><td data-testid="wa-sent-by">{sentBy(m)}</td>
                <td><span className={`pill ${m.status === "sent" ? "ok" : ""}`}>{STATUS_LABEL[m.status] || m.status}</span></td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}
