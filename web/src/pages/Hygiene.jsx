import { Fragment, useEffect, useState } from "react";
import { istDateLabel, istLabel } from "../time.js";
import { api } from "../api.js";
import { stageLabel, sourceLabel } from "../labels.js";
import { ErrorState } from "../states.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";

const GROUPS_SHOWN = 20;
const FIELD_NAMES = {
  company: "Company", contact_name: "Name", title: "Title", email: "Email", domain: "Website", city: "City", state: "State",
  country: "Country", sector: "Industry", company_size_bucket: "Company size", linkedin_url: "LinkedIn", company_linkedin_url: "Company LinkedIn",
};

function Kpi({ n, label }) {
  return <div className="kpi"><b>{n}</b><span>{label}</span></div>;
}

function LeadCard({ lead, suggested, others, onMerge, busy }) {
  const rows = [
    ["Person", [lead.contact_name, lead.title].filter(Boolean).join(", ")], ["Email", lead.email], ["Phone", lead.phones.join(", ") || lead.phone],
    ["Website", lead.domain], ["City", lead.city], ["Stage", stageLabel(lead.stage)], ["Source", sourceLabel(lead)],
    ["Added", lead.created_at ? istDateLabel(lead.created_at) : ""],
    ["History", `${lead.events} event(s)${lead.emailed ? ", emailed" : ""}${lead.has_consent ? ", said yes" : ""}`],
    ["People", lead.other_contacts ? `${lead.other_contacts} more` : ""],
  ];
  return (
    <div className="lead-card">
      <b>{lead.company || lead.domain || lead.id}</b>
      {suggested && <span><span className="pill ok">Suggested to keep</span></span>}
      <dl>{rows.filter(([, v]) => v).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v}</dd></Fragment>)}</dl>
      <button className="btn ghost" disabled={busy} onClick={() => onMerge(lead, others)}>Keep this one, merge the others in</button>
    </div>
  );
}

function Clusters({ clusters, onMerge, busy }) {
  const [shown, setShown] = useState(GROUPS_SHOWN);
  if (!clusters.length) return <p className="sub">No duplicates found.</p>;
  return (
    <>
      {clusters.slice(0, shown).map((c) => (
        <div key={c.lead_ids.join("-")} className="section-block">
          <div className="row">{c.why.map((w) => <span key={w} className="pill warn">{w}</span>)}</div>
          <div className="compare">
            {c.leads.map((lead) => (
              <LeadCard key={lead.id} lead={lead} suggested={lead.id === c.suggested_keep} busy={busy}
                others={c.leads.filter((x) => x.id !== lead.id)} onMerge={onMerge} />
            ))}
          </div>
        </div>
      ))}
      {clusters.length > shown && <button className="btn ghost" onClick={() => setShown(shown + GROUPS_SHOWN)}>Show more ({clusters.length - shown} left)</button>}
    </>
  );
}

function Conflicts({ items, onResolve, busy }) {
  if (!items.length) return <p className="sub">Nothing to check. When an import brings a different value for a field that is already filled, it shows up here.</p>;
  return items.map((c) => (
    <div key={c.id} className="row" style={{ justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--line)" }}>
      <span style={{ flex: "1 1 240px", overflowWrap: "anywhere" }}>
        <b>{c.company || c.lead_id}</b> · {FIELD_NAMES[c.field] || c.field}: now “{String(c.kept)}”, {c.source} says “{String(c.incoming)}”
      </span>
      <span className="row">
        <button className="btn ghost" disabled={busy} onClick={() => onResolve(c, "keep")}>Keep current</button>
        <button className="btn ghost" disabled={busy} onClick={() => onResolve(c, "use_new")}>Use new</button>
      </span>
    </div>
  ));
}

function Standardise({ count, onChanged }) {
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const act = async (dryRun) => {
    setBusy(true); setError("");
    try {
      const r = await api.hygieneStandardise(dryRun);
      setPlan(dryRun ? r : null);
      if (!dryRun) onChanged(`Tidied ${r.changed} lead(s).`);
    } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <>
      <p className="sub" style={{ margin: "0 0 8px" }}>
        Applies the import clean-up to leads saved earlier: names, company names (legal suffix kept separately), titles, phones (+91),
        website domains, city, state and country. Emails, stages and consent are never changed. {count} lead(s) would change.
      </p>
      {error && <p role="alert" className="tip error">{error}</p>}
      {!plan && <button className="btn ghost" disabled={busy || count === 0} onClick={() => act(true)}>{busy ? "Checking…" : "Preview the clean-up"}</button>}
      {plan && (
        <>
          <ul>
            {plan.changes.slice(0, 30).map((c) => (
              <li key={c.lead_id} style={{ overflowWrap: "anywhere" }}><b>{c.company || c.lead_id}</b>:{" "}
                {Object.entries(c.fields).map(([f, v]) => `${f} “${Array.isArray(v.from) ? v.from.join(", ") : v.from ?? ""}” → “${Array.isArray(v.to) ? v.to.join(", ") : v.to}”`).join("; ")}
              </li>
            ))}
          </ul>
          {plan.changed > 30 && <p className="sub">…and {plan.changed - 30} more.</p>}
          <div className="row">
            <button className="btn" disabled={busy || plan.changed === 0} onClick={() => act(false)}>{busy ? "Saving…" : `Apply to ${plan.changed} lead(s)`}</button>
            <button className="btn ghost" onClick={() => setPlan(null)}>Cancel</button>
          </div>
        </>
      )}
    </>
  );
}

export default function Hygiene({ go }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api.hygieneReport().then((r) => { setReport(r); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  const act = async (work, done) => {
    setBusy(true);
    try { await work(); setMessage(done); await load(); } catch (e) { setMessage(e.message); }
    setBusy(false);
  };
  const merge = (keep, others) => {
    const names = others.map((o) => o.company || o.contact_name || o.id).join(", ");
    if (!window.confirm(`Merge ${names} into ${keep.company || keep.id}? The others are removed; their history, people, phones and messages move to this lead. The strictest status (unsubscribed, disqualified) is kept.`)) return;
    act(() => api.hygieneMerge(keep.id, others.map((o) => o.id)), `Merged ${others.length} lead(s) into ${keep.company || keep.id}.`);
  };
  const resolve = (c, choice) => act(() => api.resolveConflict(c.lead_id, c.id, choice), choice === "keep" ? "Kept the current value." : "Used the new value.");

  if (error) return <ErrorState message="Could not load the data hygiene report" detail={error} onRetry={load} />;
  if (!report) return <p className="empty">Loading…</p>;
  const m = report.missing, e = report.emails;
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div><h2>Data hygiene</h2><p className="sub">Find gaps, duplicates and differing values across every source, and fix them in one place.</p></div>
        {go && <button className="btn ghost" onClick={() => go("leads")}>Back to leads</button>}
      </div>
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message} <button className="linkish" aria-label="Dismiss message" onClick={() => setMessage("")}>✕</button></div>}
      <div className="card" style={{ marginBottom: 14 }}>
        <h3 style={{ marginTop: 0 }}>Overview of {report.total} leads</h3>
        <div className="kpi-row">
          <Kpi n={m.email} label="no email" /><Kpi n={m.phone} label="no phone" /><Kpi n={m.decision_maker} label="no decision maker" />
          <Kpi n={m.website} label="no website" /><Kpi n={e.invalid} label="invalid emails" /><Kpi n={e.role} label="shared inboxes (info@, hr@)" />
          <Kpi n={e.free_mail} label="personal emails" /><Kpi n={e.suppressed} label="on do-not-contact list" />
          <Kpi n={report.foreign_phones} label="numbers outside India" /><Kpi n={report.stale} label={`no activity in ${report.stale_days} days`} />
          <Kpi n={report.duplicate_clusters} label="groups of duplicates" /><Kpi n={report.conflicts} label="values to check" />
        </div>
      </div>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="row dl-head">
          <h3 style={{ marginTop: 0 }}>Duplicates ({report.duplicate_clusters})</h3>
          <DownloadButton path={exportPath("duplicates")} label="Download duplicates" disabled={!report.duplicate_clusters}
            title="Every duplicate group, one row per lead, with why they match" testId="download-duplicates" />
        </div>
        <p className="sub" style={{ margin: "0 0 8px" }}>Each group shows why the leads match. Pick the one to keep; nothing is lost from the others.</p>
        <Clusters clusters={report.clusters} onMerge={merge} busy={busy} />
      </div>
      <div className="card" style={{ marginBottom: 14 }}>
        <h3 style={{ marginTop: 0 }}>Different values to check ({report.conflicts})</h3>
        <Conflicts items={report.conflict_list} onResolve={resolve} busy={busy} />
      </div>
      <div className="card">
        <h3 style={{ marginTop: 0 }}>Standardise existing leads</h3>
        <Standardise count={report.needs_standardising} onChanged={(text) => { setMessage(text); load(); }} />
      </div>
    </>
  );
}
