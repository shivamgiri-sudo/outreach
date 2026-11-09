// Shared building blocks for the Setup sections: a modal dialog, the workspace chooser, menus, a password hint.
import { useEffect, useId, useRef, useState } from "react";
import { useModalFocus } from "../../DrawerShell.jsx";
import Icon from "../../icons.jsx";

export const ROLE_LABEL = { owner: "Owner", admin: "Workspace admin", caller: "Caller", viewer: "Viewer" };
export const ROLE_HELP = {
  owner: "Everything: all data, settings and connections, in every workspace.",
  admin: "A caller who also adds and manages logins for its own workspaces.",
  caller: "Works leads, calls, meetings and tasks. No settings, connections or CSV exports.",
  viewer: "Read only. Every change is refused.",
};

// A modal dialog: focus stays inside, Escape and the backdrop close it.
export function Modal({ title, description, onClose, children, wide = false, testId }) {
  const panel = useRef(null);
  const id = useId();
  useModalFocus(panel, onClose, true, "[data-autofocus]");
  return (
    <>
      <div className="scrim dialog-scrim" aria-hidden="true" onClick={onClose} />
      <div ref={panel} className={`dialog setup-modal${wide ? " wide" : ""}`} role="dialog" aria-modal="true"
        aria-labelledby={`${id}-t`} aria-describedby={description ? `${id}-d` : undefined} data-testid={testId}>
        <div className="setup-modal-head">
          <h2 id={`${id}-t`} className="dialog-title">{title}</h2>
          <button type="button" className="icon-btn setup-x" aria-label="Close" onClick={onClose}><Icon name="close" size={18} /></button>
        </div>
        {description && <p id={`${id}-d`} className="dialog-msg">{description}</p>}
        {children}
      </div>
    </>
  );
}

// Which workspaces a login may see. value = { all: bool, ids: [] }. "All workspaces" appears only when it may be given.
export function WorkspaceChooser({ spaces, value, onChange, canGrantAll, legend = "Workspaces this login may see", testId }) {
  const name = useId();
  const pickIds = (ids) => onChange({ all: false, ids });
  return (
    <fieldset className="ws-chooser" data-testid={testId}>
      <legend>{legend} <span className="req" aria-hidden="true">*</span></legend>
      {canGrantAll && (
        <div className="ws-mode" role="radiogroup" aria-label="Workspace access">
          <label className="check-row"><input type="radio" name={name} checked={!value.all} onChange={() => onChange({ all: false, ids: value.ids })} /> Only the workspaces I tick</label>
          <label className="check-row"><input type="radio" name={name} checked={value.all} onChange={() => onChange({ all: true, ids: [] })} /> All workspaces, including ones added later</label>
        </div>
      )}
      {!value.all && (
        <div className="ws-boxes">
          {spaces.map((w) => (
            <label key={w.id} className="check-row ws-box">
              <input type="checkbox" checked={value.ids.includes(w.id)}
                onChange={(e) => pickIds(e.target.checked ? [...value.ids, w.id] : value.ids.filter((x) => x !== w.id))} />
              {w.name}
            </label>
          ))}
        </div>
      )}
      <p className="hint">{value.all ? "Sees every client's leads. Only owners can give this." : value.ids.length ? `${value.ids.length} picked.` : "Tick at least one workspace."}</p>
    </fieldset>
  );
}
export const chooserValid = (v) => v.all || v.ids.length > 0;

// Password strength hint (a guide, not a rule; the server checks length and common passwords).
export function strength(pw) {
  if (!pw) return { score: 0, label: "" };
  let score = 0;
  if (pw.length >= 10) score++;
  if (pw.length >= 14) score++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  if (pw.length < 10) return { score: 1, label: "Too short: at least 10 characters" };
  return score >= 4 ? { score: 4, label: "Strong" } : score >= 3 ? { score: 3, label: "Good" } : { score: 2, label: "Fair: longer is better" };
}

export function PasswordField({ id, label, value, onChange, hint = true, autoFocus = false }) {
  const [show, setShow] = useState(false);
  const s = strength(value);
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <div className="secret-wrap">
        <input id={id} name={`${id}-new`} type={show ? "text" : "password"} required minLength={10} autoComplete="new-password"
          value={value} onChange={(e) => onChange(e.target.value)} aria-describedby={hint ? `${id}-s` : undefined} data-autofocus={autoFocus ? "" : undefined} />
        <button type="button" className="icon-btn reveal" aria-label={show ? "Hide password" : "Show password"} aria-pressed={show} onClick={() => setShow(!show)}>
          <Icon name={show ? "eyeOff" : "eye"} size={18} />
        </button>
      </div>
      {hint && (
        <div id={`${id}-s`} className="strength" data-score={s.score} aria-live="polite">
          <span className="bar" aria-hidden="true"><i /><i /><i /><i /></span>
          <span>{s.label || "At least 10 characters. A short sentence works well."}</span>
        </div>
      )}
    </div>
  );
}

// A small action menu (the "Manage" button on a row). items: [{ label, onSelect, danger, hidden }]
export function ActionMenu({ label, items, testId }) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(null);                  // fixed position, so a scrolling table never clips the menu
  const wrap = useRef(null);
  const id = useId();
  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (!wrap.current?.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") { setOpen(false); wrap.current?.querySelector("button")?.focus(); } };
    const shut = () => setOpen(false);
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    window.addEventListener("resize", shut);
    window.addEventListener("scroll", shut, true);
    wrap.current?.querySelector("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("mousedown", away); document.removeEventListener("keydown", key);
      window.removeEventListener("resize", shut); window.removeEventListener("scroll", shut, true);
    };
  }, [open]);
  const shown = items.filter((i) => !i.hidden);
  if (!shown.length) return null;
  const move = (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const all = [...wrap.current.querySelectorAll("[role=menuitem]")];
    const at = all.indexOf(document.activeElement);
    all[(at + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length]?.focus();
  };
  return (
    <div className="menu-wrap" ref={wrap}>
      <button type="button" className="btn ghost small" aria-haspopup="menu" aria-expanded={open} aria-controls={id}
        aria-label={label} onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          const width = 228, below = window.innerHeight - r.bottom > 260;
          setAt({ left: Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)), ...(below ? { top: r.bottom + 4 } : { bottom: window.innerHeight - r.top + 4 }) });
          setOpen(!open);
        }} data-testid={testId}>
        Manage <Icon name="more" size={16} />
      </button>
      {open && (
        <div className="menu" role="menu" id={id} aria-label={label} onKeyDown={move} style={at || undefined}>
          {shown.map((i, n) => (
            <div key={i.label}>
              {i.danger && n > 0 && !shown[n - 1].danger && <hr className="menu-sep" role="separator" />}
              <button type="button" role="menuitem" className={i.danger ? "danger" : ""} onClick={() => { setOpen(false); i.onSelect(); }}>{i.label}</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Status({ message }) {
  if (!message) return null;
  const bad = message.error;
  return <p className={`setup-status${bad ? " bad" : ""}`} role={bad ? "alert" : "status"}>{message.text}</p>;
}

export const istDate = (iso) => {
  if (!iso) return "";
  try { return new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" }); }
  catch { return iso; }
};
