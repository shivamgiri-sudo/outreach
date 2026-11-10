// Hand out a list: Assign (whole list to one person or back to the admin pool), Split (between several people) and
// Share (view only). Only owners and workspace admins see the buttons; the server refuses everyone else.
import { useEffect, useId, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../api.js";
import { Modal } from "./setup/common.jsx";

export const canHandOut = () => isOwner() || isWorkspaceAdmin();

// The people a list can be given to (same choices as the lead owner box).
export function useMembers() {
  const [names, setNames] = useState([]);
  useEffect(() => { api.leadOwners().then((r) => setNames(r.owners || [])).catch(() => {}); }, []);
  return names;
}

function Actions({ busy, label, disabled, onClose, onGo }) {
  return (
    <div className="row" style={{ marginTop: 14, justifyContent: "flex-end" }}>
      <button type="button" className="btn ghost" onClick={onClose}>Close</button>
      <button type="button" className="btn" disabled={busy || disabled} onClick={onGo}>{busy ? "Working…" : label}</button>
    </div>
  );
}

export function AssignDialog({ list, onClose, onDone }) {
  const members = useMembers();
  const [to, setTo] = useState(list.assigned_to || "");
  const [takeOver, setTakeOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [res, setRes] = useState(null);
  const id = useId();
  const go = async () => {
    setBusy(true); setError("");
    try { setRes(await api.assignList(list.id, to, takeOver)); onDone?.(); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <Modal title={`Assign "${list.name}"`} description="The person gets every lead in this list that nobody else owns. Choose the admin pool to take it back." onClose={onClose} testId="assign-dialog">
      <label htmlFor={`${id}-to`}>Give this list to</label>
      <select id={`${id}-to`} data-autofocus value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">Admin pool (nobody)</option>
        {(list.assigned_to && !members.includes(list.assigned_to) ? [list.assigned_to, ...members] : members).map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
      <label className="row" style={{ margin: "10px 0 0", gap: 8 }}>
        <input type="checkbox" checked={takeOver} onChange={(e) => setTakeOver(e.target.checked)} />
        Also take over leads that someone else already owns
      </label>
      <p className="sub">Without this, leads owned by another person stay with them and are listed below. Running sequences carry on under the new owner.</p>
      {error && <div className="tip error" role="alert">{error}</div>}
      {res && (
        <div className="tip" role="status" data-testid="assign-result">
          Moved <b>{res.moved}</b> lead(s).{res.skipped?.length > 0 && <> Left with their owner: <b>{res.skipped.length}</b>.</>}
          {res.skipped?.length > 0 && <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{res.skipped.slice(0, 20).map((s) => <li key={s.lead_id}>{s.lead_id} owned by {s.owner}</li>)}</ul>}
        </div>
      )}
      <Actions busy={busy} label={to ? `Assign to ${to}` : "Move to admin pool"} disabled={!!res} onClose={onClose} onGo={go} />
    </Modal>
  );
}

export function SplitDialog({ list, onClose, onDone }) {
  const members = useMembers();
  const [picked, setPicked] = useState([]);
  const [mode, setMode] = useState("even");
  const [counts, setCounts] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [res, setRes] = useState(null);
  const total = list.stats?.members ?? 0;
  const toggle = (n) => setPicked((p) => (p.includes(n) ? p.filter((x) => x !== n) : [...p, n]));
  const nums = picked.map((n) => Math.max(0, parseInt(counts[n], 10) || 0));
  const sum = nums.reduce((a, b) => a + b, 0);
  const ok = picked.length >= 2 && (mode === "even" || (sum > 0 && sum <= total));
  const go = async () => {
    setBusy(true); setError("");
    try { setRes(await api.splitList(list.id, picked, mode, mode === "count" ? nums : [])); onDone?.(); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <Modal title={`Split "${list.name}"`} description={`Cuts this list of ${total} lead(s) into one new list per person.`} onClose={onClose} testId="split-dialog">
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend><b>Who gets a share (pick at least two)</b></legend>
        <div style={{ maxHeight: 220, overflow: "auto" }}>
          {members.map((n) => (
            <div key={n} className="row" style={{ gap: 8, padding: "4px 0", flexWrap: "wrap" }}>
              <label className="row" style={{ gap: 8, margin: 0, flex: 1, minWidth: 160 }}>
                <input type="checkbox" checked={picked.includes(n)} onChange={() => toggle(n)} /> {n}
              </label>
              {mode === "count" && picked.includes(n) && (
                <input type="number" min="0" aria-label={`Leads for ${n}`} style={{ width: 90 }} value={counts[n] ?? ""} onChange={(e) => setCounts({ ...counts, [n]: e.target.value })} />
              )}
            </div>
          ))}
          {members.length === 0 && <p className="empty">No team members to choose from yet.</p>}
        </div>
      </fieldset>
      <fieldset style={{ border: 0, padding: 0, margin: "10px 0 0" }}>
        <legend><b>How to cut it</b></legend>
        <label className="row" style={{ gap: 8, margin: 0 }}><input type="radio" name="split-mode" checked={mode === "even"} onChange={() => setMode("even")} /> Evenly</label>
        <label className="row" style={{ gap: 8, margin: 0 }}><input type="radio" name="split-mode" checked={mode === "count"} onChange={() => setMode("count")} /> A set number each</label>
      </fieldset>
      {mode === "count" && <p className="sub" role="status">{sum} of {total} lead(s) given out{sum > total ? ". That is more than the list has." : "."}</p>}
      {error && <div className="tip error" role="alert">{error}</div>}
      {res && <div className="tip" role="status" data-testid="split-result">Made {res.lists?.length || 0} list(s): {(res.lists || []).map((l) => `${l.name}${l.assigned_to ? ` (${l.assigned_to})` : ""}`).join(", ")}.</div>}
      <Actions busy={busy} label={`Split between ${picked.length || "…"}`} disabled={!ok || !!res} onClose={onClose} onGo={go} />
    </Modal>
  );
}

export function ShareDialog({ list, onClose, onDone }) {
  const members = useMembers();
  const [users, setUsers] = useState(list.shared_with || []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const toggle = (n) => { setSaved(false); setUsers((p) => (p.includes(n) ? p.filter((x) => x !== n) : [...p, n])); };
  const go = async () => {
    setBusy(true); setError("");
    try { await api.shareList(list.id, users); setSaved(true); onDone?.(); } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <Modal title={`Share "${list.name}"`} description="People you pick can see this list and its leads but cannot send anything from it." onClose={onClose} testId="share-dialog">
      <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
        <legend><b>View only for</b></legend>
        <div style={{ maxHeight: 240, overflow: "auto" }}>
          {members.filter((n) => n !== list.assigned_to).map((n) => (
            <label key={n} className="row" style={{ gap: 8, margin: 0, padding: "4px 0" }}><input type="checkbox" checked={users.includes(n)} onChange={() => toggle(n)} /> {n}</label>
          ))}
        </div>
      </fieldset>
      {error && <div className="tip error" role="alert">{error}</div>}
      {saved && <div className="tip" role="status">Saved. Shared with {users.length} person(s).</div>}
      <Actions busy={busy} label="Save sharing" onClose={onClose} onGo={go} />
    </Modal>
  );
}
