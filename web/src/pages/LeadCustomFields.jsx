// The owner's custom lead fields (Setup → Industry & fields): the drawer editor, the optional Leads columns and filters.
import { useEffect, useState } from "react";
import { apiRequest, canEditContacts, canWrite } from "../api.js";

let cache = null;
const listeners = new Set();
export function loadLeadFields(force = false) {
  if (!cache || force) {
    cache = apiRequest("/api/lead-fields").catch(() => ({ fields: [], types: [] }));
    cache.then((r) => listeners.forEach((fn) => fn(r)));
  }
  return cache;
}
export function useLeadFields() {
  const [data, setData] = useState({ fields: [], types: [] });
  useEffect(() => {
    listeners.add(setData);
    loadLeadFields().then(setData);
    return () => listeners.delete(setData);
  }, []);
  return data;
}

const INPUT = { number: "number", date: "date", url: "url", email: "email", phone: "tel" };

export function showValue(field, value) {
  if (value === undefined || value === null || value === "") return "";
  if (field.type === "number" && typeof value === "number") return value.toLocaleString("en-IN");
  return String(value);
}

function FieldInput({ field, value, onChange, id }) {
  if (field.type === "select") {
    return (
      <select id={id} value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {field.options.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
  }
  return <input id={id} type={INPUT[field.type] || "text"} value={value ?? ""} step={field.type === "number" ? "any" : undefined}
    onChange={(e) => onChange(e.target.value)} />;
}

// Drawer → Data tab. Each save is logged like a contact correction (who, when, old → new).
export function LeadCustomData({ lead, onSaved }) {
  const { fields } = useLeadFields();
  const [draft, setDraft] = useState({});
  const [editing, setEditing] = useState(false);
  const [msg, setMsg] = useState("");
  useEffect(() => { setDraft({ ...(lead.custom || {}) }); setEditing(false); setMsg(""); }, [lead.id]);
  if (!fields.length) return null;
  const save = async (e) => {
    e.preventDefault();
    const changes = Object.fromEntries(fields.map((f) => [f.key, draft[f.key] ?? ""]).filter(([k, v]) => String(v) !== String((lead.custom || {})[k] ?? "")));
    if (!Object.keys(changes).length) { setEditing(false); return; }
    try {
      const saved = await apiRequest(`/api/leads/${lead.id}/custom`, { method: "PATCH", body: { values: changes } });
      onSaved(saved); setEditing(false); setMsg(`Saved ${saved.changed.length} field${saved.changed.length === 1 ? "" : "s"}.`);
    } catch (err) { setMsg(err.message); }
  };
  return (
    <section data-testid="lead-custom-fields">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3>Details</h3>
        {canWrite() && canEditContacts() && !editing && <button type="button" className="btn ghost" onClick={() => setEditing(true)}>Edit details</button>}
      </div>
      {msg && <p className="sub" role="status">{msg}</p>}
      {editing && canWrite() ? (
        <form onSubmit={save}>
          {fields.map((f) => (
            <label key={f.key} htmlFor={`cf-${f.key}`}>{f.label}
              <FieldInput id={`cf-${f.key}`} field={f} value={draft[f.key]} onChange={(v) => setDraft({ ...draft, [f.key]: v })} />
            </label>
          ))}
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn" type="submit">Save details</button>
            <button className="btn ghost" type="button" onClick={() => { setDraft({ ...(lead.custom || {}) }); setEditing(false); }}>Cancel</button>
          </div>
        </form>
      ) : (
        <dl className="kv">
          {fields.map((f) => (
            <div key={f.key} className="row" style={{ justifyContent: "space-between" }}>
              <dt className="sub" style={{ margin: 0 }}>{f.label}</dt>
              <dd style={{ margin: 0 }}>{f.type === "url" && lead.custom?.[f.key]
                ? <a href={lead.custom[f.key]} target="_blank" rel="noopener noreferrer">{lead.custom[f.key]}</a>
                : showValue(f, lead.custom?.[f.key]) || <small className="sub">—</small>}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

// Leads page: filter value per filterable field. Numbers and dates filter by a minimum / maximum, the rest by text or choice.
export function customMatches(lead, filters, fields) {
  return fields.every((f) => {
    const want = filters[f.key];
    if (!want || (typeof want === "object" && !want.min && !want.max)) return true;
    const value = lead.custom?.[f.key];
    if (f.type === "number" || f.type === "date") {
      if (value === undefined || value === null || value === "") return false;
      const v = f.type === "number" ? Number(value) : String(value);
      const lo = want.min ? (f.type === "number" ? Number(want.min) : want.min) : null;
      const hi = want.max ? (f.type === "number" ? Number(want.max) : want.max) : null;
      return (lo === null || v >= lo) && (hi === null || v <= hi);
    }
    if (f.type === "select") return String(value || "") === want;
    return String(value || "").toLowerCase().includes(String(want).toLowerCase());
  });
}

export function CustomFilters({ fields, value, onChange }) {
  const shown = fields.filter((f) => f.filterable);
  if (!shown.length) return null;
  const set = (key, v) => onChange({ ...value, [key]: v });
  return shown.map((f) => {
    if (f.type === "select") {
      return (
        <select key={f.key} aria-label={`Filter by ${f.label}`} value={value[f.key] || ""} onChange={(e) => set(f.key, e.target.value)}>
          <option value="">Any {f.label.toLowerCase()}</option>
          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      );
    }
    if (f.type === "number" || f.type === "date") {
      const cur = value[f.key] || {};
      const type = f.type === "number" ? "number" : "date";
      return (
        <span key={f.key} className="row" style={{ gap: 4, margin: 0 }}>
          <input type={type} aria-label={`${f.label} from`} placeholder={`${f.label} from`} value={cur.min || ""} style={{ width: 130 }}
            onChange={(e) => set(f.key, { ...cur, min: e.target.value })} />
          <input type={type} aria-label={`${f.label} to`} placeholder="to" value={cur.max || ""} style={{ width: 100 }}
            onChange={(e) => set(f.key, { ...cur, max: e.target.value })} />
        </span>
      );
    }
    return <input key={f.key} aria-label={`Filter by ${f.label}`} placeholder={f.label} value={value[f.key] || ""} style={{ width: 140 }}
      onChange={(e) => set(f.key, e.target.value)} />;
  });
}
