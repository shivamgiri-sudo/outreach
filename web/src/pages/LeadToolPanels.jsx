import { useState } from "react";
import { api, canWrite, EXCEL_ACCEPT, readLeadFile } from "../api.js";
import { sourceLabel, sourceOptions } from "../labels.js";
import { Button, Card, Field, Input, Select } from "../ui/index.js";

const SAMPLE = "company,contact_name,title,email,phone,website,open_roles,company_size,country\n";
const SHEET_SOURCES = { "Team sheet — Leads tab": "leads", "Team sheet — Demo Shared tab": "demo", "Team sheet — Decision Makers tab": "decision_makers" };

// The three small "add leads" panels opened from the page header: contacts on a website, job pages, quick paste.
// which = "scrape" | "jobs" | "paste" | "". Messages go back to the page through setMessage.
export default function LeadToolPanels({ which, onClose, setMessage, load }) {
  const [scrapeUrl, setScrapeUrl] = useState("");
  const [scrapeCompany, setScrapeCompany] = useState("");
  const [jobsText, setJobsText] = useState("");
  const [jobsUrl, setJobsUrl] = useState("");
  const [csv, setCsv] = useState(SAMPLE);
  const [source, setSource] = useState("apollo");
  const importSources = sourceOptions(["outbound", "import", "referral", "manual"]).map((x) => x.id);

  const runJobs = async () => {
    try {
      // A CSV: the first line names columns (company and title, in any order), not HTML.
      const head = jobsText.trim().split(/\r?\n/)[0].toLowerCase();
      const looksCsv = !head.includes("<") && /(^|,)\s*"?company"?\s*(,|$)/.test(head) && head.includes("title");
      const r = await api.ingestJobs({ url: jobsUrl, csv: looksCsv ? jobsText : "", html: looksCsv ? "" : jobsText });
      setMessage(`Read ${r.jobs_found} job(s): ${r.leads_created} new company lead(s), ${r.leads_updated} updated, ${r.reposts} repost(s) spotted.${r.note ? " " + r.note : ""}`);
      load();
    } catch (e) { setMessage(e.message); }
  };

  const runScrape = async () => {
    try {
      const r = await api.scrape({ url: scrapeUrl, company: scrapeCompany, add_leads: true });
      const skipped = r.emails.length - r.added - (r.not_for_outreach || 0);
      setMessage(`Found ${r.emails.length} email(s) and ${r.phones.length} phone(s). Added ${r.added} lead(s).`
        + (skipped > 0 ? ` ${skipped} already in your leads.` : "")
        + (r.not_for_outreach ? ` ${r.not_for_outreach} complaint, legal or no-reply address(es) left out.` : ""));
      load();
    } catch (e) { setMessage(e.message); }
  };

  const runImport = async () => {
    try {
      if (SHEET_SOURCES[source]) {
        const r = await api.importSheet(csv, SHEET_SOURCES[source]);
        setMessage(SHEET_SOURCES[source] === "decision_makers"
          ? `Matched ${r.matched} people to companies, filled ${r.filled} empty contacts.${r.unmatched.length ? ` Not found yet: ${r.unmatched.join(", ")}.` : ""}`
          : `Read ${r.rows} sheet rows: added ${r.created} companies, merged ${r.merged}, skipped ${r.duplicates} already here.`);
        onClose(); setCsv(SAMPLE); load();
        return;
      }
      const r = await api.importCsv(csv, source);
      setMessage(`Added ${r.created}, merged ${r.merged} into companies already found from job pages, skipped ${r.duplicates} duplicates, rejected ${r.rejected} with no contact details.`
        + (r.elsewhere ? ` Skipped ${r.elsewhere} already held in another workspace.` : "")
        + (r.suppressed ? ` Left out ${r.suppressed} on the do-not-contact list or erased at their request (they are never imported again).` : ""));
      onClose(); setCsv(SAMPLE); load();
    } catch (e) { setMessage(e.message); }
  };

  const readFile = (file) => file && readLeadFile(file).then((r) => { setCsv(r.csv); if (r.sheet) setMessage(`Read sheet "${r.sheet}" (${r.rows} rows) from ${file.name}.`); })
    .catch((e) => setMessage(e.message));

  if (!canWrite()) return null;           // viewers have no write access
  if (which === "scrape") return (
    <Card className="lead-panel">
      <p className="sub">Reads public contact details from a page (honours robots.txt, keeps the source link). Only public business contacts.</p>
      <div className="lead-panel-row">
        <Input className="grow2" placeholder="https://company.com/contact" aria-label="Page address" value={scrapeUrl} onChange={(e) => setScrapeUrl(e.target.value)} />
        <Input placeholder="Company name" aria-label="Company name" value={scrapeCompany} onChange={(e) => setScrapeCompany(e.target.value)} />
        <Button onClick={runScrape}>Find & add</Button>
      </div>
    </Card>
  );
  if (which === "jobs") return (
    <Card className="lead-panel">
      <h3>Read job openings (hiring signals)</h3>
      <p className="sub">Give a company careers page link, or paste page HTML, or paste a CSV with columns company,title,location,date_posted,website. More open roles and older or reposted jobs raise the lead score.</p>
      <Input placeholder="https://company.com/careers" aria-label="Careers page address" value={jobsUrl} onChange={(e) => setJobsUrl(e.target.value)} />
      <textarea rows={4} className="lead-panel-text" placeholder="Optional: paste page HTML or CSV here" aria-label="Page HTML or CSV" value={jobsText} onChange={(e) => setJobsText(e.target.value)} />
      <Button onClick={runJobs}>Read jobs</Button>
    </Card>
  );
  if (which === "paste") return (
    <Card className="lead-panel">
      <div className="lead-panel-row">
        <Field label="Where did this list come from?">
          <Select value={source} onChange={(e) => setSource(e.target.value)}>
            {[...importSources, ...Object.keys(SHEET_SOURCES)].map((s) => <option key={s} value={s}>{SHEET_SOURCES[s] ? s : sourceLabel(s)}</option>)}
          </Select>
        </Field>
        <Field label="Upload a CSV or Excel file (Apollo, Sales Navigator export…)">
          <input type="file" accept={EXCEL_ACCEPT} onChange={(e) => readFile(e.target.files[0])} />
        </Field>
      </div>
      <label>Or paste CSV (first row = column names)</label>
      <textarea rows={6} value={csv} onChange={(e) => setCsv(e.target.value)} />
      <p className="sub">
        Columns: company, contact_name, title, email, phone, website, open_roles, company_size, country (Apollo-style names such
        as First Name or Mobile Phone also work). Duplicates are removed automatically. For a preview, column choices and undo, use “Import leads”.
      </p>
      <Button onClick={runImport}>Import</Button>
    </Card>
  );
  return null;
}
