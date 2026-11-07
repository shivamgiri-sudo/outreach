import { useState } from "react";
import { api, canEditContacts, canWrite } from "../api.js";
import { useConfirm } from "../Dialog.jsx";

// Every way to reach this lead (main + extra phones and emails, LinkedIn profile) and the other people at its company.
// Each add / change / removal is saved with who and when (Change log) and shows on the lead's timeline.
const dial = (n) => `tel:${String(n).replace(/[^+\d]/g, "")}`;
const isUrl = (u) => /^https?:\/\//i.test(u || "");

function Point({ kind, value, main, bad, lead, onLead, setMsg }) {
  const confirm = useConfirm();
  const edit = canWrite() && canEditContacts();
  const run = async (fn, done) => { setMsg(""); try { onLead(await fn()); setMsg(done); } catch (e) { setMsg(e.message); } };
  return (
    <li className="row" style={{ gap: 8, flexWrap: "wrap", padding: "4px 0" }} data-testid={`point-${kind}`}>
      {kind === "phone" ? <a href={dial(value)}>{value}</a> : <a href={`mailto:${value}`}>{value}</a>}
      {main && <span className="pill ok">Main</span>}
      {bad && <span className="pill hot" title={`Marked ${bad.status.replace(/_/g, " ")} by ${bad.by}`}>{bad.status === "wrong_number" ? "Wrong number" : "Inactive"}</span>}
      {!main && edit && <>
        <button className="link" onClick={() => run(() => api.primaryContactPoint(lead.id, kind, value), `${value} is now the main ${kind}.`)}>Make main</button>
        <button className="link danger" onClick={async () => (await confirm({ message: `Remove ${value} from this lead?`, confirmLabel: "Remove", danger: true }))
          && run(() => api.removeContactPoint(lead.id, kind, value), `${value} removed.`)}>Remove</button>
      </>}
    </li>
  );
}

export function ContactPoints({ lead, onLead }) {
  const [kind, setKind] = useState("phone");
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState("");
  const edit = canWrite() && canEditContacts();
  const phones = [lead.phone, ...(lead.phones || [])].filter((v, i, a) => v && a.indexOf(v) === i);
  const emails = [lead.email, ...(lead.emails || [])].filter((v, i, a) => v && a.indexOf(v) === i);
  const bad = lead.bad_numbers || {};
  const mainBad = lead.phone_status?.number === lead.phone ? lead.phone_status : null;
  const add = async (e) => {
    e.preventDefault(); setMsg("");
    try { onLead(await api.addContactPoint(lead.id, kind, value)); setValue(""); setMsg(`Added ${kind}.`); } catch (err) { setMsg(err.message); }
  };
  return (
    <section className="card" style={{ margin: "12px 0" }} aria-label="Contact details" data-testid="contact-points">
      <b>Contact details</b>
      <div className="grid g2" style={{ gap: 10, marginTop: 6 }}>
        <div><small className="sub-inline">Phones</small>
          <ul className="plain-list">{phones.length ? phones.map((p) => <Point key={p} kind="phone" value={p} main={p === lead.phone} bad={p === lead.phone ? mainBad : bad[p]} lead={lead} onLead={onLead} setMsg={setMsg} />)
            : <li className="sub-inline">No phone</li>}</ul></div>
        <div><small className="sub-inline">Emails</small>
          <ul className="plain-list">{emails.length ? emails.map((m) => <Point key={m} kind="email" value={m} main={m === lead.email} lead={lead} onLead={onLead} setMsg={setMsg} />)
            : <li className="sub-inline">No email</li>}</ul></div>
      </div>
      <p style={{ margin: "6px 0" }}><small className="sub-inline">LinkedIn profile: </small>
        {isUrl(lead.linkedin_url) ? <a href={lead.linkedin_url} target="_blank" rel="noopener noreferrer" data-testid="linkedin-url" style={{ overflowWrap: "anywhere" }}>{lead.linkedin_url}</a>
          : <span className="sub-inline">not added (use Edit details)</span>}</p>
      {edit && (
        <form className="row" onSubmit={add} style={{ gap: 6, flexWrap: "wrap" }}>
          <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Add a" style={{ width: "auto" }}>
            <option value="phone">Another phone</option><option value="email">Another email</option>
          </select>
          <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={kind === "phone" ? "98765 43210" : "name@company.com"} aria-label={kind === "phone" ? "Phone number" : "Email address"} style={{ flex: 1, minWidth: 160 }} />
          <button className="btn ghost" disabled={!value.trim()}>Add</button>
        </form>
      )}
      {msg && <p className="sub" role="status">{msg}</p>}
    </section>
  );
}

const EMPTY = { contact_name: "", title: "", email: "", phone: "", linkedin_url: "" };
const FIELDS = [["contact_name", "Name"], ["title", "Designation"], ["email", "Email"], ["phone", "Phone"], ["linkedin_url", "LinkedIn profile"]];

function PersonForm({ start, onSave, onCancel, label }) {
  const [v, setV] = useState({ ...EMPTY, ...start });
  return (
    <form className="inline-form" onSubmit={(e) => { e.preventDefault(); onSave(v); }} aria-label={label} data-testid="person-form">
      <div className="split-2">{FIELDS.map(([k, l]) => <label key={k}>{l}<input value={v[k] || ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label>)}</div>
      <div className="row"><button className="btn" disabled={!v.contact_name.trim()}>{label}</button><button type="button" className="btn ghost" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

export function LeadPeople({ lead, onLead }) {
  const [form, setForm] = useState(null);           // "new" or a person id
  const [msg, setMsg] = useState("");
  const confirm = useConfirm();
  const edit = canWrite() && canEditContacts();
  const people = (lead.other_contacts || []).map((p, i) => ({ ...p, _id: p.id || String(i) }));
  const run = async (fn, done) => { setMsg(""); try { onLead(await fn()); setForm(null); setMsg(done); } catch (e) { setMsg(e.message); } };
  if (!people.length && !edit) return null;
  return (
    <section className="card" style={{ margin: "12px 0" }} aria-label="People on this record" data-testid="lead-people">
      <div className="row" style={{ justifyContent: "space-between" }}><b>More people at {lead.company || "this company"} ({people.length})</b>
        {edit && form !== "new" && <button className="btn ghost" onClick={() => setForm("new")}>Add person</button>}</div>
      {form === "new" && <PersonForm label="Add person" onCancel={() => setForm(null)} onSave={(v) => run(() => api.addPerson(lead.id, v), `${v.contact_name} added.`)} />}
      <ul className="plain-list">{people.map((p) => (
        <li key={p._id} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
          {form === p._id ? <PersonForm label="Save" start={p} onCancel={() => setForm(null)} onSave={(v) => run(() => api.changePerson(lead.id, p._id, v), "Saved.")} /> : (
            <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
              <span style={{ minWidth: 0 }}><b>{p.contact_name || "Unnamed"}</b>{p.title && <small className="sub-inline"> · {p.title}</small>}<br />
                <small>{p.phone && <a href={dial(p.phone)}>{p.phone}</a>}{p.phone && p.email && " · "}{p.email && <a href={`mailto:${p.email}`}>{p.email}</a>}
                  {isUrl(p.linkedin_url) && <> · <a href={p.linkedin_url} target="_blank" rel="noopener noreferrer">LinkedIn</a></>}</small>
                {p.added_by && <small className="sub-inline"> · added by {p.added_by}</small>}</span>
              {edit && <span className="row" style={{ gap: 6 }}><button className="link" onClick={() => setForm(p._id)}>Edit</button>
                <button className="link danger" onClick={async () => (await confirm({ message: `Remove ${p.contact_name || "this person"}?`, confirmLabel: "Remove", danger: true }))
                  && run(() => api.removePerson(lead.id, p._id), "Removed.")}>Remove</button></span>}
            </div>
          )}
        </li>
      ))}</ul>
      {msg && <p className="sub" role="status">{msg}</p>}
    </section>
  );
}
