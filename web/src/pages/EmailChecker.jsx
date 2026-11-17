import { useEffect, useRef, useState } from "react";
import { api, canAdminister, canWrite } from "../api.js";
import { ops } from "../opsApi.js";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState, Field, PageHeader, Stat, Stats, Tabs, useToast } from "../ui/index.js";
import ReverseLookup from "./ReverseLookup.jsx";
import "../data-pages.css";

const MAX = 10000;
const POLL_MS = 1500;
// [status, label, badge tone, plain-English meaning]
const STATUS = [
  ["valid", "Valid", "success", "Passed every check."],
  ["risky", "Risky", "warning", "A shared mailbox (info@, hr@) or a domain that keeps bouncing."],
  ["catch_all", "Catch-all", "warning", "The mail server accepts every address, so this mailbox cannot be confirmed."],
  ["unknown", "Unknown", "muted", "The domain could not be looked up right now. Try again later."],
  ["no_mail_server", "No mail server", "danger", "The domain does not exist or takes no email."],
  ["bounced", "Bounced before", "danger", "An email to this address bounced before."],
  ["typo", "Typo", "danger", "Looks like a typo of a popular domain (see the suggestion)."],
  ["disposable", "Throwaway", "danger", "A throwaway inbox service: nobody reads it."],
  ["suppressed", "Do not contact", "danger", "On your do-not-contact list."],
  ["duplicate", "Duplicate", "muted", "The same address is earlier in the list."],
  ["invalid_syntax", "Not an email", "danger", "This is not a valid email address."],
];
const BY = Object.fromEntries(STATUS.map((s) => [s[0], s]));
const FLAG_TEXT = { role: "Shared mailbox", free: "Free provider", already_lead: "Already a lead", suppressed: "Do not contact", bounced: "Bounced before", typo: "Typo" };

function Results({ run, onImport, importing }) {
  const s = run.summary;
  return (
    <>
      <Stats>
        <Stat value={s.total} label="Addresses" />
        <Stat value={s.statuses.valid} label="Valid" tone="green" />
        <Stat value={s.statuses.risky + s.statuses.catch_all + s.statuses.unknown} label="Risky or unconfirmed" tone="amber" />
        <Stat value={s.total - s.statuses.valid - s.statuses.risky - s.statuses.catch_all - s.statuses.unknown} label="Do not send" tone="violet" />
      </Stats>
      <Card>
        <CardHeader title="What we found" description={`Showing the first ${run.shown} of ${s.total}. Download the CSV for every row.`}
          actions={<>
            <Button variant="secondary" icon="download" onClick={() => ops.emailCheckCsv(run.id)}>Download CSV</Button>
            {canWrite() && run.valid_new > 0 && <Button onClick={onImport} disabled={importing}>{importing ? "Adding…" : `Add ${run.valid_new} valid ones as leads`}</Button>}
          </>} />
        <div className="dp-badges">
          {STATUS.filter(([k]) => s.statuses[k]).map(([k, label, tone, why]) => <Badge key={k} tone={tone} title={why}>{label}: {s.statuses[k]}</Badge>)}
          {Object.entries(s.flags).filter(([, n]) => n).map(([k, n]) => <Badge key={k} tone="info">{FLAG_TEXT[k] || k}: {n}</Badge>)}
        </div>
        <DataTable label="Checked addresses" maxHeight="60vh" density="dense">
          <thead><tr><th>Email</th><th>Status</th><th>Notes</th></tr></thead>
          <tbody>
            {run.rows.map((r, i) => {
              const [, label, tone, why] = BY[r.status] || [r.status, r.status, "neutral", ""];
              return (
                <tr key={`${i}-${r.email}`}>
                  <td data-label="Email" className="u-anywhere">{r.email}</td>
                  <td data-label="Status"><Badge tone={tone} title={why}>{label}</Badge></td>
                  <td data-label="Notes" className="u-anywhere">
                    {r.suggest && <span className="ec-suggest">Did you mean <b>{r.suggest}</b>? </span>}
                    {r.flags.filter((f) => f !== r.status).map((f) => <Badge key={f} tone="info" className="ec-flag">{FLAG_TEXT[f] || f}</Badge>)}
                    {r.reason && <small className="sub">{r.reason}</small>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </DataTable>
      </Card>
    </>
  );
}

// Standalone bulk email checker. The server lets owners and workspace admins run it (role check: canAdminister()).
export default function EmailChecker() {
  const toast = useToast();
  const [text, setText] = useState("");
  const [file, setFile] = useState("");
  const [run, setRun] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [tab, setTab] = useState("check");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const follow = (id) => {
    timer.current = setTimeout(() => ops.emailCheckRun(id).then((r) => {
      setRun(r);
      if (r.status === "running") follow(id);
      else if (r.status === "failed") setError(r.error);
    }).catch((e) => setError(e.message)), POLL_MS);
  };
  const lines = text.split(/[\s,;]+/).filter((x) => x.includes("@")).length;
  const start = async () => {
    setBusy(true); setError(""); setRun(null); clearTimeout(timer.current);
    try {
      const r = await ops.emailCheckStart(text, file);
      setRun(r);
      if (r.status === "running") follow(r.id);
    } catch (e) { setError(e.message); }
    setBusy(false);
  };
  const pick = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f.name); setText(await f.text());
  };
  const addAsLeads = async () => {
    setImporting(true);
    try {
      const { emails } = await ops.emailCheckValid(run.id);
      const r = await api.importCsv(`email\n${emails.join("\n")}`, "email checker");
      toast(`${r.created || 0} lead(s) added, ${r.duplicates || 0} already there.`, "ok");
    } catch (e) { toast(e.message, "err"); }
    setImporting(false);
  };

  if (!canAdminister()) {
    return <><PageHeader title="Email checker" /><EmptyState icon="lock" title="Owners and workspace admins only">Ask the owner to check a list for you.</EmptyState></>;
  }
  const working = run?.status === "running";
  return (
    <>
      <PageHeader title="Email checker" help="emailcheck"
        description="Check a list of email addresses before you send: spelling, throwaway inboxes, mail servers, and whether they are already leads, bounced or on your do-not-contact list." />
      <Tabs label="Email checker tools" value={tab} onChange={setTab}
        items={[{ id: "check", label: "Check addresses" }, { id: "reverse", label: "Reverse lookup", testId: "tab-reverse" }]} />
      {tab === "reverse" ? <ReverseLookup /> : <>
      <Card>
        <CardHeader title="Your list" description={`Paste addresses (one per line, or separated by commas) or choose a CSV with an email column. Up to ${MAX.toLocaleString()} at a time.`} />
        <div className="dp-fields">
          <Field label="Email addresses">
            <textarea className="ec-paste" rows={8} value={text} onChange={(e) => { setText(e.target.value); setFile(""); }}
              placeholder={"ravi@acme.com\nsita@beta.in"} aria-label="Email addresses to check" />
          </Field>
          <div className="dp-toolbar">
            <label className="btn ghost ec-pick">
              <input type="file" accept=".csv,.txt,text/csv,text/plain" className="ec-file" onChange={pick} />Choose CSV file
            </label>
            <small className="sub">{file ? `${file}: ` : ""}{lines.toLocaleString()} address(es) found{lines > MAX ? ` — more than ${MAX.toLocaleString()}, split the file` : ""}</small>
            <Button icon="check" onClick={start} disabled={busy || working || !lines || lines > MAX}>{busy || working ? "Checking…" : "Check addresses"}</Button>
          </div>
          <small className="sub">No mailbox is contacted: the check uses spelling rules, public DNS records and your own history. A catch-all or unknown address is never marked valid.</small>
        </div>
      </Card>
      {error && <p className="error" role="alert">{error}</p>}
      {working && (
        <Card>
          <p className="sub" role="status">Checking… {run.done.toLocaleString()} of {run.total.toLocaleString()} steps done. You can leave this page open; big lists take a few minutes.</p>
          <progress className="ec-progress" value={run.done} max={run.total || 1} />
        </Card>
      )}
      {run?.status === "done" && <Results run={run} onImport={addAsLeads} importing={importing} />}
      </>}
    </>
  );
}
