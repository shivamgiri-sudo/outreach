import { useMemo, useRef, useState } from "react";
import { api } from "../../api.js";
import { FieldInput } from "./Fields.jsx";

// Same sample data the server uses for "Send test", so the preview matches what is sent.
const SAMPLE = {
  to: "+919812345678", phone: "+919812345678", email: "test.lead@example.com", subject: "Test from Outreach OS",
  text: 'Hello from Outreach OS. A "test" message.', name: "Test Lead", company: "Example Pvt Ltd", template: "first_after_yes",
  title: "Product demo — Example", start: "2026-11-05T10:00:00+05:30", end: "2026-11-05T10:30:00+05:30",
  join_url: "https://meet.example.com/test",
};
const PLACEHOLDER = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;
const jsonEscape = (v) => JSON.stringify(String(v)).slice(1, -1);
const formEscape = (v) => encodeURIComponent(String(v)).replace(/%20/g, "+");
const fill = (text, escape) => String(text || "").replace(PLACEHOLDER, (_, key) => escape(SAMPLE[key] ?? ""));

function preview(values, allowed) {
  const all = [values.url, values.body_template, ...(values.headers || []).map((h) => h.value)].join(" ");
  const unknown = [...new Set([...all.matchAll(PLACEHOLDER)].map((m) => m[1]).filter((k) => !allowed.includes(k)))];
  const method = values.method || "POST";
  const headers = Object.fromEntries((values.headers || []).filter((h) => h.key).map((h) => [h.key, h.secret ? "••••" : fill(h.value, (v) => v)]));
  let body = null;
  let problem = unknown.length ? `Unknown placeholder(s): ${unknown.map((u) => `{{${u}}}`).join(", ")}` : "";
  if (method !== "GET" && (values.body_type || "json") !== "none" && (values.body_template || "").trim()) {
    if (values.body_type === "form") body = fill(values.body_template, formEscape);
    else {
      body = fill(values.body_template, jsonEscape);
      try { body = JSON.stringify(JSON.parse(body), null, 2); } catch { problem = problem || "The body is not valid JSON once filled in. Put text placeholders inside quotes."; }
    }
  }
  const url = fill(values.url, encodeURIComponent);
  return { text: `${method} ${url || "(no address yet)"}\n${Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join("\n")}${body ? `\n\n${body}` : ""}`, problem };
}

export function CustomHttpBuilder({ provider, values, setValues }) {
  const bodyRef = useRef(null);
  const byKey = Object.fromEntries(provider.fields.map((f) => [f.key, f]));
  const allowed = provider.placeholders || Object.keys(SAMPLE);
  const set = (key) => (v) => setValues({ ...values, [key]: v });
  const shown = useMemo(() => preview(values, allowed), [values, allowed]);

  const insert = (name) => {
    const el = bodyRef.current;
    const current = values.body_template || "";
    const start = el?.selectionStart ?? current.length;
    const end = el?.selectionEnd ?? start;
    const token = `{{${name}}}`;
    setValues({ ...values, body_template: current.slice(0, start) + token + current.slice(end) });
    requestAnimationFrame(() => { if (el) { el.focus(); el.setSelectionRange(start + token.length, start + token.length); } });
  };
  const field = (key, extra = {}) => byKey[key] && (
    <FieldInput key={key} field={byKey[key]} value={values[key]} saved={provider.values?.[key]} onChange={set(key)} {...extra} />
  );

  return (
    <div className="custom-builder" data-testid="custom-builder">
      <div className="split-2">{field("method")}{field("body_type")}</div>
      {field("url")}
      {field("headers")}
      {field("body_template", { inputRef: bodyRef })}
      <p className="field-help">Click to insert at the cursor:</p>
      <div className="chips" role="group" aria-label="Placeholders">
        {allowed.map((p) => <button type="button" className="chip" key={p} onClick={() => insert(p)}>{`{{${p}}}`}</button>)}
      </div>
      <div className="split-2">{field("success_path")}{field("success_value")}</div>
      <div className="split-2">{field("id_path")}{field("join_url_path")}</div>
      <h4>Request preview (sample data, keys hidden)</h4>
      <pre data-testid="custom-preview">{shown.text}</pre>
      {shown.problem && <p className="result bad" role="alert">{shown.problem}</p>}
    </div>
  );
}

export function CustomWebhookMapper({ channel, provider, values, setValues }) {
  const [sample, setSample] = useState("");
  const [result, setResult] = useState(null);
  const set = (key) => (v) => setValues({ ...values, [key]: v });
  const run = async () => {
    try { setResult(await api.testChannel(channel, "custom_webhook", { sample, mapping: values })); }
    catch (e) { setResult({ ok: false, message: e.message }); }
  };
  return (
    <div className="custom-builder" data-testid="custom-mapper">
      <p className="sub">Tell the app where each piece sits in the provider's payload, using dot paths such as <code>data.from</code> or <code>messages.0.text</code>.</p>
      {provider.fields.map((f) => <FieldInput key={f.key} field={f} value={values[f.key]} saved={provider.values?.[f.key]} onChange={set(f.key)} />)}
      <label htmlFor="sample-payload">Paste a sample payload (JSON)</label>
      <textarea id="sample-payload" rows={6} value={sample} onChange={(e) => setSample(e.target.value)}
        placeholder='{"data": {"from": "919812345678", "text": "Yes, interested", "id": "abc"}}' />
      <button type="button" className="btn ghost" style={{ marginTop: 8 }} disabled={!sample.trim()} onClick={run}>Preview what the app reads</button>
      {result && (
        <div className={`tip ${result.ok ? "" : "error"}`} role="status" style={{ marginTop: 8 }}>
          {result.message}
          {result.extracted?.length > 0 && <pre>{JSON.stringify(result.extracted.map((m) => (
            m.type ? { type: m.type, email: m.email, id: m.provider_id } : { from: m.from_email || m.from_phone, text: m.text, id: m.message_id })), null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}
