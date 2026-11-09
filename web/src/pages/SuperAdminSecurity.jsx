import { useEffect, useState } from "react";
import { api } from "../api.js";
import { copyText } from "../clipboard.js";
import { istLabel } from "../time.js";
import { useConfirm } from "../Dialog.jsx";

// Super admins only (renders nothing for anyone else; the server refuses them too). Self-contained so any Setup
// layout can mount it: sealed recovery codes (made / made again, shown once, .txt download), every owner's two-step
// status, the super admin list (set on the server) and whether the server admin password sign-in is on.
function download(codes, email) {
  const text = [`Outreach OS super admin recovery codes for ${email}`, `Made ${new Date().toISOString()}`,
    "Each code works once, together with a password reset link sent to your email.",
    "It resets your password and your two-step login. Keep this file offline.", "", ...codes, ""].join("\n");
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: "super-admin-recovery-codes.txt" });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function SuperAdminSecurity() {
  const [data, setData] = useState(null);
  const [me, setMe] = useState(null);
  const [form, setForm] = useState({ password: "", code: "" });
  const [codes, setCodes] = useState([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const confirm = useConfirm();
  useEffect(() => {
    api.me().then((m) => { setMe(m); if (m.super_admin) api.superAdminStatus().then(setData).catch((e) => setMessage(e.message)); }).catch(() => {});
  }, []);
  if (!me?.super_admin) return null;
  if (!data) return message ? <p className="sub">{message}</p> : null;
  const left = data.recovery.left;
  const make = async (e) => {
    e.preventDefault();
    if (left && !(await confirm({ message: "Make new recovery codes? The old ones stop working at once.", confirmLabel: "Make new codes", danger: true }))) return;
    setBusy(true); setMessage("");
    try {
      const out = await api.superAdminRecoveryCodes(form.password, form.code);
      setCodes(out.recovery_codes); setData(out); setForm({ password: "", code: "" });
    } catch (err) { setMessage(err.message); }
    setBusy(false);
  };
  const login = data.admin_login;
  return (
    <div className="card" style={{ marginBottom: 16 }} data-testid="super-admin-security">
      <b>Super admin</b>
      <p className="sub" style={{ margin: "2px 0 8px" }}>Only super admins see this. Owners and super admins must use two-step login.</p>

      <h4 style={{ margin: "10px 0 4px" }}>Recovery codes</h4>
      {data.recovery.available ? <>
        <p className="sub" style={{ margin: "2px 0 6px" }}>
          Lost your phone or password? One code, together with a reset link to your email, resets your password and your two-step login.
          {" "}{left ? `${left} unused code(s)${data.recovery.made_at ? `, made ${istLabel(data.recovery.made_at)}` : ""}.` : "You have no codes yet."}
        </p>
        {codes.length > 0 && (
          <div className="tip" data-testid="super-recovery-codes" style={{ margin: "8px 0" }}>
            <b>Save these codes now: they are not shown again.</b> Keep them offline (printed or in a safe).
            <ul style={{ columns: 2, margin: "6px 0", fontFamily: "monospace" }}>{codes.map((c) => <li key={c}>{c}</li>)}</ul>
            <span className="row">
              <button type="button" className="btn" onClick={() => download(codes, me.email)}>Download .txt</button>
              <button type="button" className="btn ghost" onClick={() => copyText(codes.join("\n"))}>Copy</button>
              <button type="button" className="btn ghost" onClick={() => setCodes([])}>I saved them</button>
            </span>
          </div>
        )}
        <form className="row" style={{ flexWrap: "wrap", gap: 8 }} onSubmit={make}>
          <input type="password" aria-label="Your password" placeholder="Your password" autoComplete="current-password" value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <input aria-label="Authenticator code" placeholder="Authenticator code" inputMode="numeric" autoComplete="one-time-code" maxLength={12}
            value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
          <button className="btn" type="submit" disabled={busy || !form.password} data-testid="make-super-codes">{left ? "Make new codes" : "Make recovery codes"}</button>
        </form>
      </> : <p className="sub">Recovery codes belong to a super admin's own login. Sign in with your email to make them.</p>}
      {message && <p className="sub" role="alert">{message}</p>}

      <h4 style={{ margin: "14px 0 4px" }}>Owners' two-step login</h4>
      <ul className="today-list" data-testid="owners-two-step">
        {data.owners.map((o) => (
          <li key={o.email} className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <span><b>{o.name}</b> <small>{o.email}</small>{o.super_admin && <span className="pill ok" style={{ marginLeft: 6 }}>Super admin</span>}</span>
            <span className={`pill ${o.two_step_on ? "ok" : "warn"}`}>{o.two_step_on ? "Two-step on" : "Sets it up at next sign-in"}</span>
          </li>
        ))}
        {!data.owners.length && <li className="sub">No owner logins yet.</li>}
      </ul>

      <h4 style={{ margin: "14px 0 4px" }}>Super admins</h4>
      <p className="sub" style={{ margin: "2px 0" }}>{data.super_admins.length ? data.super_admins.join(", ") : "None named: every owner counts as one."}
        {" "}Set on the server (OUTREACH_SUPER_ADMIN, comma-separated); it cannot be changed here.</p>

      <h4 style={{ margin: "14px 0 4px" }}>Server admin password sign-in</h4>
      <p className="sub" style={{ margin: "2px 0" }} data-testid="admin-login-status">
        {!login.password_set ? "Off: no OUTREACH_ADMIN_PASSWORD is set on the server."
          : login.mode === "off" ? "Off. To use it in an emergency, set OUTREACH_ADMIN_LOGIN=1 on the server and sign in through an SSH tunnel."
          : login.local_only ? "On, from the server itself only (SSH tunnel). Never over the internet. Every use alerts the super admins."
          : "On (test or development mode)."}
      </p>
    </div>
  );
}
