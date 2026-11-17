import { useEffect, useRef, useState } from "react";
import { isOwner } from "../api.js";
import { megabytes, register } from "../registerApi.js";
import { istLabel } from "../time.js";
import { Badge, Button, Card, CardHeader } from "../ui/index.js";
import CompanyRegisterBatch from "./CompanyRegisterBatch.jsx";
import "../register.css";

const pct = (done, total) => (total ? Math.min(100, Math.round((done / total) * 100)) : 0);

function ImportProgress({ state }) {
  if (!state || state.status === "idle") return null;
  const cur = state.current;
  return (
    <div role="status" className="u-mt-8">
      {state.status === "running" && cur && (
        <>
          <p className="sub u-m-4-0">Importing {cur.name}: {(cur.written || 0).toLocaleString("en-IN")} companies so far ({pct(cur.bytes_done, cur.bytes_total)}%).</p>
          <progress className="reg-progress" max={100} value={pct(cur.bytes_done, cur.bytes_total)} aria-label={`Import of ${cur.name}`} />
        </>
      )}
      {state.queue?.length > 0 && <p className="sub u-m-4-0">Waiting: {state.queue.join(", ")}</p>}
      {state.files?.length > 0 && (
        <ul className="reg-files">
          {state.files.map((f, i) => (
            <li key={`${f.name}-${i}`}>{f.status === "failed" ? `${f.name}: could not be imported. ${f.error}`
              : `${f.name}: ${f.written.toLocaleString("en-IN")} companies (${f.added.toLocaleString("en-IN")} new), ${f.skipped} row(s) skipped.`}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PullBox({ pull, onStart, busy }) {
  const running = pull?.status === "running";
  return (
    <details className="u-mt-12">
      <summary>Or pull through the data.gov.in API</summary>
      <p className="sub">Uses your data.gov.in key and the dataset's resource id from Integrations &gt; Lead data &gt; data.gov.in company master data.
        Reads 500 companies a page, one page every few seconds, up to 50 pages a run (25,000 companies). The next run carries on where this one stopped.</p>
      <Button variant="secondary" size="sm" disabled={busy || running} onClick={onStart}>{running ? "Pulling…" : "Pull the next 50 pages"}</Button>
      {pull && pull.status !== "idle" && (
        <p className="sub" role="status">
          {pull.status === "failed" ? `Stopped: ${pull.error}` : `${running ? "Working" : "Last pull"}: ${pull.pages} page(s), ${pull.written.toLocaleString("en-IN")} companies${pull.total ? ` (offset ${Number(pull.offset || 0).toLocaleString("en-IN")} of ${Number(pull.total).toLocaleString("en-IN")})` : ""}${pull.complete ? ", dataset fully read" : ""}.`}
        </p>
      )}
    </details>
  );
}

// Owners: the local Indian company register on the Data sources page: upload the RoC-wise CSV files (or pull them
// through data.gov.in), see counts per state, then match leads in batches.
export default function CompanyRegisterCard() {
  const [data, setData] = useState(null);
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const timer = useRef(null);
  const load = () => register.summary().then((d) => {
    setData(d);
    clearTimeout(timer.current);
    if (d.import?.status === "running" || d.pull?.status === "running") timer.current = setTimeout(load, 3000);
  }).catch((e) => setError(e.message));
  useEffect(() => { if (isOwner()) load(); return () => clearTimeout(timer.current); }, []);
  if (!isOwner()) return null;

  const send = async () => {
    setBusy(true); setError("");
    try {
      for (const f of files) await register.upload(f);          // one at a time: each file streams to the server's disk
      setFiles([]);
      load();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };
  const pull = async () => {
    setError("");
    try { await register.pull(50); load(); } catch (e) { setError(e.message); }
  };
  const last = data?.last_import;
  return (
    <Card as="section" className="an-section" aria-label="Company register">
      <CardHeader title="Company register (India)"
        description="The Ministry of Corporate Affairs company master data, kept on this server so leads can be matched without a lookup per lead. Gives CIN, status, incorporation date, capital, class, registered state, RoC, address and business activity." />
      {data && (
        <>
          <p className="u-m-4-0">
            <b>{(data.total || 0).toLocaleString("en-IN")}</b> companies
            {data.size_bytes > 0 && <span className="sub"> · {megabytes(data.size_bytes)} on disk</span>}
            {last && <span className="sub"> · last import {istLabel(last.at)} ({last.name})</span>}
          </p>
          {data.per_state?.length > 0 && (
            <div className="reg-states" aria-label="Companies per state">
              {data.per_state.slice(0, 40).map((s) => <Badge key={s.state}>{s.state}: {s.count.toLocaleString("en-IN")}</Badge>)}
            </div>
          )}
        </>
      )}
      <div className="reg-upload">
        <label htmlFor="register-files" className="sub">RoC-wise CSV files from data.gov.in (choose one or many)</label>
        <input id="register-files" type="file" multiple accept=".csv,text/csv" onChange={(e) => setFiles(Array.from(e.target.files || []))} />
        <Button disabled={busy || files.length === 0} onClick={send}>{busy ? "Uploading…" : files.length > 1 ? `Import ${files.length} files` : "Import"}</Button>
      </div>
      <p className="reg-note">Search data.gov.in for "Company Master Data" and download the CSV of each state (RoC) you need. Files are read row by row
        and matched by CIN, so importing a newer file again updates the companies in it.</p>
      <ImportProgress state={data?.import} />
      <PullBox pull={data?.pull} onStart={pull} busy={busy} />
      {error && <p className="error" role="alert">{error}</p>}
      <p className="reg-note">{data?.licence} The free data has no directors, phone numbers or websites; a director lookup would need a licensed source such as OpenCorporates.</p>
      <CompanyRegisterBatch empty={!data?.total} />
    </Card>
  );
}
