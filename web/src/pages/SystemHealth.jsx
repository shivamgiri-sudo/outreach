import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import { istLabel } from "../time.js";

// Owner only: is everything running? Background jobs, sending, queue, backups, LinkedIn agent, database, version.
const JOB_LABEL = { autorun: "Background timer (every 15 min)", sequence_run: "Last sequence run", imap_poll: "Reply mailbox check (IMAP)",
  reminders: "Reminders (every minute)", flows: "Flow steps (every minute)", capture_sync: "Lead capture sync" };

function ago(seconds) {
  if (seconds == null) return "";
  if (seconds < 90) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}
const when = (iso, seconds) => (iso ? `${istLabel(iso)}${seconds != null ? ` (${ago(seconds)})` : ""}` : "never");

function Row({ label, children, tone }) {
  return <tr><td><b>{label}</b></td><td>{tone !== undefined && <span className={`pill ${tone}`} style={{ marginRight: 6 }}>{tone === "ok" ? "OK" : tone === "warn" ? "Check" : "—"}</span>}{children}</td></tr>;
}

export default function SystemHealth() {
  const [h, setH] = useState(null);
  const [error, setError] = useState("");
  const load = () => { setError(""); ops.health().then(setH).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, []);
  if (error) return <div className="tip error" role="alert">{error} <button className="btn ghost" onClick={load}>Try again</button></div>;
  if (!h) return <p className="empty">Loading…</p>;
  const s = h.sending;
  return (
    <div data-testid="system-health">
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div><h3 style={{ margin: 0 }}>System health</h3><p className="sub" style={{ margin: "4px 0 0" }}>Checked {istLabel(h.checked_at)}.</p></div>
        <button className="btn ghost" onClick={load}>Check again</button>
      </div>
      <div className={`tip ${h.ok ? "" : "error"}`} role="status" style={{ margin: "10px 0" }}>
        {h.ok ? "Everything looks fine." : <>Needs a look: {h.problems.join(" ")}</>}
      </div>
      <div className="card table-card" style={{ padding: 0 }}>
        <div className="table-scroll"><table>
          <tbody>
            {!h.autorun_enabled && <Row label="Background jobs">Off on this server (OUTREACH_AUTORUN is not 1): sends run only when someone presses Run.</Row>}
            {Object.entries(h.jobs).map(([job, j]) => (
              <Row key={job} label={JOB_LABEL[job] || job} tone={j.ok == null ? "" : j.ok && !j.stale ? "ok" : "warn"}>
                {when(j.at, j.age_seconds)}{j.summary ? ` · ${j.summary}` : ""}{j.failures ? ` · ${j.failures} failure(s) in a row` : ""}{j.stale ? " · late" : ""}
              </Row>
            ))}
            <Row label="Sending" tone={s.paused || s.provider_errors_24h ? "warn" : "ok"}>
              {s.live ? "Real sending is on" : "Practice mode"}{s.email_ready ? "" : " · no sending domain connected"}
              {s.changed ? ` · switched ${s.changed.on ? "on" : "off"} by ${s.changed.by}, ${istLabel(s.changed.at)}` : ""}
              {s.paused ? ` · paused: ${s.paused.reason}` : ""}
              {s.provider_errors_24h ? ` · ${s.provider_errors_24h} provider error(s) in 24 h, last ${istLabel(s.last_error_at)}: ${s.last_error}` : ""}
            </Row>
            <Row label="Send queue" tone={h.queue.in_flight || h.queue.send_failed ? "warn" : "ok"}>
              {h.queue.active_sequences} lead(s) in an active sequence · {h.queue.in_flight} interrupted send(s) · {h.queue.send_failed} stopped after failures · {h.queue.outbox_rows} emails in the outbox
            </Row>
            <Row label="Last reply mailbox poll">{h.imap.last_poll_at ? istLabel(h.imap.last_poll_at) : "never (or IMAP is not set up)"}</Row>
            <Row label="Backups" tone={h.backups.count ? "ok" : ""}>{h.backups.latest_at ? `${when(h.backups.latest_at, h.backups.age_seconds)} · ${h.backups.count} copies` : ""} {h.backups.note}</Row>
            <Row label="LinkedIn agent">
              {h.linkedin_agent.enabled ? "Switched on" : "Switched off"}{h.linkedin_agent.accounts.length === 0 ? " · no account has connected yet" : ""}
              {h.linkedin_agent.accounts.map((a) => (
                <div key={a.account}><small>{a.name || a.account}: {a.online ? "online" : "offline"}, last seen {a.last_seen ? istLabel(a.last_seen) : "never"}{a.paused ? " · paused" : ""}</small></div>
              ))}
            </Row>
            <Row label="Database">{h.store.kind}{h.store.secrets_encrypted ? " · saved keys encrypted" : " · saved keys not encrypted (set OUTREACH_SECRET_KEY)"}</Row>
            <Row label="Version">{h.version.commit || "unknown (set OUTREACH_GIT_COMMIT when deploying)"}</Row>
          </tbody>
        </table></div>
      </div>
    </div>
  );
}
