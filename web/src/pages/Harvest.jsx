// Lead harvest: keyword x location searches on Google Places (your key) and OpenStreetMap, saved as de-duplicated leads.
import { useCallback, useEffect, useState } from "react";
import { canWrite } from "../api.js";
import { harvestApi } from "../harvestApi.js";
import "../harvest.css";
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Skeleton, Switch, useToast } from "../ui/index.js";
import { DndCard, JobCard } from "./HarvestParts.jsx";

const LIVE = new Set(["queued", "running"]);
const EMPTY = { name: "", keywords: "", locations: "", csv: "", csvName: "", sources: ["osm"], max_tiles: 9, credit_cap: "", deep_crawl: false };

function SourcePick({ id, title, info, form, setForm, go }) {
  const on = form.sources.includes(id);
  const usable = info?.connected && info?.allowed;
  const toggle = (v) => setForm({ ...form, sources: v ? [...form.sources, id] : form.sources.filter((s) => s !== id) });
  return (
    <div className="hv-source">
      <label className="dp-check">
        <input type="checkbox" checked={on && usable} disabled={!usable} onChange={(e) => toggle(e.target.checked)} />
        <span><b>{title}</b> {info?.paid ? <Badge tone="warning">Paid</Badge> : <Badge tone="success">Free</Badge>}<br /><small className="sub">{info?.cost_note}</small></span>
      </label>
      {info && !info.connected && (
        <p className="hv-hint">{info.reason} <button type="button" className="linkish" onClick={() => go("integrations")}>Open Integrations</button></p>
      )}
      {info && info.connected && !info.allowed && <p className="hv-hint">Only an owner can run paid Google searches.</p>}
    </div>
  );
}

function JobForm({ overview, onCreated, go }) {
  const toast = useToast();
  const [form, setForm] = useState(EMPTY);
  const [est, setEst] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => { setForm({ ...form, [k]: e.target.value }); setEst(null); };
  const body = () => ({ name: form.name, keywords: form.keywords, locations: form.locations, csv: form.csv, sources: form.sources,
    max_tiles: Number(form.max_tiles) || 9, deep_crawl: form.deep_crawl, ...(form.credit_cap !== "" ? { credit_cap: Number(form.credit_cap) } : {}) });
  const readCsv = async (file) => { if (file) { setForm({ ...form, csv: await file.text(), csvName: file.name }); setEst(null); } };
  const check = async () => {
    setBusy(true);
    try { setEst(await harvestApi.estimate(body())); } catch (e) { toast(e.message, "err"); }
    setBusy(false);
  };
  const create = async (start) => {
    setBusy(true);
    try {
      const job = await harvestApi.create({ ...body(), start });
      toast(start ? "Job started. It runs in the background; you can leave this page." : "Job saved. Press Start when ready.", "ok");
      setForm(EMPTY); setEst(null); onCreated(job);
    } catch (e) { toast(e.message, "err"); }
    setBusy(false);
  };
  const google = form.sources.includes("google_places");
  const e = est?.estimate;
  return (
    <Card>
      <CardHeader title="New harvest job" description={`Each keyword is searched in each location (up to ${overview.limits.max_pairs} searches per job).`} />
      <div className="hv-form">
        <Field label="Job name (optional)"><Input value={form.name} onChange={set("name")} maxLength={80} placeholder="Pune dentists" /></Field>
        <div className="hv-two">
          <Field label="Keywords" help="Comma or one per line: dentist, software company"><textarea rows={3} value={form.keywords} onChange={set("keywords")} disabled={!!form.csv} /></Field>
          <Field label="Locations" help="One per line: a city or state, a PIN code (560001), or lat,lng,radius km (12.97,77.59,3)"><textarea rows={3} value={form.locations} onChange={set("locations")} disabled={!!form.csv} /></Field>
        </div>
        <div className="row u-gap-8">
          <label className="btn ghost sm hv-file">{form.csvName ? `CSV: ${form.csvName}` : "Upload CSV of keyword, location"}<input type="file" accept=".csv,text/csv" onChange={(ev) => readCsv(ev.target.files?.[0])} /></label>
          {form.csv && <Button size="sm" variant="ghost" onClick={() => setForm({ ...form, csv: "", csvName: "" })}>Remove file</Button>}
          <Button size="sm" variant="ghost" icon="download" onClick={() => harvestApi.template().catch((err) => toast(err.message, "err"))}>Download template</Button>
        </div>
        <fieldset className="hv-sources">
          <legend>Sources</legend>
          <SourcePick id="google_places" title="Google Places" info={overview.sources.google_places} form={form} setForm={setForm} go={go} />
          <SourcePick id="osm" title="OpenStreetMap" info={overview.sources.osm} form={form} setForm={setForm} go={go} />
        </fieldset>
        <div className="hv-two">
          {google && <Field label="Most extra areas per search" help="When Google returns its full 60, the area is split into up to this many smaller areas (each up to 3 more credits)."><Input type="number" min={1} max={overview.limits.max_tiles} value={form.max_tiles} onChange={set("max_tiles")} /></Field>}
          {google && <Field label="Credit cap for this job" help={`Paid searches stop here. Leave empty for the estimate's maximum (at most ${overview.limits.max_credit_cap}).`}><Input type="number" min={0} max={overview.limits.max_credit_cap} value={form.credit_cap} onChange={set("credit_cap")} /></Field>}
        </div>
        <label className="row hv-switch"><Switch checked={form.deep_crawl} onChange={(v) => setForm({ ...form, deep_crawl: v })} label="Queue new leads for website lookup" /> Queue new leads for the website lookup (Fill in contacts)</label>
        {est && (
          <div className="hv-estimate" role="status" data-testid="harvest-estimate">
            <b>{est.pairs} search{est.pairs === 1 ? "" : "es"}.</b>{" "}
            {e.google_max > 0 ? <>Google credits: at least {e.google_min}, usually up to {e.google_typical}, at most {e.google_max} (cap {form.credit_cap !== "" ? form.credit_cap : e.default_cap}). </> : null}
            {e.note}
            {est.errors.length > 0 && <ul className="hv-notes">{est.errors.slice(0, 8).map((x) => <li key={x.row}>Row {x.row}: {x.error}</li>)}</ul>}
          </div>
        )}
        {canWrite() && (
          <div className="row u-gap-8">
            <Button variant="secondary" disabled={busy || !form.sources.length} onClick={check}>Check estimate</Button>
            <Button variant="secondary" disabled={busy || !est || !est.pairs} onClick={() => create(false)}>Save job</Button>
            <Button disabled={busy || !est || !est.pairs} onClick={() => create(true)}>Save and start</Button>
          </div>
        )}
      </div>
    </Card>
  );
}

export default function Harvest({ go }) {
  const [overview, setOverview] = useState(null);
  const [jobs, setJobs] = useState(null);
  const [open, setOpen] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(() => {
    harvestApi.jobs().then(setJobs).catch((e) => setError(e.message));
    harvestApi.overview().then(setOverview).catch((e) => setError(e.message));
  }, []);
  useEffect(() => { load(); }, [load]);
  const live = (jobs || []).some((j) => LIVE.has(j.status));
  useEffect(() => {
    if (!live) return undefined;
    const t = setInterval(() => harvestApi.jobs().then(setJobs).catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [live]);
  return (
    <>
      <PageHeader title="Lead harvest" help="harvest"
        description="Search businesses by keyword and location, then save them as leads with phones cleaned, duplicates merged and DND numbers marked." />
      {error && <p className="dp-warn" role="alert">{error}</p>}
      {!overview ? <Skeleton height={220} /> : (
        <div className="hv-layout">
          <div className="hv-main">
            {canWrite() && <JobForm overview={overview} go={go} onCreated={(job) => { setOpen(job.id); load(); }} />}
            <section aria-label="Harvest jobs" className="hv-jobs">
              <h3>Jobs</h3>
              {jobs === null ? <Skeleton height={120} /> : jobs.length === 0
                ? <EmptyState icon="search" title="No harvest jobs yet">Create one above. Results go straight into Leads.</EmptyState>
                : jobs.map((j) => <JobCard key={j.id} job={j} open={open === j.id} onToggle={() => setOpen(open === j.id ? "" : j.id)} onChanged={load} />)}
            </section>
          </div>
          <aside className="hv-side">
            <DndCard count={overview.dnd_numbers} onChanged={load} />
            <Card flat className="hv-legal">
              <b>Where the data comes from</b>
              <p className="sub">Google Places (New) through your own Google key, and OpenStreetMap (© OpenStreetMap contributors). Google Maps pages are never scraped and no other sites are crawled.</p>
            </Card>
          </aside>
        </div>
      )}
    </>
  );
}
