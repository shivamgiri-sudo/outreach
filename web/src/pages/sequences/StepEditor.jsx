import { useRef } from "react";
import { PLACEHOLDERS, humanDay, lintStep } from "./lint.js";

const MAX_VARIANTS = 3;
const NEXT_NAMES = ["B", "C", "D"];

function Lint({ items }) {
  if (!items.length) return <p className="sub" style={{ color: "var(--ok)" }}>No problems found.</p>;
  return (
    <ul style={{ margin: "4px 0", paddingLeft: 18 }} aria-label="Checks for this step">
      {items.map((x, i) => (
        <li key={i} style={{ color: x.level === "error" ? "var(--hot)" : "var(--warn)", fontSize: 13 }}>
          <b>{x.level === "error" ? "Fix: " : "Tip: "}</b>{x.text}
        </li>
      ))}
    </ul>
  );
}

// One step: day, name, subject, body, A/B variants, checks. `readOnly` shows the same fields disabled.
export default function StepEditor({ step, index, prev, readOnly, onChange, onRemove, canRemove, previewVariant, onPreviewVariant }) {
  const bodyRef = useRef(null);
  const set = (patch) => onChange({ ...step, ...patch });
  const insert = (name) => {
    const el = bodyRef.current;
    const token = `{{${name}}}`;
    const body = String(step.body || "");
    const at = el && typeof el.selectionStart === "number" ? el.selectionStart : body.length;
    set({ body: body.slice(0, at) + token + body.slice(at) });
    requestAnimationFrame(() => { if (el) { el.focus(); el.selectionStart = el.selectionEnd = at + token.length; } });
  };
  const variants = step.variants || [];
  const setVariant = (i, patch) => set({ variants: variants.map((v, j) => (j === i ? { ...v, ...patch } : v)) });
  const addVariant = () => {
    const name = NEXT_NAMES.find((n) => !variants.some((v) => v.name === n)) || `V${variants.length + 2}`;
    set({ variants: [...variants, { name, subject: step.subject || "", body: "" }] });
  };
  const checks = lintStep(step, prev);
  return (
    <div className="card" style={{ marginBottom: 12, padding: 16 }}>
      <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <b>Step {index + 1} · Day {humanDay(step.day)}</b>
        {!readOnly && canRemove && <button type="button" className="btn ghost" onClick={onRemove}>Remove step</button>}
      </div>
      <div className="split-2">
        <label>Send on day (Day 1 = first email)
          <input type="number" min={1} max={366} disabled={readOnly || index === 0} value={humanDay(step.day)}
            onChange={(e) => set({ day: Math.max(0, Number(e.target.value || 1) - 1) })} />
        </label>
        <label>Step name<input disabled={readOnly} maxLength={120} value={step.name || ""} onChange={(e) => set({ name: e.target.value })} /></label>
      </div>
      <label>Subject (A)<input disabled={readOnly} maxLength={200} value={step.subject || ""} onChange={(e) => set({ subject: e.target.value })} /></label>
      <label>Email (A)
        <textarea ref={bodyRef} disabled={readOnly} rows={10} value={step.body || ""} onChange={(e) => set({ body: e.target.value })} />
      </label>
      {!readOnly && (
        <div className="row" style={{ flexWrap: "wrap", gap: 6, margin: "4px 0 8px" }} aria-label="Insert a placeholder">
          {PLACEHOLDERS.map((p) => <button type="button" key={p} className="chip" onClick={() => insert(p)}>{`{{${p}}}`}</button>)}
        </div>
      )}
      <details open={variants.length > 0}>
        <summary>A/B test ({variants.length ? `${variants.length + 1} versions` : "off"}){step.winner ? ` · winner: ${step.winner}` : ""}</summary>
        <p className="sub">Leads are split evenly between A and the variants (fixed per lead). Leave a variant field empty to keep A's text.</p>
        {variants.map((v, i) => (
          <div key={i} className="inline-form">
            <div className="split-2">
              <label>Variant name<input disabled={readOnly} maxLength={40} value={v.name} onChange={(e) => setVariant(i, { name: e.target.value })} /></label>
              <label>Subject ({v.name || "?"})<input disabled={readOnly} maxLength={200} value={v.subject || ""} onChange={(e) => setVariant(i, { subject: e.target.value })} /></label>
            </div>
            <label>Email ({v.name || "?"}, optional)<textarea disabled={readOnly} rows={5} value={v.body || ""} placeholder="Empty = same email as A" onChange={(e) => setVariant(i, { body: e.target.value })} /></label>
            {!readOnly && <div><button type="button" className="btn ghost" onClick={() => set({ variants: variants.filter((_, j) => j !== i) })}>Remove variant {v.name}</button></div>}
          </div>
        ))}
        {!readOnly && variants.length < MAX_VARIANTS && <button type="button" className="btn ghost" onClick={addVariant}>Add a variant</button>}
      </details>
      <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 8 }} role="group" aria-label="Preview version">
        <span className="sub">Preview:</span>
        {["A", ...variants.map((v) => v.name)].map((n) => (
          <button type="button" key={n} className={`chip${previewVariant === n ? " on" : ""}`} onClick={() => onPreviewVariant(n)}>{n}</button>
        ))}
      </div>
      <Lint items={checks} />
    </div>
  );
}
