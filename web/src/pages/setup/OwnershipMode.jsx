// Setup > Assignment: the ownership-mode switch with its migration report and the "Fix owners" button.
// Turning it on while leads have no owner asks for an explicit acknowledgement first (the server enforces it too).
import { useEffect, useId, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../../api.js";

export default function OwnershipMode() {
  if (!isOwner() && !isWorkspaceAdmin()) return null;
  return <OwnershipModeBody />;
}

function OwnershipModeBody() {
  const [mode, setMode] = useState(null);       // { on, unowned }
  const [report, setReport] = useState(null);
  const [preview, setPreview] = useState(null);
  const [ack, setAck] = useState(false);
  const [picked, setPicked] = useState([]);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const id = useId();
  const load = () => {
    api.ownershipMode().then(setMode).catch((e) => setMsg({ text: e.message, error: true }));
    api.ownershipReport().then((r) => { setReport(r); setPicked([]); }).catch(() => {});
    api.ownershipPreview().then(setPreview).catch(() => setPreview(null));
  };
  useEffect(load, []);
  if (!mode) return <div className="card setup-card">{msg ? <div className="tip error" role="alert">{msg.text}</div> : "Loading…"}</div>;
  const needAck = !mode.on && mode.unowned > 0;
  const flip = async () => {
    setBusy(true); setMsg(null);
    try { await api.setOwnershipMode(!mode.on, ack); setAck(false); setMsg({ text: mode.on ? "Ownership mode is off." : "Ownership mode is on. People now see and send only their own leads." }); load(); } catch (e) { setMsg({ text: e.message, error: true }); }
    setBusy(false);
  };
  const fix = async (ids) => {
    setBusy(true); setMsg(null);
    try { await api.fixOwners(ids); setMsg({ text: `Owners fixed for ${ids.length} lead(s).` }); load(); } catch (e) { setMsg({ text: e.message, error: true }); }
    setBusy(false);
  };
  const mism = report?.mismatches || [];
  const allPicked = mism.length > 0 && picked.length === mism.length;
  return (
    <section className="card setup-card" aria-labelledby={`${id}-h`} data-testid="ownership-mode">
      <h3 id={`${id}-h`} style={{ marginTop: 0 }}>Ownership mode</h3>
      <p className="sub">When on, a person sees and sends only the leads they own, plus lists assigned or shared with them. Admins see everything. Review the report below before turning it on.</p>
      <p><span className={`pill ${mode.on ? "ok" : ""}`} data-testid="ownership-state">{mode.on ? "On" : "Off"}</span> <small className="sub">{mode.unowned} lead(s) have no owner.</small></p>
      {report && (
        <ul className="sub" data-testid="ownership-report" style={{ paddingLeft: 18 }}>
          <li>{report.lists_assigned} list(s) assigned to a person</li>
          <li>{report.lists_pool} list(s) in the admin pool</li>
          <li>{report.unowned} lead(s) with no owner</li>
          <li>{mism.length} lead(s) whose owner differs from their list's assignee</li>
        </ul>
      )}
      {preview && preview.members.length > 0 && (
        <div data-testid="ownership-preview">
          <h4 style={{ marginBottom: 4 }}>{mode.on ? "What each person sees now" : "What each person would see once it is on"}</h4>
          <div className="table-scroll"><table>
            <thead><tr><th>Person</th><th>Role</th><th style={{ textAlign: "right" }}>Leads</th><th style={{ textAlign: "right" }}>Lists</th><th>Sees</th></tr></thead>
            <tbody>{preview.members.map((m) => (
              <tr key={m.email} data-testid="ownership-preview-row">
                <td><b>{m.name}</b><br /><small>{m.email}</small></td><td>{m.role}</td>
                <td style={{ textAlign: "right" }}>{m.leads}</td><td style={{ textAlign: "right" }}>{m.lists}</td><td><small>{m.sees}</small></td>
              </tr>))}
            </tbody>
          </table></div>
          {preview.unowned_note && <p className="sub" data-testid="ownership-preview-note">{preview.unowned_note}</p>}
        </div>
      )}
      {needAck && (
        <label className="row tip" style={{ gap: 8 }}>
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="ownership-ack" />
          I understand {mode.unowned} lead(s) have no owner and only admins will see them until they are assigned.
        </label>
      )}
      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn" disabled={busy || (needAck && !ack)} onClick={flip} data-testid="ownership-toggle">{mode.on ? "Turn ownership mode off" : "Turn ownership mode on"}</button>
      </div>
      {msg && <div className={`tip${msg.error ? " error" : ""}`} role={msg.error ? "alert" : "status"} style={{ marginTop: 10 }}>{msg.text}</div>}
      {mism.length > 0 && (
        <>
          <h4>Owner differs from list</h4>
          <div className="table-scroll"><table data-testid="ownership-mismatches">
            <thead><tr>
              <th><input type="checkbox" aria-label="Select all mismatches" checked={allPicked} onChange={() => setPicked(allPicked ? [] : mism.map((m) => m.lead_id))} /></th>
              <th>Lead</th><th>Owner now</th><th>List assignee</th>
            </tr></thead>
            <tbody>{mism.map((m) => (
              <tr key={m.lead_id}>
                <td><input type="checkbox" aria-label={`Select ${m.company || m.name || m.lead_id}`} checked={picked.includes(m.lead_id)} onChange={() => setPicked((p) => (p.includes(m.lead_id) ? p.filter((x) => x !== m.lead_id) : [...p, m.lead_id]))} /></td>
                <td><b>{m.company || m.lead_id}</b><br /><small>{m.name}</small></td><td>{m.owner || "nobody"}</td><td>{m.list_assignee || "admin pool"}</td>
              </tr>))}
            </tbody>
          </table></div>
          <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
            <button className="btn" disabled={busy || !picked.length} onClick={() => fix(picked)}>Fix owners for {picked.length} selected</button>
            <button className="btn ghost" disabled={busy} onClick={() => fix(mism.map((m) => m.lead_id))}>Fix owners for all {mism.length}</button>
          </div>
          <p className="sub">Fix owners makes the lead's owner the same as its list's assignee. Nothing changes until you press it.</p>
        </>
      )}
    </section>
  );
}
