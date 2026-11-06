import { useCallback, useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import Icon from "../icons.jsx";
import DrawerShell from "../DrawerShell.jsx";

const MODES = [
  ["find_new", "Find new decision makers", "Searches Apollo and Google Places for HR, talent and founder contacts at new companies."],
  ["enrich_existing", "Fill in contacts for leads I already have", "Looks for emails, phones and decision makers for leads that have no email yet."],
  ["both", "Both", "Finds new people first, then fills in the leads you already have."],
];
const POLL_MS = 2000;
const LIVE = ["queued", "running"];
const TITLE_EXAMPLES = ["HR Head", "Talent Acquisition", "Founder", "Recruitment Manager", "CEO"];
const CITY_EXAMPLES = ["Noida", "Delhi", "Gurgaon", "Mumbai", "Pune", "Bengaluru"];
const SOURCES = [["apollo", "Apollo"], ["hunter", "Hunter"], ["firecrawl", "Firecrawl"], ["google_places", "Google Places"], ["crawler", "Company websites"]];

// Leads to show when the Leads page opens after "Open these leads" (set here, taken once by the Leads page).
let pendingIds = null;
export const setPendingLeadIds = (ids) => { pendingIds = ids; };
export const takePendingLeadIds = () => { const ids = pendingIds; pendingIds = null; return ids; };

function Chips({ label, values, examples, onChange }) {
  const [text, setText] = useState("");
  const add = (v) => { const t = v.trim(); if (t && !values.includes(t)) onChange([...values, t]); setText(""); };
  return (
    <div>
      <label htmlFor={`find-${label}`}>{label}</label>
      <div className="row">
        <input id={`find-${label}`} style={{ flex: "1 1 160px", width: "auto" }} value={text} placeholder="Type and press Enter"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(text); } }} />
        <button className="btn ghost" type="button" onClick={() => add(text)} aria-label={`Add to ${label}`}>Add</button>
      </div>
      <div className="chips" aria-label={`${label}: chosen`}>
        {values.map((v) => <button key={v} type="button" className="chip on" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>{v} ✕</button>)}
      </div>
      <div className="chips" aria-label={`${label}: examples`}>
        {examples.filter((v) => !values.includes(v)).map((v) => <button key={v} type="button" className="chip" onClick={() => add(v)}>+ {v}</button>)}
      </div>
    </div>
  );
}

function Connections({ status, go }) {
  if (!status) return null;
  return (
    <div className="find-conn" aria-label="What is connected">
      {SOURCES.map(([id, label]) => {
        const s = status[id];
        if (!s) return null;
        return s.connected
          ? <span key={id} className="pill ok">{label}: connected</span>
          : <span key={id} className="pill warn" title={s.reason}>{label}: not connected</span>;
      })}
      {!status.apollo?.connected && (
        <div className="tip" style={{ flexBasis: "100%" }} role="note">
          Apollo is not connected, so new decision makers cannot be searched and contacts can only come from company websites.{" "}
          <button type="button" className="linkish" onClick={() => go("integrations")}>Connect Apollo, Hunter or Google Places in Integrations</button>
        </div>
      )}
    </div>
  );
}

function Num({ label, value }) {
  return <div className="card stat" style={{ padding: 12 }}><span className="sub-inline">{label}</span><b style={{ fontSize: 24, marginTop: 2 }}>{value ?? 0}</b></div>;
}

function Progress({ run }) {
  const pct = run.total ? Math.min(100, Math.round((run.done / run.total) * 100)) : (run.status === "done" ? 100 : 5);
  return (
    <div className="card" data-testid="find-progress" style={{ marginTop: 14 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>{run.phase}</b><span className="sub-inline">{run.done} of {run.total || "…"}</span>
      </div>
      <div className="find-track" role="progressbar" aria-label="Progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
        <div className="find-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="sub" style={{ margin: "8px 0 0" }}>
        {run.found} new leads · {run.enriched} existing leads completed · {run.credits} of {run.credit_limit} credits used. You can close this window; the run keeps going.
      </p>
    </div>
  );
}

function PreviewRows({ rows }) {
  if (!rows?.length) return <p className="sub" style={{ marginTop: 12 }}>The preview found nothing to do. Check the notes above.</p>;
  return (
    <div className="card table-card" style={{ marginTop: 12 }}>
      <table className="leads-table" data-testid="find-preview-table">
        <thead><tr><th>Company</th><th>Person</th><th>What would happen</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td data-label="Company"><b>{r.company || r.domain}</b>{r.domain && <><br /><small>{r.domain}</small></>}</td>
              <td data-label="Person">{r.contact_name || "—"}<br /><small>{r.email || ""}</small></td>
              <td data-label="What would happen">
                {r.kind === "fill"
                  ? <ul style={{ margin: 0, paddingLeft: 16 }}>{r.steps.map((s) => <li key={s}>{s}</li>)}</ul>
                  : (r.would === "add" ? "New lead would be added" : r.would === "merge" ? "Would fill gaps of a lead you have" : r.would === "duplicate"
                    ? "Already in your leads" : r.would === "suppressed" ? "Skipped: unsubscribed, bounced or disqualified" : r.would)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Report({ run, onOpenLeads }) {
  const r = run.report;
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  if (!r) return null;
  const enrol = async () => {
    setBusy(true);
    const results = await Promise.allSettled(r.qualified_ids.map((id) => api.enroll(id)));
    const ok = results.filter((x) => x.status === "fulfilled").length;
    setMessage(`Started the email sequence for ${ok} of ${r.qualified_ids.length}.${ok < r.qualified_ids.length ? " The rest have no email, are already in it, or cannot be emailed." : ""} Emails still follow the Outbox rules: practice mode until you switch to live, daily limits and business hours.`);
    setBusy(false);
  };
  return (
    <div className="card" data-testid="find-report" style={{ marginTop: 14 }}>
      <h3 style={{ margin: 0 }}>{r.dry_run ? "Preview: nothing was saved" : run.status === "done" ? "Done" : "Run report"}</h3>
      <div className="grid g4" style={{ marginTop: 12, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 130px), 1fr))" }}>
        <Num label={r.dry_run ? "New leads it would add" : "New leads added"} value={r.new_leads} />
        <Num label={r.dry_run ? "Leads it would check" : "Existing leads completed"} value={r.dry_run ? r.checked || run.enriched : r.existing_completed} />
        {!r.dry_run && <Num label="Emails found" value={r.emails_found} />}
        {!r.dry_run && <Num label="Phones found" value={r.phones_found} />}
        {!r.dry_run && <Num label="Decision makers" value={r.decision_makers} />}
        {!r.dry_run && <Num label="Ready for email" value={r.qualified_ids.length} />}
        <Num label={`Credits used (limit ${r.credit_limit})`} value={r.credits_used} />
      </div>
      {r.stopped_reason && <div className="tip" style={{ marginTop: 10 }} role="status">{r.stopped_reason}</div>}
      {run.errors?.length > 0 && (
        <div className="tip" style={{ marginTop: 10 }} role="status">
          <b>What didn't work</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{run.errors.map((e, i) => <li key={i} style={{ overflowWrap: "anywhere" }}><b>{e.source}:</b> {e.message}</li>)}</ul>
        </div>
      )}
      {!r.dry_run && r.lead_ids.length > 0 && (
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" onClick={() => onOpenLeads(r.lead_ids)}>Open these leads</button>
          {isOwner() && r.qualified_ids.length > 0 && (
            <button className="btn ghost" disabled={busy} onClick={enrol}>{busy ? "Starting…" : `Start the email sequence for ${r.qualified_ids.length} qualified lead${r.qualified_ids.length === 1 ? "" : "s"}`}</button>
          )}
        </div>
      )}
      {message && <div className="tip" role="status" style={{ marginTop: 10 }}>{message}</div>}
      {run.preview && <PreviewRows rows={run.preview} />}
    </div>
  );
}

export function FindLeadsCard({ onOpen }) {
  return (
    <div className="card find-card" data-testid="find-card">
      <span className="stat-icon"><Icon name="search" /></span>
      <div className="find-card-text">
        <h3 style={{ margin: 0 }}>Find &amp; enrich leads</h3>
        <p className="sub" style={{ margin: "4px 0 0" }}>One click: search for HR, talent and founder contacts at new companies, and fill in the emails and phones of leads you already have. Only your connected providers and companies' own public pages are used.</p>
      </div>
      <button className="btn cta" onClick={onOpen}>Find &amp; enrich leads</button>
    </div>
  );
}

export default function FindLeadsDrawer({ onClose, go, onOpenLeads }) {
  const [mode, setMode] = useState("both");
  const [cities, setCities] = useState(["Noida", "Delhi", "Gurgaon"]);
  const [titles, setTitles] = useState(["HR Head", "Talent Acquisition", "Founder"]);
  const [industries, setIndustries] = useState([]);
  const [careers, setCareers] = useState("");
  const [limits, setLimits] = useState({ max_new: 25, max_enrich: 30, max_credits: 40 });
  const [estimate, setEstimate] = useState(null);
  const [run, setRun] = useState(null);
  const [error, setError] = useState("");
  const live = run && LIVE.includes(run.status);

  const body = useCallback((dry_run) => ({
    mode, cities, titles, industries, careers_pages: careers.split(/[\r\n,]+/).map((s) => s.trim()).filter(Boolean), dry_run,
    max_new: Number(limits.max_new) || 0, max_enrich: Number(limits.max_enrich) || 0, max_credits: Number(limits.max_credits) || 0,
  }), [mode, cities, titles, industries, careers, limits]);


  useEffect(() => {              // pick up a run that is already working (the window was closed and opened again)
    api.findRecent().then((d) => { if (d.active) setRun(d.active); }).catch(() => {});
  }, []);

  useEffect(() => {              // the estimate follows the choices
    if (live) return undefined;
    const timer = setTimeout(() => api.findEstimate(body(false)).then(setEstimate).catch(() => {}), 350);
    return () => clearTimeout(timer);
  }, [body, live]);

  useEffect(() => {              // progress, every 2 seconds, until the run ends
    if (!run || !LIVE.includes(run.status)) return undefined;
    const timer = setInterval(() => api.findRun(run.id).then(setRun).catch((e) => setError(e.message)), POLL_MS);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);

  const start = async (dry) => {
    setError(""); setRun(null);
    try { setRun(await api.findStart(body(dry))); } catch (e) { setError(e.message); }
  };
  const openLeads = (ids) => { onOpenLeads(ids); onClose(); };
  const fill = estimate?.fill, find = estimate?.find;
  const owner = isOwner();

  return (
    <DrawerShell onClose={onClose} className="wizard" labelledBy="find-title" testId="find-drawer">
        <div className="drawer-head">
          <div><h2 id="find-title" tabIndex={-1}>Find &amp; enrich leads</h2>
            <p className="sub" style={{ margin: 0 }}>Plain steps: choose, preview, run. Nothing is emailed from here.</p></div>
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>

        <fieldset className="find-modes" disabled={live} style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="sub" style={{ padding: 0 }}>What should it do?</legend>
          {MODES.map(([id, label, hint]) => (
            <label key={id} className="check-row" style={{ fontWeight: 400 }}>
              <input type="radio" name="find-mode" checked={mode === id} onChange={() => setMode(id)} />
              <span><b>{label}</b><small className="sub" style={{ display: "block", margin: 0 }}>{hint}</small></span>
            </label>
          ))}
        </fieldset>

        <Connections status={estimate?.connections} go={go} />

        <fieldset disabled={live} style={{ border: 0, padding: 0, margin: 0 }}>
          {mode !== "enrich_existing" && <Chips label="Cities" values={cities} examples={CITY_EXAMPLES} onChange={setCities} />}
          <Chips label="Job titles to look for" values={titles} examples={TITLE_EXAMPLES} onChange={setTitles} />
          <div className="find-limits">
            {mode !== "enrich_existing" && <label>Most new leads<input type="number" min="0" max="100" value={limits.max_new} onChange={(e) => setLimits({ ...limits, max_new: e.target.value })} /></label>}
            {mode !== "find_new" && <label>Most existing leads to check<input type="number" min="0" max="100" value={limits.max_enrich} onChange={(e) => setLimits({ ...limits, max_enrich: e.target.value })} /></label>}
            <label>Credit limit (Apollo, Hunter, Firecrawl)<input type="number" min="0" max="500" value={limits.max_credits} onChange={(e) => setLimits({ ...limits, max_credits: e.target.value })} /></label>
          </div>
          {mode !== "enrich_existing" && (
            <details style={{ marginTop: 8 }}>
              <summary>More options for finding new companies</summary>
              <Chips label="Industries" values={industries} examples={["BPO", "IT services", "Retail", "Logistics", "Staffing"]} onChange={setIndustries} />
              <label htmlFor="find-careers">Careers page links (optional, one per line)</label>
              <textarea id="find-careers" rows={2} placeholder="https://company.in/careers" value={careers} onChange={(e) => setCareers(e.target.value)} />
            </details>
          )}
        </fieldset>

        {estimate && !live && (
          <div className="tip" style={{ marginTop: 12 }} data-testid="find-estimate">
            {fill && <div>Leads to fill in: <b>{fill.will_check}</b> ({fill.with_website} with a website, {fill.name_only} with only a company name; {fill.available} waiting in all).</div>}
            {find && <div>{find.runnable ? <>New leads: up to <b>{find.new_cap}</b> from {find.searches.join(", ")}.</> : find.problems.map((p) => <div key={p}>{p}</div>)}</div>}
            <div>Provider credits this run could use: up to <b>{estimate.credits_estimate_max}</b> of your limit of {estimate.credit_limit}.
              {estimate.over_limit && " The run stops at the limit."} {estimate.connections.apollo.connected ? "" : "No Apollo, Hunter or Firecrawl credits are used while they are not connected."}</div>
          </div>
        )}

        {error && <div className="tip error" role="alert" style={{ marginTop: 12 }}>{error}</div>}
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn ghost" disabled={live} onClick={() => start(true)}>Preview</button>
          {owner
            ? <button className="btn cta" disabled={live} onClick={() => start(false)}>{live ? "Working…" : "Run now"}</button>
            : <span className="sub">Only the owner can start a real run.</span>}
        </div>
        <p className="sub" style={{ margin: "8px 0 0" }}>Preview shows what would happen and saves nothing. It may search with Apollo or Google Places, which can use credits.</p>

        {run && live && <Progress run={run} />}
        {run && !live && run.status === "interrupted" && <div className="tip error" role="alert" style={{ marginTop: 14 }}>This run was interrupted because the app restarted. Run it again: leads already checked are skipped.</div>}
        {run && !live && run.status === "failed" && <div className="tip error" role="alert" style={{ marginTop: 14 }}>The run stopped because of a problem. {run.errors?.map((e) => e.message).join(" ")}</div>}
        {run && !live && run.report && <Report run={run} onOpenLeads={openLeads} />}
    </DrawerShell>
  );
}
