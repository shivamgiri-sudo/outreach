import { BAND_LABELS, STAGE_LABELS, sourceId, sourceLabel, stageLabel } from "../labels.js";
import { SALES_STATUSES } from "../salesStage.js";
import { CONTACT_STATUSES, contactOf, isFresh } from "../contactStatus.js";
import { LinkedInStageFilter, LI_STAGES } from "./LeadLinkedInStage.jsx";
import { AgeingFilter } from "./LeadAgeing.jsx";
import { CustomFilters } from "./LeadCustomFields.jsx";
import { isWaiting } from "./LeadFirstTouch.jsx";
import { HAS_FIELDS, hasDetail } from "./leadFilters.js";
import LeadViews from "./LeadViews.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import Icon from "../icons.jsx";
import { Button, Chip, Input, Popover, Select } from "../ui/index.js";

function HasFilter({ leads, value, onChange }) {
  const count = (w) => leads.filter((l) => hasDetail(l, w)).length;
  const toggle = (w) => onChange(value.includes(w) ? value.filter((x) => x !== w) : [...value, w]);
  const label = value.length ? `With ${value.map((w) => HAS_FIELDS.find(([k]) => k === w)[1].split(" ")[0].replace("Phone", "phone").replace("Email", "email")).join(" + ")}` : "Any contact details";
  return (
    <details className="multi-filter">
      <summary aria-label="Filter by contact details" data-testid="has-filter">{label}</summary>
      <div className="multi-pop" role="group" aria-label="Contact details">
        {HAS_FIELDS.map(([w, text]) => (
          <label key={w} className="check-row">
            <input type="checkbox" checked={value.includes(w)} onChange={() => toggle(w)} /> With {text.toLowerCase()} <small>({count(w)})</small>
          </label>
        ))}
        <small className="sub">Tick more than one to see leads that have all of them (e.g. phone + email).</small>
        {value.length > 0 && <button type="button" className="linkish" onClick={() => onChange([])}>Clear</button>}
      </div>
    </details>
  );
}

function SourceFilter({ leads, value, onChange }) {
  const counts = {};
  leads.forEach((l) => { const id = sourceId(l); counts[id] = (counts[id] || 0) + 1; });
  const ids = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  const toggle = (id) => onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  return (
    <details className="multi-filter">
      <summary aria-label="Filter by source">{value.length ? `${value.length} source${value.length > 1 ? "s" : ""}` : "All sources"}</summary>
      <div className="multi-pop" role="group" aria-label="Sources">
        {ids.map((id) => (
          <label key={id} className="check-row">
            <input type="checkbox" checked={value.includes(id)} onChange={() => toggle(id)} /> {sourceLabel(id)} <small>({counts[id]})</small>
          </label>
        ))}
        {value.length > 0 && <button type="button" className="linkish" onClick={() => onChange([])}>Clear sources</button>}
      </div>
    </details>
  );
}

const OWNER_LABEL = { __me: "My leads", __none: "Unassigned" };
const TYPE_LABEL = { valid: "Valid", invalid: "Invalid lead", consultant: "Consultant lead" };

// Everything the Leads page filters by is in `f` (values) and `set` (setters), from useLeadFilters().
export default function LeadToolbar({ leads, shown, f, set, owners, allTags, rules, customFields, viewFilters, applyView, clearFilters, filtered, customOn, nMore, tools }) {
  const chips = [];
  const add = (key, text, clear) => chips.push({ key, text, clear });
  if (f.query.trim()) add("q", `Search: ${f.query.trim()}`, () => set.query(""));
  if (f.stage) add("stage", `Stage: ${stageLabel(f.stage)}`, () => set.stage(""));
  if (f.salesStage) add("sales", `Sales stage: ${f.salesStage}`, () => set.salesStage(""));
  if (f.band) add("band", BAND_LABELS[f.band] || f.band, () => set.band(""));
  if (f.srcFilter.length) add("src", `Source: ${f.srcFilter.map((s) => sourceLabel(s)).join(", ")}`, () => set.srcFilter([]));
  if (f.inboundOnly) add("inb", "Inbound only", () => set.inboundOnly(false));
  if (f.ownerFilter) add("owner", `Owner: ${OWNER_LABEL[f.ownerFilter] || f.ownerFilter}`, () => set.ownerFilter(""));
  if (f.tagFilter) add("tag", `Tag: ${f.tagFilter}`, () => set.tagFilter(""));
  if (f.hasFilter.length) add("has", `Has: ${f.hasFilter.join(" + ")}`, () => set.hasFilter([]));
  if (f.liStage) add("li", `LinkedIn: ${(LI_STAGES.find(([k]) => k === f.liStage) || [, "no link"])[1]}`, () => set.liStage(""));
  if (f.contact) add("contact", f.contact === "fresh" ? "Fresh to attempt" : `Contact: ${(CONTACT_STATUSES.find(([k]) => k === f.contact) || [, f.contact])[1]}`, () => set.contact(""));
  if (f.leadType) add("type", `Type: ${TYPE_LABEL[f.leadType] || f.leadType}`, () => set.leadType(""));
  if (f.ageing) add("age", `Ageing: ${f.ageing}`, () => set.ageing(""));
  if (f.waitingOnly) add("wait", "Waiting for first touch", () => set.waitingOnly(false));
  if (customOn) add("custom", "Custom fields", () => set.customFilter({}));

  return (
    <div className="lead-toolbar" role="search">
      <div className="lead-filter-row">
        <div className="lead-search"><Input icon="search" aria-label="Search leads" placeholder="Search company, person, email or phone" value={f.query} onChange={(e) => set.query(e.target.value)} onClear={() => set.query("")} /></div>
        <Select aria-label="Filter by stage" value={f.stage} onChange={(e) => set.stage(e.target.value)}>
          <option value="">All stages</option>
          {[...new Set([...Object.keys(STAGE_LABELS), ...leads.map((l) => l.stage).filter(Boolean)])].map((k) => {
            const n = leads.filter((l) => l.stage === k).length;
            return <option key={k} value={k} disabled={!n && k !== f.stage}>{stageLabel(k)} ({n})</option>;
          })}
        </Select>
        <Select aria-label="Filter by owner" value={f.ownerFilter} onChange={(e) => set.ownerFilter(e.target.value)}>
          <option value="">All owners</option>
          <option value="__me">My leads</option>
          <option value="__none">Unassigned</option>
          {owners.map((n) => <option key={n} value={n}>{n}</option>)}
        </Select>
        <Select aria-label="Filter by contact status" value={f.contact} onChange={(e) => set.contact(e.target.value)} data-testid="contact-filter">
          <option value="">Any contact status</option>
          {CONTACT_STATUSES.map(([k, v]) => <option key={k} value={k}>{v} ({leads.filter((l) => contactOf(l).status === k).length})</option>)}
        </Select>
        <AgeingFilter leads={leads} value={f.ageing} onChange={set.ageing} rules={rules} />
        <Popover ariaLabel="More filters" align="right" label={<><Icon name="sliders" size={16} />More filters{nMore > 0 && <span className="pill info">{nMore}</span>}</>}>
          <div className="ui-pop-grid">
            <label>Sales stage
              <Select aria-label="Filter by sales stage" value={f.salesStage} onChange={(e) => set.salesStage(e.target.value)} data-testid="sales-stage-filter">
                <option value="">All sales stages</option>
                {SALES_STATUSES.map((k) => <option key={k} value={k}>{k}</option>)}
              </Select></label>
            <label>Temperature
              <Select aria-label="Filter by temperature" value={f.band} onChange={(e) => set.band(e.target.value)}>
                <option value="">Hot, warm and cold</option>
                {Object.entries(BAND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select></label>
            <label>Lead type
              <Select aria-label="Filter by lead type" value={f.leadType} onChange={(e) => set.leadType(e.target.value)} data-testid="quality-filter">
                <option value="">Any lead type</option>
                <option value="valid">Valid ({leads.filter((l) => !l.lead_quality).length})</option>
                <option value="invalid">Invalid lead ({leads.filter((l) => l.lead_quality?.kind === "invalid").length})</option>
                <option value="consultant">Consultant lead ({leads.filter((l) => l.lead_quality?.kind === "consultant").length})</option>
              </Select></label>
            <label>LinkedIn stage<LinkedInStageFilter value={f.liStage} onChange={set.liStage} /></label>
            {allTags.length > 0 && <label>Tag
              <Select aria-label="Filter by tag" value={f.tagFilter} onChange={(e) => set.tagFilter(e.target.value)}>
                <option value="">All tags</option>
                {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select></label>}
            <div className="field"><span className="field-label">Source</span><SourceFilter leads={leads} value={f.srcFilter} onChange={set.srcFilter} /></div>
            <div className="field"><span className="field-label">Contact details</span><HasFilter leads={leads} value={f.hasFilter} onChange={set.hasFilter} /></div>
            <CustomFilters fields={customFields} value={f.customFilter} onChange={set.customFilter} />
            <label className="check-row"><input type="checkbox" checked={f.inboundOnly} onChange={(e) => set.inboundOnly(e.target.checked)} /> Inbound only</label>
          </div>
        </Popover>
      </div>

      <div className="lead-view-row">
        <button type="button" className={`tab-chip${f.contact === "fresh" ? " on" : ""}`} aria-pressed={f.contact === "fresh"} onClick={() => set.contact(f.contact === "fresh" ? "" : "fresh")}
          title="Never contacted, still open, not on the do-not-contact list, with a phone or email" data-testid="fresh-to-attempt">
          Fresh to attempt ({leads.filter(isFresh).length})
        </button>
        <button type="button" className={`tab-chip${f.waitingOnly ? " on" : ""}`} aria-pressed={f.waitingOnly} onClick={() => set.waitingOnly(!f.waitingOnly)} data-testid="waiting-first-touch">
          Waiting for first touch ({leads.filter(isWaiting).length})
        </button>
        <span className="lead-view-sep" aria-hidden="true" />
        <LeadViews filters={viewFilters} onApply={applyView} onClear={clearFilters} />
        <span className="lead-count" role="status">Showing {shown.length} of {leads.length}</span>
        {tools && <span className="lead-tools">{tools}</span>}
        <span className="dl-group lead-download">
          <DownloadButton path={exportPath("leads")} label="Download all" title="Every lead as a CSV that opens in Excel" testId="download-leads-all" />
          {filtered && <DownloadButton path={exportPath("leads", { q: f.query.trim(), stage: f.stage, band: f.band, source: f.srcFilter.join(","), inbound: f.inboundOnly ? "1" : "", tag: f.tagFilter })}
            body={f.onlyIds || f.ownerFilter || customOn || f.hasFilter.length || f.liStage || f.waitingOnly ? { ids: shown.map((l) => l.id) } : undefined} label={`Download these ${shown.length}`}
            title="Only the leads that match the search and filters" testId="download-leads-shown" />}
        </span>
      </div>

      {chips.length > 0 && (
        <div className="ui-chips lead-chips" aria-label="Active filters">
          {chips.map((c) => <Chip key={c.key} onRemove={c.clear} label={`Remove filter ${c.text}`}>{c.text}</Chip>)}
          <Button variant="ghost" size="sm" onClick={clearFilters}>Clear all</Button>
        </div>
      )}
    </div>
  );
}
