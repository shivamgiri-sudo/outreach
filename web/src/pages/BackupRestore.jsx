import { useState } from "react";
import { api } from "../api.js";
import DownloadButton from "../DownloadButton.jsx";
import { useConfirm } from "../Dialog.jsx";

const NAMES = {
  leads: "Leads", outbox: "Emails sent or practised", inbox: "Replies received", tasks: "Tasks", activities: "Logged calls, meetings, notes",
  whatsapp_outbox: "WhatsApp messages", transactional_outbox: "Reminders & invites", campaigns: "Campaigns", suppressed: "Do-not-contact list",
  integrations: "Connections", settings: "Settings", templates: "Templates", stories: "Success stories", workflows: "Workflow rules",
  notifications: "Notifications", imports: "Import history", jobs: "Job posts", partners: "Partners", delivery_events: "Delivery events",
  provider_runs: "Data source comparisons",
};

function Summary({ plan }) {
  return (
    <>
      <p className="sub" style={{ margin: "10px 0 6px" }}>
        Backup made {plan.backup.created_at_ist || plan.backup.created_at}{plan.backup.app_commit ? ` (app version ${plan.backup.app_commit})` : ""}.
        {" "}Connection keys included: <b>{plan.backup.includes_secrets ? "yes" : "no"}</b>.
      </p>
      <div className="table-card">
        <table className="stack-table" data-testid="restore-summary">
          <thead><tr><th>What</th><th>Now</th><th>After the restore</th></tr></thead>
          <tbody>
            {plan.collections.filter((c) => c.now || c.after).map((c) => (
              <tr key={c.name}><td data-label="What">{NAMES[c.name] || c.name}</td><td data-label="Now">{c.now}</td><td data-label="After the restore">{c.after}</td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="sub" style={{ margin: "8px 0 4px" }}>{plan.will_replace} {plan.pre_restore_copy}</p>
      <ul className="steps">{plan.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </>
  );
}

export default function BackupRestore() {
  const [withSecrets, setWithSecrets] = useState(false);
  const [file, setFile] = useState(null);
  const [plan, setPlan] = useState(null);
  const [keepSecrets, setKeepSecrets] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(null);

  const check = async () => {
    setBusy("check"); setError(""); setPlan(null); setDone(null);
    try { setPlan(await api.restoreBackup(file)); } catch (e) { setError(e.message); }
    setBusy("");
  };
  const confirm = useConfirm();
  const restore = async () => {
    if (!(await confirm({ message: "Replace ALL data in this app with the backup? The current data is kept as a copy on the server, and real sending will be switched off.", confirmLabel: "Replace all data", danger: true }))) return;
    setBusy("restore"); setError("");
    try { setDone(await api.restoreBackup(file, { confirm: true, keepSecrets })); setPlan(null); } catch (e) { setError(e.message); }
    setBusy("");
  };

  return (
    <>
      <div className="card" style={{ marginBottom: 16 }}>
        <b>Download a backup</b>
        <p className="sub" style={{ margin: "2px 0 8px" }}>
          One file with everything: leads, messages, replies, tasks, campaigns, settings. Keep it to move to your own server
          or to go back to today's state. For spreadsheets, use the download buttons on each page instead.
        </p>
        <DownloadButton path="/api/backup?include_secrets=0" filename="outreach-backup.json.gz" label="Download backup" className="btn" testId="download-backup" />
        <p className="sub" style={{ margin: "6px 0 0" }}>Without connection keys: after a restore you enter them again on the Integrations page.</p>
        <label className="check-row">
          <input type="checkbox" checked={withSecrets} onChange={(e) => setWithSecrets(e.target.checked)} />
          <span>Also include connection keys, passwords and secrets</span>
        </label>
        {withSecrets && (
          <div className="tip error" role="alert" style={{ marginTop: 8 }}>
            <b>Careful: this file will hold your API keys, mailbox passwords, calendar tokens and the server secret in plain text.</b>{" "}
            Anyone who gets it can send email and WhatsApp as you and read your replies. Store it like a password (an encrypted
            drive or password manager). Never email it, chat it or put it in a shared folder. Delete it once you have moved.
            <div className="row" style={{ marginTop: 8 }}>
              <DownloadButton path="/api/backup?include_secrets=1" filename="outreach-backup-WITH-SECRETS.json.gz"
                label="Download backup with keys" className="btn danger-solid" testId="download-backup-secrets" />
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <b>Restore from a backup</b>
        <p className="sub" style={{ margin: "2px 0 8px" }}>
          Replaces everything in this app with the file. First you see what would change; nothing happens until you confirm.
        </p>
        <label htmlFor="restore-file">Backup file (.json.gz)</label>
        <input id="restore-file" type="file" accept=".gz,.json,application/gzip,application/json"
          onChange={(e) => { setFile(e.target.files[0] || null); setPlan(null); setDone(null); setError(""); }} />
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn ghost" disabled={!file || !!busy} onClick={check}>{busy === "check" ? "Checking…" : "Check this file"}</button>
        </div>
        {error && <div className="tip error" role="alert" style={{ marginTop: 10 }}>{error}</div>}
        {plan && (
          <div className="tip" role="status" style={{ marginTop: 10 }} data-testid="restore-plan">
            <b>{plan.message}</b>
            <Summary plan={plan} />
            {plan.backup.includes_secrets && (
              <label className="check-row">
                <input type="checkbox" checked={keepSecrets} onChange={(e) => setKeepSecrets(e.target.checked)} />
                <span>Also use the backup's server secret (only when moving this same install to a new server)</span>
              </label>
            )}
            <div className="row" style={{ marginTop: 8 }}>
              <button className="btn danger-solid" disabled={!!busy} onClick={restore}>{busy === "restore" ? "Restoring…" : "Replace everything with this backup"}</button>
              <button className="btn ghost" disabled={!!busy} onClick={() => setPlan(null)}>Cancel</button>
            </div>
          </div>
        )}
        {done && (
          <div className="tip" role="status" style={{ marginTop: 10 }} data-testid="restore-done">
            <b>Restored.</b> {done.message}
            {done.notes.slice(1).map((n) => <p key={n} className="sub" style={{ margin: "6px 0 0" }}>{n}</p>)}
          </div>
        )}
      </div>
    </>
  );
}
