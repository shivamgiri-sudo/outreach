import { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { BAND_LABELS, STAGE_LABELS, STAGE_ORDER, sourceLabel, stageLabel } from "../labels.js";
import LeadDrawer from "./LeadDrawer.jsx";

const SAMPLE = "company,contact_name,title,email,phone,website,open_roles,company_size,country\n";
const SOURCES = ["apollo", "sales_navigator", "job_board", "web_scraper", "manual"];
const SHEET_SOURCES = { "team sheet — Leads tab": "leads", "team sheet — Demo Shared tab": "demo", "team sheet — Decision Makers tab": "decision_makers" };

const SORTS = {
  score: (a, b) => b.score - a.score,
  company: (a, b) => (a.company || a.domain || "").localeCompare(b.company || b.domain || ""),
  stage: (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage),
};

function matches(lead, query, stage, band) {
  const q = query.trim().toLowerCase();
  const text = [lead.company, lead.domain, lead.contact_name, lead.email, lead.phone, lead.role].join(" ").toLowerCase();
  return (!q || text.includes(q)) && (!stage || lead.stage === stage) && (!band || lead.band === band);
}

function toCsv(rows) {
  const cols = ["company", "contact_name", "title", "email", "phone", "website", "stage", "score", "source"];
  const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  return [cols.join(","), ...rows.map((r) => cols.map((c) => cell(r[c])).join(","))].join("\n");
}

export default function Leads({ focusLead }) {
  const [leads, setLeads] = useState([]);
  const [open, setOpen] = useState(focusLead || null);
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState("");
  const [band, setBand] = useState("");
  const [sort, setSort] = useState("score");
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState("");
  useEffect(() => { if (focusLead) setOpen(focusLead); }, [focusLead]);
  const [showImport, setShowImport] = useState(false);
  const [csv, setCsv] = useState(SAMPLE);
  const [source, setSource] = useState("apollo");
  const [message, setMessage] = useState("");
  const [showScrape, setShowScrape] = useState(false);
  const [showJobs, setShowJobs] = useState(false);
  const [scrapeUrl, setScrapeUrl] = useState("");
  const [scrapeCompany, setScrapeCompany] = useState("");

  const [jobsText, setJobsText] = useState("");
  const [jobsUrl, setJobsUrl] = useState("");

  const runJobs = async () => {
    try {
      const looksCsv = jobsText.trim().toLowerCase().startsWith("company,");
      const r = await api.ingestJobs({ url: jobsUrl, csv: looksCsv ? jobsText : "", html: looksCsv ? "" : jobsText });
      setMessage(`Read ${r.jobs_found} job(s): ${r.leads_created} new company lead(s), ${r.leads_updated} updated, ${r.reposts} repost(s) spotted.`);
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const runScrape = async () => {
    try {
      const r = await api.scrape({ url: scrapeUrl, company: scrapeCompany, add_leads: true });
      setMessage(`Found ${r.emails.length} email(s) and ${r.phones.length} phone(s). Added ${r.added} lead(s).`);
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
      setMessage(`Added ${r.created}, merged ${r.merged} into companies already found from job pages, skipped ${r.duplicates} duplicates, rejected ${r.rejected} with no contact details.`);
      setShowImport(false);
      setCsv(SAMPLE);
      load();
    } catch (e) {
      setMessage(e.message);
    }
  };

  const readFile = (file) => file && file.text().then(setCsv);

  const shown = useMemo(() => leads.filter((l) => matches(l, query, stage, band)).sort(SORTS[sort]), [leads, query, stage, band, sort]);
  const togglePick = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const allPicked = shown.length > 0 && shown.every((l) => picked.includes(l.id));

  const bulkEnroll = async () => {
    setBusy("enroll");
    const results = await Promise.allSettled(picked.map((id) => api.enroll(id)));
    const ok = results.filter((r) => r.status === "fulfilled").length;
    const rest = picked.length - ok;
    setMessage(`Started the email sequence for ${ok} of ${picked.length}.${rest ? " The rest have no email, are already in it, or cannot be emailed." : ""}`);
    setPicked([]); setBusy(""); load();
  };

  const bulkExport = () => {
    const blob = new Blob([toCsv(leads.filter((l) => picked.includes(l.id)))], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "leads.csv"; a.click();
    URL.revokeObjectURL(a.href);
  };

  const header = (key, label) => (
    <th aria-sort={sort === key ? (key === "score" ? "descending" : "ascending") : "none"}>
      <button className="th-sort" onClick={() => setSort(key)}>{label}{sort === key ? " ▾" : ""}</button>
    </th>
  );

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>Leads</h2><p className="sub">Sorted by buying-signal score. Click a lead to see what to say.</p></div>
        <div className="row">
          <button className="btn ghost" onClick={() => setShowScrape(!showScrape)}>Find contacts on a website</button>
          <button className="btn ghost" onClick={() => setShowJobs(!showJobs)}>Read job pages</button>
          <button className="btn" onClick={() => setShowImport(!showImport)}>Import leads</button>
        </div>
      </div>
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message} <button className="linkish" aria-label="Dismiss message" onClick={() => setMessage("")}>✕</button></div>}

      {quality && quality.total > 0 && (
        <div className="card row" style={{ marginBottom: 14, justifyContent: "space-between" }}>
          <span>
            Of {quality.total} companies: <b>{quality.missing_email}</b> have no email, <b>{quality.missing_phone}</b> no phone,{" "}
            <b>{quality.missing_decision_maker}</b> no decision maker, <b>{quality.missing_website}</b> no website.
          </span>
          <span className="row">
            <button className="btn ghost" disabled={enriching || quality.enrichable_now === 0} onClick={runEnrich}>
              {enriching ? "Checking websites…" : `Fill gaps from company websites (${quality.enrichable_now})`}
            </button>
            {apolloReady && <button className="btn ghost" title="Uses up to 10 Apollo credits" onClick={runApollo}>Look up emails in Apollo (10)</button>}
            <a className="btn ghost" href="/api/enrich/apollo-export" download="apollo-enrich-list.csv">Download list for Apollo</a>
          </span>
        </div>
      )}

      {showScrape && (
        <div className="card" style={{ marginBottom: 14 }}>
          <p className="sub">Reads public contact details from a page (honours robots.txt, keeps the source link). Only public business contacts.</p>
          <div className="row">
            <input style={{ flex: 2 }} placeholder="https://company.com/contact" value={scrapeUrl} onChange={(e) => setScrapeUrl(e.target.value)} />
            <input style={{ flex: 1 }} placeholder="Company name" value={scrapeCompany} onChange={(e) => setScrapeCompany(e.target.value)} />
            <button className="btn" onClick={runScrape}>Find & add</button>
          </div>
        </div>
      )}

      {showJobs && (
        <div className="card" style={{ marginBottom: 14 }}>
          <b>Read job openings (hiring signals)</b>
          <p className="sub">Give a company careers page link, or paste page HTML, or paste a CSV with columns company,title,location,date_posted,website. More open roles and older or reposted jobs raise the lead score.</p>
          <input placeholder="https://company.com/careers" value={jobsUrl} onChange={(e) => setJobsUrl(e.target.value)} />
          <textarea rows={4} style={{ marginTop: 8 }} placeholder="Optional: paste page HTML or CSV here" value={jobsText} onChange={(e) => setJobsText(e.target.value)} />
          <button className="btn" style={{ marginTop: 8 }} onClick={runJobs}>Read jobs</button>
        </div>
      )}

      {showImport && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="row">
            <div style={{ flex: 1 }}>
              <label>Where did this list come from?</label>
              <select value={source} onChange={(e) => setSource(e.target.value)}>
                {[...SOURCES, ...Object.keys(SHEET_SOURCES)].map((s) => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div style={{ flex: 1 }}>
              <label>Upload a CSV (Apollo, Sales Navigator export…)</label>
              <input type="file" accept=".csv" onChange={(e) => readFile(e.target.files[0])} />
            </div>
          </div>
          <label>Or paste CSV (first row = column names)</label>
          <textarea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} />
          <p className="sub" style={{ margin: "6px 0" }}>
            Columns: company, contact_name, title, email, phone, website, open_roles, company_size, country. Duplicates are removed automatically.
          </p>
          <button className="btn" onClick={runImport}>Import</button>
        </div>
      )}

      {leads.length > 0 && (
        <div className="toolbar" role="search">
          <input aria-label="Search leads" placeholder="Search company, person, email or phone" value={query} onChange={(e) => setQuery(e.target.value)} />
          <select aria-label="Filter by stage" value={stage} onChange={(e) => setStage(e.target.value)}>
            <option value="">All stages</option>
            {Object.entries(STAGE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select aria-label="Filter by temperature" value={band} onChange={(e) => setBand(e.target.value)}>
            <option value="">Hot, warm and cold</option>
            {Object.entries(BAND_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <span className="sub" style={{ margin: 0 }}>Showing {shown.length} of {leads.length}</span>
        </div>
      )}
      {picked.length > 0 && (
        <div className="bulkbar" role="region" aria-label="Actions for selected leads">
          <b>{picked.length} selected</b>
          <button className="btn" disabled={busy === "enroll"} onClick={bulkEnroll}>{busy === "enroll" ? "Starting…" : "Start email sequence"}</button>
          <button className="btn ghost" onClick={bulkExport}>Download CSV</button>
          <button className="btn ghost" onClick={() => setPicked([])}>Clear</button>
        </div>
      )}

      <div className="card table-card">
        {leads.length === 0 ? (
          <p className="empty">No leads yet. Click “Import leads” to add your first list.</p>
        ) : shown.length === 0 ? (
          <p className="empty">No leads match. <button className="linkish" onClick={() => { setQuery(""); setStage(""); setBand(""); }}>Clear the filters</button></p>
        ) : (
          <table className="leads-table">
            <thead><tr>
              <th><input type="checkbox" aria-label="Select all shown" checked={allPicked}
                onChange={() => setPicked(allPicked ? [] : shown.map((l) => l.id))} /></th>
              {header("company", "Company")}<th>Contact</th>{header("stage", "Stage")}{header("score", "Score")}<th>Source</th><th>Next step</th>
            </tr></thead>
            <tbody>
              {shown.map((l) => (
                <tr key={l.id} className="click" onClick={() => setOpen(l.id)}>
                  <td className="pick" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select ${l.company || l.domain}`} checked={picked.includes(l.id)} onChange={() => togglePick(l.id)} />
                  </td>
                  <td data-label="Company"><button className="linkish" onClick={(e) => { e.stopPropagation(); setOpen(l.id); }}><b>{l.company || l.domain}</b></button></td>
                  <td data-label="Contact">{l.contact_name || "—"}<br /><small>{l.email || l.phone || ""}</small></td>
                  <td data-label="Stage">{stageLabel(l.stage)}</td>
                  <td data-label="Score"><span className={`pill ${l.band}`}>{l.score} · {l.band}</span></td>
                  <td data-label="Source">{sourceLabel(l.source)}</td>
                  <td data-label="Next step">{l.suggestion.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {open && <LeadDrawer id={open} onClose={() => { setOpen(null); load(); }} />}
    </>
  );
}
