// Setup > My password & two-step: anyone with their own login changes their own password here; afterwards every
// session of theirs signs out.
import { useEffect, useState } from "react";
import { api, setToken } from "../../api.js";
import { PasswordField } from "./common.jsx";

export default function OwnPassword() {
  const [me, setMe] = useState(null);
  const [form, setForm] = useState({ current: "", next: "", again: "" });
  const [message, setMessage] = useState("");
  useEffect(() => { api.me().then(setMe).catch((e) => setMessage(e.message)); }, []);
  if (!me) return <p className="hint">{message || "Loading…"}</p>;
  if (!me.email) return (
    <section className="card setup-card"><h3>My password</h3>
      <p className="hint">You signed in with the server admin password. Change it on the server (OUTREACH_ADMIN_PASSWORD), or sign in with your own login to change that one here.</p></section>
  );
  const save = (e) => {
    e.preventDefault();
    if (form.next !== form.again) { setMessage("The two new passwords do not match."); return; }
    setMessage("");
    api.changeOwnPassword(form.current, form.next).then(() => {
      setMessage("Password changed. Sign in again with the new password.");
      setTimeout(() => { setToken(""); window.dispatchEvent(new Event("auth-expired")); }, 1500);
    }).catch((err) => setMessage(err.message));
  };
  return (
    <form className="card setup-card setup-form narrow" onSubmit={save} data-testid="own-password">
      <h3>My password</h3>
      <p className="hint">Signed in as {me.name} ({me.email}). Changing it signs you out everywhere.</p>
      <input type="email" name="username" autoComplete="username" value={me.email} readOnly hidden />
      <div className="field"><label htmlFor="own-current">Current password</label>
        <input id="own-current" type="password" required autoComplete="current-password" value={form.current} onChange={(e) => setForm({ ...form, current: e.target.value })} /></div>
      <PasswordField id="own-next" label="New password (10+ characters)" value={form.next} onChange={(next) => setForm({ ...form, next })} />
      <PasswordField id="own-again" label="New password again" value={form.again} onChange={(again) => setForm({ ...form, again })} hint={false} />
      <div className="row"><button className="btn" type="submit">Change password</button></div>
      {message && <p className="setup-status" role="status">{message}</p>}
    </form>
  );
}
