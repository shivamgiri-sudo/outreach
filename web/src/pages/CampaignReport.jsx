import { istLabel } from "../time.js";
import "../campaigns.css";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState } from "../ui/index.js";

export const STATUS = {
  draft: ["", "Draft"], running: ["ok", "Runs every weekday"], paused: ["warn", "Paused"], done: ["", "Done"],
};

const WOULD = {
  add: "Would add", merge: "Would fill gaps of a lead you have", duplicate: "Already in your leads",
  suppressed: "Skipped: unsubscribed, bounced or disqualified", over_cap: "Over this run's limit", no_contact: "Skipped: nothing to identify it",
  attach: "Picked by you: would join", attach_blocked: "Picked by you: left out",
};

const TONES = { ok: "success", warn: "warning", "": "neutral" };

export function StatusPill({ status }) {
  const [tone, label] = STATUS[status] || ["", status];
  return <Badge tone={TONES[tone] || "neutral"}>{label}</Badge>;
}

function Num({ label, value }) {
  return <div className="card stat"><b>{value ?? 0}</b><span>{label}</span></div>;
}

// The numbers of one run (or preview), with what didn't work per source.
export function RunReport({ report, onOpenLeads }) {
  if (!report) return null;
  const v = report.verification || {};
  return (
    <Card className="camp-section" data-testid="run-report">
      <CardHeader title={report.dry_run ? "Preview: nothing was saved" : "Run report"}
        actions={<small className="sub-inline">{istLabel(report.started_at)}{report.trigger === "auto" ? " · automatic" : ""}</small>} />
      <div className="camp-stats">
        <Num label="Found" value={report.discovered} />
        <Num label={report.dry_run ? "Would add" : "New leads"} value={report.new} />
        <Num label="Already known" value={report.duplicates} />
        <Num label="Skipped (unsubscribed, bounced…)" value={report.suppressed_skipped} />
        {!report.dry_run && <Num label="Emails found" value={report.enriched_emails} />}
        {!report.dry_run && <Num label="Phones found" value={report.enriched_phones} />}
        <Num label={report.dry_run ? "Would qualify" : "Ready for email"} value={report.qualified} />
        <Num label={report.dry_run ? "Would start sequence" : "Sequence started"} value={report.enrolled} />
      </div>
      <p className="sub u-m0">
        {report.merged ? `${report.merged} existing lead(s) completed. ` : ""}
        {report.attached ? (report.dry_run ? `${report.attached} picked lead(s) would be included. ` : `${report.attached} picked lead(s) joined. `) : ""}
        {report.over_cap ? `${report.over_cap} left for the next run (limit per run). ` : ""}
        {!report.dry_run && `Emails checked: ${v.valid || 0} verified, ${v.unverified || 0} format-checked only, ${v.invalid || 0} bad, ${v.risky || 0} unsure. `}
        Credits used (estimate): {report.credits_used_estimate}.
      </p>
      {report.errors?.length > 0 && (
        <div className="tip u-mt-12" role="status">
          <b>What didn't work</b>
          <ul className="camp-errors">
            {report.errors.map((e, i) => <li key={i} className="u-anywhere"><b>{e.source}:</b> {e.message}</li>)}
          </ul>
        </div>
      )}
      {onOpenLeads && !report.dry_run && <div className="u-mt-12"><Button onClick={onOpenLeads}>Open these leads</Button></div>}
    </Card>
  );
}

// What a preview would do with each company it found.
export function PreviewTable({ rows }) {
  if (!rows?.length) return <EmptyState icon="search" title="The preview found no companies">Check the errors above, or go back to step 2 and add a source such as a careers page.</EmptyState>;
  return (
    <Card className="table-card u-mt-12">
      <DataTable tableClass="leads-table" data-testid="preview-table">
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
                {r.score === null ? (r.reasons?.length ? <small>{r.reasons.join("; ")}</small> : "—") : r.qualified ? <Badge tone="success">{r.would_enrol ? "Yes, sequence would start" : "Yes"}</Badge>
                  : <small>{r.reasons.join("; ")}</small>}
              </td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </Card>
  );
}
