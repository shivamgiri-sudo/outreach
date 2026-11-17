import { useEffect, useState } from "react";
import { getToken, isOwner } from "../api.js";
import { istLabel } from "../time.js";
import { GuideButton } from "../GuidePanel.jsx";
import "../data-pages.css";
import "../analytics-pages.css";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState, Field, Input, PageHeader, Skeleton } from "../ui/index.js";
import CompanyRegisterCard from "./CompanyRegisterCard.jsx";

const DEFAULT_SAMPLE = 25;
const MAX_SAMPLE = 100;

function errorText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg || String(d)).join("; ");
  return "";
}

async function call(path, body) {
  const token = getToken();
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(errorText(detail.detail) || `Request failed (${res.status})`);
  }
  return res.json();
}

const sources = {
  providers: () => call("/api/providers"),
  runs: () => call("/api/providers/runs"),
  compare: (providers, sample_size) => call("/api/providers/compare", { providers, sample_size }),
  apply: (run_id, provider) => call("/api/providers/apply", { run_id, provider }),
  importScraper: (json) => call("/api/providers/import-contact-scraper", { json }),
};

const when = (iso) => istLabel(iso);

function Alert({ text }) {
  return text ? <p role="alert" className="dp-warn">{text}</p> : null;
}

function ProviderList({ list, go }) {
  return (
    <Card>
      <CardHeader title="Providers" />
      {list.length === 0 && <EmptyState icon="database" title="No providers available">Providers appear here once they are enabled for your account.</EmptyState>}
      {list.map((p) => (
        <div className="dp-partner" key={p.id}>
          <span className="u-flex1 u-minwidth-220">
            <b>{p.title}</b><br /><small className="sub">{p.configured ? p.cost_note : p.reason}</small>
          </span>
          <span className="row">
            <Badge tone={p.configured ? "success" : "warning"}>{p.configured ? "Connected" : "Not set up"}</Badge>
            {p.setup_id && <GuideButton keys={`integration:${p.setup_id}`} label="How do I get this?" testId={`source-guide-${p.id}`} />}
            {!p.configured && <Button variant="secondary" onClick={() => go("setup")}>Set up in Setup</Button>}
          </span>
        </div>
      ))}
    </Card>
  );
}

function CompareForm({ list, onDone }) {
  const [picked, setPicked] = useState(() => list.filter((p) => p.configured).map((p) => p.id));
  const [size, setSize] = useState(DEFAULT_SAMPLE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const toggle = (id) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  const run = async () => {
    setBusy(true); setError("");
    try { onDone(await sources.compare(picked, Number(size))); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <Card>
      <CardHeader title="Compare" description="Each ticked provider looks up the same leads. Nothing is saved to your leads until you choose." />
      {list.map((p) => (
        <label key={p.id} className="dp-check">
          <input type="checkbox" checked={picked.includes(p.id)} onChange={() => toggle(p.id)} />
          <span>{p.title}{!p.configured && <small className="sub u-m0"> (not set up, will be skipped)</small>}</span>
        </label>
      ))}
      <div className="u-mt-12 u-maxwidth-220">
        <label htmlFor="sample-size">How many leads to test</label>
        <input id="sample-size" type="number" min="1" max={MAX_SAMPLE} value={size} onChange={(e) => setSize(e.target.value)} />
      </div>
      <div className="row u-mt-12">
        <Button disabled={busy || picked.length === 0 || !(size >= 1 && size <= MAX_SAMPLE)} onClick={run}>
          {busy ? "Running… this can take a few minutes" : "Run comparison"}
        </Button>
      </div>
      <Alert text={error} />
    </Card>
  );
}

function ResultRow({ m, run, onApplied }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  if (m.skipped) {
    return <tr><td><b>{m.title}</b></td><td colSpan={9}><Badge tone="warning">Skipped</Badge> {m.reason}</td></tr>;
  }
  const applied = (run.applied || []).includes(m.provider);
  const use = async () => {
    setBusy(true); setError(""); setNote("");
    try {
      const r = await sources.apply(run.id, m.provider);
      setNote(`Added ${r.filled_emails} emails and ${r.filled_phones} phones.`);
      onApplied();
    } catch (e) { setError(e.message); }
    setBusy(false);
  };
  return (
    <tr>
      <td><b>{m.title}</b>{m.stopped && <><br /><small className="dp-hot">Stopped: {m.stopped}</small></>}</td>
      <td>{m.leads_checked}</td>
      <td>{m.with_email}</td>
      <td>{m.with_phone}</td>
      <td>{m.coverage_pct}%</td>
      <td>{m.credits_used}</td>
      <td>{m.credits_used === 0 ? "Free" : m.cost_per_contact}</td>
      <td>{m.bounced ?? 0}</td>
      <td title={(m.error_messages || []).join("\n")}>{m.errors}</td>
      <td>
        {isOwner() ? <Button variant="secondary" disabled={busy || applied} onClick={use}>
          {applied ? "Used" : busy ? "Saving…" : "Use these results"}
        </Button> : applied ? "Used" : ""}
        {note && <><br /><small className="dp-ok">{note}</small></>}
        <Alert text={error} />
      </td>
    </tr>
  );
}

function Results({ run, onApplied }) {
  if (!run) return null;
  return (
    <Card className="an-card-table an-section">
      <h3>Results for {run.sample_size} leads <small className="sub">({when(run.at)})</small></h3>
      <DataTable label="Comparison results">
        <thead><tr>
          <th>Provider</th><th>Checked</th><th>Found email</th><th>Found phone</th><th>Coverage %</th>
          <th>Credits</th><th>Cost per contact</th><th>Bounced</th><th>Errors</th><th />
        </tr></thead>
        <tbody>{run.providers.map((m) => <ResultRow key={`${run.id}-${m.provider}`}m={m} run={run} onApplied={onApplied} />)}</tbody>
      </DataTable>
      <p className="an-notes u-p-16">"Use these results" only fills emails and phones that are empty, and skips unsubscribed or disqualified leads. Bounced grows once sending starts.</p>
    </Card>
  );
}

function ImportCard() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const readFile = async (e) => {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    try { setText(await file.text()); setError(""); }
    catch { setError("Could not read that file."); }
  };

  const send = async () => {
    setBusy(true); setError(""); setResult(null);
    try { setResult(await sources.importScraper(text)); }
    catch (e) { setError(e.message); }
    setBusy(false);
  };

  return (
    <Card>
      <CardHeader title="Import from the website contact scraper" description="Run the open-source scraper on your computer (homepage or key pages only, no proxies), then paste or upload its JSON." />
      <label htmlFor="scraper-file">JSON file</label>
      <input id="scraper-file" type="file" accept=".json,application/json" onChange={readFile} />
      <label htmlFor="scraper-json">Or paste the JSON</label>
      <textarea id="scraper-json" rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder='[{"domain": "example.in", "emails": [...], "phones": [...]}]' />
      <div className="row u-mt-12">
        <Button disabled={busy || !text.trim()} onClick={send}>{busy ? "Importing…" : "Import"}</Button>
      </div>
      <Alert text={error} />
      {result && (
        <div className="tip u-mt-12">
          Matched <b>{result.matched}</b> leads. Added <b>{result.filled_emails}</b> emails and <b>{result.filled_phones}</b> phones.
          {result.unmatched_domains.length > 0 && <><br /><small>No lead for: {result.unmatched_domains.join(", ")}</small></>}
        </div>
      )}
    </Card>
  );
}

function History({ runs, current, onShow }) {
  return (
    <Card className="an-section">
      <CardHeader title="Past comparisons" />
      {runs.length === 0 && <EmptyState icon="history" title="No comparisons yet">Run a comparison above; each run is kept here so you can look back.</EmptyState>}
      {runs.map((r) => (
        <div className="dp-partner" key={r.id}>
          <span className="u-flex1 u-minwidth-220">
            <b>{when(r.at)}</b> · {r.sample_size} leads<br />
            <small className="sub">
              {r.providers.map((m) => (m.skipped ? `${m.title}: skipped` : `${m.title}: ${m.coverage_pct}%`)).join(" · ")}
            </small>
          </span>
          <Button variant="secondary" disabled={current && current.id === r.id} onClick={() => onShow(r)}>
            {current && current.id === r.id ? "Showing" : "Show"}
          </Button>
        </div>
      ))}
    </Card>
  );
}

export default function DataSources({ go }) {
  const [list, setList] = useState(null);
  const [runs, setRuns] = useState([]);
  const [current, setCurrent] = useState(null);
  const [error, setError] = useState("");

  const loadRuns = async (keepId) => {
    const latest = await sources.runs();
    setRuns(latest);
    setCurrent(latest.find((r) => r.id === keepId) || latest[0] || null);
  };

  useEffect(() => {
    Promise.all([sources.providers(), sources.runs()])
      .then(([p, r]) => { setList(p); setRuns(r); setCurrent(r[0] || null); })
      .catch((e) => setError(e.message));
  }, []);

  const refresh = (keepId) => loadRuns(keepId).catch((e) => setError(e.message));

  if (error && !list) return <><PageHeader title="Data sources" /><Alert text={error} /></>;
  if (!list) return <div className="dp-skel" aria-busy="true"><Skeleton height={36} width="40%" /><Skeleton height={220} /></div>;

  return (
    <>
      <PageHeader title="Data sources" description="Try free plans side by side on the same Indian leads, then keep the best. Only public business contacts are used, and every value keeps where it came from." />
      <Alert text={error} />
      <div className="dp-two">
        <ProviderList list={list} go={go} />
        {isOwner() && <CompareForm list={list} onDone={(run) => { setCurrent(run); refresh(run.id); }} />}
      </div>
      <Results run={current} onApplied={() => current && refresh(current.id)} />
      {isOwner() && <div className="an-section"><ImportCard /></div>}
      <CompanyRegisterCard />
      <History runs={runs} current={current} onShow={setCurrent} />
    </>
  );
}
