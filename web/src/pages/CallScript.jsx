// The call script and objection answers, filled with the lead's name, role and company. Owners edit it here
// (saved on the server as settings.call_script, so every caller sees the same words).
import { useState } from "react";
import { api, isOwner } from "../api.js";

// `vars` are the brand values from the server ({{my_company}}, {{my_value_1}}, …), filled the same way.
export function fillScript(text, lead, rep, vars = {}, roleFallback = "your open roles") {
  const first = (lead?.contact_name || "").replace(/^(mr|mrs|ms|dr)\.?\s+/i, "").trim().split(/\s+/)[0] || "there";
  const filled = String(text || "").replaceAll("{{rep}}", rep || "your name").replaceAll("{{first_name}}", first)
    .replaceAll("{{role}}", lead?.role || roleFallback).replaceAll("{{company}}", lead?.company || "your company");
  // A brand value that is not set yet is left out (a caller must never read "{{my_value_1}}" aloud); missingBrand()
  // names it so the script can say where to add it.
  let gaps = false;
  const out = filled.replace(BRAND_RE, (m, key) => vars[key] || ((gaps = true), ""))
    .replace(/\{\{custom\.(\w+)\}\}/g, (_, key) => String(lead?.custom?.[key] ?? ""));   // the owner's custom lead fields
  return gaps ? out.replace(/(\s*[.,;:]){2,}/g, ".").replace(/^\s*[.,;:]\s*/, "").replace(/\s{2,}/g, " ").trim() : out;
}

const BRAND_RE = /\{\{(my_[a-z0-9_]+|sign_off|sender)\}\}/g;
const BRAND_NAMES = { my_value_1: "selling points", my_value_2: "selling points", my_value_3: "selling points", my_cta: "next step (call to action)",
  my_company: "company name", my_product: "product", my_tagline: "tagline", my_website: "website", sign_off: "sign-off", sender: "sender name" };
export function missingBrand(text, vars = {}) {
  const keys = [...String(text || "").matchAll(BRAND_RE)].map((m) => m[1]).filter((k) => !vars[k]);
  return [...new Set(keys.map((k) => BRAND_NAMES[k] || k.replace(/^my_/, "").replace(/_/g, " ")))];
}

function Line({ label, text, lead, rep, data }) {
  const filled = fillScript(text, lead, rep, data.variables, data.role_fallback);
  const missing = missingBrand(text, data.variables);
  return (
    <>{label}{filled}{missing.length > 0 && <small className="sub-inline" data-testid="script-gap">
      {filled ? " " : ""}(Add your {missing.join(", ")} in Setup → Brand &amp; offer.)</small>}</>
  );
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
      <p className="sub" style={{ margin: 0 }}>Use {"{{first_name}}"}, {"{{rep}}"}, {"{{role}}"} and {"{{company}}"}; they are filled in for each lead. Your own details ({"{{my_company}}"}, {"{{my_product}}"}, {"{{my_value_1}}"}…{"{{my_value_3}}"}, {"{{my_cta}}"}) come from Setup → Brand &amp; offer. Only say what is true today.</p>
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
      {data.script.map((s, i) => <p key={i}><Line label={<><b>{s.title}:</b> </>} text={s.text} lead={lead} rep={rep} data={data} /></p>)}
      <button className="link" onClick={() => setShowObjections(!showObjections)} aria-expanded={showObjections}>
        {showObjections ? "Hide answers to objections" : `If they say… (${data.objections.length} answers)`}
      </button>
      {showObjections && data.objections.map((o, i) => <p key={i}><Line label={<><b>“{o.q}”</b> — </>} text={o.a} lead={lead} rep={rep} data={data} /></p>)}
      {data.custom && data.updated_by && <p className="sub">Edited by {data.updated_by}.</p>}
    </div>
  );
}
