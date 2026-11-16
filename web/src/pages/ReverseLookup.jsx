import { useEffect, useRef, useState } from "react";
import { api, canWrite } from "../api.js";
import { ops } from "../opsApi.js";
import { Badge, Button, Card, CardHeader, DataTable, Field, Stat, Stats, useToast } from "../ui/index.js";
import "../data-pages.css";

const MAX = 2000;
const POLL_MS = 1500;
// [confidence, label, badge tone, plain-English meaning]
const CONF = {
  high: ["High", "success", "An exact match in your own data, or the name and this address on the same card of the company's website."],
  medium: ["Medium", "warning", "A name from the person's public Gravatar profile."],
  low: ["Low", "muted", "Only the company is known, not the person."],
  "": ["Not found", "neutral", "Nothing free and public names this address."],
};
const STEP = { lead: "Your lead", lead_contact: "Lead contact", prospect_db: "Prospect database", website: "Company website",
  site_markup: "Website details", gravatar: "Gravatar", lead_domain: "Your leads at this company" };
const FLAG = { free: "Free webmail", free_mail_company: "Webmail for a company contact", already_lead: "Already a lead",
  role: "Shared mailbox", suppressed: "Do not contact", bounced: "Bounced before", typo: "Typo" };

const isUrl = (u) => /^https?:\/\//i.test(u || "");

function Person({ r }) {
  const photo = isUrl(r.photo) ? r.photo : "";
  const logo = isUrl(r.logo) ? r.logo : "";
  return (
    <div className="rl-person">
      {photo ? <img className="rl-photo" src={photo} alt="" loading="lazy" referrerPolicy="no-referrer" />
        : logo ? <img className="rl-photo rl-logo" src={logo} alt="" loading="lazy" referrerPolicy="no-referrer" /> : null}
      <div>
        <b>{r.name || "Unknown person"}</b>
        {(r.title || r.company) && <small className="sub">{[r.title, r.company].filter(Boolean).join(" · ")}</small>}
        {isUrl(r.linkedin) && <small><a href={r.linkedin} target="_blank" rel="noreferrer noopener">LinkedIn</a></small>}
      </div>
    </div>
  );
}

function Results({ run, onFill, filling }) {
  const s = run.summary;
  return (
    <>
      <Stats>
        <Stat value={s.total} label="Addresses" />
        <Stat value={s.high} label="High confidence" tone="green" />
        <Stat value={s.medium} label="Medium" tone="amber" />
        <Stat value={s.low} label="Company only" tone="violet" />
      </Stats>
      <Card>
        <CardHeader title="Who is behind each address" description={`Showing the first ${run.shown} of ${s.total}. Download the CSV for every row.`}
          actions={<>
            <Button variant="secondary" icon="download" onClick={() => ops.reverseCsv(run.id)}>Download CSV</Button>
            {canWrite() && run.importable > 0 && <Button onClick={onFill} disabled={filling}>{filling ? "Working…" : `Create/fill leads (${run.importable})`}</Button>}
          </>} />
        <DataTable label="Reverse lookup results" maxHeight="60vh" density="dense">
          <thead><tr><th>Email</th><th>Person</th><th>Confidence</th><th>Found by</th></tr></thead>
          <tbody>
            {run.rows.map((r, i) => {
              const [label, tone, why] = CONF[r.confidence] || CONF[""];
              return (
                <tr key={`${i}-${r.email}`}>
                  <td data-label="Email" className="u-anywhere">{r.email}<small className="sub">{r.status.replace(/_/g, " ")}</small></td>
                  <td data-label="Person"><Person r={r} /></td>
                  <td data-label="Confidence"><Badge tone={tone} title={why}>{label}</Badge></td>
                  <td data-label="Found by" className="u-anywhere">
                    {r.sources.map((x) => <small key={x.step} className="sub">{STEP[x.step] || x.step}: {x.gave.join(", ")}</small>)}
                    {r.flags.map((f) => <Badge key={f} tone={f === "free_mail_company" ? "warning" : "info"} className="ec-flag">{FLAG[f] || f}</Badge>)}
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

// Reverse lookup: who is behind each email address. Free steps only; the server limits it to owners and workspace admins.
export default function ReverseLookup() {
  const toast = useToast();
  const [text, setText] = useState("");
  const [file, setFile] = useState("");
  const [run, setRun] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filling, setFilling] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const follow = (id) => {
    timer.current = setTimeout(() => ops.reverseRun(id).then((r) => {
      setRun(r);
      if (r.status === "running") follow(id);
      else if (r.status === "failed") setError(r.error);
    }).catch((e) => setError(e.message)), POLL_MS);
  };
  const lines = text.split(/[\s,;]+/).filter((x) => x.includes("@")).length;
  const start = async () => {
    setBusy(true); setError(""); setRun(null); clearTimeout(timer.current);
    try {
      const r = await ops.reverseStart(text, file);
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
  const fill = async () => {
    setFilling(true);
    try {
      const { csv } = await ops.reverseImport(run.id);
      const r = await api.importCommit({ csv, source: "Reverse lookup" });
      const s = r.summary || {};
      toast(`${s.create || 0} new lead(s), ${s.merge || 0} existing lead(s) filled (${s.fields_filled || 0} empty field(s)), ${s.unchanged || 0} unchanged.`, "ok");
    } catch (e) { toast(e.message, "err"); }
    setFilling(false);
  };
  const working = run?.status === "running";
  return (
    <>
      <Card>
        <CardHeader title="Who is behind these addresses?" description={`Paste addresses or choose a CSV with an email column. Up to ${MAX.toLocaleString()} at a time.`} />
        <div className="dp-fields">
          <Field label="Email addresses">
            <textarea className="ec-paste" rows={6} value={text} onChange={(e) => { setText(e.target.value); setFile(""); }}
              placeholder={"meera@cardco.in\nkiran@gmail.com"} aria-label="Email addresses to look up" />
          </Field>
          <div className="dp-toolbar">
            <label className="btn ghost ec-pick">
              <input type="file" accept=".csv,.txt,text/csv,text/plain" className="ec-file" onChange={pick} />Choose CSV file
            </label>
            <small className="sub">{file ? `${file}: ` : ""}{lines.toLocaleString()} address(es) found{lines > MAX ? ` — more than ${MAX.toLocaleString()}, split the file` : ""}</small>
            <Button icon="search" onClick={start} disabled={busy || working || !lines || lines > MAX}>{busy || working ? "Looking up…" : "Find who they are"}</Button>
          </div>
          <small className="sub">Free steps only: your own leads and prospect database first, then the company's own website and public Gravatar profiles. No paid lookup is used and no email is sent.</small>
        </div>
      </Card>
      {error && <p className="error" role="alert">{error}</p>}
      {working && (
        <Card>
          <p className="sub" role="status">Looking up… {run.done.toLocaleString()} of {run.total.toLocaleString()} done. Company websites are read politely, so big lists take a while.</p>
          <progress className="ec-progress" value={run.done} max={run.total || 1} />
        </Card>
      )}
      {run?.status === "done" && <Results run={run} onFill={fill} filling={filling} />}
    </>
  );
}
