// A workspace's LinkedIn sequence: connection note + follow-up messages (delay and "after" rule each) and personas
// (job-title pattern -> hook line). Main's is settings "linkedin_sequence"; another workspace uses main's until it has
// its own copy (app/ws_templates.py), which only owners and that workspace's admins change. Leads part-way through keep
// the steps they started with when the shape of the sequence changes.
import { useEffect, useState } from "react";
import { apiRequest, isOwner } from "../api.js";
import Abbr from "../components/Abbr.jsx";

const LIMIT = 300;

function Preview({ text, kind, title, workspace }) {
  const [p, setP] = useState(null);
  useEffect(() => {
    if (!text.trim()) { setP(null); return undefined; }
    const t = setTimeout(() => apiRequest("/api/linkedin/sequence/preview", { method: "POST", body: { text, kind, title, workspace } })
      .then(setP).catch(() => setP(null)), 350);
    return () => clearTimeout(t);
  }, [text, kind, title, workspace]);
  if (!p) return null;
  const over = kind === "connect" && p.longest_length > LIMIT;
  return (
    <div className="trail-text" style={{ marginTop: 6 }} data-testid="li-preview">
      <small className="sub-inline">Preview for a sample lead ({title || "Founder"}):</small>
      <div style={{ whiteSpace: "pre-wrap" }}>{p.text}</div>
      {kind === "connect" && <small className={over ? "pill err" : "sub-inline"}>{p.length} / {LIMIT} characters · {p.longest_length} with a long name and company</small>}
      {p.problem && <small className="pill err">{p.problem}</small>}
    </div>
  );
}

function StepRow({ step, index, count, onChange, onRemove, onMove, readOnly, sampleTitle, workspace }) {
  const connect = index === 0;
  const set = (key, value) => onChange({ ...step, [key]: value });
  return (
    <div className="card" style={{ marginBottom: 8 }} data-testid={`li-step-${index + 1}`}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <b>Step {index + 1}: {connect ? "connection request" : "message"}</b>
        {!readOnly && !connect && (
          <span className="row" style={{ gap: 4 }}>
            <button type="button" className="btn ghost" disabled={index < 2} onClick={() => onMove(-1)} aria-label={`Move step ${index + 1} up`}>↑</button>
            <button type="button" className="btn ghost" disabled={index === count - 1} onClick={() => onMove(1)} aria-label={`Move step ${index + 1} down`}>↓</button>
            <button type="button" className="btn ghost" onClick={onRemove}>Remove</button>
          </span>
        )}
      </div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <label style={{ flex: 2, minWidth: 180 }}>Purpose
          <input value={step.purpose || ""} maxLength={120} disabled={readOnly} onChange={(e) => set("purpose", e.target.value)} />
        </label>
        {!connect && <label style={{ flex: 1, minWidth: 120 }}>Days later
          <input type="number" min={0} max={60} value={step.days} disabled={readOnly} onChange={(e) => set("days", Number(e.target.value))} />
        </label>}
        {!connect && <label style={{ flex: 1, minWidth: 160 }}>After
          <select value={step.after} disabled={readOnly || index === 1} onChange={(e) => set("after", e.target.value)}>
            <option value="accepted">they accept the connection</option>
            <option value="previous">the previous message</option>
          </select>
        </label>}
      </div>
      <label htmlFor={`li-text-${index}`}>{connect ? `Connection note (LinkedIn allows ${LIMIT} characters)` : "Message"}</label>
      <textarea id={`li-text-${index}`} rows={connect ? 3 : 5} value={step.text} disabled={readOnly} maxLength={connect ? 600 : 2000}
        onChange={(e) => set("text", e.target.value)} style={{ width: "100%" }} />
      <Preview text={step.text} kind={connect ? "connect" : "message"} title={sampleTitle} workspace={workspace} />
    </div>
  );
}

export default function LinkedInSequenceEditor({ onMessage }) {
  const [data, setData] = useState(null);
  const [steps, setSteps] = useState([]);
  const [personas, setPersonas] = useState([]);
  const [hook, setHook] = useState("");
  const [noteB, setNoteB] = useState("");
  const [sampleTitle, setSampleTitle] = useState("Founder");
  const readOnly = !data || !data.can_edit || !data.own;
  const load = () => apiRequest("/api/linkedin/sequence").then((r) => {
    setData(r); setSteps(r.steps.map((s) => ({ ...s }))); setPersonas(r.personas.map((p) => ({ ...p }))); setHook(r.default_hook); setNoteB(r.variant_b || "");
  }).catch((e) => onMessage?.(e.message));
  useEffect(() => { load(); }, []);
  if (!data) return null;

  const save = async () => {
    try {
      const r = await apiRequest("/api/linkedin/sequence", { method: "PUT", body: { steps, personas, default_hook: hook, variant_b: noteB, workspace: data.workspace } });
      onMessage?.(r.new_version ? `Saved as version ${r.version}. People already part-way keep the steps they started with.` : "LinkedIn steps saved.");
      load();
    } catch (e) { onMessage?.(e.message); }
  };
  const reset = async () => {
    try {
      await apiRequest("/api/linkedin/sequence/reset", { method: "POST", body: { workspace: data.workspace } });
      onMessage?.(data.workspace === "main" ? "Back to the built-in steps." : "Back to Main's steps."); load();
    } catch (e) { onMessage?.(e.message); }
  };
  const copyIn = async () => {
    try {
      await apiRequest("/api/linkedin/sequence/copy", { method: "POST", body: { workspace: data.workspace } });
      onMessage?.("Copied into your workspace. Change the steps below and save."); load();
    } catch (e) { onMessage?.(e.message); }
  };
  const inherited = data.workspace !== "main" && !data.own;
  const move = (i, d) => setSteps((list) => { const out = [...list]; [out[i], out[i + d]] = [out[i + d], out[i]]; return out.map((s, j) => (j === 1 ? { ...s, after: "accepted" } : s)); });
  const addStep = () => setSteps([...steps, { kind: "message", after: "previous", days: 4, purpose: `Message ${steps.length}`, text: "Hi {first_name}, " }]);
  const setPersona = (i, key, value) => setPersonas(personas.map((p, j) => (i === j ? { ...p, [key]: value } : p)));

  return (
    <details className="card" style={{ marginBottom: 12 }} data-testid="li-sequence-editor">
      <summary><b>Steps &amp; personas</b> <small className="sub-inline">
        {inherited ? "Main's steps" : data.builtin ? "built-in steps" : `your steps · version ${data.version}`} · {steps.length} steps · {personas.length} personas
        {(isOwner() || data.workspace !== "main") && <> · <span className="pill" data-testid="li-ws">{data.workspace_name}</span></>}</small></summary>
      {inherited && <p className="tip" role="note">This workspace uses Main's LinkedIn steps (read-only here).
        {data.can_edit ? " Copy them into your workspace to change them; only this workspace's leads get your copy." : " An admin of this workspace can copy them in to change them."}
        {data.can_edit && <> <button type="button" className="btn" onClick={copyIn} data-testid="li-copy">Copy into my workspace</button></>}</p>}
      {!inherited && !data.can_edit && <p className="sub">Read-only: only an owner or an admin of this workspace changes these steps.</p>}
      <p className="sub">Placeholders: {"{first_name}"}, {"{hook}"} (the persona line below), {"{company}"}, {"{case_study_url}"} and any email placeholder such as {"{{my_company}}"} or {"{{custom.<field>}}"}.
        Changing days, the number of steps or the order while people are part-way creates a new version; they finish on the old one.</p>
      <label style={{ maxWidth: 320 }}>Preview as a person whose title is
        <input value={sampleTitle} onChange={(e) => setSampleTitle(e.target.value)} maxLength={120} />
      </label>
      {steps.map((s, i) => (
        <StepRow key={i} step={s} index={i} count={steps.length} readOnly={readOnly} sampleTitle={sampleTitle} workspace={data.workspace}
          onChange={(v) => setSteps(steps.map((x, j) => (i === j ? v : x)))} onRemove={() => setSteps(steps.filter((_, j) => j !== i))} onMove={(d) => move(i, d)} />
      ))}
      {!readOnly && steps.length < 10 && <button type="button" className="btn ghost" onClick={addStep}>Add a message</button>}

      <h4>Connection note B (<Abbr term="A/B test">A/B test</Abbr>)</h4>
      <textarea aria-label="Connection note B" rows={3} value={noteB} maxLength={600} disabled={readOnly} onChange={(e) => setNoteB(e.target.value)} style={{ width: "100%" }} />
      <Preview text={noteB} kind="connect" title={sampleTitle} workspace={data.workspace} />

      <h4>Personas</h4>
      <p className="sub">Matched on the person's job title, top to bottom; the first match gives the {"{hook}"} line. The pattern is a regular expression (for example <code>{"\\b(founder|ceo)\\b"}</code>).</p>
      {personas.map((p, i) => (
        <div key={i} className="row" style={{ flexWrap: "wrap", alignItems: "flex-end", marginBottom: 6 }}>
          <label style={{ flex: "0 0 120px" }}>Key<input value={p.key} disabled={readOnly} maxLength={30} onChange={(e) => setPersona(i, "key", e.target.value)} /></label>
          <label style={{ flex: 1, minWidth: 160 }}>Title pattern<input value={p.pattern} disabled={readOnly} maxLength={300} onChange={(e) => setPersona(i, "pattern", e.target.value)} /></label>
          <label style={{ flex: 2, minWidth: 200 }}>Hook<input value={p.hook} disabled={readOnly} maxLength={400} onChange={(e) => setPersona(i, "hook", e.target.value)} /></label>
          {!readOnly && <button type="button" className="btn ghost" onClick={() => setPersonas(personas.filter((_, j) => j !== i))} aria-label={`Remove persona ${p.key}`}>Remove</button>}
        </div>
      ))}
      {!readOnly && personas.length < 20 && <button type="button" className="btn ghost" onClick={() => setPersonas([...personas, { key: `persona_${personas.length + 1}`, pattern: "", hook: "" }])}>Add a persona</button>}
      <label>Hook for everyone else<input value={hook} disabled={readOnly} maxLength={400} onChange={(e) => setHook(e.target.value)} /></label>
      {!readOnly && (
        <div className="row" style={{ marginTop: 10 }}>
          <button type="button" className="btn" onClick={save}>Save LinkedIn steps</button>
          {data.workspace === "main" && !data.builtin && <button type="button" className="btn ghost" onClick={reset}>Use the built-in steps</button>}
          {data.workspace !== "main" && <button type="button" className="btn ghost" onClick={reset}>Use Main's steps again</button>}
        </div>
      )}
    </details>
  );
}
