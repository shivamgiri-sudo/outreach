// Setup → Industry & fields: apply an industry preset, and define custom lead fields.
import { useEffect, useState } from "react";
import { apiRequest, isOwner } from "../api.js";
import { loadLeadFields } from "./LeadCustomFields.jsx";

const WHAT = { sequence: "email sequence", linkedin: "LinkedIn steps and personas", call_script: "call script", snippets: "snippets",
  scoring: "scoring hints", icp_hints: "target titles and signals", icp: "ideal-customer description", lead_term: "lead number label",
  stage_labels: "stage names", flows: "starter flows" };

export function PresetsCard({ onApplied }) {
  const [data, setData] = useState(null);
  const [pick, setPick] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { apiRequest("/api/presets").then((r) => { setData(r); setPick(r.applied?.id || ""); }).catch((e) => setMsg(e.message)); }, []);
  if (!data) return msg ? <p className="sub">{msg}</p> : null;
  const chosen = data.presets.find((p) => p.id === pick);
  const apply = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await apiRequest(`/api/presets/${pick}/apply`, { method: "POST", body: { overwrite } });
      setResult(r); onApplied?.(r);
    } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  return (
    <div className="card" style={{ marginBottom: 14 }} data-testid="presets-card">
      <h3 style={{ marginTop: 0 }}>Industry preset</h3>
      <p className="sub">Start from words written for your kind of business: an email sequence, LinkedIn steps and personas, a call script,
        snippets, ideal-customer hints, the label for the lead's number field, stage names and two starter flows. Claims come from your
        brand settings ({"{{my_value_1}}"} …), so nothing is promised that you haven't written.</p>
      <div className="preset-grid" role="radiogroup" aria-label="Industry presets"
        style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))" }}>
        {data.presets.map((p) => (
          <label key={p.id} className={`card ${pick === p.id ? "on" : ""}`} style={{ margin: 0, cursor: "pointer", outline: pick === p.id ? "2px solid var(--accent, #0369a1)" : "none" }}>
            <span className="row" style={{ margin: 0, gap: 6 }}>
              <input type="radio" name="preset" value={p.id} checked={pick === p.id} onChange={() => { setPick(p.id); setResult(null); }} />
              <b>{p.name}</b>{p.default && <span className="pill">built in</span>}{data.applied?.id === p.id && <span className="pill ok">applied</span>}
            </span>
            <small className="sub" style={{ display: "block", margin: "4px 0 0" }}>{p.description}</small>
          </label>
        ))}
      </div>
      {chosen && (
        <p className="sub" style={{ marginTop: 10 }}>
          {chosen.name}: {chosen.email_steps} emails, {chosen.linkedin_steps} LinkedIn steps, {chosen.personas} personas, {chosen.snippets} snippets,
          flows “{chosen.flows.join("”, “")}”. The number field is called “{chosen.lead_term}”.
        </p>
      )}
      {isOwner() ? (
        <div className="row" style={{ flexWrap: "wrap" }}>
          <label className="row" style={{ margin: 0 }}><input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} />
            Replace what I have already changed (LinkedIn steps, call script, snippets, scoring)</label>
          <button className="btn" disabled={!pick || busy} onClick={apply}>{busy ? "Applying…" : "Apply preset"}</button>
        </div>
      ) : <p className="sub">Only the owner can apply a preset.</p>}
      {msg && <p className="pill err" role="alert">{msg}</p>}
      {result && (
        <div className="tip" role="status" style={{ marginTop: 10 }} data-testid="preset-result">
          <b>{result.name} applied.</b>{" "}
          {result.changed.length ? `Added or replaced: ${result.changed.map((k) => WHAT[k] || k).join(", ")}.` : "Nothing needed changing."}
          {result.kept.length > 0 && <> Kept yours: {result.kept.map((k) => WHAT[k.what] || k.what).join(", ")} (tick “Replace” to use the preset's).</>}
          {result.sequence_id && <> The new email sequence is under Sequences &amp; templates; the default one is untouched.</>}
        </div>
      )}
    </div>
  );
}

const TYPES = [["text", "Text"], ["number", "Number"], ["date", "Date"], ["select", "Choice list"], ["url", "Web link"], ["phone", "Phone"], ["email", "Email"]];
const blank = () => ({ key: "", label: "", type: "text", options: [], show_in_table: false, filterable: false });

export function LeadFieldsCard() {
  const [fields, setFields] = useState(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { apiRequest("/api/lead-fields").then((r) => setFields(r.fields)).catch((e) => setMsg(e.message)); }, []);
  if (!fields) return msg ? <p className="sub">{msg}</p> : null;
  const set = (i, key, value) => setFields(fields.map((f, j) => (i === j ? { ...f, [key]: value } : f)));
  const save = async () => {
    try {
      const r = await apiRequest("/api/lead-fields", { method: "PUT", body: { fields: fields.map((f) => ({ ...f, options: typeof f.options === "string" ? f.options.split(",") : f.options })) } });
      setFields(r.fields); setMsg("Fields saved."); loadLeadFields(true);
    } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="card" style={{ marginBottom: 14 }} data-testid="lead-fields-card">
      <h3 style={{ marginTop: 0 }}>Custom lead fields</h3>
      <p className="sub">Track what your industry needs (budget, property type, seats, course…). Each field can be edited on the lead,
        shown as a column and filter on Leads, filled from an imported column with the same name, exported, used in flows, and written
        into any message as {"{{custom.<key>}}"} (empty when the lead has no value).</p>
      {fields.length === 0 && <p className="sub">No custom fields yet.</p>}
      {fields.map((f, i) => (
        <div key={i} className="row" style={{ flexWrap: "wrap", alignItems: "flex-end", borderBottom: "1px solid var(--line, #ddd)", paddingBottom: 8, marginBottom: 8 }}>
          <label style={{ flex: 2, minWidth: 160 }}>Name<input aria-label={`Field ${i + 1} name`} value={f.label} maxLength={60} onChange={(e) => set(i, "label", e.target.value)} /></label>
          <label style={{ flex: 1, minWidth: 130 }}>Key<input aria-label={`Field ${i + 1} key`} value={f.key} maxLength={40} placeholder="made from the name"
            onChange={(e) => set(i, "key", e.target.value.toLowerCase())} /></label>
          <label style={{ flex: 1, minWidth: 120 }}>Type<select aria-label={`Field ${i + 1} type`} value={f.type} onChange={(e) => set(i, "type", e.target.value)}>
            {TYPES.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          {f.type === "select" && <label style={{ flex: 2, minWidth: 180 }}>Choices (comma separated)
            <input aria-label={`Field ${i + 1} choices`} value={Array.isArray(f.options) ? f.options.join(", ") : f.options} onChange={(e) => set(i, "options", e.target.value)} /></label>}
          <label className="row" style={{ margin: 0 }}><input type="checkbox" checked={f.show_in_table} onChange={(e) => set(i, "show_in_table", e.target.checked)} /> Column</label>
          <label className="row" style={{ margin: 0 }}><input type="checkbox" checked={f.filterable} onChange={(e) => set(i, "filterable", e.target.checked)} /> Filter</label>
          <button type="button" className="btn ghost" onClick={() => setFields(fields.filter((_, j) => j !== i))} aria-label={`Remove field ${f.label || i + 1}`}>Remove</button>
        </div>
      ))}
      <div className="row">
        <button type="button" className="btn ghost" onClick={() => setFields([...fields, blank()])} disabled={fields.length >= 40}>Add a field</button>
        <button type="button" className="btn" onClick={save}>Save fields</button>
      </div>
      {msg && <p className="sub" role="status">{msg}</p>}
    </div>
  );
}

export default function IndustrySetup() {
  return <><PresetsCard /><LeadFieldsCard /></>;
}
