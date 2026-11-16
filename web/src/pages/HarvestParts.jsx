// Lead harvest: one job card (status, progress, actions), its results table, and the DND numbers card.
import { useEffect, useState } from "react";
import { canWrite, isManager } from "../api.js";
import { harvestApi } from "../harvestApi.js";
import { istLabel } from "../time.js";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState, Field, Segmented, useToast } from "../ui/index.js";

const STATUS_TONE = { draft: "muted", queued: "info", running: "info", paused: "warning", done: "success", failed: "danger", cancelled: "neutral" };
const STATUS_TEXT = { draft: "Not started", queued: "Waiting", running: "Running", paused: "Paused", done: "Done", failed: "Failed", cancelled: "Cancelled" };
const LINE_TEXT = { mobile: "Mobile", fixed_line: "Landline", toll_free: "Toll-free", voip: "VoIP", mobile_or_fixed: "Mobile or landline", unknown: "Unknown" };
const SOURCE_TEXT = { google_places: "Google", osm: "OpenStreetMap" };
const ACTIONS = { draft: ["start", "cancel"], queued: ["pause", "cancel"], running: ["pause", "cancel"], paused: ["resume", "cancel"] };
const ACTION_TEXT = { start: "Start", pause: "Pause", resume: "Resume", cancel: "Cancel" };

export function StatusChip({ status }) {
  return <Badge tone={STATUS_TONE[status] || "neutral"} data-testid="harvest-status">{STATUS_TEXT[status] || status}</Badge>;
}

export function JobCard({ job, onChanged, open, onToggle }) {
  const toast = useToast();
  const [busy, setBusy] = useState("");
  const p = job.progress || {};
  const act = async (action) => {
    if (action === "cancel" && !window.confirm("Cancel this job? Leads already found stay in Leads.")) return;
    setBusy(action);
    try { await harvestApi.act(job.id, action); onChanged(); } catch (e) { toast(e.message, "err"); }
    setBusy("");
  };
  const exportCsv = async () => {
    try { await harvestApi.exportCsv(job.id); } catch (e) { toast(e.message, "err"); }
  };
  const c = job.counts || {};
  return (
    <Card className="hv-job" data-testid="harvest-job">
      <div className="hv-job-head">
        <div className="hv-job-title">
          <b>{job.name}</b>
          <small className="sub">{p.pairs_total} search{p.pairs_total === 1 ? "" : "es"} · {(job.sources || []).map((s) => SOURCE_TEXT[s] || s).join(" + ")} · {istLabel(job.created_at)} · {job.by}</small>
        </div>
        <StatusChip status={job.status} />
      </div>
      <progress className="hv-progress" max={Math.max(1, p.pairs_total || 1)} value={p.pairs_done || 0} aria-label={`${p.pairs_done} of ${p.pairs_total} searches done`} />
      <p className="hv-job-nums">
        <span>Found <b>{c.found || 0}</b> / est. {p.est_found || 0}</span>
        <span>New <b>{c.new || 0}</b></span>
        <span>Already in Leads <b>{c.duplicate || 0}</b></span>
        <span>On DND list <b>{c.dnd || 0}</b></span>
        {job.credit_cap > 0 && <span>Credits <b>{job.credits_used}</b> of {job.credit_cap}</span>}
      </p>
      {(job.notes || []).length > 0 && <ul className="hv-notes">{job.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      {job.error && <p className="dp-warn" role="alert">{job.error}</p>}
      <div className="row u-gap-8">
        {canWrite() && (ACTIONS[job.status] || []).map((a) => (
          <Button key={a} size="sm" variant={a === "cancel" ? "danger" : a === "pause" ? "secondary" : "primary"} disabled={!!busy} onClick={() => act(a)}>
            {busy === a ? "…" : ACTION_TEXT[a]}
          </Button>
        ))}
        <Button size="sm" variant="secondary" aria-expanded={open} onClick={onToggle}>{open ? "Hide results" : "Show results"}</Button>
        {isManager() && <Button size="sm" variant="ghost" icon="download" onClick={exportCsv}>Export CSV</Button>}
      </div>
      {open && <Results job={job} />}
    </Card>
  );
}

function PhoneCell({ row }) {
  if (!row.phone) return <span className="sub">—</span>;
  return <span className="hv-phone">{row.phone} <Badge tone={row.phone_line_type === "mobile" ? "success" : "muted"}>{LINE_TEXT[row.phone_line_type] || "Phone"}</Badge></span>;
}

function Results({ job }) {
  const [outcome, setOutcome] = useState("");
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    harvestApi.results(job.id, outcome).then((d) => live && setData(d)).catch((e) => live && setError(e.message));
    return () => { live = false; };
  }, [job.id, outcome, job.counts?.found]);
  if (error) return <p className="dp-warn" role="alert">{error}</p>;
  if (!data) return <p className="sub">Loading results…</p>;
  return (
    <div className="hv-results">
      <div className="row u-between">
        <Segmented label="Show" value={outcome} onChange={setOutcome}
          items={[{ id: "", label: "All" }, { id: "new", label: "New" }, { id: "duplicate", label: "Already in Leads" }, { id: "skipped", label: "Skipped" }]} />
        <small className="sub">{data.total} listing{data.total === 1 ? "" : "s"}{data.total > data.rows.length ? `, first ${data.rows.length} shown` : ""}</small>
      </div>
      {data.rows.length === 0 ? <EmptyState icon="search" title="Nothing here yet">Results appear as the job runs.</EmptyState> : (
        <DataTable label="Harvest results" density="dense" maxHeight="480px" data-testid="harvest-results">
          <thead><tr><th>Company</th><th>Category</th><th>Phone</th><th>Website</th><th>Rating</th><th>Reviews</th><th>City</th><th>Status</th><th>Lead</th></tr></thead>
          <tbody>
            {data.rows.map((r, i) => (
              <tr key={`${r.lead_id || "x"}-${i}`}>
                <td data-label="Company"><b>{r.company}</b><br /><small className="sub">{SOURCE_TEXT[r.source] || r.source} · {r.keyword} in {r.location}</small></td>
                <td data-label="Category">{r.category || "—"}</td>
                <td data-label="Phone"><PhoneCell row={r} /></td>
                <td data-label="Website">{r.website ? <a href={r.website} target="_blank" rel="noopener noreferrer nofollow">{r.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}</a> : "—"}</td>
                <td data-label="Rating">{r.rating ?? "—"}</td>
                <td data-label="Reviews">{r.reviews_count ?? "—"}</td>
                <td data-label="City">{r.city || "—"}</td>
                <td data-label="Status" className="hv-badges">
                  <Badge tone={r.outcome === "new" ? "success" : r.outcome === "duplicate" ? "info" : "muted"}>{r.outcome === "new" ? "New" : r.outcome === "duplicate" ? "Already in Leads" : r.outcome === "closed" ? "Closed" : "Skipped"}</Badge>
                  {r.dnd && <Badge tone="danger">DND</Badge>}
                </td>
                <td data-label="Lead">{r.lead_id ? <a href={`#leads?lead=${encodeURIComponent(r.lead_id)}`}>Open in Leads</a> : "—"}</td>
              </tr>
            ))}
          </tbody>
        </DataTable>
      )}
      <p className="hv-attrib">{data.attribution} Map pages are never scraped; results come from the official Google Places API and OpenStreetMap.</p>
    </div>
  );
}

export function DndCard({ count, onChanged }) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const readFile = async (file) => { if (file) setText(await file.text()); };
  const upload = async () => {
    setBusy(true);
    try {
      const r = await harvestApi.uploadDnd(text);
      toast(`${r.added} number(s) added to the do-not-contact list (${r.already} already there, ${r.invalid} not readable).`, "ok");
      setText(""); onChanged();
    } catch (e) { toast(e.message, "err"); }
    setBusy(false);
  };
  return (
    <Card>
      <CardHeader title="Do-not-call numbers" description={`${count} number(s) on the do-not-contact list. Harvested leads with these numbers are kept but marked DND.`} />
      {isManager() ? (
        <div className="hv-form">
          <Field label="Paste numbers or a CSV (any column)" help="One number per line or cell. +91, spaces and dashes are fine.">
            <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder={"98220 12345\n+91 80 4000 1500"} />
          </Field>
          <div className="row u-gap-8">
            <label className="btn ghost sm hv-file">Choose CSV file<input type="file" accept=".csv,.txt,text/csv,text/plain" onChange={(e) => readFile(e.target.files?.[0])} /></label>
            <Button size="sm" disabled={busy || !text.trim()} onClick={upload}>{busy ? "Adding…" : "Add to do-not-contact list"}</Button>
          </div>
        </div>
      ) : <p className="sub">Owners and workspace admins can add numbers.</p>}
    </Card>
  );
}
