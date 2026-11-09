// Dialogs for Setup > Team members: add a member, change role, workspaces, reset password, remove.
import { useState } from "react";
import { Modal, PasswordField, ROLE_HELP, ROLE_LABEL, WorkspaceChooser, chooserValid } from "./common.jsx";

const fromUser = (u) => ({ all: !(u.workspaces || []).length && !u.no_workspace && u.role !== "admin", ids: u.workspaces || [] });

function Actions({ onClose, busy, label, danger = false, disabled = false }) {
  return (
    <div className="dialog-actions">
      <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      <button type="submit" className={`btn${danger ? " danger-solid" : ""}`} disabled={busy || disabled} data-testid="dialog-submit">{busy ? "Saving…" : label}</button>
    </div>
  );
}

function useSubmit(action, onDone) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try { await action(); onDone(); } catch (err) { setError(err.message); setBusy(false); }
  };
  return { busy, error, submit };
}

export function AddMemberDialog({ roles, spaces, canGrantAll, workspaceAdmin, onSave, onClose }) {
  const [form, setForm] = useState({ email: "", name: "", role: "caller", password: "" });
  const [access, setAccess] = useState({ all: false, ids: workspaceAdmin && spaces.length === 1 ? [spaces[0].id] : [] });
  const isOwnerRole = form.role === "owner";
  const needsChoice = !isOwnerRole;
  const valid = !needsChoice || chooserValid(access);
  const { busy, error, submit } = useSubmit(() => onSave({ ...form, workspaces: isOwnerRole || access.all ? [] : access.ids, all_workspaces: !isOwnerRole && access.all }), onClose);
  return (
    <Modal title="Add a team member" description="They sign in with this email and password. Give the password to them in person or by phone." onClose={onClose} wide testId="add-member-dialog">
      <form onSubmit={submit} autoComplete="off" className="setup-form">
        <div className="split-2">
          <div className="field"><label htmlFor="nm-email">Work email</label>
            <input id="nm-email" name="member-email" type="email" required autoComplete="off" data-autofocus value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
          <div className="field"><label htmlFor="nm-name">Name</label>
            <input id="nm-name" name="member-name" autoComplete="off" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
        </div>
        <div className="field"><label htmlFor="nm-role">Role</label>
          <select id="nm-role" value={form.role} onChange={(e) => { setForm({ ...form, role: e.target.value }); if (e.target.value === "admin") setAccess((a) => ({ ...a, all: false })); }}>
            {roles.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </select>
          <p className="hint" id="nm-role-help">{ROLE_HELP[form.role]}</p>
        </div>
        <PasswordField id="nm-pass" label="Starting password" value={form.password} onChange={(password) => setForm({ ...form, password })} />
        {needsChoice
          ? <WorkspaceChooser spaces={spaces} value={access} onChange={setAccess} canGrantAll={canGrantAll && form.role !== "admin"} testId="new-user-workspaces" />
          : <p className="hint">Owners always see every workspace.</p>}
        {error && <p className="field-error" role="alert">{error}</p>}
        <Actions onClose={onClose} busy={busy} label="Add member" disabled={!valid} />
      </form>
    </Modal>
  );
}

export function RoleDialog({ user, roles, onSave, onClose }) {
  const [role, setRole] = useState(user.role);
  const { busy, error, submit } = useSubmit(() => onSave({ role }), onClose);
  return (
    <Modal title={`Change role: ${user.name}`} onClose={onClose} testId="role-dialog">
      <form onSubmit={submit} className="setup-form">
        <div className="field"><label htmlFor="rd-role">Role</label>
          <select id="rd-role" data-autofocus value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => <option key={r} value={r}>{ROLE_LABEL[r]}</option>)}
          </select>
          <p className="hint">{ROLE_HELP[role]}</p>
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <Actions onClose={onClose} busy={busy} label="Save role" disabled={role === user.role} />
      </form>
    </Modal>
  );
}

export function SpacesDialog({ user, spaces, canGrantAll, onSave, onClose }) {
  const [access, setAccess] = useState(fromUser(user));
  const { busy, error, submit } = useSubmit(() => onSave({ workspaces: access.all ? [] : access.ids }), onClose);
  return (
    <Modal title={`Workspaces: ${user.name}`} description="A login limited to some workspaces sees only those clients' leads, lists and numbers." onClose={onClose} testId="spaces-dialog">
      <form onSubmit={submit} className="setup-form">
        <WorkspaceChooser spaces={spaces} value={access} onChange={setAccess} canGrantAll={canGrantAll && user.role !== "admin"} />
        {error && <p className="field-error" role="alert">{error}</p>}
        <Actions onClose={onClose} busy={busy} label="Save workspaces" disabled={!chooserValid(access)} />
      </form>
    </Modal>
  );
}

export function ResetDialog({ user, onSave, onClose }) {
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const mismatch = again && pw !== again;
  const { busy, error, submit } = useSubmit(() => onSave({ password: pw }), onClose);
  return (
    <Modal title={`Reset password: ${user.name}`} description="Every session of theirs signs out. Give them the new password in person or by phone." onClose={onClose} testId="reset-dialog">
      <form onSubmit={submit} autoComplete="off" className="setup-form">
        <PasswordField id="rp-pass" label="New password" value={pw} onChange={setPw} autoFocus />
        <PasswordField id="rp-again" label="New password again" value={again} onChange={setAgain} hint={false} />
        {mismatch && <p className="field-error" role="alert">The two passwords do not match.</p>}
        {error && <p className="field-error" role="alert">{error}</p>}
        <Actions onClose={onClose} busy={busy} label="Reset password" disabled={pw.length < 10 || pw !== again} />
      </form>
    </Modal>
  );
}

export function RemoveDialog({ user, onSave, onClose }) {
  const { busy, error, submit } = useSubmit(onSave, onClose);
  return (
    <Modal title={`Remove ${user.name}?`} onClose={onClose} testId="remove-dialog">
      <form onSubmit={submit} className="setup-form">
        <p className="dialog-msg">The login <b>{user.email}</b> is deleted and its sessions stop working. Leads, tasks and history it worked on stay. This cannot be undone; you can add the person again later.</p>
        {error && <p className="field-error" role="alert">{error}</p>}
        <Actions onClose={onClose} busy={busy} label="Remove login" danger />
      </form>
    </Modal>
  );
}
