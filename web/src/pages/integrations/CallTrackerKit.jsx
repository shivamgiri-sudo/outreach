import { useEffect, useState } from "react";
import { api } from "../../api.js";
import DownloadButton from "../../DownloadButton.jsx";
import { istLabel } from "../../time.js";

// Integrations → DialHalo → Integration Kit: send leads in bulk, who is matched, the calling report per person, and the nightly reconcile.
const day = (d) => d.toISOString().slice(0, 10);

export default function CallTrackerKit({ ws, conn, onSaved }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [people, setPeople] = useState(null);
  const [report, setReport] = useState(null);
  const [range, setRange] = useState(() => ({ from: day(new Date(Date.now() - 29 * 86400000)), to: day(new Date()) }));

  const safe = async (fn, ok) => {
    setBusy(true);
    try { const out = await fn(); setMsg({ ok: true, text: ok(out) }); return out; }
    catch (e) { setMsg({ ok: false, text: e.message }); return null; }
    finally { setBusy(false); }
  };
  const loadPeople = () => api.calltrackerPeople(ws).then(setPeople).catch((e) => setMsg({ ok: false, text: e.message }));
  const loadReport = () => api.calltrackerPeopleReport(ws, range.from, range.to).then(setReport).catch((e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { setPeople(null); setReport(null); loadPeople(); loadReport(); }, [ws]);

  const sendUnsent = () => safe(() => api.calltrackerSend({ workspace: ws, unsent_only: true }),
    (o) => o.message || `Sent ${o.sent} (${o.created} new, ${o.updated} updated). ${o.errors.length} refused, ${o.skipped.length} skipped (no phone). ${o.owner_unmatched.length} have an owner with no DialHalo member.`);
  const toggleAuto = () => api.saveCalltracker({ workspace: ws, auto_send: !conn.auto_send }).then((c) => onSaved(c.card || c)).catch((e) => setMsg({ ok: false, text: e.message }));
  const reconcile = () => safe(() => api.calltrackerReconcile(ws), (o) => `Checked DialHalo: ${o.matched} calls on your leads, ${o.applied} added or updated.`).then(() => loadReport());

  return (
    <div className="card" data-testid="ct-kit">
      <b>Integration kit</b>
      <p className="sub">Send many leads at once, see who is matched in DialHalo, and track calling per person. Opted-out leads are never sent.</p>
      {msg && <div className={`tip ${msg.ok ? "" : "error"}`} role="status" data-testid="ct-kit-msg">{msg.text}</div>}
      <div className="row prov-actions" style={{ flexWrap: "wrap", gap: 8 }}>
        <button className="btn" disabled={busy} onClick={sendUnsent} data-testid="ct-send-unsent">Send all leads not yet in DialHalo</button>
        <button className="btn ghost" disabled={busy} onClick={reconcile} data-testid="ct-reconcile">Check DialHalo for missed calls</button>
        <label className="row"><input type="checkbox" checked={Boolean(conn.auto_send)} onChange={toggleAuto} data-testid="ct-auto-send" /> Send leads automatically when they belong to someone who dials in DialHalo</label>
      </div>
      <p className="sub">Last check for missed calls: {conn.last_reconcile ? istLabel(conn.last_reconcile) : "not yet (runs every night)"}</p>

      {people && (
        <div data-testid="ct-people">
          <b>People matching</b>
          <p className="sub">{people.matched} of {people.people.length} Outreach people have a DialHalo member with the same email.{people.only_in_dialhalo.length > 0 && ` ${people.only_in_dialhalo.length} DialHalo member(s) are not Outreach users: ${people.only_in_dialhalo.slice(0, 5).join(", ")}.`}</p>
          {people.warnings.length > 0 && (
            <div className="tip error" role="alert" data-testid="ct-people-warn">
              {people.warnings.map((w) => <div key={w.email}><b>{w.name}</b> owns {w.leads} lead{w.leads === 1 ? "" : "s"} but {w.reason || "has no DialHalo member"}: their leads reach DialHalo's round robin, not their own phone.</div>)}
            </div>
          )}
        </div>
      )}

      <div data-testid="ct-report">
        <b>Calling by person</b>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <label>From <input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></label>
          <label>To <input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></label>
          <button className="btn ghost" onClick={loadReport}>Show</button>
          <DownloadButton path={`/api/calltracker/people-report?workspace=${encodeURIComponent(ws)}&from=${range.from}&to=${range.to}&format=csv`} filename="calling-by-person.csv" label="Download CSV" testId="ct-report-csv" />
        </div>
        {report && (
          <table>
            <thead><tr><th>Person</th><th>Calls on leads</th><th>Connected</th><th>Interested</th><th>Meetings</th><th>Talk (min)</th><th>Leads called</th><th>All calls in DialHalo</th></tr></thead>
            <tbody>
              {report.rows.length === 0 && <tr><td colSpan={8}>No calls in this period.</td></tr>}
              {report.rows.map((r) => (
                <tr key={r.email}><td>{r.name}<div className="sub-inline">{r.email}</div></td><td>{r.calls}</td><td>{r.connected}</td><td>{r.interested}</td><td>{r.meetings}</td>
                  <td>{r.talk_minutes}</td><td>{r.leads_called}</td><td>{r.dialhalo_calls ?? "—"}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
