// Left filter panel of Prospect search (Apollo-style). `value` is the filter object sent to /api/prospects/*.
import { useId, useState } from "react";

const LABEL = {
  owner: "Owner", founder: "Founder", c_suite: "C-suite", vp: "VP", head: "Head", director: "Director", manager: "Manager", senior: "Senior", entry: "Entry",
  hr: "HR", talent: "Talent / recruiting", sales: "Sales", marketing: "Marketing", engineering: "Engineering", finance: "Finance", operations: "Operations",
  it: "IT", leadership: "Leadership", other: "Other", verified: "Verified", valid: "Valid", catch_all: "Catch-all", risky: "Risky", invalid: "Invalid",
  unknown: "Unknown", hiring: "Hiring", funding: "Funding", news: "News", tech_change: "Tech change", expansion: "Expansion", leadership_change: "Leadership change",
};
export const label = (v) => LABEL[v] || v;
const splitText = (t) => String(t || "").split(",").map((x) => x.trim()).filter(Boolean);

export function countFilters(f) {
  return Object.entries(f).filter(([, v]) => (Array.isArray(v) ? v.length : v !== "" && v !== false && v != null)).length;
}

function Section({ title, count, open: start = false, children }) {
  const [open, setOpen] = useState(start);
  const id = useId();
  return (
    <div className="pf-section">
      <button type="button" className="pf-head" aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
        <span>{title}</span>{count > 0 && <span className="pill">{count}</span>}<span aria-hidden="true" className="pf-caret">{open ? "−" : "+"}</span>
      </button>
      {open && <div id={id} className="pf-body">{children}</div>}
    </div>
  );
}

function Checks({ options, value = [], onChange, limit = 10, name }) {
  const [all, setAll] = useState(false);
  const shown = all ? options : options.slice(0, limit);
  const toggle = (v) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  return (
    <div className="pf-checks" role="group" aria-label={name}>
      {shown.map((o) => {
        const v = typeof o === "string" ? o : o.value;
        return (
          <label key={v} className="pf-check">
            <input type="checkbox" checked={value.includes(v)} onChange={() => toggle(v)} />
            <span>{label(v)}</span>{o.count != null && <small className="sub">{o.count}</small>}
          </label>
        );
      })}
      {options.length > limit && <button type="button" className="linkish" onClick={() => setAll(!all)}>{all ? "Show fewer" : `Show all ${options.length}`}</button>}
    </div>
  );
}

function TextList({ label: text, value = [], onChange, placeholder, list }) {
  const [draft, setDraft] = useState(value.join(", "));
  const id = useId();
  return (
    <>
      <label htmlFor={id}>{text}</label>
      <input id={id} value={draft} placeholder={placeholder} list={list} onChange={(e) => setDraft(e.target.value)}
        onBlur={() => onChange(splitText(draft))} onKeyDown={(e) => { if (e.key === "Enter") onChange(splitText(draft)); }} />
    </>
  );
}

export default function ProspectFilters({ value, onChange, facets, kind, onClear }) {
  const set = (k) => (v) => onChange({ ...value, [k]: v });
  const f = facets || {};
  const people = kind === "people";
  const num = (k) => (e) => set(k)(e.target.value === "" ? "" : Number(e.target.value));
  return (
    <aside className="pf" aria-label="Filters" data-testid="prospect-filters">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>Filters</b>
        {countFilters(value) > 0 && <button type="button" className="linkish" onClick={onClear}>Clear all</button>}
      </div>
      {people && (
        <Section title="Job title" count={(value.title?.length || 0) + (value.title_exclude?.length || 0)} open>
          <TextList label="Title has any of" value={value.title} onChange={set("title")} placeholder="hr, talent, recruit" />
          <TextList label="Leave out titles with" value={value.title_exclude} onChange={set("title_exclude")} placeholder="intern, assistant" />
        </Section>
      )}
      {people && <Section title="Seniority" count={value.seniority?.length}><Checks name="Seniority" options={f.seniorities || []} value={value.seniority} onChange={set("seniority")} /></Section>}
      {people && <Section title="Department" count={value.department?.length}><Checks name="Department" options={f.departments || []} value={value.department} onChange={set("department")} /></Section>}
      <Section title="Industry" count={value.industry?.length}><Checks name="Industry" options={f.industries || []} value={value.industry} onChange={set("industry")} /></Section>
      <Section title="Company size" count={(value.employees?.length || 0)}><Checks name="Employees" options={f.employee_buckets || []} value={value.employees} onChange={set("employees")} /></Section>
      {(f.revenues || []).length > 0 && <Section title="Revenue" count={value.revenue?.length}><Checks name="Revenue" options={f.revenues} value={value.revenue} onChange={set("revenue")} /></Section>}
      <Section title="Location" count={(value.city?.length || 0) + (value.state?.length || 0) + (value.country?.length || 0)}>
        <TextList label="City" value={value.city} onChange={set("city")} placeholder="Pune, Mumbai" list="pf-cities" />
        <TextList label="State" value={value.state} onChange={set("state")} placeholder="Maharashtra" list="pf-states" />
        <TextList label="Country" value={value.country} onChange={set("country")} placeholder="India" list="pf-countries" />
        <datalist id="pf-cities">{(f.cities || []).map((c) => <option key={c.value} value={c.value} />)}</datalist>
        <datalist id="pf-states">{(f.states || []).map((c) => <option key={c.value} value={c.value} />)}</datalist>
        <datalist id="pf-countries">{(f.countries || []).map((c) => <option key={c.value} value={c.value} />)}</datalist>
      </Section>
      <Section title="Technologies" count={value.technologies?.length}><Checks name="Technologies" options={f.technologies || []} value={value.technologies} onChange={set("technologies")} /></Section>
      <Section title="Company keywords" count={value.keywords?.length}><TextList label="Keywords (any)" value={value.keywords} onChange={set("keywords")} placeholder="saas, export" /></Section>
      <Section title="Buying signals" count={(value.signal_kinds?.length || 0) + (value.signal_days ? 1 : 0)}>
        <Checks name="Signal kinds" options={f.signal_kinds || []} value={value.signal_kinds} onChange={set("signal_kinds")} />
        <label htmlFor="pf-sigdays">Within</label>
        <select id="pf-sigdays" value={value.signal_days || ""} onChange={num("signal_days")}>
          <option value="">Any time</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option>
        </select>
      </Section>
      {people && (
        <Section title="Email and phone" count={["verified_only", "has_email", "has_phone"].filter((k) => value[k]).length + (value.email_status?.length || 0)}>
          {[["verified_only", "Verified emails only"], ["has_email", "Has an email"], ["has_phone", "Has a phone"]].map(([k, t]) => (
            <label key={k} className="pf-check"><input type="checkbox" checked={!!value[k]} onChange={(e) => set(k)(e.target.checked)} /><span>{t}</span></label>
          ))}
          <Checks name="Email status" options={f.email_statuses || []} value={value.email_status} onChange={set("email_status")} />
        </Section>
      )}
      <Section title="Founded" count={(value.founded_min ? 1 : 0) + (value.founded_max ? 1 : 0)}>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input aria-label="Founded from year" inputMode="numeric" placeholder="From" value={value.founded_min || ""} onChange={num("founded_min")} />
          <input aria-label="Founded to year" inputMode="numeric" placeholder="To" value={value.founded_max || ""} onChange={num("founded_max")} />
        </div>
      </Section>
      <Section title="Data" count={(value.source?.length || 0) + (value.updated_days ? 1 : 0) + (value.not_in_crm ? 1 : 0)}>
        <label className="pf-check"><input type="checkbox" checked={!!value.not_in_crm} onChange={(e) => set("not_in_crm")(e.target.checked)} /><span>Not already in my leads</span></label>
        <label htmlFor="pf-upd">Updated within</label>
        <select id="pf-upd" value={value.updated_days || ""} onChange={num("updated_days")}>
          <option value="">Any time</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="180">180 days</option>
        </select>
        <div className="sub" style={{ marginTop: 8 }}>Source</div>
        <Checks name="Source" options={f.sources || []} value={value.source} onChange={set("source")} />
      </Section>
    </aside>
  );
}
