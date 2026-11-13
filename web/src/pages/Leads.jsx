import { useEffect, useMemo, useState } from "react";
import { api, canWrite, EXCEL_ACCEPT, isOwner, readLeadFile } from "../api.js";
import { EmptyArt } from "../states.jsx";
import { BAND_LABELS, STAGE_LABELS, STAGE_ORDER, sourceId, sourceLabel, sourceOptions, stageLabel, useSourceRegistry } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";
import ImportWizard from "./ImportWizard.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import { useHashParam } from "../route.js";
import { useConfirm, usePrompt } from "../Dialog.jsx";
import FindLeadsDrawer, { FindLeadsCard, takePendingLeadIds } from "./FindLeads.jsx";
import LeadBulkBar from "./LeadBulkBar.jsx";
import LeadViews from "./LeadViews.jsx";
import { istDateLabel, istLabel } from "../time.js";
import { CustomFilters, customMatches, showValue, useLeadFields } from "./LeadCustomFields.jsx";
import PhoneTag from "./PhoneTag.jsx";
import { SALES_STATUSES, salesStageOf } from "../salesStage.js";
import { LinkedInCell, LinkedInStageFilter, liStageMatches } from "./LeadLinkedInStage.jsx";
import HelpLink from "../components/HelpLink.jsx";
import Abbr, { Stage } from "../components/Abbr.jsx";
import { FirstTouchTimer, isWaiting } from "./LeadFirstTouch.jsx";
import { CONTACT_STATUSES, contactOf, isFresh } from "../contactStatus.js";

const SAMPLE = "company,contact_name,title,email,phone,website,open_roles,company_size,country\n";
const SHEET_SOURCES = { "Team sheet — Leads tab": "leads", "Team sheet — Demo Shared tab": "demo", "Team sheet — Decision Makers tab": "decision_makers" };

const SORTS = {
  score: (a, b) => b.score - a.score,
  company: (a, b) => (a.company || a.domain || "").localeCompare(b.company || b.domain || ""),
  stage: (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage),
  activity: (a, b) => (b.last_activity_at || "").localeCompare(a.last_activity_at || ""),
  due: (a, b) => (a.next_task_due || "9999").localeCompare(b.next_task_due || "9999"),
};

// Owner filter values: "" everyone, "__me" the signed-in person, "__none" nobody yet, else a name.
function ownerMatches(lead, owner, me) {
  if (!owner) return true;
  if (owner === "__none") return !lead.owner;
  if (owner === "__me") return Boolean(lead.owner) && (lead.owner === me.name || lead.owner === me.email);
  return lead.owner === owner;
}

// A phone typed the way people write it ("+91 98907 79946", "098907-79946") finds the stored "+919890779946".
export function phoneMatches(phone, query) {
  const want = String(query || "").replace(/\D/g, "").replace(/^0+/, "");
  return want.length >= 6 && /^[\d\s()+.-]+$/.test(String(query).trim()) && String(phone || "").replace(/\D/g, "").includes(want);
}

// "Has" filter: every ticked detail must be on the lead (tick Phone + Email for leads with both).
export const HAS_FIELDS = [["phone", "Phone number"], ["email", "Email"], ["linkedin", "LinkedIn profile"]];
export function hasDetail(lead, what) {
  if (what === "phone") return Boolean(String(lead.phone || "").trim());
  if (what === "email") return Boolean(String(lead.email || "").trim());
  if (what === "linkedin") return /linkedin\.com\/(in|pub)\//i.test(String(lead.linkedin_url || ""));
  return true;
}

function HasFilter({ leads, value, onChange }) {
  const count = (w) => leads.filter((l) => hasDetail(l, w)).length;
  const toggle = (w) => onChange(value.includes(w) ? value.filter((x) => x !== w) : [...value, w]);
  const label = value.length ? `With ${value.map((w) => HAS_FIELDS.find(([k]) => k === w)[1].split(" ")[0].replace("Phone", "phone").replace("Email", "email")).join(" + ")}` : "Any contact details";
  return (
    <details className="multi-filter">
      <summary aria-label="Filter by contact details" data-testid="has-filter">{label}</summary>
      <div className="multi-pop" role="group" aria-label="Contact details">
        {HAS_FIELDS.map(([w, text]) => (
          <label key={w} className="row" style={{ margin: 0 }}>
            <input type="checkbox" checked={value.includes(w)} onChange={() => toggle(w)} /> With {text.toLowerCase()} <small>({count(w)})</small>
          </label>
        ))}
        <small className="sub">Tick more than one to see leads that have all of them (e.g. phone + email).</small>
        {value.length > 0 && <button type="button" className="linkish" onClick={() => onChange([])}>Clear</button>}
      </div>
    </details>
  );
}

function matches(lead, query, stage, band, onlyIds, sources = [], inboundOnly = false, owner = "", tag = "", me = {}, has = []) {
  const q = query.trim().toLowerCase();
  const text = [lead.company, lead.domain, lead.contact_name, lead.email, lead.phone, lead.role].join(" ").toLowerCase();
  return (!q || text.includes(q) || phoneMatches(lead.phone, q)) && (!stage || lead.stage === stage) && (!band || lead.band === band) && (!onlyIds || onlyIds.includes(lead.id))
    && (!sources.length || sources.includes(sourceId(lead))) && (!inboundOnly || Boolean(lead.inbound)) && ownerMatches(lead, owner, me)
    && (!tag || (lead.tags || []).some((t) => String(t).toLowerCase() === tag.toLowerCase()))
    && has.every((w) => hasDetail(lead, w));
}

// A short plain-English line about the campaign, ad or form behind a lead (shown as a tooltip and in the drawer).
export function attributionText(a = {}) {
  const parts = [a.campaign_name || a.utm_campaign, a.adset_name, a.ad_name, a.form_name || (a.form_id && `form ${a.form_id}`), a.utm_source && `source ${a.utm_source}`]
    .filter(Boolean);
  return [...new Set(parts)].join(" · ");
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
          <label key={id} className="row" style={{ margin: 0 }}>
            <input type="checkbox" checked={value.includes(id)} onChange={() => toggle(id)} /> {sourceLabel(id)} <small>({counts[id]})</small>
          </label>
        ))}
        {value.length > 0 && <button type="button" className="linkish" onClick={() => onChange([])}>Clear sources</button>}
      </div>
    </details>
  );
}

// A plain link can't send the login token, so fetch the file with it and save it.
async function downloadApolloList() {
  const blob = await api.download("/api/enrich/apollo-export");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "apollo-enrich-list.csv"; a.click();
  URL.revokeObjectURL(a.href);
}

export default function Leads({ go }) {
  const [leads, setLeads] = useState([]);
  const [open, setOpen] = useHashParam("lead");          // #leads?lead=<id>: refresh, Back and shared links keep the drawer
  const confirm = useConfirm();
  const ask = usePrompt();
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState("");
  const [band, setBand] = useState("");
  const [srcFilter, setSrcFilter] = useState([]);
  const [inboundOnly, setInboundOnly] = useState(false);
  const [ownerFilter, setOwnerFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [hasFilter, setHasFilter] = useState([]);
  const [liStage, setLiStage] = useState("");
  const [salesStage, setSalesStage] = useState("");
  const [waitingOnly, setWaitingOnly] = useState(false);
  const [contact, setContact] = useState("");        // "" any, never | tried | connected | replied | fresh
  const [customFilter, setCustomFilter] = useState({});
  const { fields: customFields } = useLeadFields();
  const tableFields = customFields.filter((f) => f.show_in_table);
  const customOn = Object.values(customFilter).some((v) => (typeof v === "object" ? v.min || v.max : v));
  const [owners, setOwners] = useState([]);
  const [me, setMe] = useState({ name: "", email: "" });
  useEffect(() => {
    api.leadOwners().then((r) => setOwners(r.owners)).catch(() => {});
    api.me().then((r) => setMe({ name: r.name, email: r.email })).catch(() => {});
  }, []);
  useSourceRegistry();
  const [sort, setSort] = useState("score");
  const [picked, setPicked] = useState([]);
  const [finding, setFinding] = useState(false);
  const [onlyIds, setOnlyIds] = useState(() => takePendingLeadIds());
  const [showImport, setShowImport] = useState(false);
  const [showWizard, setShowWizard] = useState(false);
  const [csv, setCsv] = useState(SAMPLE);
  const [source, setSource] = useState("apollo");
  const importSources = sourceOptions(["outbound", "import", "referral", "manual"]).map((x) => x.id);
  const [message, setMessage] = useState("");
  const [showScrape, setShowScrape] = useState(false);
  const [showJobs, setShowJobs] = useState(false);
  const [scrapeUrl, setScrapeUrl] = useState("");
  const [scrapeCompany, setScrapeCompany] = useState("");

  const [jobsText, setJobsText] = useState("");
  const [jobsUrl, setJobsUrl] = useState("");

  const runJobs = async () => {
    try {
      // A CSV: the first line names columns (company and title, in any order), not HTML.
      const head = jobsText.trim().split(/\r?\n/)[0].toLowerCase();
      const looksCsv = !head.includes("<") && /(^|,)\s*"?company"?\s*(,|$)/.test(head) && head.includes("title");
      const r = await api.ingestJobs({ url: jobsUrl, csv: looksCsv ? jobsText : "", html: looksCsv ? "" : jobsText });
      setMessage(`Read ${r.jobs_found} job(s): ${r.leads_created} new company lead(s), ${r.leads_updated} updated, ${r.reposts} repost(s) spotted.${r.note ? " " + r.note : ""}`);
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const runScrape = async () => {
    try {
      const r = await api.scrape({ url: scrapeUrl, company: scrapeCompany, add_leads: true });
      const skipped = r.emails.length - r.added - (r.not_for_outreach || 0);
      setMessage(`Found ${r.emails.length} email(s) and ${r.phones.length} phone(s). Added ${r.added} lead(s).`
        + (skipped > 0 ? ` ${skipped} already in your leads.` : "")
        + (r.not_for_outreach ? ` ${r.not_for_outreach} complaint, legal or no-reply address(es) left out.` : ""));
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const load = () => {
    api.leads().then(setLeads).catch((e) => setMessage(e.message));
    api.quality().then(setQuality).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  const [quality, setQuality] = useState(null);
  const [enriching, setEnriching] = useState(false);
  const [apolloReady, setApolloReady] = useState(false);
  useEffect(() => {
    api.integrations().then((list) => setApolloReady(list.some((i) => i.id === "apollo" && i.mode === "api" && i.status === "configured"))).catch(() => {});
  }, []);

  const runApollo = async () => {
    try {
      const r = await api.enrichApollo(10);
      setMessage(`Apollo: checked ${r.checked}, matched ${r.matched}, ${r.errors} errors, about ${r.credits_used_estimate} credits used.`);
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const runEnrich = async () => {
    setEnriching(true);
    try {
      const r = await api.enrich(20);
      setMessage(`Checked ${r.checked} company websites, found new contact details for ${r.enriched}. ${r.skipped_no_website} have no website yet.`);
      load();
    } catch (e) {
      setMessage(e.message);
    }
    setEnriching(false);
  };

  const runImport = async () => {
    try {
      if (SHEET_SOURCES[source]) {
        const r = await api.importSheet(csv, SHEET_SOURCES[source]);
        setMessage(SHEET_SOURCES[source] === "decision_makers"
          ? `Matched ${r.matched} people to companies, filled ${r.filled} empty contacts.${r.unmatched.length ? ` Not found yet: ${r.unmatched.join(", ")}.` : ""}`
          : `Read ${r.rows} sheet rows: added ${r.created} companies, merged ${r.merged}, skipped ${r.duplicates} already here.`);
        setShowImport(false); setCsv(SAMPLE); load();
        return;
      }
      const r = await api.importCsv(csv, source);
      setMessage(`Added ${r.created}, merged ${r.merged} into companies already found from job pages, skipped ${r.duplicates} duplicates, rejected ${r.rejected} with no contact details.`
        + (r.elsewhere ? ` Skipped ${r.elsewhere} already held in another workspace.` : "")
        + (r.suppressed ? ` Left out ${r.suppressed} on the do-not-contact list or erased at their request (they are never imported again).` : ""));
      setShowImport(false);
      setCsv(SAMPLE);
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const readFile = (file) => file && readLeadFile(file).then((r) => { setCsv(r.csv); if (r.sheet) setMessage(`Read sheet "${r.sheet}" (${r.rows} rows) from ${file.name}.`); })
    .catch((e) => setMessage(e.message));

  const shown = useMemo(() => leads.filter((l) => matches(l, query, stage, band, onlyIds, srcFilter, inboundOnly, ownerFilter, tagFilter, me, hasFilter)
    && customMatches(l, customFilter, customFields) && liStageMatches(l, liStage) && (!salesStage || salesStageOf(l) === salesStage) && (!waitingOnly || isWaiting(l)) && (!contact || (contact === "fresh" ? isFresh(l) : contactOf(l).status === contact))).sort(SORTS[sort]),
    [leads, query, stage, band, sort, onlyIds, srcFilter, inboundOnly, ownerFilter, tagFilter, me, customFilter, customFields, hasFilter, liStage, salesStage, waitingOnly, contact]);
  const allTags = useMemo(() => [...new Set(leads.flatMap((l) => l.tags || []).map(String))].sort((a, b) => a.localeCompare(b)), [leads]);
  const viewFilters = { query, stage, band, sources: srcFilter, inbound: inboundOnly, owner: ownerFilter, tag: tagFilter, has: hasFilter, li_stage: liStage, contact, sort };
  const applyView = (f) => {
    setQuery(f.query || ""); setStage(f.stage || ""); setBand(f.band || ""); setSrcFilter(f.sources || []); setInboundOnly(Boolean(f.inbound));
    setOwnerFilter(f.owner || ""); setTagFilter(f.tag || ""); setHasFilter(Array.isArray(f.has) ? f.has : []); setLiStage(f.li_stage || ""); setContact(f.contact || ""); setSort(SORTS[f.sort] ? f.sort : "score"); setOnlyIds(null);
  };
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const pages = Math.max(1, Math.ceil(shown.length / pageSize));
  const safePage = Math.min(page, pages - 1);
  useEffect(() => { setPage(0); }, [query, stage, band, sort, onlyIds, srcFilter, inboundOnly, ownerFilter, tagFilter, pageSize, customFilter, hasFilter, liStage, waitingOnly, contact]);
  const visible = shown.slice(safePage * pageSize, (safePage + 1) * pageSize);
  const togglePick = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const allPicked = shown.length > 0 && shown.every((l) => picked.includes(l.id));

  const [spaces, setSpaces] = useState([]);
  useEffect(() => { api.workspaces().then(setSpaces).catch(() => {}); }, []);

  const filtered = Boolean(waitingOnly || query.trim() || stage || band || onlyIds || srcFilter.length || inboundOnly || ownerFilter || tagFilter || customOn || hasFilter.length || liStage || contact);
  const clearFilters = () => { setQuery(""); setStage(""); setBand(""); setOnlyIds(null); setSrcFilter([]); setInboundOnly(false); setOwnerFilter(""); setTagFilter(""); setCustomFilter({}); setHasFilter([]); setLiStage(""); setSalesStage(""); setWaitingOnly(false); setContact(""); };

  const header = (key, label) => (
    <th aria-sort={sort === key ? (key === "score" ? "descending" : "ascending") : "none"}>
      <button className="th-sort" onClick={() => setSort(key)}>{label}{sort === key ? " ▾" : ""}</button>
    </th>
  );

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>Leads <HelpLink chapter="leads" label="Leads" /></h2><p className="sub">Sorted by buying-signal score. Click a lead to see what to say.</p></div>
        {canWrite() && <div className="row">
          <button className="btn ghost" onClick={() => setShowScrape(!showScrape)}>Find contacts on a website</button>
          <button className="btn ghost" onClick={() => setShowJobs(!showJobs)}>Read job pages</button>
          <button className="btn ghost" onClick={() => { setShowImport(!showImport); setShowWizard(false); }}>Quick paste</button>
          <button className="btn" onClick={() => { setShowWizard(!showWizard); setShowImport(false); }}>Import leads</button>
        </div>}
      </div>
      <FindLeadsCard onOpen={() => setFinding(true)} />
      {onlyIds && (
        <div className="tip" role="status" style={{ marginBottom: 12 }}>
          Showing the {onlyIds.length} lead(s) from your last Find &amp; enrich. <button className="linkish" onClick={() => setOnlyIds(null)}>Show all leads</button>
        </div>
      )}
      {finding && <FindLeadsDrawer go={go} onClose={() => { setFinding(false); load(); }} onOpenLeads={(ids) => { setOnlyIds(ids); setQuery(""); setStage(""); setBand(""); }} />}
      {showWizard && canWrite() && <ImportWizard onClose={() => { setShowWizard(false); load(); }} onDone={load} />}
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message} <button className="linkish" aria-label="Dismiss message" onClick={() => setMessage("")}>✕</button></div>}

      {quality && quality.total > 0 && (
        <div className="card row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
          <span>
            Of {quality.total} companies: <b>{quality.missing_email}</b> have no email, <b>{quality.missing_phone}</b> no phone,{" "}
            <b>{quality.missing_decision_maker}</b> no decision maker, <b>{quality.missing_website}</b> no website.
          </span>
          <span className="row">
            {isOwner() && <button className="btn ghost" disabled={enriching || quality.enrichable_now === 0} onClick={runEnrich}>
              {enriching ? "Checking websites…" : `Fill gaps from company websites (${quality.enrichable_now})`}
            </button>}
            {apolloReady && isOwner() && <button className="btn ghost" title="Uses up to 10 Apollo credits" onClick={runApollo}>Look up emails in Apollo (10)</button>}
            {isOwner() && <button className="btn ghost" onClick={downloadApolloList}>Download list for Apollo</button>}
            {go && <button className="btn ghost" onClick={() => go("hygiene")}>Duplicates and clean-up</button>}
          </span>
        </div>
      )}

      {showScrape && canWrite() && (
        <div className="card" style={{ marginBottom: 14 }}>
          <p className="sub">Reads public contact details from a page (honours robots.txt, keeps the source link). Only public business contacts.</p>
          <div className="row">
            <input style={{ flex: 2 }} placeholder="https://company.com/contact" value={scrapeUrl} onChange={(e) => setScrapeUrl(e.target.value)} />
            <input style={{ flex: 1 }} placeholder="Company name" value={scrapeCompany} onChange={(e) => setScrapeCompany(e.target.value)} />
            <button className="btn" onClick={runScrape}>Find & add</button>
          </div>
        </div>
      )}

      {showJobs && canWrite() && (
        <div className="card" style={{ marginBottom: 14 }}>
          <b>Read job openings (hiring signals)</b>
          <p className="sub">Give a company careers page link, or paste page HTML, or paste a CSV with columns company,title,location,date_posted,website. More open roles and older or reposted jobs raise the lead score.</p>
          <input placeholder="https://company.com/careers" value={jobsUrl} onChange={(e) => setJobsUrl(e.target.value)} />
          <textarea rows={4} style={{ marginTop: 8 }} placeholder="Optional: paste page HTML or CSV here" value={jobsText} onChange={(e) => setJobsText(e.target.value)} />
          <button className="btn" style={{ marginTop: 8 }} onClick={runJobs}>Read jobs</button>
        </div>
      )}

      {showImport && canWrite() && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="row">
            <div style={{ flex: 1 }}>
              <label>Where did this list come from?</label>
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                {[...importSources, ...Object.keys(SHEET_SOURCES)].map((s) => <option key={s} value={s}>{SHEET_SOURCES[s] ? s : sourceLabel(s)}</option>)}
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label>Upload a CSV or Excel file (Apollo, Sales Navigator export…)</label>
              <input type="file" accept={EXCEL_ACCEPT} onChange={(e) => readFile(e.target.files[0])} />
            </div>
          </div>
          <label>Or paste CSV (first row = column names)</label>
          <textarea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} />
          <p className="sub" style={{ margin: "6px 0" }}>
            Columns: company, contact_name, title, email, phone, website, open_roles, company_size, country (Apollo-style names such as
            First Name or Mobile Phone also work). Duplicates are removed automatically. For a preview, column choices and undo, use “Import leads”.
          </p>
          <button className="btn" onClick={runImport}>Import</button>
        </div>
      )}

      {leads.length > 0 && (
        <div className="toolbar" role="search">
          <input aria-label="Search leads" placeholder="Search company, person, email or phone" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select aria-label="Filter by stage" value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="">All stages</option>
            {Object.keys(STAGE_LABELS).map((k) => <option key={k} value={k}>{stageLabel(k)}</option>)}
          </select>
          <select aria-label="Filter by sales stage" value={salesStage} onChange={(e) => setSalesStage(e.target.value)} data-testid="sales-stage-filter">
            <option value="">All sales stages</option>
            {SALES_STATUSES.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <select aria-label="Filter by temperature" value={band} onChange={(e) => setBand(e.target.value)}>
            <option value="">Hot, warm and cold</option>
            {Object.entries(BAND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <SourceFilter leads={leads} value={srcFilter} onChange={setSrcFilter} />
          <HasFilter leads={leads} value={hasFilter} onChange={setHasFilter} />
          <select aria-label="Filter by owner" value={ownerFilter} onChange={(e) => setOwnerFilter(e.target.value)}>
            <option value="">All owners</option>
            <option value="__me">My leads</option>
            <option value="__none">Unassigned</option>
            {owners.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          {allTags.length > 0 && <select aria-label="Filter by tag" value={tagFilter} onChange={(e) => setTagFilter(e.target.value)}>
            <option value="">All tags</option>
            {allTags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>}
          <select aria-label="Filter by contact status" value={contact} onChange={(e) => setContact(e.target.value)} data-testid="contact-filter">
            <option value="">Any contact status</option>
            {CONTACT_STATUSES.map(([k, v]) => <option key={k} value={k}>{v} ({leads.filter((l) => contactOf(l).status === k).length})</option>)}
          </select>
          <button type="button" className={`btn ghost${contact === "fresh" ? " on" : ""}`} aria-pressed={contact === "fresh"} onClick={() => setContact(contact === "fresh" ? "" : "fresh")}
            title="Never contacted, still open, not on the do-not-contact list, with a phone or email" data-testid="fresh-to-attempt">
            Fresh to attempt ({leads.filter(isFresh).length})
          </button>
          <button type="button" className={`btn ghost${waitingOnly ? " on" : ""}`} aria-pressed={waitingOnly} onClick={() => setWaitingOnly(!waitingOnly)} data-testid="waiting-first-touch">
            Waiting for first touch ({leads.filter(isWaiting).length})
          </button>
          <LinkedInStageFilter value={liStage} onChange={setLiStage} />
          <CustomFilters fields={customFields} value={customFilter} onChange={setCustomFilter} />
          <label className="row" style={{ margin: 0 }}><input type="checkbox" checked={inboundOnly} onChange={(e) => setInboundOnly(e.target.checked)} /> Inbound only</label>
          <LeadViews filters={viewFilters} onApply={applyView} />
          <span className="sub" style={{ margin: 0 }}>Showing {shown.length} of {leads.length}</span>
          <span className="dl-group">
            <DownloadButton path={exportPath("leads")} label="Download all" title="Every lead as a CSV that opens in Excel" testId="download-leads-all" />
            {filtered && <DownloadButton path={exportPath("leads", { q: query.trim(), stage, band, source: srcFilter.join(","), inbound: inboundOnly ? "1" : "", tag: tagFilter })} body={onlyIds || ownerFilter || customOn || hasFilter.length || liStage || waitingOnly ? { ids: shown.map((l) => l.id) } : undefined} label={`Download these ${shown.length}`}
              title="Only the leads that match the search and filters" testId="download-leads-shown" />}
          </span>
        </div>
      )}
      {picked.length > 0 && canWrite() && (
        <LeadBulkBar picked={picked} pageIds={visible.map((l) => l.id)} owners={owners} spaces={spaces} onClear={() => setPicked([])}
          onDone={(msg, changed) => { setMessage(msg); if (changed) { setPicked([]); load(); } }} />
      )}

      <div className="card table-card">
        {leads.length === 0 ? (
          <div className="empty"><EmptyArt name="leads" /><p>{canWrite() ? "No leads yet. Click “Import leads” to add your first list." : "No leads yet."}</p></div>
        ) : shown.length === 0 ? (
          <p className="empty">No leads match. <button className="linkish" onClick={clearFilters}>Clear the filters</button></p>
        ) : (
          <table className="leads-table">
            <thead><tr>
              {canWrite() && <th><label className="pick-hit"><input type="checkbox" aria-label="Select all shown" checked={allPicked}
                onChange={() => setPicked(allPicked ? [] : shown.map((l) => l.id))} /></label></th>}
              {header("company", "Company")}<th>Contact</th>{header("stage", "Stage")}<th>Sales stage</th><th>Contact status</th>{header("score", "Score")}<th>Owner</th>{waitingOnly && <th>First touch</th>}<th>Tags</th>{tableFields.map((f) => <th key={f.key}>{f.label}</th>)}<th>Source</th>{header("activity", "Last activity")}{header("due", "Next task due")}<th>LinkedIn</th><th>Next step</th>
            </tr></thead>
            <tbody>
              {visible.map((l) => (
                <tr key={l.id} className="click" onClick={() => setOpen(l.id)}>
                  {canWrite() && <td className="pick" onClick={(e) => e.stopPropagation()}>
                    <label className="pick-hit"><input type="checkbox" aria-label={`Select ${l.company || l.domain || l.contact_name}`} checked={picked.includes(l.id)} onChange={() => togglePick(l.id)} /></label>
                  </td>}
                  <td data-label="Company"><button className="linkish" onClick={(e) => { e.stopPropagation(); setOpen(l.id); }}><b>{l.company || l.domain || l.contact_name}</b></button>{l.is_test && <span className="pill warn" style={{ marginLeft: 6 }}>Test</span>}</td>
                  <td data-label="Contact">{l.contact_name || "—"}<br /><small>{l.email || l.phone || ""}</small><PhoneTag lead={l} /></td>
                  <td data-label="Stage">{stageLabel(l.stage)}{l.do_not_contact && <span className="pill warn" style={{ marginLeft: 6 }} title="On the do-not-contact list: never emailed, called or messaged">Do not contact</span>}</td>
                  <td data-label="Sales stage" data-testid="sales-stage-cell"><Stage status={salesStageOf(l)} full /></td>
                  <td data-label="Contact status" data-testid="contact-cell">{(() => { const c = contactOf(l); return <span className={`pill ${c.status === "never" ? "ok" : c.status === "tried" ? "warn" : ""}`}>{CONTACT_STATUSES.find((x) => x[0] === c.status)[1]}{c.attempts ? ` · ${c.attempts}` : ""}</span>; })()}</td>
                  <td data-label="Score"><span className={`pill ${l.band}`}>{l.score} · {l.band}</span></td>
                  <td data-label="Owner">{l.owner || <small className="sub">—</small>}</td>
                  {waitingOnly && <td data-label="First touch"><FirstTouchTimer lead={l} /></td>}
                  <td data-label="Tags">{(l.tags || []).length ? (l.tags || []).map((t) => <span key={t} className="pill rec" style={{ margin: "0 4px 2px 0" }}>{t}</span>) : <small className="sub">—</small>}</td>
                  {tableFields.map((f) => <td key={f.key} data-label={f.label}>{showValue(f, l.custom?.[f.key]) || <small className="sub">—</small>}</td>)}
                  <td data-label="Source" title={attributionText(l.attribution) || undefined}>
                    {sourceLabel(l)}{l.inbound && <span className="pill ok" style={{ marginLeft: 6 }}>Inbound</span>}
                    {attributionText(l.attribution) && <><br /><small>{attributionText(l.attribution)}</small></>}
                  </td>
                  <td data-label="Last activity"><small title={istLabel(l.last_activity_at)}>{istDateLabel(l.last_activity_at) || "—"}</small></td>
                  <td data-label="Next task due"><small className={l.next_task_due && new Date(l.next_task_due) < new Date() ? "pill err" : ""}>{l.next_task_due ? istLabel(l.next_task_due) : "—"}</small></td>
                  <td data-label="LinkedIn"><LinkedInCell lead={l} /></td>
                  <td data-label="Next step">{l.suggestion.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {shown.length > pageSize && (
          <div className="pager" role="navigation" aria-label="Pages of leads">
            <span className="sub" style={{ margin: 0 }}>{safePage * pageSize + 1}–{Math.min(shown.length, (safePage + 1) * pageSize)} of {shown.length}</span>
            <span className="row">
              <button className="btn ghost" disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>Previous</button>
              <span>Page {safePage + 1} of {pages}</span>
              <button className="btn ghost" disabled={safePage >= pages - 1} onClick={() => setPage(safePage + 1)}>Next</button>
              <select aria-label="Leads per page" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n} per page</option>)}
              </select>
            </span>
          </div>
        )}
      </div>
      {open && <LeadDrawer id={open} ids={shown.map((l) => l.id)} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
