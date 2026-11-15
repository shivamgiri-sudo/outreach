import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";
import IcpCard from "./IcpCard.jsx";
import DownloadButton, { exportPath } from "../DownloadButton.jsx";
import "../data-pages.css";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState, Field, Input, PageHeader, Skeleton } from "../ui/index.js";

const TRUST_PILL = { confirmed: "success", published: "success", published_doubtful: "warning", published_mismatch: "warning", invalid: "warning", guess_mx: "neutral", guess_risky: "warning", guess_rejected: "warning", none: "neutral" };
const TRUST_SHORT = { confirmed: "Confirmed", published: "Published", published_doubtful: "Doubtful", published_mismatch: "Wrong company?", invalid: "Invalid", guess_mx: "Guess", guess_risky: "Risky guess", guess_rejected: "Rejected", none: "No email" };
const STEPS = [
  ["1", "Hiring signal", "Open roles, how long they have been open and reposts, from the job sources."],
  ["2", "Who posted it", "Staffing firms and job boards are flagged as agencies, so you target the real employer."],
  ["3", "Decision maker", "HR head, TA head, CHRO or founder, from Find & enrich and your connected finders."],
  ["4", "Email", "A published address is used as is. Otherwise the company's own format is inferred and candidate addresses are built. A guess is never used for sending."],
  ["5", "Verify and compile", "Each guess is checked without sending anything. Only a mailbox a verifier calls valid becomes the lead's email. The sheet shows how far to trust each row."],
];

export default function HiringEngine() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState(null);
  const [maxLeads, setMaxLeads] = useState(50);
  const [maxCredits, setMaxCredits] = useState(30);
  const [recheck, setRecheck] = useState(false);
  const [checkPublished, setCheckPublished] = useState(0);
  const load = () => { setError(""); api.hiringSummary().then(setData).catch((e) => setError(e.message)); };
  useEffect(() => { load(); }, []);

  const run = async (dry) => {
    setBusy(true); setReport(null);
    try { setReport({ ...(await api.hiringRun({ max_leads: Number(maxLeads), max_credits: Number(maxCredits), dry_run: dry, recheck_guesses: recheck, check_published: Number(checkPublished) })), dry }); load(); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <>
      <PageHeader title="Hiring engine"
        description="One pass that turns a company's hiring signal into the right person and an email, and says honestly how far each result can be trusted." />
      <IcpCard />
      {error && <ErrorState message="The hiring engine could not load." detail={error} onRetry={load} />}
      <div className="dp-two">
        <Card>
          <CardHeader title="How it works" />
          <ol className="dp-steps">{STEPS.map(([n, title, text]) => <li key={n}><b>{title}.</b> {text}</li>)}</ol>
        </Card>
        <Card>
          <CardHeader title="Run it" description="Works on your busiest hirers first. Emails are only checked, never sent." />
          <div className="dp-run-grid">
            <Field label="Leads to check"><Input type="number" min="1" max="1000" value={maxLeads} onChange={(e) => setMaxLeads(e.target.value)} /></Field>
            <Field label="Also verify this many published emails"><Input type="number" min="0" max="1000" value={checkPublished} onChange={(e) => setCheckPublished(e.target.value)} /></Field>
            <Field label="Most paid checks"><Input type="number" min="0" max="500" value={maxCredits} onChange={(e) => setMaxCredits(e.target.value)} /></Field>
          </div>
          <label className="dp-check">
            <input type="checkbox" checked={recheck} onChange={(e) => setRecheck(e.target.checked)} />
            Also re-check guesses that are still unconfirmed (uses paid checks)
          </label>
          <div className="row u-mt-12">
            <Button variant="secondary" disabled={busy} onClick={() => run(true)}>Preview (changes nothing)</Button>
            <Button disabled={busy} onClick={() => run(false)}>{busy ? "Working…" : "Run the engine"}</Button>
          </div>
          {report && (
            <p className="tip u-mt-12" role="status">
              {report.dry ? "Preview: " : ""}checked {report.checked} leads · {report.employers} employers, {report.agencies} agencies ·
              {" "}{report.guessed} addresses built · {report.confirmed} confirmed, {report.kept_as_guess} kept as guesses, {report.rejected} rejected
              {report.skipped_no_name_or_domain ? ` · ${report.skipped_no_name_or_domain} have no decision maker name yet (run Find & enrich first)` : ""}
              {report.published_checked ? ` · ${report.published_checked} published emails checked: ${report.published_valid || 0} valid, ${report.published_invalid || 0} invalid, ${report.published_risky || 0} risky` : ""}
              {report.credits_used ? ` · ${report.credits_used} paid check(s) used` : ""}.
              {report.errors?.length > 0 && <><br />{report.errors.join(" ")}</>}
            </p>
          )}
        </Card>
      </div>
      {!data && !error && <div className="dp-skel" aria-busy="true"><Skeleton height={40} /><Skeleton height={220} /></div>}
      {data && (
        <>
          <div className="dp-badges">
            {Object.keys(data.labels).map((k) => (
              <Badge key={k} tone={TRUST_PILL[k]} title={data.labels[k]}>{TRUST_SHORT[k]}: {data.trust[k]}</Badge>
            ))}
            <Badge>Agencies: {data.posted_by.agency || 0}</Badge>
            <span className="grow">
              <DownloadButton path={exportPath("hiring_sheet")} label="Download the sheet" testId="download-hiring-sheet"
                title="One row per company with the decision maker, email and a trust column" />
            </span>
          </div>
          <Card className="table-card">
            {data.top.length === 0 ? <EmptyState icon="bot" title="No companies to show yet">Read job pages or run a campaign first; the engine works from the hiring signals they find.</EmptyState> : (
            <DataTable label="Hiring engine results">
              <thead><tr><th>Company</th><th>Open roles</th><th>Posted by</th><th>Decision maker</th><th>Email</th><th>Trust</th></tr></thead>
              <tbody>
                {data.top.map((r, i) => (
                  <tr key={i}>
                    <td><b>{r.company}</b><br /><small>{r.website}</small></td>
                    <td>{r.open_roles}{r.oldest_open_role_days ? <><br /><small>oldest {r.oldest_open_role_days} days</small></> : null}</td>
                    <td><Badge tone={r.posted_by === "agency" ? "warning" : "neutral"} title={r.posted_by_reason}>{r.posted_by}</Badge></td>
                    <td>{r.decision_maker || "—"}<br /><small>{r.title}</small></td>
                    <td>{r.email || r.guessed_email || "—"}{r.email_notes ? <><br /><small>{r.email_notes}</small></> : null}</td>
                    <td><Badge tone={TRUST_PILL[r.email_trust]} title={r.email_trust_detail}>{TRUST_SHORT[r.email_trust]}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </DataTable>)}
          </Card>
          <p className="sub u-mt-12">Showing the top {data.top.length} of {data.total}. The download has every row.</p>
        </>
      )}
    </>
  );
}
