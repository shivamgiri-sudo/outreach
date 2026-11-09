// Setup > Workspaces: one row per client workspace with its lead and member counts.
// Owners create, rename, manage members and delete; a workspace admin renames and manages members of its own.
import { useEffect, useState } from "react";
import { api, isOwner } from "../../api.js";
import { GuideButton } from "../../GuidePanel.jsx";
import Icon from "../../icons.jsx";
import { Status, istDate } from "./common.jsx";
import { DeleteDialog, MembersDialog, NameDialog } from "./WorkspaceDialogs.jsx";

export default function Workspaces({ owner = isOwner() }) {
  const [rows, setRows] = useState(null);
  const [message, setMessage] = useState(null);
  const [dialog, setDialog] = useState(null);            // { kind, ws }
  const load = () => api.workspacesOverview().then((w) => { setRows(w); window.dispatchEvent(new Event("workspaces-changed")); })
    .catch((e) => setMessage({ text: e.message, error: true }));
  useEffect(() => { load(); }, []);
  const close = () => setDialog(null);
  const say = (text) => { setMessage({ text }); load(); };

  return (
    <section className="card setup-card" aria-labelledby="ws-h">
      <div className="card-head">
        <div><h3 id="ws-h" className="sr-only">Workspaces</h3>
          <p className="hint">{rows ? `${rows.length} workspace${rows.length === 1 ? "" : "s"}. ` : ""}A contact sits in one workspace only, so two clients never contact the same person from here.</p></div>
        <div className="row">
          <GuideButton keys="app:workspaces" label="How do workspaces work?" testId="workspaces-guide" />
          {owner && <button className="btn" onClick={() => setDialog({ kind: "create" })} data-testid="create-workspace"><Icon name="plus" size={16} /> Create workspace</button>}
        </div>
      </div>
      <Status message={message} />
      {!rows ? <p className="hint">Loading…</p> : (
        <div className="table-wrap">
          <table className="setup-table ws-table">
            <thead><tr><th scope="col">Workspace</th><th scope="col" className="num">Leads</th><th scope="col" className="num">Members</th><th scope="col">Created</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((w) => (
                <tr key={w.id} data-testid={`ws-${w.name}`}>
                  <td data-label="Workspace"><b>{w.name}</b>{w.id === "main" && <span className="pill tiny">Default</span>}</td>
                  <td data-label="Leads" className="num">{w.leads}</td>
                  <td data-label="Members" className="num">{w.members}</td>
                  <td data-label="Created">{w.id === "main" ? <span className="muted">Built in</span> : <>{w.created_by || "Unknown"}<br /><small>{istDate(w.created_at)}</small></>}</td>
                  <td className="actions">
                    <div className="row-actions">
                      {w.id !== "main" && <button className="btn ghost small" onClick={() => setDialog({ kind: "rename", ws: w })} aria-label={`Rename ${w.name}`}><Icon name="pencil" size={15} /> Rename</button>}
                      <button className="btn ghost small" onClick={() => setDialog({ kind: "members", ws: w })} aria-label={`Members of ${w.name}`}><Icon name="users" size={15} /> Members</button>
                      {owner && w.id !== "main" && <button className="btn ghost small danger sep" onClick={() => setDialog({ kind: "delete", ws: w })} aria-label={`Delete ${w.name}`}><Icon name="trash" size={15} /> Delete</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="hint">Pick the workspace in the left menu: imports go into it, and Leads, Lists and Intelligence show only its leads. Move single leads from the Leads page (select, then “Move to workspace”).</p>
      {dialog?.kind === "create" && <NameDialog title="Create a workspace" label="Create workspace" onClose={close}
        onSave={(name) => api.createWorkspace(name).then(() => say(`Workspace “${name}” added.`))} />}
      {dialog?.kind === "rename" && <NameDialog title={`Rename ${dialog.ws.name}`} label="Save name" initial={dialog.ws.name} onClose={close}
        onSave={(name) => api.renameWorkspace(dialog.ws.id, name).then(() => say(`Renamed to “${name}”.`))} />}
      {dialog?.kind === "members" && <MembersDialog ws={dialog.ws} owner={owner} onClose={close} onChanged={load} />}
      {dialog?.kind === "delete" && <DeleteDialog ws={dialog.ws} others={(rows || []).filter((w) => w.id !== dialog.ws.id)} onClose={close}
        onDone={(out) => { close(); say(`Workspace “${out.name}” deleted.${out.moved ? ` ${out.moved} leads moved to ${out.moved_to}.` : ""}${out.deleted ? ` ${out.deleted} leads deleted.` : ""}${out.left_without_workspace?.length ? ` ${out.left_without_workspace.length} login(s) now have no workspace.` : ""}`); }} />}
    </section>
  );
}
