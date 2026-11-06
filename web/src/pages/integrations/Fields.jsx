import { useState } from "react";
import { copyText } from "../../clipboard.js";
import { FieldHint } from "../../GuidePanel.jsx";

export const STATUS = {
  configured: ["ok", "Connected"],
  incomplete: ["warn", "Missing details"],
  not_configured: ["", "Not set up"],
  error: ["err", "Error"],
};

export function StatusPill({ status, oauth, saved }) {
  const [tone, label] = STATUS[status] || STATUS.not_configured;
  const text = status === "incomplete" && oauth && saved ? "Not connected yet" : label;
  return <span className={`pill ${tone}`} data-testid="status-pill">{text}</span>;
}

export function CopyRow({ label, value, testid }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-block">
      {label && <span className="copy-label">{label}</span>}
      <div className="copy-row">
        <code data-testid={testid}>{value}</code>
        <button type="button" className="btn ghost" onClick={async () => { setCopied(await copyText(value)); setTimeout(() => setCopied(false), 1800); }}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

function SecretInput({ id, value, placeholder, onChange, a11y }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="secret-wrap">
      <input id={id} type={shown ? "text" : "password"} autoComplete="off" value={value} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)} {...a11y} />
      <button type="button" className="btn ghost" aria-pressed={shown} onClick={() => setShown(!shown)}>{shown ? "Hide" : "Show"}</button>
    </div>
  );
}

export function HeadersEditor({ id, rows, onChange }) {
  const list = rows && rows.length ? rows : [];
  const set = (i, patch) => onChange(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div id={id} className="headers-editor">
      {list.map((row, i) => (
        <div className="kv-row" key={i}>
          <input aria-label={`Header ${i + 1} name`} placeholder="Authorization" value={row.key || ""} onChange={(e) => set(i, { key: e.target.value })} />
          <input aria-label={`Header ${i + 1} value`} placeholder="Bearer your-key" type={row.secret ? "password" : "text"} autoComplete="off"
            value={row.value || ""} onChange={(e) => set(i, { value: e.target.value })} />
          <label className="row kv-secret"><input type="checkbox" checked={!!row.secret} onChange={(e) => set(i, { secret: e.target.checked })} />secret</label>
          <button type="button" className="btn ghost danger" aria-label={`Remove header ${i + 1}`} onClick={() => onChange(list.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button type="button" className="btn ghost" onClick={() => onChange([...list, { key: "", value: "", secret: false }])}>+ Add header</button>
    </div>
  );
}

// One form field from the catalogue: text, url, email, number, phone, select, textarea, headers; secrets get show / hide.
// `guide` is the field help from the setup guides: what the value looks like, and a friendly check once the box is left.
export function FieldInput({ field, value, saved, onChange, inputRef, guide }) {
  const id = `f-${field.key}`;
  const [touched, setTouched] = useState(false);
  const placeholder = field.secret && saved ? `${saved} (saved; leave empty to keep)` : field.placeholder || "";
  const described = [field.help && `${id}-help`, guide && `${id}-hint`, guide && `${id}-problem`].filter(Boolean).join(" ") || undefined;
  const a11y = { "aria-describedby": described, onBlur: () => setTouched(true) };
  let control;
  if (field.kind === "headers") control = <HeadersEditor id={id} rows={value} onChange={onChange} />;
  else if (field.secret) control = <SecretInput id={id} value={value || ""} placeholder={placeholder} onChange={onChange} a11y={a11y} />;
  else if (field.kind === "select") {
    control = (
      <select id={id} value={value || field.default || field.options[0]} onChange={(e) => onChange(e.target.value)} aria-describedby={described}>
        {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  } else if (field.kind === "textarea") {
    control = <textarea id={id} ref={inputRef} rows={4} value={value || ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} {...a11y} />;
  } else {
    const type = { email: "email", number: "text", url: "url", phone: "tel" }[field.kind] || "text";
    control = <input id={id} type={type} inputMode={field.kind === "number" ? "numeric" : undefined} value={value || ""}
      placeholder={placeholder} onChange={(e) => onChange(e.target.value)} {...a11y} />;
  }
  return (
    <div className="field">
      <label htmlFor={id}>{field.label}{field.required && <span aria-hidden="true"> *</span>}</label>
      {control}
      {field.help && <p className="field-help" id={`${id}-help`}>{field.help}</p>}
      <FieldHint id={id} help={guide} value={value} touched={touched} />
    </div>
  );
}

// Starting values for the form: defaults, then saved plain values. Secrets start empty (the saved one is kept).
export function initialValues(provider) {
  const out = {};
  for (const f of provider.fields) {
    const saved = provider.values?.[f.key];
    if (f.kind === "headers") out[f.key] = saved || [];
    else if (f.secret) out[f.key] = "";
    else out[f.key] = saved ?? f.default ?? "";
  }
  return out;
}

export function missingRequired(provider, values) {
  return provider.fields.filter((f) => f.required && !(values[f.key] || (f.secret && provider.values?.[f.key]) || f.default)).map((f) => f.label);
}
