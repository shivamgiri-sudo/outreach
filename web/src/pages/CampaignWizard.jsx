import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { PreviewTable, RunReport } from "./CampaignReport.jsx";

const EXAMPLES = {
  industries: ["BPO", "IT services", "Retail", "Logistics", "Healthcare", "BFSI", "Staffing", "EdTech"],
  cities: ["Bengaluru", "Pune", "Mumbai", "Delhi NCR", "Hyderabad", "Chennai", "Noida", "Gurugram"],
  roles_hiring: ["customer support", "sales executive", "telecaller", "delivery associate", "field sales", "store staff"],
  keywords: ["call centre", "e-commerce", "D2C", "warehouse"],
};
const SIZES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001+"];
const STEPS = ["1. Who to target", "2. Sources", "3. Enrichment and rules", "4. Review and run"];
const EARLY = ["new", "verified", "enriched"];

export const EMPTY_CAMPAIGN = {
  name: "",
  target: { industries: [], cities: [], roles_hiring: [], company_size_buckets: [], keywords: [] },
  sources: { careers_pages: [], careers_html: [], apollo_search: false, apollo_hiring: false, google_places: false, csv_import_ids: [], max_results: 25, extra: [], extra_params: {} },
  enrich: { crawler: true, hunter: false, firecrawl: false, apollo_match: false, extra: [], extra_params: {} },
  rules: { credit_cap: 100, min_score: 40, require_business_email: true, skip_role_addresses: false, max_new_leads_per_run: 50, auto_enrol: false, caller: "", only_companies: [], sequence_id: "default" },
};

function Chips({ label, field, values, onChange }) {
  const [text, setText] = useState("");
  const add = (v) => { const t = v.trim(); if (t && !values.includes(t)) onChange([...values, t]); setText(""); };
  return (
    <div>
      <label htmlFor={`chip-${field}`}>{label}</label>
      <div className="row">
        <input id={`chip-${field}`} style={{ flex: "1 1 180px", width: "auto" }} value={text} placeholder="Type and press Enter"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(text); } }} />
        <button className="btn ghost" type="button" onClick={() => add(text)} aria-label={`Add to ${label}`}>Add</button>
      </div>
      <div className="chips" aria-label={`${label}: chosen`}>
        {values.map((v) => <button key={v} type="button" className="chip on" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>{v} ✕</button>)}
      </div>
      <div className="chips" aria-label={`${label}: examples`}>
        {EXAMPLES[field].filter((v) => !values.includes(v)).map((v) => <button key={v} type="button" className="chip" onClick={() => add(v)}>+ {v}</button>)}
      </div>
    </div>
  );
}

function Ready({ state, go }) {
  if (!state) return null;
  return state.connected ? <span className="pill ok">Connected</span>
    : <span className="row" style={{ gap: 6 }}><span className="pill warn">Not connected</span>
      <button type="button" className="linkish" onClick={() => go("integrations")}>Set up in Integrations</button></span>;
}

function Check({ checked, onChange, children, hint }) {
  return (
    <label className="row" style={{ fontWeight: 400, alignItems: "flex-start", margin: "10px 0 0", flexWrap: "nowrap" }}>
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} style={{ marginTop: 4 }} />
      <span style={{ minWidth: 0 }}>{children}{hint && <small className="sub" style={{ display: "block", margin: 0 }}>{hint}</small>}</span>
    </label>
  );
}

function MoreSources({ items, picked, onPick, params, onParams, go, kind }) {
  if (!items.length) return null;
  const toggle = (id, on) => onPick(on ? [...picked, id] : picked.filter((x) => x !== id));
  return (
    <div data-testid={`more-${kind}`}>
      {items.map((src) => (
        <div key={src.id}>
          <Check checked={picked.includes(src.id)} onChange={(v) => toggle(src.id, v)}
            hint={`${src.why} ${src.free ? "Free." : src.cost_note}`}>
            <b>{src.title}</b> {src.keyless || src.connected ? <span className="pill ok">{src.keyless ? "No key needed" : "Connected"}</span>
              : <span className="row" style={{ gap: 6 }}><span className="pill warn">Not connected</span>
                <button type="button" className="linkish" onClick={() => go("integrations")}>Set up in Integrations</button></span>}
          </Check>
          {kind === "discover" && src.takes_identifiers && picked.includes(src.id) && (
            <div style={{ marginLeft: 26 }}>
              <label htmlFor={`ids-${src.id}`}>Company identifiers for {src.title} (one per line)</label>
              <textarea id={`ids-${src.id}`} rows={3} placeholder="acme-hr" value={(params[src.id] || {}).slugs ?? ""}
                onChange={(e) => onParams({ ...params, [src.id]: { slugs: e.target.value } })} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function LeadPicker({ picked, onChange }) {
  const [leads, setLeads] = useState([]);
  const [q, setQ] = useState("");
  useEffect(() => { api.leads().then((all) => setLeads(all.filter((l) => EARLY.includes(l.stage) && !l.sequence && !l.campaign_id))).catch(() => {}); }, []);
  const shown = leads.filter((l) => `${l.company} ${l.domain} ${l.email}`.toLowerCase().includes(q.toLowerCase())).slice(0, 30);
  if (!leads.length) return <p className="sub" style={{ margin: "4px 0 0" }}>No imported leads waiting (only new leads not yet emailed can join).</p>;
  return (
    <div>
      <input aria-label="Search your leads" placeholder="Search your imported leads" value={q} onChange={(e) => setQ(e.target.value)} />
      <div style={{ maxHeight: 180, overflowY: "auto", marginTop: 6 }}>
        {shown.map((l) => (
          <Check key={l.id} checked={picked.includes(l.id)} onChange={(on) => onChange(on ? [...picked, l.id] : picked.filter((x) => x !== l.id))}>
            {l.company || l.domain} <small className="sub-inline">{l.email || "no email yet"}</small>
          </Check>
        ))}
      </div>
      <small className="sub-inline">{picked.length} picked</small>
    </div>
  );
}

export default function CampaignWizard({ initial, go, onSaved, onOpenLeads, onCancel }) {
  const [c, setC] = useState(() => JSON.parse(JSON.stringify(initial || EMPTY_CAMPAIGN)));
  const [step, setStep] = useState(0);
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [preview, setPreview] = useState(null);
  const [report, setReport] = useState(null);
  const [paste, setPaste] = useState({ url: "", html: "" });
  useEffect(() => {
    api.campaignSources().then(setStatus).catch(() => {});
    if (!initial) api.team().then((t) => setC((x) => ({ ...x, rules: { ...x.rules, caller: x.rules.caller || t.caller_name || "Sales team" } }))).catch(() => {});
  }, []);
  const set = (part, key, value) => setC((x) => ({ ...x, [part]: { ...x[part], [key]: value } }));
  const [pagesText, setPagesText] = useState(() => c.sources.careers_pages.join("\n"));
  const pages = pagesText.split(/\s+/).map((x) => x.trim()).filter(Boolean);

  const body = () => {
    const pasted = paste.html.trim() ? [...c.sources.careers_html, { url: paste.url.trim(), html: paste.html }] : c.sources.careers_html;
    return { ...c, name: c.name.trim(), sources: { ...c.sources, careers_pages: pages, careers_html: pasted, max_results: Number(c.sources.max_results) || 25 },
      rules: { ...c.rules, max_new_leads_per_run: Number(c.rules.max_new_leads_per_run) || 50, min_score: Number(c.rules.min_score), credit_cap: Number(c.rules.credit_cap ?? 100) } };
  };
  const save = async () => {
    if (!c.name.trim()) { setStep(0); throw new Error("Give the campaign a name first."); }
    const badPage = pages.find((p) => !/^https?:\/\/\S+$/i.test(p));
    if (badPage) { setStep(1); throw new Error(`Careers page links must start with http:// or https:// (check "${badPage.slice(0, 60)}").`); }
    const payload = body();
    const saved = c.id ? await api.saveCampaign(c.id, payload) : await api.createCampaign(payload);
    setC((x) => ({ ...x, id: saved.id, sources: { ...x.sources, careers_html: saved.sources.careers_html } }));
    setPaste({ url: "", html: "" });
    onSaved?.(saved);
    return saved;
  };
  const act = async (kind) => {
    setBusy(kind); setError("");
    try {
      const saved = await save();
      if (kind === "preview") { setReport(null); setPreview(await api.runCampaign(saved.id, true)); }
      if (kind === "real") { setPreview(null); setReport(await api.runCampaign(saved.id, false)); }
    } catch (e) { setError(e.message); }
    setBusy("");
  };
  const [catalog, setCatalog] = useState([]);
  useEffect(() => { api.sources().then((d) => setCatalog(d.sources)).catch(() => {}); }, []);
  const [sequences, setSequences] = useState([]);
  useEffect(() => { api.sequences().then((rows) => setSequences(Array.isArray(rows) ? rows : [])).catch(() => {}); }, []);
  const found = catalog.filter((x) => x.discovers && !x.legacy);
  const enrichers = catalog.filter((x) => (x.enriches || x.verifies) && x.id !== "apollo");
  const s = c.sources, r = c.rules, t = c.target;
  const pageCount = pages.length + s.careers_html.length + (paste.html.trim() ? 1 : 0);
  const anySource = s.apollo_search || s.apollo_hiring || s.google_places || (s.extra || []).length > 0 || pageCount > 0 || s.csv_import_ids.length > 0;

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>{c.id ? `Edit: ${c.name}` : "New campaign"}</h2><p className="sub">Pick who to reach, where to look, and the rules. Preview first; nothing is saved to your leads until you run it for real.</p></div>
        <button className="btn ghost" onClick={onCancel}>Back to campaigns</button>
      </div>
      <ol className="wizard-steps" aria-label="Campaign steps">
        {STEPS.map((label, i) => <li key={label}><button type="button" className={i === step ? "on" : ""} aria-current={i === step ? "step" : undefined} onClick={() => setStep(i)}>{label}</button></li>)}
      </ol>

      {step === 0 && (
        <div className="card">
          <label htmlFor="camp-name">Campaign name</label>
          <input id="camp-name" value={c.name} placeholder="e.g. Pune BPOs hiring support staff" onChange={(e) => setC({ ...c, name: e.target.value })} />
          <Chips label="Industries" field="industries" values={t.industries} onChange={(v) => set("target", "industries", v)} />
          <Chips label="Cities" field="cities" values={t.cities} onChange={(v) => set("target", "cities", v)} />
          <Chips label="Roles they hire for" field="roles_hiring" values={t.roles_hiring} onChange={(v) => set("target", "roles_hiring", v)} />
          <label>Company size (employees)</label>
          <div className="chips">
            {SIZES.map((b) => {
              const on = t.company_size_buckets.includes(b);
              return <button key={b} type="button" className={`chip${on ? " on" : ""}`} aria-pressed={on}
                onClick={() => set("target", "company_size_buckets", on ? t.company_size_buckets.filter((x) => x !== b) : [...t.company_size_buckets, b])}>{b}</button>;
            })}
          </div>
          <Chips label="Keywords" field="keywords" values={t.keywords} onChange={(v) => set("target", "keywords", v)} />
        </div>
      )}

      {step === 1 && (
        <div className="card">
          <p className="sub" style={{ margin: 0 }}>Only lawful sources: provider accounts you connect with your own keys, and companies' own careers pages. Job boards and LinkedIn are never read.</p>
          <h3 style={{ margin: "14px 0 0" }}>Company careers pages</h3>
          <small className="sub-inline">Free. Reads job openings (hiring signals) from each company's own careers page, honouring robots.txt.</small>
          <label htmlFor="camp-pages">Careers page links (one per line)</label>
          <textarea id="camp-pages" rows={3} placeholder="https://company.in/careers" value={pagesText} onChange={(e) => setPagesText(e.target.value)} />
          <details style={{ marginTop: 8 }}>
            <summary>Site blocks automatic reading? Paste the page's HTML instead</summary>
            <label htmlFor="camp-paste-url">Page link (optional)</label>
            <input id="camp-paste-url" value={paste.url} placeholder="https://company.in/careers" onChange={(e) => setPaste({ ...paste, url: e.target.value })} />
            <label htmlFor="camp-paste-html">Page HTML</label>
            <textarea id="camp-paste-html" rows={4} value={paste.html} onChange={(e) => setPaste({ ...paste, html: e.target.value })}
              placeholder="Open the careers page in your browser, View page source, copy everything, paste here." />
            {s.careers_html.length > 0 && <small className="sub-inline">{s.careers_html.length} pasted page(s) saved with this campaign.
              <button type="button" className="linkish" style={{ marginLeft: 6 }} onClick={() => set("sources", "careers_html", [])}>Remove them</button></small>}
          </details>
          <Check checked={s.apollo_search} onChange={(v) => set("sources", "apollo_search", v)} hint="HR heads, talent acquisition leads and founders at matching companies. Uses your Apollo plan.">
            <b>Apollo people search</b> <Ready state={status?.apollo} go={go} />
          </Check>
          <Check checked={s.apollo_hiring} onChange={(v) => set("sources", "apollo_hiring", v)} hint="Companies with open jobs for the roles above, with their job postings. Uses your Apollo plan.">
            <b>Apollo: companies hiring for these roles</b> <Ready state={status?.apollo} go={go} />
          </Check>
          <Check checked={s.google_places} onChange={(v) => set("sources", "google_places", v)} hint="Businesses by type and city, with website and office phone. Google bills each search.">
            <b>Google Places</b> <Ready state={status?.google_places} go={go} />
          </Check>
          <h3 style={{ margin: "14px 0 0" }}>More sources (free ones first)</h3>
          <small className="sub-inline">Public job-board feeds, official job and search APIs, open data, and your own keys. Each is lawful: no LinkedIn, no job-board scraping.</small>
          <MoreSources kind="discover" items={[...found].sort((a, b) => Number(!a.free) - Number(!b.free))} picked={s.extra || []} go={go}
            onPick={(v) => set("sources", "extra", v)} params={s.extra_params || {}} onParams={(v) => set("sources", "extra_params", v)} />
          <label htmlFor="camp-max">Most results per paid search, per run</label>
          <input id="camp-max" type="number" min="1" max="100" style={{ maxWidth: 140 }} value={s.max_results} onChange={(e) => set("sources", "max_results", e.target.value)} />
          <label>Leads you already imported (optional)</label>
          <LeadPicker picked={s.csv_import_ids} onChange={(v) => set("sources", "csv_import_ids", v)} />
        </div>
      )}

      {step === 2 && (
        <div className="grid g2">
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Fill in missing contacts</h3>
            <Check checked={c.enrich.crawler} onChange={(v) => set("enrich", "crawler", v)} hint="Free. Reads public contact pages and honours robots.txt.">Company website</Check>
            <Check checked={c.enrich.hunter} onChange={(v) => set("enrich", "hunter", v)} hint="One search per company that still has no email.">Hunter <Ready state={status?.hunter} go={go} /></Check>
            <Check checked={c.enrich.firecrawl} onChange={(v) => set("enrich", "firecrawl", v)} hint="For websites that need JavaScript. A few page credits per company.">Firecrawl <Ready state={status?.firecrawl} go={go} /></Check>
            <Check checked={c.enrich.apollo_match} onChange={(v) => set("enrich", "apollo_match", v)} hint="Reveals the work email of people found in Apollo. About one credit per person.">Apollo email lookup <Ready state={status?.apollo} go={go} /></Check>
            <h4 style={{ margin: "14px 0 0" }}>More ways to find and check emails</h4>
            <small className="sub-inline">Tried free first, then your paid keys, cheapest first; it stops at the first verified email.</small>
            <MoreSources kind="enrich" items={[...enrichers].sort((a, b) => Number(!a.free) - Number(!b.free))} picked={c.enrich.extra || []} go={go}
              onPick={(v) => set("enrich", "extra", v)} params={{}} onParams={() => {}} />
            <label htmlFor="camp-credits">Most credits to spend per run</label>
            <input id="camp-credits" type="number" min="0" max="5000" style={{ maxWidth: 140 }} value={r.credit_cap ?? 100} onChange={(e) => set("rules", "credit_cap", e.target.value)} />
            <p className="sub" style={{ margin: "10px 0 0" }}>Only empty fields are filled, and each value keeps where it came from. Email check: {status?.email_verifier?.connected ? "your email verifier is used." : "format and business domain only (connect ZeroBounce or NeverBounce for a real check)."}</p>
          </div>
          <div className="card">
            <h3 style={{ marginTop: 0 }}>Who is ready for email</h3>
            <label htmlFor="camp-score">Lowest score: {r.min_score}</label>
            <input id="camp-score" type="range" min="0" max="100" step="5" value={r.min_score} onChange={(e) => set("rules", "min_score", Number(e.target.value))} />
            <Check checked={r.require_business_email} onChange={(v) => set("rules", "require_business_email", v)}>Only business emails (no Gmail, Yahoo…)</Check>
            <Check checked={r.skip_role_addresses} onChange={(v) => set("rules", "skip_role_addresses", v)}>Skip shared mailboxes (hr@, info@…)</Check>
            <label htmlFor="camp-cap">Most new leads per run</label>
            <input id="camp-cap" type="number" min="1" max="200" style={{ maxWidth: 140 }} value={r.max_new_leads_per_run} onChange={(e) => set("rules", "max_new_leads_per_run", e.target.value)} />
            <label htmlFor="camp-caller">Who gets the update after each run</label>
            <input id="camp-caller" value={r.caller} onChange={(e) => set("rules", "caller", e.target.value)} />
            <Check checked={r.auto_enrol} onChange={(v) => set("rules", "auto_enrol", v)}><b>Start the email sequence automatically for qualifying leads</b></Check>
            {r.auto_enrol && (
              <div style={{ marginTop: 8 }}>
                <label htmlFor="camp-sequence">Which email sequence</label>
                <select id="camp-sequence" value={r.sequence_id || "default"} onChange={(e) => set("rules", "sequence_id", e.target.value)}>
                  {!sequences.some((q) => q.id === "default") && <option value="default">Default (6 emails)</option>}
                  {sequences.map((q) => <option key={q.id} value={q.id}>{q.name}</option>)}
                </select>
                <label htmlFor="only-companies">Only email these companies (optional)</label>
                <textarea id="only-companies" rows={2} placeholder="One company name per line. Leave empty to email every qualifying lead."
                  value={(r.only_companies || []).join("\n")}
                  onChange={(e) => set("rules", "only_companies", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean))} />
                <small className="sub-inline">{(r.only_companies || []).length ? `Only ${(r.only_companies || []).length} chosen compan${(r.only_companies || []).length === 1 ? "y" : "ies"} will be emailed; other qualifying leads stay in the list unstarted.` : "Nothing is held back."}</small>
              </div>
            )}
            {r.auto_enrol && <div className="tip" style={{ marginTop: 8 }} role="note">Sending still follows the Outbox rules: practice mode until "Send for real" is on, daily caps and warm-up, one email per company a day, Mon–Fri 09:00–18:00 IST, and unsubscribes are never emailed.</div>}
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="card">
          <h3 style={{ marginTop: 0 }}>{c.name || "Unnamed campaign"}</h3>
          <ul className="steps">
            <li>Target: {[t.industries.join(", "), t.cities.join(", "), t.roles_hiring.length ? `hiring ${t.roles_hiring.join(", ")}` : "", t.company_size_buckets.length ? `${t.company_size_buckets.join(", ")} employees` : ""].filter(Boolean).join(" · ") || "anyone (add targeting for better results)"}</li>
            <li>Sources: {[pageCount ? `${pageCount} careers page(s)` : "", s.apollo_search && "Apollo people", s.apollo_hiring && "Apollo hiring companies", s.google_places && "Google Places", (s.extra || []).length ? `${s.extra.length} more source(s)` : "", s.csv_import_ids.length ? `${s.csv_import_ids.length} imported lead(s)` : ""].filter(Boolean).join(", ") || "none yet"}</li>
            <li>Ready for email: score {r.min_score}+{r.require_business_email ? ", business email" : ""}{r.skip_role_addresses ? ", no shared mailboxes" : ""}; up to {r.max_new_leads_per_run} new leads a run.</li>
            <li>{r.auto_enrol ? `Qualifying leads start the email sequence “${(sequences.find((q) => q.id === (r.sequence_id || "default")) || { name: "Default (6 emails)" }).name}” (sending rules still apply).` : "Nobody is emailed automatically; you start the sequence yourself."} {r.caller || "The caller"} gets an update after each run.</li>
          </ul>
          {canWrite() && <div className="row" style={{ marginTop: 12 }}>
            <button className="btn ghost" disabled={!!busy || !anySource} onClick={() => act("preview")}>{busy === "preview" ? "Searching… (can take a minute)" : "Run now (preview)"}</button>
            <button className="btn" disabled={!!busy || !anySource} onClick={() => act("real")}>{busy === "real" ? "Running… (can take a few minutes)" : "Run for real"}</button>
            <button className="btn ghost" disabled={!!busy} onClick={() => { setBusy("save"); setError(""); save().catch((e) => setError(e.message)).finally(() => setBusy("")); }}>Save without running</button>
          </div>}
          {!anySource && <p className="sub" style={{ margin: "8px 0 0" }}>Pick at least one source in step 2.</p>}
          <p className="sub" style={{ margin: "8px 0 0" }}>The preview searches the sources (paid ones can use credits) but saves nothing and does not look up missing emails.</p>
        </div>
      )}

      {error && <div className="tip error" role="alert" style={{ marginTop: 12 }}>{error}</div>}
      {preview && <><RunReport report={preview} /><PreviewTable rows={preview.preview} /></>}
      {report && <RunReport report={report} onOpenLeads={() => onOpenLeads(c.id)} />}
      {step < 3 && (
        <div className="row" style={{ marginTop: 12, justifyContent: "space-between" }}>
          <button className="btn ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
          <button className="btn" onClick={() => setStep(step + 1)}>Next</button>
        </div>
      )}
    </div>
  );
}
