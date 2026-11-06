import { useEffect, useState } from "react";
import { api, getRole, isOwner } from "../../api.js";
import { copyText } from "../../clipboard.js";
import { lintText } from "./lint.js";

const CHANNELS = [["email", "Email"], ["whatsapp", "WhatsApp"], ["linkedin", "LinkedIn"], ["sms", "SMS"]];
const EMPTY = { name: "", channel: "email", subject: "", body: "" };

// The snippet library: short reusable messages per channel. Callers add their own; the owner can change or delete any.
export default function Snippets({ me }) {
  const [rows, setRows] = useState([]);
  const [filter, setFilter] = useState("");
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState("");
  const load = () => api.snippets().then(setRows).catch((e) => setMsg(e.message));
  useEffect(() => { load(); }, []);
  const canWrite = getRole() !== "viewer";
  const mayChange = (s) => isOwner() || (me?.email && s.created_by_email === me.email);
  const save = async (e) => {
    e.preventDefault(); setMsg("");
    try {
      if (form.id) await api.updateSnippet(form.id, form); else await api.createSnippet(form);
      setForm(null); load();
    } catch (err) { setMsg(err.message); }
  };
  const remove = async (s) => {
    if (!window.confirm(`Delete the snippet "${s.name}"?`)) return;
    try { await api.deleteSnippet(s.id); load(); } catch (err) { setMsg(err.message); }
  };
  const copy = async (s) => {
    setCopied((await copyText((s.subject ? `Subject: ${s.subject}\n\n` : "") + s.body)) ? s.id : "failed");
    setTimeout(() => setCopied(""), 1500);
  };
  const shown = rows.filter((s) => !filter || s.channel === filter);
  const checks = form ? lintText(form.subject, form.body, { needsUnsub: false }).filter((x) => !/unsubscribe/i.test(x.text)) : [];
  return (
    <section aria-label="Snippet library">
      <p className="sub">Reusable messages for one-off emails (pick them in a contact's “Write an email”), WhatsApp, LinkedIn and SMS. One-off emails always get an unsubscribe line added.</p>
      <div className="row" style={{ flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
        <div className="row" role="group" aria-label="Filter by channel" style={{ flexWrap: "wrap", gap: 6 }}>
          <button type="button" className={`chip${filter === "" ? " on" : ""}`} onClick={() => setFilter("")}>All</button>
          {CHANNELS.map(([k, label]) => <button type="button" key={k} className={`chip${filter === k ? " on" : ""}`} onClick={() => setFilter(k)}>{label}</button>)}
        </div>
        {canWrite && !form && <button type="button" className="btn" onClick={() => setForm({ ...EMPTY, channel: filter || "email" })}>New snippet</button>}
      </div>
      {msg && <p className="error" role="alert">{msg}</p>}
      {form && (
        <form className="inline-form" onSubmit={save} aria-label={form.id ? "Edit snippet" : "New snippet"}>
          <div className="split-2">
            <label>Name<input required maxLength={120} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
            <label>Channel<select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              {CHANNELS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}</select></label>
          </div>
          {form.channel === "email" && <label>Subject<input maxLength={200} value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} /></label>}
          <label>Text<textarea required rows={6} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></label>
          {checks.map((x, i) => <p key={i} className="sub" style={{ color: x.level === "error" ? "var(--hot)" : "var(--warn)", margin: 0 }}>{x.text}</p>)}
          <div className="row"><button className="btn" type="submit" disabled={checks.some((x) => x.level === "error")}>Save snippet</button>
            <button className="btn ghost" type="button" onClick={() => setForm(null)}>Cancel</button></div>
        </form>
      )}
      {!shown.length && <p className="sub">No snippets yet{filter ? " for this channel" : ""}.</p>}
      {shown.map((s) => (
        <div key={s.id} className="card" style={{ marginBottom: 12 }}>
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
            <b>{s.name} <span className="pill">{CHANNELS.find(([k]) => k === s.channel)?.[1] || s.channel}</span></b>
            <span className="row" style={{ gap: 6, flexWrap: "wrap" }}>
              <button type="button" className="btn ghost" onClick={() => copy(s)}>{copied === s.id ? "Copied" : "Copy"}</button>
              {mayChange(s) && <button type="button" className="btn ghost" onClick={() => setForm({ ...EMPTY, ...s })}>Edit</button>}
              {mayChange(s) && <button type="button" className="btn ghost" onClick={() => remove(s)}>Delete</button>}
            </span>
          </div>
          {s.subject && <p><b>Subject:</b> {s.subject}</p>}
          <pre>{s.body}</pre>
          <p className="sub" style={{ margin: 0 }}>By {s.created_by || "someone"}</p>
        </div>
      ))}
    </section>
  );
}
