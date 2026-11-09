// Dialogs for Setup > Workspaces: create, rename, members, delete (with what happens to the leads).
import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { Modal, ROLE_LABEL } from "./common.jsx";

function Buttons({ onClose, busy, label, danger = false, disabled = false }) {
  return (
    <div className="dialog-actions">
      <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      <button type="submit" className={`btn${danger ? " danger-solid" : ""}`} disabled={busy || disabled} data-testid="dialog-submit">{busy ? "Working…" : label}</button>
    </div>
  );
}

function useAction(action, onDone) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e) => {
    e?.preventDefault?.();
    setBusy(true); setError("");
    try { const out = await action(); onDone(out); } catch (err) { setError(err.message); setBusy(false); }
  };
  return { busy, error, submit };
}

export function NameDialog({ title, initial = "", label, onSave, onClose }) {
  const [name, setName] = useState(initial);
  const { busy, error, submit } = useAction(() => onSave(name.trim()), onClose);
  return (
    <Modal title={title} onClose={onClose} testId="ws-name-dialog">
      <form onSubmit={submit} autoComplete="off" className="setup-form">
        <div className="field"><label htmlFor="ws-name">Workspace name</label>
          <input id="ws-name" name="workspace-name" maxLength={60} required data-autofocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Client name" />
          <p className="hint">Usually the client's name. People see it in the workspace picker.</p></div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <Buttons onClose={onClose} busy={busy} label={label} disabled={!name.trim() || name.trim() === initial} />
      </form>
    </Modal>
  );
}

export function MembersDialog({ ws, owner, onChanged, onClose }) {
  const [rows, setRows] = useState(null);
  const [users, setUsers] = useState([]);
  const [pick, setPick] = useState("");
  const [confirming, setConfirming] = useState("");
  const [message, setMessage] = useState("");
  const load = () => api.workspaceMembers(ws.id).then(setRows).catch((e) => setMessage(e.message));
  useEffect(() => { load(); api.users().then(setUsers).catch(() => {}); }, []);
  const members = (rows || []).filter((m) => m.access === "member");
  const everyone = (rows || []).filter((m) => m.access === "all");
  const inside = new Set((rows || []).map((m) => m.email));
  const addable = users.filter((u) => u.role !== "owner" && !inside.has(u.email) && ((u.workspaces || []).length || u.no_workspace || u.role === "admin"));
  const act = (fn, done) => fn().then(() => { setMessage(done); setConfirming(""); setPick(""); load(); onChanged(); }).catch((e) => setMessage(e.message));
  return (
    <Modal title={`Members: ${ws.name}`} description="Logins limited to this workspace. Owners and logins with access to all workspaces see it too." onClose={onClose} wide testId="members-dialog">
      {message && <p className="setup-status" role="status">{message}</p>}
      {!rows ? <p className="hint">Loading…</p> : (
        <ul className="member-list">
          {members.length === 0 && <li className="hint">Nobody is limited to this workspace yet.</li>}
          {members.map((m) => (
            <li key={m.email}>
              <span><b>{m.name}</b> <small>{m.email}</small> <span className={`role-pill r-${m.role}`}>{ROLE_LABEL[m.role]}</span></span>
              {confirming === m.email ? (
                <span className="row confirm-inline">
                  <small>{(m.workspaces || []).length === 1 ? "Their only workspace: they will see nothing." : "Take them out?"}</small>
                  <button type="button" className="btn ghost small" onClick={() => setConfirming("")}>Keep</button>
                  <button type="button" className="btn danger-solid small" onClick={() => act(() => api.removeWorkspaceMember(ws.id, m.email), `${m.name} taken out of ${ws.name}.`)}>Take out</button>
                </span>
              ) : <button type="button" className="btn ghost small danger" onClick={() => setConfirming(m.email)} aria-label={`Take ${m.email} out of ${ws.name}`}>Take out</button>}
            </li>
          ))}
        </ul>
      )}
      {everyone.length > 0 && <p className="hint">Also see it: {everyone.map((m) => m.name).join(", ")}.</p>}
      <form className="row add-member-row" onSubmit={(e) => { e.preventDefault(); if (pick) act(() => api.addWorkspaceMember(ws.id, pick), "Member added."); }}>
        <label htmlFor="ws-add-member" className="sr-only">Login to add</label>
        <select id="ws-add-member" value={pick} onChange={(e) => setPick(e.target.value)} disabled={!addable.length}>
          <option value="">{addable.length ? "Add an existing login…" : "No other login to add"}</option>
          {addable.map((u) => <option key={u.email} value={u.email}>{u.name} ({u.email})</option>)}
        </select>
        <button className="btn" disabled={!pick}>Add</button>
      </form>
      {owner && <p className="hint">New people: add them in Team members and tick this workspace.</p>}
      <div className="dialog-actions"><button type="button" className="btn ghost" onClick={onClose}>Done</button></div>
    </Modal>
  );
}

export function DeleteDialog({ ws, others, onDone, onClose }) {
  const [mode, setMode] = useState(ws.leads ? "move" : "none");
  const [moveTo, setMoveTo] = useState(others[0]?.id || "");
  const [typed, setTyped] = useState("");
  const matches = typed.trim().toLowerCase() === ws.name.toLowerCase();
  const body = { confirm_name: typed.trim(), ...(mode === "move" && ws.leads ? { move_to: moveTo } : {}), ...(mode === "delete" ? { delete_leads: true } : {}) };
  const { busy, error, submit } = useAction(() => api.deleteWorkspace(ws.id, body), onDone);
  return (
    <Modal title={`Delete workspace ${ws.name}?`} onClose={onClose} wide testId="delete-ws-dialog">
      <form onSubmit={submit} autoComplete="off" className="setup-form">
        {ws.leads > 0 ? (
          <fieldset className="ws-chooser">
            <legend>It holds {ws.leads} lead{ws.leads === 1 ? "" : "s"}. What should happen to them?</legend>
            <label className="check-row"><input type="radio" name="lead-fate" checked={mode === "move"} onChange={() => setMode("move")} /> Move them to another workspace</label>
            {mode === "move" && (
              <div className="field indent"><label htmlFor="ws-move-to">Move to</label>
                <select id="ws-move-to" value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
                  {others.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.leads} leads)</option>)}
                </select></div>
            )}
            <label className="check-row danger-text"><input type="radio" name="lead-fate" checked={mode === "delete"} onChange={() => setMode("delete")} /> Delete them permanently, with their tasks and this workspace's lists</label>
          </fieldset>
        ) : <p className="dialog-msg">It has no leads.</p>}
        <p className="dialog-msg">{ws.members ? `${ws.members} login${ws.members === 1 ? "" : "s"} limited to it lose access. Anyone left with no workspace sees nothing until you give them one.` : "No login is limited to it."}</p>
        <div className="field"><label htmlFor="ws-confirm">Type <b>{ws.name}</b> to confirm</label>
          <input id="ws-confirm" name="confirm-workspace" data-autofocus value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} /></div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <Buttons onClose={onClose} busy={busy} label={mode === "delete" ? "Delete workspace and leads" : "Delete workspace"} danger disabled={!matches || (mode === "move" && ws.leads > 0 && !moveTo)} />
      </form>
    </Modal>
  );
}
