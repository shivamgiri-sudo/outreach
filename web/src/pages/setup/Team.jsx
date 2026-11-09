// Setup > Team members: one table of logins, a Manage menu per row, and Add member as a dialog.
// Owners see everyone; a workspace admin sees (and the server lets it change) only its own workspaces' logins.
import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { GuideButton } from "../../GuidePanel.jsx";
import Icon from "../../icons.jsx";
import { ActionMenu, ROLE_LABEL, Status, istDate } from "./common.jsx";
import { AddMemberDialog, RemoveDialog, ResetDialog, RoleDialog, SpacesDialog } from "./TeamDialogs.jsx";

const ROLES = ["owner", "admin", "caller", "viewer"];

function Spaces({ u, names }) {
  if (u.role === "owner") return <span className="ws-chip all">All workspaces</span>;
  if (u.no_workspace) return <span className="ws-chip none" title="Sees nothing until given a workspace">No workspace</span>;
  if (!(u.workspaces || []).length) return <span className="ws-chip all">All workspaces</span>;
  return <span className="ws-chips">{u.workspaces.map((id) => <span key={id} className="ws-chip">{names[id] || id}</span>)}</span>;
}

export default function Team({ workspaceAdmin = false }) {
  const [rows, setRows] = useState(null);
  const [spaces, setSpaces] = useState([]);
  const [isSuper, setIsSuper] = useState(true);
  const [message, setMessage] = useState(null);
  const [dialog, setDialog] = useState(null);           // { kind, user }
  const load = () => api.users().then(setRows).catch((e) => setMessage({ text: e.message, error: true }));
  useEffect(() => {
    load();
    api.workspaces().then(setSpaces).catch(() => {});
    api.me().then((m) => setIsSuper(m.super_admin !== false)).catch(() => {});
  }, []);
  const names = Object.fromEntries(spaces.map((w) => [w.id, w.name]));
  const roleChoices = (u) => ROLES.filter((r) => (isSuper || r !== "owner" || u?.role === "owner") && (!workspaceAdmin || r !== "owner"));
  const done = (text) => { setMessage({ text }); load(); };
  const change = (u, body, text) => api.changeUser(u.email, body).then(() => done(text));
  const close = () => setDialog(null);
  const canTouch = (u) => isSuper || u.role !== "owner";

  return (
    <section className="card setup-card" data-testid="users-card" aria-labelledby="team-h">
      <div className="card-head">
        <div><h3 id="team-h" className="sr-only">Team members</h3>
          <p className="hint count-line">{rows ? `${rows.length} login${rows.length === 1 ? "" : "s"}` : "Loading…"}{workspaceAdmin ? " in your workspaces" : ""}</p></div>
        <div className="row">
          <GuideButton keys={["app:team_logins", "setup:login"]} label="How do I add my team?" testId="users-guide" />
          <button className="btn" onClick={() => setDialog({ kind: "add" })} data-testid="add-member"><Icon name="plus" size={16} /> Add member</button>
        </div>
      </div>
      <Status message={message} />
      {rows && rows.length === 0 && <p className="empty">No logins yet. Add the first one; without a server admin password it must be an owner.</p>}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="setup-table team-table">
            <thead><tr><th scope="col">Name</th><th scope="col">Role</th><th scope="col">Workspaces</th><th scope="col">Two-step</th><th scope="col">Last sign-in</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((u) => (
                <tr key={u.email} data-testid={`member-${u.email}`}>
                  <td data-label="Name"><b>{u.name}</b>{u.super_admin && <span className="pill ok tiny">Super admin</span>}<br /><small>{u.email}</small></td>
                  <td data-label="Role"><span className={`role-pill r-${u.role}`}>{ROLE_LABEL[u.role] || u.role}</span>
                    {u.role === "caller" && u.can_edit_contacts === false && <small className="block">Cannot edit contacts</small>}</td>
                  <td data-label="Workspaces"><Spaces u={u} names={names} /></td>
                  <td data-label="Two-step">{u.two_step_on ? <span className="pill ok">On</span> : <span className="pill">Off</span>}</td>
                  <td data-label="Last sign-in">{u.last_sign_in ? istDate(u.last_sign_in) : <span className="muted">Never</span>}</td>
                  <td className="actions">
                    <ActionMenu label={`Manage ${u.email}`} testId={`manage-${u.email}`} items={[
                      { label: "Change role…", hidden: u.super_admin || !canTouch(u), onSelect: () => setDialog({ kind: "role", user: u }) },
                      { label: "Workspaces…", hidden: u.role === "owner" || spaces.length < 1, onSelect: () => setDialog({ kind: "spaces", user: u }) },
                      { label: u.can_edit_contacts === false ? "Allow editing contacts" : "Stop editing contacts", hidden: u.role !== "caller" || workspaceAdmin,
                        onSelect: () => change(u, { can_edit_contacts: u.can_edit_contacts === false }, u.can_edit_contacts === false ? `${u.name} may edit contact details.` : `${u.name} can no longer edit contact details.`).catch((e) => setMessage({ text: e.message, error: true })) },
                      { label: "Reset password…", hidden: !canTouch(u), onSelect: () => setDialog({ kind: "reset", user: u }) },
                      { label: "Remove login…", danger: true, hidden: u.super_admin || !canTouch(u), onSelect: () => setDialog({ kind: "remove", user: u }) },
                    ]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {dialog?.kind === "add" && <AddMemberDialog roles={roleChoices(null)} spaces={spaces} canGrantAll={!workspaceAdmin} workspaceAdmin={workspaceAdmin} onClose={close}
        onSave={(body) => api.addUser(body).then(() => done(`Login added for ${body.email}.`))} />}
      {dialog?.kind === "role" && <RoleDialog user={dialog.user} roles={roleChoices(dialog.user)} onClose={close}
        onSave={(body) => change(dialog.user, body, `${dialog.user.name} is now ${ROLE_LABEL[body.role].toLowerCase()}.`)} />}
      {dialog?.kind === "spaces" && <SpacesDialog user={dialog.user} spaces={spaces} canGrantAll={!workspaceAdmin} onClose={close}
        onSave={(body) => change(dialog.user, body, body.workspaces.length ? `${dialog.user.name} now sees: ${body.workspaces.map((x) => names[x] || x).join(", ")}.` : `${dialog.user.name} sees every workspace.`)} />}
      {dialog?.kind === "reset" && <ResetDialog user={dialog.user} onClose={close}
        onSave={(body) => change(dialog.user, body, `Password changed for ${dialog.user.email}; their old sessions are signed out.`)} />}
      {dialog?.kind === "remove" && <RemoveDialog user={dialog.user} onClose={close}
        onSave={() => api.removeUser(dialog.user.email).then(() => done(`Login removed for ${dialog.user.email}.`))} />}
    </section>
  );
}
