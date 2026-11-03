import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";

export default function Outbox() {
  const [box, setBox] = useState([]);
  const [cfg, setCfg] = useState({ daily_cap: 20, sender_name: "Priya" });
  const [message, setMessage] = useState("");
  const [wa, setWa] = useState([]);
  const [loadError, setLoadError] = useState("");
  const load = () => {
    setLoadError("");
    const fail = (e) => setLoadError(e.message);
    api.outbox().then(setBox).catch(fail); api.settings().then(setCfg).catch(fail); api.whatsappOutbox().then(setWa).catch(fail);
  };
  useEffect(() => { load(); }, []);

  const run = async () => {
    try {
      const r = await api.runSequences();
      const extras = [
        r.failed && `${r.failed} failed (retried in an hour, at most 3 tries)`,
        r.blocked && `${r.blocked} waiting for a missing detail`,
        r.needs_check && `${r.needs_check} need a check (the app stopped mid-send)`,
      ].filter(Boolean).join(", ");
      setMessage(r.skipped ? `Nothing sent: ${r.skipped}.` : `${r.sent} email(s) queued${extras ? `, ${extras}` : ""}. Sends are spread out: a few per run, one per company a day, at least 2 days between steps.`);
    } catch (e) {
      setMessage(e.message);
    }
    load();
  };
  const save = async () => { setCfg(await api.saveSettings({ daily_cap: Number(cfg.daily_cap), sender_name: cfg.sender_name, live_sending: !!cfg.live_sending })); setMessage("Settings saved."); };

  return (
    <>
      <h2>Outbox & sending</h2>
      <p className="sub">Emails stay in practice mode (dry run) unless a sending domain is connected in Setup AND "Send for real" is ticked. Until both, nothing leaves this computer.</p>
      {loadError && <ErrorState message="The outbox could not load." detail={loadError} onRetry={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}
      <div className="card row" style={{ marginBottom: 14 }}>
        <div><label>Sender name</label><input value={cfg.sender_name} onChange={(e) => setCfg({ ...cfg, sender_name: e.target.value })} /></div>
        <div><label>Max emails per day</label><input type="number" min="1" value={cfg.daily_cap} onChange={(e) => setCfg({ ...cfg, daily_cap: e.target.value })} /></div>
        <label className="row" style={{ fontWeight: 400, margin: 0 }}>
          <input type="checkbox" style={{ width: "auto" }} checked={!!cfg.live_sending} onChange={(e) => setCfg({ ...cfg, live_sending: e.target.checked })} />
          Send for real
        </label>
        <button className="btn ghost" onClick={save}>Save</button>
        <button className="btn" onClick={run}>Run sequences now</button>
      </div>
      <p className="sub">Sends only Mon–Fri, 9am–6pm IST, one step per lead, and stops when a lead replies or unsubscribes.</p>
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {box.length === 0 ? <p className="empty">Nothing queued yet. Start a sequence from a lead, then click Run.</p> : (
          <table>
            <thead><tr><th>When</th><th>To</th><th>Step</th><th>Subject</th><th>Status</th></tr></thead>
            <tbody>{box.map((m, i) => (
              <tr key={i}><td>{m.sent_at.slice(0, 16).replace("T", " ")}</td><td>{m.to}</td><td>{m.step}</td>
                <td title={m.body}>{m.subject}</td><td><span className={`pill ${m.status === "sent" ? "ok" : "warn"}`} title={m.error || ""}>{m.status}</span></td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    <h3>WhatsApp messages (practice mode)</h3>
      <div className="card" style={{ padding: 0, overflowX: "auto" }}>
        {wa.length === 0 ? <p className="empty">No WhatsApp messages yet. Record a lead's yes, then send from the lead.</p> : (
          <table>
            <thead><tr><th>When</th><th>To</th><th>Template</th><th>Message</th><th>Status</th></tr></thead>
            <tbody>{wa.map((m, i) => (
              <tr key={i}><td>{m.sent_at.slice(0, 16).replace("T", " ")}</td><td>{m.to}</td><td>{m.template}</td><td>{m.body}</td>
                <td><span className="pill warn">{m.status}</span></td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}
