import { istLabel } from "../time.js";

export const STATUS = {
  draft: ["", "Draft"], running: ["ok", "Runs every weekday"], paused: ["warn", "Paused"], done: ["", "Done"],
};

const WOULD = {
  add: "Would add", merge: "Would fill gaps of a lead you have", duplicate: "Already in your leads",
  suppressed: "Skipped: unsubscribed, bounced or disqualified", over_cap: "Over this run's limit", no_contact: "Skipped: nothing to identify it",
};

export function StatusPill({ status }) {
  const [tone, label] = STATUS[status] || ["", status];
  return <span className={`pill ${tone}`}>{label}</span>;
}

function Num({ label, value }) {
  return <div className="card stat" style={{ padding: 12 }}><span className="sub-inline">{label}</span><b style={{ fontSize: 24, marginTop: 2 }}>{value ?? 0}</b></div>;
}

// The numbers of one run (or preview), with what didn't work per source.
export function RunReport({ report, onOpenLeads }) {
  if (!report) return null;
  const v = report.verification || {};
  return (
    <div className="card" data-testid="run-report" style={{ marginTop: 14 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 style={{ margin: 0 }}>{report.dry_run ? "Preview: nothing was saved" : "Run report"}</h3>
        <small className="sub-inline">{istLabel(report.started_at)}{report.trigger === "auto" ? " · automatic" : ""}</small>
      </div>
      <div className="grid g4" style={{ marginTop: 12, gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 130px), 1fr))" }}>
        <Num label="Found" value={report.discovered} />
        <Num label={report.dry_run ? "Would add" : "New leads"} value={report.new} />
        <Num label="Already known" value={report.duplicates} />
        <Num label="Skipped (unsubscribed, bounced…)" value={report.suppressed_skipped} />
        {!report.dry_run && <Num label="Emails found" value={report.enriched_emails} />}
        {!report.dry_run && <Num label="Phones found" value={report.enriched_phones} />}
        <Num label={report.dry_run ? "Would qualify" : "Ready for email"} value={report.qualified} />
        <Num label={report.dry_run ? "Would start sequence" : "Sequence started"} value={report.enrolled} />
      </div>
      <p className="sub" style={{ margin: "10px 0 0" }}>
        {report.merged ? `${report.merged} existing lead(s) completed. ` : ""}
        {report.attached ? `${report.attached} picked lead(s) joined. ` : ""}
        {report.over_cap ? `${report.over_cap} left for the next run (limit per run). ` : ""}
        {!report.dry_run && `Emails checked: ${v.valid || 0} verified, ${v.unverified || 0} format-checked only, ${v.invalid || 0} bad, ${v.risky || 0} unsure. `}
        Credits used (estimate): {report.credits_used_estimate}.
      </p>
      {report.errors?.length > 0 && (
        <div className="tip" style={{ marginTop: 10 }} role="status">
          <b>What didn't work</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {report.errors.map((e, i) => <li key={i} style={{ overflowWrap: "anywhere" }}><b>{e.source}:</b> {e.message}</li>)}
          </ul>
        </div>
      )}
      {onOpenLeads && !report.dry_run && <button className="btn" style={{ marginTop: 12 }} onClick={onOpenLeads}>Open these leads</button>}
    </div>
  );
}

// What a preview would do with each company it found.
export function PreviewTable({ rows }) {
  if (!rows?.length) return <p className="sub" style={{ marginTop: 12 }}>The preview found no companies. Check the errors above or add sources.</p>;
  return (
    <div className="card table-card" style={{ marginTop: 12 }}>
      <table className="leads-table" data-testid="preview-table">
        <thead><tr><th>Company</th><th>Contact</th><th>Found by</th><th>Score</th><th>What would happen</th><th>Ready for email?</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td data-label="Company"><b>{r.company || r.domain}</b>{r.role && <><br /><small>Hiring: {r.role}</small></>}</td>
              <td data-label="Contact">{r.contact_name || "—"}<br /><small>{r.email || r.phone || ""}</small></td>
              <td data-label="Found by">{r.sources.join(", ")}</td>
              <td data-label="Score">{r.score ?? "—"}</td>
              <td data-label="What would happen">{WOULD[r.would] || r.would}</td>
              <td data-label="Ready for email?">
                {r.score === null ? "—" : r.qualified ? <span className="pill ok">{r.would_enrol ? "Yes, sequence would start" : "Yes"}</span>
                  : <small>{r.reasons.join("; ")}</small>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
