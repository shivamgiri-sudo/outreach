// The call script and objection answers, filled with the lead's name, role and company. Owners edit it here
// (saved on the server as settings.call_script, so every caller sees the same words).
import { useState } from "react";
import { api, isOwner } from "../api.js";

export function fillScript(text, lead, rep) {
  const first = (lead?.contact_name || "").replace(/^(mr|mrs|ms|dr)\.?\s+/i, "").trim().split(/\s+/)[0] || "there";
  return String(text || "").replaceAll("{{rep}}", rep || "your name").replaceAll("{{first_name}}", first)
    .replaceAll("{{role}}", lead?.role || "your open roles").replaceAll("{{company}}", lead?.company || "your company")
    .replace(/\{\{custom\.(\w+)\}\}/g, (_, key) => String(lead?.custom?.[key] ?? ""));   // the owner's custom lead fields
}

function Editor({ data, onSaved, onCancel }) {
  const [script, setScript] = useState(data.script.map((s) => ({ ...s })));
  const [objections, setObjections] = useState(data.objections.map((o) => ({ ...o })));
  const [msg, setMsg] = useState("");
  const set = (list, setList, i, key, value) => setList(list.map((x, j) => (i === j ? { ...x, [key]: value } : x)));
  const save = async (e) => {
    e.preventDefault();
    try { onSaved(await api.saveCallScript(script, objections)); } catch (err) { setMsg(err.message); }
  };
  const reset = async () => {
    if (!window.confirm("Put back the default script and answers? Your edits are removed.")) return;
    try { onSaved(await api.resetCallScript()); } catch (err) { setMsg(err.message); }
  };
  return (
    <form className="inline-form" onSubmit={save} aria-label="Edit the call script">
      <p className="sub" style={{ margin: 0 }}>Use {"{{first_name}}"}, {"{{rep}}"}, {"{{role}}"} and {"{{company}}"}; they are filled in for each lead. Only say what is true today.</p>
      {script.map((s, i) => (
        <div key={i} className="row" style={{ alignItems: "flex-start" }}>
          <input aria-label={`Line ${i + 1} title`} value={s.title} style={{ maxWidth: 160 }} onChange={(e) => set(script, setScript, i, "title", e.target.value)} />
          <textarea aria-label={`Line ${i + 1} text`} rows={2} value={s.text} style={{ flex: 1 }} onChange={(e) => set(script, setScript, i, "text", e.target.value)} />
          <button type="button" className="btn ghost" aria-label={`Remove line ${i + 1}`} onClick={() => setScript(script.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button type="button" className="btn ghost" onClick={() => setScript([...script, { title: "", text: "" }])}>Add a line</button>
      <h4 style={{ margin: "8px 0 0" }}>If they say…</h4>
      {objections.map((o, i) => (
        <div key={i} className="row" style={{ alignItems: "flex-start" }}>
          <input aria-label={`Objection ${i + 1}`} value={o.q} style={{ maxWidth: 220 }} onChange={(e) => set(objections, setObjections, i, "q", e.target.value)} />
          <textarea aria-label={`Answer ${i + 1}`} rows={2} value={o.a} style={{ flex: 1 }} onChange={(e) => set(objections, setObjections, i, "a", e.target.value)} />
          <button type="button" className="btn ghost" aria-label={`Remove objection ${i + 1}`} onClick={() => setObjections(objections.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button type="button" className="btn ghost" onClick={() => setObjections([...objections, { q: "", a: "" }])}>Add an answer</button>
      {msg && <p className="error" role="alert">{msg}</p>}
      <div className="row"><button className="btn" type="submit">Save script</button>
        <button className="btn ghost" type="button" onClick={onCancel}>Cancel</button>
        {data.custom && <button className="btn ghost" type="button" onClick={reset}>Use the default</button>}</div>
    </form>
  );
}

export default function CallScript({ data, lead, rep, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [showObjections, setShowObjections] = useState(false);
  if (!data) return null;
  if (editing) return <Editor data={data} onCancel={() => setEditing(false)} onSaved={(d) => { setEditing(false); onChanged(d); }} />;
  return (
    <div data-testid="call-script">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>Call script{lead ? ` for ${lead.contact_name || lead.company}` : ""}</h3>
        {isOwner() && <button className="btn ghost" onClick={() => setEditing(true)}>Edit script</button>}
      </div>
      {data.script.map((s, i) => <p key={i}><b>{s.title}:</b> {fillScript(s.text, lead, rep)}</p>)}
      <button className="link" onClick={() => setShowObjections(!showObjections)} aria-expanded={showObjections}>
        {showObjections ? "Hide answers to objections" : `If they say… (${data.objections.length} answers)`}
      </button>
      {showObjections && data.objections.map((o, i) => <p key={i}><b>“{o.q}”</b> — {fillScript(o.a, lead, rep)}</p>)}
      {data.custom && data.updated_by && <p className="sub">Edited by {data.updated_by}.</p>}
    </div>
  );
}
