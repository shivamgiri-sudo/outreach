import { useEffect, useState } from "react";
import { api } from "../../api.js";
import DownloadButton from "../../DownloadButton.jsx";
import { istLabel } from "../../time.js";
import { Button, Card, CardHeader, Field, Select } from "../../ui";
import { LEAD_SYNC, linkName } from "./CallTrackerLinks.jsx";

// Integrations → DialHalo → Integration kit, for ONE linked DialHalo workspace (picker when there are several):
// "send now" in bulk, people matching, calling by person (+ CSV) and the check for missed calls. Automatic sending is
// each link's "Lead sending" setting; a lead lives in one DialHalo workspace, so leads already in another one are skipped.
const day = (d) => d.toISOString().slice(0, 10);

export default function CallTrackerKit({ ws, links, onRefresh }) {
  const usable = links.filter((l) => l.enabled !== false && l.api_key_set);
  const [linkId, setLinkId] = useState(() => (usable.find((l) => l.primary) || usable[0] || {}).id || "");
  const link = usable.find((l) => l.id === linkId) || usable.find((l) => l.primary) || usable[0];
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [people, setPeople] = useState(null);
  const [report, setReport] = useState(null);
  const [range, setRange] = useState(() => ({ from: day(new Date(Date.now() - 29 * 86400000)), to: day(new Date()) }));
  const id = link?.id || "";

  const safe = async (fn, ok) => {
    setBusy(true);
    try { const out = await fn(); setMsg({ ok: true, text: ok(out) }); return out; }
    catch (e) { setMsg({ ok: false, text: e.message }); return null; }
    finally { setBusy(false); }
  };
  const loadPeople = () => api.calltrackerPeople(ws, id).then(setPeople).catch((e) => setMsg({ ok: false, text: e.message }));
  const loadReport = () => api.calltrackerPeopleReport(ws, range.from, range.to, id).then(setReport).catch((e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { setPeople(null); setReport(null); setMsg(null); if (id) { loadPeople(); loadReport(); } }, [ws, id]);
  if (!link) return null;

  const name = linkName(link);
  const off = link.lead_sync === "off";
  const sendUnsent = () => safe(() => api.calltrackerSend({ workspace: ws, link: id, unsent_only: true }),
    (o) => o.message || `Sent ${o.sent} to ${name} (${o.created} new, ${o.updated} updated). ${o.errors.length} refused, ${o.skipped.length} skipped (no phone, or already in another DialHalo workspace). ${o.owner_unmatched.length} have an owner with no member there.`)
    .then(() => onRefresh && onRefresh());
  const reconcile = () => safe(() => api.calltrackerReconcile(ws, id), (o) => `Checked ${name}: ${o.matched} calls on your leads, ${o.applied} added or updated.`)
    .then(() => { loadReport(); onRefresh && onRefresh(); });

  return (
    <Card data-testid="ct-kit">
      <CardHeader title="Integration kit"
        description="Send many leads at once, see who is matched, and track calling per person. Opted-out leads are never sent; a lead already in another DialHalo workspace stays there." />
      {usable.length > 1 && (
        <Field label="DialHalo workspace">
          <Select value={id} onChange={(e) => setLinkId(e.target.value)} data-testid="ct-kit-link">
            {usable.map((l) => <option key={l.id} value={l.id}>{linkName(l)}{l.primary ? " ★" : ""}</option>)}
          </Select>
        </Field>
      )}
      {msg && <div className={`tip ${msg.ok ? "" : "error"}`} role="status" data-testid="ct-kit-msg">{msg.text}</div>}
      <div className="row prov-actions" style={{ flexWrap: "wrap", gap: 8 }}>
        <Button disabled={busy || off} onClick={sendUnsent} data-testid="ct-send-unsent">Send all leads not yet in DialHalo to {name}</Button>
        <Button variant="secondary" disabled={busy} onClick={reconcile} data-testid="ct-reconcile">Check {name} for missed calls</Button>
      </div>
      <p className="sub">
        Automatic sending for {name}: <b>{LEAD_SYNC[link.lead_sync] || link.lead_sync}</b> (change it under "Lead sending" on its card above).
        {off && " Lead sending is off, so Send is switched off too."}
      </p>
      <p className="sub">Last check for missed calls: {link.last_reconcile ? istLabel(link.last_reconcile) : "not yet (runs every night)"}
        {link.last_reconcile_status && !link.last_reconcile_status.ok ? ` (last try failed: ${link.last_reconcile_status.detail})` : ""}</p>

      {people && (
        <div data-testid="ct-people">
          <b>People matching</b>
          <p className="sub">{people.matched} of {people.people.length} Outreach people have a member in {name} with the same email.{people.only_in_dialhalo.length > 0 && ` ${people.only_in_dialhalo.length} member(s) there are not Outreach users: ${people.only_in_dialhalo.slice(0, 5).join(", ")}.`}</p>
          {people.warnings.length > 0 && (
            <div className="tip error" role="alert" data-testid="ct-people-warn">
              {people.warnings.map((w) => <div key={w.email}><b>{w.name}</b> owns {w.leads} lead{w.leads === 1 ? "" : "s"} but {w.reason || "has no member there"}: their leads reach DialHalo's distribution, not their own phone.</div>)}
            </div>
          )}
        </div>
      )}

      <div data-testid="ct-report">
        <b>Calling by person</b>
        <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
          <label>From <input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></label>
          <label>To <input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></label>
          <Button variant="secondary" onClick={loadReport}>Show</Button>
          <DownloadButton path={`/api/calltracker/people-report?workspace=${encodeURIComponent(ws)}&link=${encodeURIComponent(id)}&from=${range.from}&to=${range.to}&format=csv`}
            filename="calling-by-person.csv" label="Download CSV" testId="ct-report-csv" />
        </div>
        {report && (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Person</th><th>Calls on leads</th><th>Connected</th><th>Interested</th><th>Meetings</th><th>Talk (min)</th><th>Leads called</th><th>All calls in {name}</th></tr></thead>
              <tbody>
                {report.rows.length === 0 && <tr><td colSpan={8}>No calls in this period.</td></tr>}
                {report.rows.map((r) => (
                  <tr key={r.email}><td>{r.name}<div className="sub-inline">{r.email}</div></td><td>{r.calls}</td><td>{r.connected}</td><td>{r.interested}</td><td>{r.meetings}</td>
                    <td>{r.talk_minutes}</td><td>{r.leads_called}</td><td>{r.dialhalo_calls ?? "—"}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}
